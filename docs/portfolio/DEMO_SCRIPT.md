# Portfolio demo script

## Reset before recording

```powershell
.\scripts\demo.ps1 deterministic
.\scripts\demo.ps1 reset
```

```bash
./scripts/demo.sh deterministic
./scripts/demo.sh reset
```

Open `http://localhost:3000`. Default customer: Amira Haddad. Demo-only staff: `novacart` / `support@novacart.test` / `synthetic-demo-password`.

## Exact fictional scenarios

| Scenario | Identity / ID | Exact prompt or action |
|---|---|---|
| Cited return policy | Amira | `What is the NovaCart return policy?` |
| In transit | Amira / `NC-1002` | `Track my order NC-1002` |
| Address confirmation | Amira / `NC-1001` | `Change shipping address NC-1001 immediately; recipient: Amira Haddad; line1: 10 Demo Street; city: Boston; region: MA; postal code: 02113; country code: US` |
| Eligible refund | Amira / `NC-1004`, `MSE-PRO`, USD 69 | `Refund order NC-1004; reason: changed mind; SKU: MSE-PRO; quantity: 1; amount: USD 69.00` |
| Ineligible refund | Lucas / `NC-1006`, `CASE-RED` | `Refund order NC-1006; reason: changed mind; SKU: CASE-RED; quantity: 1; amount: USD 19.00` |
| CRM consent | Amira | `I want an enterprise product demo and consent to being contacted by sales by email` |
| Low confidence | Amira / `NC-9999` | `Track my order NC-9999` |
| Staff lifecycle | Latest “explicit human request” | Claim → reply → Resolve → Return to AI |

Cancel confirmation cards during rehearsal to preserve seed state. Reset after recording approvals.

## 60–90 second Upwork/Fiverr script

1. **0:00–0:10:** Show fictional sign-in. “NovaCart combines grounded answers, live tools, confirmations, and human escalation.”
2. **0:10–0:25:** Ask the return-policy prompt and show its validated source. “Policies come from tenant RAG, not model memory.”
3. **0:25–0:38:** Track `NC-1002`. “Mutable facts come from the commerce API.”
4. **0:38–0:53:** Submit the address prompt and stop at review. “Writes pause for approval and retries are idempotent.” Cancel.
5. **0:53–1:08:** Ask for a human, enter staff, open and claim the ticket. “Uncertain work keeps its context in an audited queue.”
6. **1:08–1:20:** Open Analytics. “Tests, evaluations, security, observability, and local load evidence ship with the project.”

## Four-to-five-minute technical walkthrough

Recommended screen order: README diagrams → FAQ → order → address → refunds → CRM → handoff/staff → analytics → evidence.

1. **Architecture (40s):** Explain Next.js/FastAPI, LangGraph checkpoints, PostgreSQL/pgvector, Redis/worker, ports/adapters, and fictional REST providers.
2. **RAG (35s):** Run the FAQ and show source, section, snippet, and validation.
3. **Live read (25s):** Track `NC-1002`; contrast API state with policy RAG.
4. **Confirmed write (45s):** Run the full address prompt; explain pending action, expiration, checkpoint version, and idempotency.
5. **Refund (40s):** Show `NC-1004` reaching review; switch to Lucas and show final-sale `NC-1006` rejected before a write.
6. **Consent (25s):** Run the enterprise prompt and show CRM review.
7. **Handoff (50s):** Ask `I need a human representative`; staff-login; open “explicit human request”; Claim; reply `Hi Amira — I have your request and will help from here.`; Resolve; Return to AI; show audit.
8. **Evidence (35s):** Show Analytics and verification docs. Call performance a local baseline. State that vendors were contract-tested but not live verified.

## Screenshot regeneration

The gallery comes from `frontend/e2e/portfolio.spec.ts` against an isolated deterministic Compose project. Normal acceptance runs exclude this tagged capture test. Regenerate and safely clean its unique volumes with:

```powershell
.\scripts\capture-portfolio.ps1
```

Never point it at persistent personal data or live providers.
