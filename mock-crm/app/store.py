import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.errors import CrmError


class CrmStore:
    def __init__(self, path: str) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS contacts (
              organization_id TEXT NOT NULL, external_ref TEXT NOT NULL, email TEXT NOT NULL,
              data TEXT NOT NULL, version INTEGER NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              PRIMARY KEY (organization_id, external_ref), UNIQUE (organization_id, email));
            CREATE TABLE IF NOT EXISTS notes (
              organization_id TEXT NOT NULL, external_ref TEXT NOT NULL, contact_ref TEXT NOT NULL,
              body TEXT NOT NULL, created_at TEXT NOT NULL,
              PRIMARY KEY (organization_id, external_ref));
            CREATE TABLE IF NOT EXISTS leads (
              organization_id TEXT NOT NULL, external_ref TEXT NOT NULL, contact_ref TEXT NOT NULL,
              data TEXT NOT NULL, version INTEGER NOT NULL, created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL, PRIMARY KEY (organization_id, external_ref),
              UNIQUE (organization_id, contact_ref));
            CREATE TABLE IF NOT EXISTS idempotency (
              organization_id TEXT NOT NULL, operation TEXT NOT NULL, key TEXT NOT NULL,
              fingerprint TEXT NOT NULL, response TEXT NOT NULL,
              PRIMARY KEY (organization_id, operation, key));
            CREATE TABLE IF NOT EXISTS tickets (
              organization_id TEXT NOT NULL, external_ref TEXT NOT NULL,
              conversation_ref TEXT NOT NULL, data TEXT NOT NULL, version INTEGER NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              PRIMARY KEY (organization_id, external_ref));
            CREATE UNIQUE INDEX IF NOT EXISTS uq_active_ticket_conversation
              ON tickets(organization_id, conversation_ref)
              WHERE json_extract(data, '$.status') IN ('open','in_progress');
            CREATE TABLE IF NOT EXISTS ticket_messages (
              organization_id TEXT NOT NULL, external_ref TEXT NOT NULL, ticket_ref TEXT NOT NULL,
              body TEXT NOT NULL, visibility TEXT NOT NULL, created_at TEXT NOT NULL,
              PRIMARY KEY (organization_id, external_ref));
            """)
            self._seed(db)

    def _seed(self, db: sqlite3.Connection) -> None:
        fixtures = [
            (
                "10000000-0000-0000-0000-000000000001",
                "amira.benali@example.test",
                "Amira",
                "Benali",
                "customer",
                "en",
            ),
            (
                "10000000-0000-0000-0000-000000000001",
                "lucas.martin@example.test",
                "Lucas",
                "Martin",
                "lead",
                "fr",
            ),
            (
                "20000000-0000-0000-0000-000000000001",
                "nora.chen@example.test",
                "Nora",
                "Chen",
                "customer",
                "en",
            ),
        ]
        now = datetime.now(timezone.utc).isoformat()
        for org, email, first, last, stage, locale in fixtures:
            ref = str(uuid5(NAMESPACE_URL, f"{org}:{email}"))
            data = json.dumps(
                {
                    "email": email,
                    "first_name": first,
                    "last_name": last,
                    "lifecycle_stage": stage,
                    "locale": locale,
                    "company": None,
                }
            )
            db.execute(
                "INSERT OR IGNORE INTO contacts VALUES (?,?,?,?,1,?,?)",
                (org, ref, email, data, now, now),
            )

    @staticmethod
    def _contact(row: sqlite3.Row) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(row["data"])
        return {
            **data,
            "external_ref": row["external_ref"],
            "provider_status": "active",
            "version": str(row["version"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def find(self, organization_id: str, email: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM contacts WHERE organization_id=? AND lower(email)=lower(?)",
                (organization_id, email),
            ).fetchone()
            return self._contact(row) if row else None

    def find_lead(self, organization_id: str, contact_ref: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM leads WHERE organization_id=? AND contact_ref=?",
                (organization_id, contact_ref),
            ).fetchone()
            if not row:
                return None
            return {
                **json.loads(row["data"]),
                "external_ref": row["external_ref"],
                "status": "open",
                "version": str(row["version"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    def upsert_lead(
        self, organization_id: str, key: str, payload: dict[str, Any], expected_version: str | None
    ) -> dict[str, Any]:
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            replay = db.execute(
                "SELECT * FROM idempotency WHERE organization_id=? AND operation='lead' AND key=?",
                (organization_id, key),
            ).fetchone()
            if replay:
                if replay["fingerprint"] != fingerprint:
                    raise CrmError(409, "idempotency_conflict", "The idempotency key was reused.")
                return dict(json.loads(replay["response"]))
            if not db.execute(
                "SELECT 1 FROM contacts WHERE organization_id=? AND external_ref=?",
                (organization_id, payload["contact_ref"]),
            ).fetchone():
                raise CrmError(404, "not_found", "The contact was not found.")
            existing = db.execute(
                "SELECT * FROM leads WHERE organization_id=? AND contact_ref=?",
                (organization_id, payload["contact_ref"]),
            ).fetchone()
            if existing and expected_version != str(existing["version"]):
                raise CrmError(409, "version_conflict", "The lead changed.")
            now = datetime.now(timezone.utc).isoformat()
            ref = (
                existing["external_ref"]
                if existing
                else str(uuid5(NAMESPACE_URL, f"{organization_id}:lead:{payload['contact_ref']}"))
            )
            version = int(existing["version"]) + 1 if existing else 1
            created = existing["created_at"] if existing else now
            db.execute(
                "INSERT OR REPLACE INTO leads VALUES (?,?,?,?,?,?,?)",
                (
                    organization_id,
                    ref,
                    payload["contact_ref"],
                    json.dumps(payload),
                    version,
                    created,
                    now,
                ),
            )
            result = {
                **payload,
                "external_ref": ref,
                "status": "open",
                "version": str(version),
                "created_at": created,
                "updated_at": now,
            }
            db.execute(
                "INSERT INTO idempotency VALUES (?,?,?,?,?)",
                (organization_id, "lead", key, fingerprint, json.dumps(result)),
            )
            return result

    def upsert(self, organization_id: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            replay = db.execute(
                """SELECT * FROM idempotency
                   WHERE organization_id=? AND operation='contact' AND key=?""",
                (organization_id, key),
            ).fetchone()
            if replay:
                if replay["fingerprint"] != fingerprint:
                    raise CrmError(409, "idempotency_conflict", "The idempotency key was reused.")
                result: dict[str, Any] = json.loads(replay["response"])
                return result
            existing = db.execute(
                "SELECT * FROM contacts WHERE organization_id=? AND lower(email)=lower(?)",
                (organization_id, payload["email"]),
            ).fetchone()
            now = datetime.now(timezone.utc).isoformat()
            ref = (
                existing["external_ref"]
                if existing
                else str(uuid5(NAMESPACE_URL, f"{organization_id}:{payload['email'].lower()}"))
            )
            version = int(existing["version"]) + 1 if existing else 1
            created = existing["created_at"] if existing else now
            db.execute(
                "INSERT OR REPLACE INTO contacts VALUES (?,?,?,?,?,?,?)",
                (
                    organization_id,
                    ref,
                    payload["email"],
                    json.dumps(payload),
                    version,
                    created,
                    now,
                ),
            )
            result = {
                **payload,
                "external_ref": ref,
                "provider_status": "active",
                "version": str(version),
                "created_at": created,
                "updated_at": now,
            }
            db.execute(
                "INSERT INTO idempotency VALUES (?,?,?,?,?)",
                (organization_id, "contact", key, fingerprint, json.dumps(result)),
            )
            return result

    def add_note(
        self, organization_id: str, contact_ref: str, key: str, body: str
    ) -> dict[str, Any]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute(
                "SELECT 1 FROM contacts WHERE organization_id=? AND external_ref=?",
                (organization_id, contact_ref),
            ).fetchone():
                raise CrmError(404, "not_found", "The contact was not found.")
            fingerprint = hashlib.sha256(body.encode()).hexdigest()
            replay = db.execute(
                "SELECT * FROM idempotency WHERE organization_id=? AND operation='note' AND key=?",
                (organization_id, key),
            ).fetchone()
            if replay:
                if replay["fingerprint"] != fingerprint:
                    raise CrmError(409, "idempotency_conflict", "The idempotency key was reused.")
                result: dict[str, Any] = json.loads(replay["response"])
                return result
            now = datetime.now(timezone.utc).isoformat()
            ref = str(uuid5(NAMESPACE_URL, f"{organization_id}:{key}"))
            result = {
                "external_ref": ref,
                "contact_ref": contact_ref,
                "body": body,
                "created_at": now,
            }
            db.execute(
                "INSERT INTO notes VALUES (?,?,?,?,?)",
                (organization_id, ref, contact_ref, body, now),
            )
            db.execute(
                "INSERT INTO idempotency VALUES (?,?,?,?,?)",
                (organization_id, "note", key, fingerprint, json.dumps(result)),
            )
            return result

    @staticmethod
    def _ticket(row: sqlite3.Row) -> dict[str, Any]:
        return {
            **json.loads(row["data"]),
            "external_ref": row["external_ref"],
            "version": str(row["version"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def find_active_ticket(
        self, organization_id: str, conversation_ref: str
    ) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM tickets WHERE organization_id=? AND "
                "conversation_ref=? AND json_extract(data, '$.status') IN ('open','in_progress')",
                (organization_id, conversation_ref),
            ).fetchone()
            return self._ticket(row) if row else None

    def list_tickets(self, organization_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                self._ticket(row)
                for row in db.execute(
                    "SELECT * FROM tickets WHERE organization_id=? ORDER BY created_at",
                    (organization_id,),
                )
            ]

    def get_ticket(self, organization_id: str, ticket_ref: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM tickets WHERE organization_id=? AND external_ref=?",
                (organization_id, ticket_ref),
            ).fetchone()
            return self._ticket(row) if row else None

    def upsert_ticket(
        self, organization_id: str, key: str, payload: dict[str, Any], expected_version: str | None
    ) -> dict[str, Any]:
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            replay = db.execute(
                "SELECT * FROM idempotency WHERE organization_id=? AND "
                "operation='ticket' AND key=?",
                (organization_id, key),
            ).fetchone()
            if replay:
                if replay["fingerprint"] != fingerprint:
                    raise CrmError(409, "idempotency_conflict", "The key was reused.")
                return dict(json.loads(replay["response"]))
            existing = db.execute(
                "SELECT * FROM tickets WHERE organization_id=? AND "
                "conversation_ref=? AND json_extract(data, '$.status') IN ('open','in_progress')",
                (organization_id, payload["conversation_ref"]),
            ).fetchone()
            if existing and expected_version != str(existing["version"]):
                raise CrmError(409, "version_conflict", "The ticket changed.")
            now = datetime.now(timezone.utc).isoformat()
            ref = (
                existing["external_ref"]
                if existing
                else str(
                    uuid5(NAMESPACE_URL, f"{organization_id}:ticket:{payload['conversation_ref']}")
                )
            )
            version = int(existing["version"]) + 1 if existing else 1
            created = existing["created_at"] if existing else now
            db.execute(
                "INSERT OR REPLACE INTO tickets VALUES (?,?,?,?,?,?,?)",
                (
                    organization_id,
                    ref,
                    payload["conversation_ref"],
                    json.dumps(payload),
                    version,
                    created,
                    now,
                ),
            )
            result = {
                **payload,
                "external_ref": ref,
                "version": str(version),
                "created_at": created,
                "updated_at": now,
            }
            db.execute(
                "INSERT INTO idempotency VALUES (?,?,?,?,?)",
                (organization_id, "ticket", key, fingerprint, json.dumps(result)),
            )
            return result

    def add_ticket_message(
        self, organization_id: str, ticket_ref: str, key: str, body: str, visibility: str
    ) -> dict[str, Any]:
        fingerprint = hashlib.sha256(f"{visibility}:{body}".encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute(
                "SELECT 1 FROM tickets WHERE organization_id=? AND external_ref=?",
                (organization_id, ticket_ref),
            ).fetchone():
                raise CrmError(404, "not_found", "The ticket was not found.")
            replay = db.execute(
                "SELECT * FROM idempotency WHERE organization_id=? AND "
                "operation='ticket_message' AND key=?",
                (organization_id, key),
            ).fetchone()
            if replay:
                if replay["fingerprint"] != fingerprint:
                    raise CrmError(409, "idempotency_conflict", "The key was reused.")
                return dict(json.loads(replay["response"]))
            now = datetime.now(timezone.utc).isoformat()
            ref = str(uuid5(NAMESPACE_URL, f"{organization_id}:ticket-message:{key}"))
            result = {
                "external_ref": ref,
                "ticket_ref": ticket_ref,
                "body": body,
                "visibility": visibility,
                "created_at": now,
            }
            db.execute(
                "INSERT INTO ticket_messages VALUES (?,?,?,?,?,?)",
                (organization_id, ref, ticket_ref, body, visibility, now),
            )
            db.execute(
                "INSERT INTO idempotency VALUES (?,?,?,?,?)",
                (organization_id, "ticket_message", key, fingerprint, json.dumps(result)),
            )
            return result
