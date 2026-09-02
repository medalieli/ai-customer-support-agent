# ADR-006: Refund decisions outside RAG and the LLM

- **Status:** Accepted (M0)
- **Context:** Retrieved prose is useful for explanations but unsafe as the final monetary eligibility engine.
- **Decision:** A pure versioned service returns `eligible`, `ineligible`, or `manual_review` plus reason codes, policy version and facts hash. RAG supplies citations only. Initially create refund requests; any Shopify-backed refund requires human approval and no autonomous money movement.
- **Consequences:** Decisions are repeatable and testable. Operations must encode/version policy rules and handle manual-review queues.
