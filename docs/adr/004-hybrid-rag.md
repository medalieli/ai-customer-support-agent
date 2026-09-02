# ADR-004: Hybrid retrieval with validated citations

- **Status:** Accepted (M0)
- **Context:** Policy and product answers require both semantic recall and exact term/version matching.
- **Decision:** Store approved versioned chunks in PostgreSQL; combine pgvector and full-text retrieval, fuse results, rerank locally with a pinned model, and deterministically validate tenant/version/citation support before display.
- **Consequences:** The system avoids a separate search service and produces traceable evidence. Ingestion governance, index tuning and bilingual evaluation are mandatory; absent evidence yields abstention.
