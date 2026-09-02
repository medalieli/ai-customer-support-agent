import { ApiStatus } from "@/components/api-status";

export default function Home() {
  return (
    <main>
      <section className="hero" aria-labelledby="page-title">
        <p className="eyebrow">NovaCart Engineering</p>
        <h1 id="page-title">Customer support platform foundation</h1>
        <p className="summary">
          The API, worker, PostgreSQL, pgvector, and Redis foundation is ready for later
          milestones. Customer chat and support workflows are intentionally not implemented yet.
        </p>
        <ApiStatus />
      </section>
      <footer>Milestone M1 · Mock providers · Synthetic data only</footer>
    </main>
  );
}
