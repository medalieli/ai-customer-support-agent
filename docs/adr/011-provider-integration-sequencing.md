# ADR-011: Provider integration delivery in M5

- **Status:** Accepted (M5)
- **Context:** The implementation request moves provider ports and optional Shopify/HubSpot adapters ahead of the durable conversation work originally labeled M5. ADR-003 already requires these adapters and their shared contracts.
- **Decision:** M5 delivers `CommerceProviderV1`, `CrmProviderV1`, normalized models, mock HTTP adapters, persistent mock CRM, and credential-gated real adapters. M12/M13 become live-account validation milestones; durable conversation/orchestration sequencing remains to be assigned before that work starts.
- **Consequences:** Local mock mode is useful earlier and adapter behavior is testable without vendor accounts. No agent tool, consent, confirmation, refund-decision, or real-money authorization is implied by an adapter existing.
