# ADR-003: Provider ports and normalized models

- **Status:** Accepted (M0)
- **Context:** The public demo cannot depend on credentials, while the portfolio must prove Shopify and HubSpot integration.
- **Decision:** Independently select `COMMERCE_PROVIDER=mock|shopify` and `CRM_PROVIDER=mock|hubspot`. Both implementations satisfy application-owned versioned ports, typed errors and normalized models; contract suites run against all adapters.
- **Consequences:** Local Demo Mode is reliable and synthetic, mixed configurations work, and agent logic is vendor-neutral. Adapters bear normalization and capability differences.
