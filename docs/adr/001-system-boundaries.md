# ADR-001: Web, application and worker boundaries

- **Status:** Accepted (M0)
- **Context:** The platform needs streaming customer UX, staff workflows, enforceable domain rules and asynchronous provider work.
- **Decision:** Use Next.js for customer chat and staff console; FastAPI for authentication context, APIs, orchestration and domain/tool enforcement; separate Redis-backed workers for asynchronous jobs. Browsers never call providers or the model directly.
- **Consequences:** Trust and authorization are centralized and independently testable. More deployable components and end-to-end tracing are required.
