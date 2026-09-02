# ADR-009: Model API, strict tools and persisted reasoning

- **Status:** Accepted (M0)
- **Context:** Orchestration needs structured tool proposals without exposing authority or retaining unnecessary reasoning.
- **Decision:** Use OpenAI Responses API with strict JSON tool schemas and bounded calls. Persist only visible messages, structured state/tool requests/results, evidence, decisions, reason codes and user-visible summaries; never request or store private chain-of-thought.
- **Consequences:** Operational state is sufficient for audit and resume while privacy risk is reduced. Model/version changes require evaluation, and application validation remains mandatory.
