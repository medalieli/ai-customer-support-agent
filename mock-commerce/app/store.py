import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.errors import CommerceError
from app.schemas import AddressUpdate, Order, RefundCreate, RefundRequest
from app.seed import SEEDED_AT, seeded_orders


class CommerceStore:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    organization_id TEXT NOT NULL,
                    customer_ref TEXT NOT NULL,
                    external_ref TEXT NOT NULL,
                    order_number TEXT NOT NULL,
                    scenario TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (organization_id, external_ref)
                );
                CREATE INDEX IF NOT EXISTS ix_orders_customer
                    ON orders (organization_id, customer_ref, order_number);
                CREATE TABLE IF NOT EXISTS idempotency_records (
                    organization_id TEXT NOT NULL,
                    customer_ref TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    response_payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (organization_id, customer_ref, operation, idempotency_key)
                );
                """
            )
            for organization_id, customer_ref, payload in seeded_orders():
                connection.execute(
                    """INSERT OR IGNORE INTO orders
                    (organization_id, customer_ref, external_ref, order_number,
                     scenario, version, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        organization_id,
                        customer_ref,
                        payload["external_ref"],
                        payload["order_number"],
                        payload["scenario"],
                        payload["version"],
                        json.dumps(payload, separators=(",", ":")),
                    ),
                )

    def list_orders(
        self, organization_id: str, customer_ref: str, limit: int, cursor: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        try:
            offset = int(cursor or "0")
        except ValueError as exc:
            raise CommerceError(422, "validation", "The pagination cursor is invalid.") from exc
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT payload FROM orders WHERE organization_id=? AND customer_ref=?
                ORDER BY order_number LIMIT ? OFFSET ?""",
                (organization_id, customer_ref, limit + 1, offset),
            ).fetchall()
        values = [json.loads(row["payload"]) for row in rows]
        next_cursor = str(offset + limit) if len(values) > limit else None
        return values[:limit], next_cursor

    def get_order(self, organization_id: str, customer_ref: str, external_ref: str) -> Order:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT payload FROM orders
                WHERE organization_id=? AND customer_ref=? AND external_ref=?""",
                (organization_id, customer_ref, external_ref),
            ).fetchone()
        if row is None:
            raise CommerceError(404, "not_found", "The requested order was not found.")
        return Order.model_validate_json(row["payload"])

    @staticmethod
    def fingerprint(value: dict[str, Any]) -> str:
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def _replay(
        self,
        connection: sqlite3.Connection,
        organization_id: str,
        customer_ref: str,
        operation: str,
        key: str,
        fingerprint: str,
    ) -> Order | None:
        row = connection.execute(
            """SELECT request_fingerprint, response_payload FROM idempotency_records
            WHERE organization_id=? AND customer_ref=? AND operation=? AND idempotency_key=?""",
            (organization_id, customer_ref, operation, key),
        ).fetchone()
        if row is None:
            return None
        if row["request_fingerprint"] != fingerprint:
            raise CommerceError(409, "idempotency_conflict", "The idempotency key was reused.")
        return Order.model_validate_json(row["response_payload"])

    def _write_result(
        self,
        connection: sqlite3.Connection,
        organization_id: str,
        customer_ref: str,
        operation: str,
        key: str,
        fingerprint: str,
        order: Order,
    ) -> None:
        connection.execute(
            """INSERT INTO idempotency_records VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                organization_id,
                customer_ref,
                operation,
                key,
                fingerprint,
                order.model_dump_json(),
                SEEDED_AT,
            ),
        )

    def update_address(
        self,
        organization_id: str,
        customer_ref: str,
        external_ref: str,
        command: AddressUpdate,
        version: int,
        key: str,
    ) -> Order:
        operation = f"address:{external_ref}"
        fingerprint = self.fingerprint(
            {"version": version, "body": command.model_dump(mode="json")}
        )
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._replay(
                connection, organization_id, customer_ref, operation, key, fingerprint
            )
            if replay:
                connection.commit()
                return replay
            order = self.get_order(organization_id, customer_ref, external_ref)
            if order.version != version:
                raise CommerceError(409, "version_conflict", "The order version is stale.")
            if order.status != "open" or order.fulfillment_status != "unfulfilled":
                raise CommerceError(
                    409, "order_state_conflict", "The shipping address can no longer be changed."
                )
            order.shipping_address = command.address
            order.version += 1
            serialized = order.model_dump_json()
            connection.execute(
                """UPDATE orders SET version=?, payload=?
                WHERE organization_id=? AND customer_ref=? AND external_ref=?""",
                (order.version, serialized, organization_id, customer_ref, external_ref),
            )
            self._write_result(
                connection, organization_id, customer_ref, operation, key, fingerprint, order
            )
            connection.commit()
            return order

    def create_refund(
        self,
        organization_id: str,
        customer_ref: str,
        external_ref: str,
        command: RefundCreate,
        version: int,
        key: str,
    ) -> Order:
        operation = f"refund:{external_ref}"
        fingerprint = self.fingerprint(
            {"version": version, "body": command.model_dump(mode="json")}
        )
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._replay(
                connection, organization_id, customer_ref, operation, key, fingerprint
            )
            if replay:
                connection.commit()
                return replay
            order = self.get_order(organization_id, customer_ref, external_ref)
            if order.version != version:
                raise CommerceError(409, "version_conflict", "The order version is stale.")
            if order.status in {"cancelled", "refunded"}:
                raise CommerceError(
                    409,
                    "order_state_conflict",
                    "A refund request cannot be created for this order state.",
                )
            if (
                command.amount.currency != order.total.currency
                or command.amount.amount <= 0
                or command.amount.amount > order.total.amount
            ):
                raise CommerceError(422, "validation", "The requested refund amount is invalid.")
            order.refund_requests.append(
                RefundRequest.model_validate(
                    {
                        "external_ref": f"refund-{uuid4()}",
                        "status": "requested",
                        "requested_at": "2026-09-02T12:00:00Z",
                        "amount": command.amount,
                        "reason": command.reason,
                    }
                )
            )
            order.version += 1
            serialized = order.model_dump_json()
            connection.execute(
                """UPDATE orders SET version=?, payload=?
                WHERE organization_id=? AND customer_ref=? AND external_ref=?""",
                (order.version, serialized, organization_id, customer_ref, external_ref),
            )
            self._write_result(
                connection, organization_id, customer_ref, operation, key, fingerprint, order
            )
            connection.commit()
            return order
