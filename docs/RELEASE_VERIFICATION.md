# Portfolio release verification — 2026-09-09

This record is being completed from actual release checks. Pending checks are not passes.

| Check | Result |
| --- | --- |
| Full-history Gitleaks 8.28.0 | Pass: 24 existing commits, no leaks |
| Historical file inventory | 591 blobs; no private markers or oversized files; largest 285,782 bytes |
| Release snapshot Gitleaks | Pass: 284 publishable files, no leaks |
| Backend tests and combined coverage | 251 passed, 88.12% coverage; 13 warnings; isolated database and mock providers |
| Backend Ruff and strict MyPy | Pass: 115 formatted files, 102 typed app/test files |
| Frontend unit tests | 7 passed; scoped coverage: 100% lines/statements/functions, 75% branches |
| Frontend lint, TypeScript, build | Pass; final browser theme checks pending |
| Mock commerce | 13 passed, 91.71% coverage; Ruff and strict MyPy pass |
| Mock CRM | 11 passed, 92.15% coverage; Ruff and strict MyPy pass |
| Desktop/mobile Playwright | Pass: 19 desktop and 19 mobile scenarios; persistence verified after restart |
| OpenAI browser smoke and portfolio capture | Pass: real OpenAI plus mock providers; 1 portfolio journey passed and 8 screenshots refreshed |
| Clean-checkout quick start | Pending |
| Compose and documentation validation | Base/demo/production-like Compose pass; 44 local links/images in 48 Markdown files pass |
| GitHub Actions | Not yet published |

Frontend coverage is configured only for `components/api-status.tsx`; it is not whole-interface coverage. Backend coverage excludes the existing worker, models, seeds, API agent endpoint, live vendor adapters, and application entry point. No thresholds or exclusions were relaxed for this release.

The routing evaluator maps classified intents to tools. Its citation metric measures routing to retrieval, not citation correctness; its zero side-effect counts do not prove end-to-end idempotency. Browser/API tests separately exercise citations, confirmations, and retry behavior. Recorded OpenAI results vary with provider output.

The performance figures in the README are the historical 2026-09-06 deterministic local baseline, not a new load run or an OpenAI performance claim. No Shopify or HubSpot APIs are called during release validation.

The deterministic routing evaluation passed all 52 cases again on 2026-09-09; its release baseline is in `backend/evaluation/baselines/deterministic-2026-09-08.json`. npm audit reported zero vulnerabilities. Bandit reported no medium/high findings. A direct-pinned-dependency pip-audit reported no known vulnerabilities; the first full resolver attempt failed on local DNS and is not recorded as a complete dependency-graph pass.

The initial backend pass found an incorrect test field name, a probabilistic ciphertext tampering mutation, and a database connection timeout during Docker image export. The field assertion and tampering mutation were corrected, terminal webhook regression coverage was added, and the complete suite subsequently passed without relaxing the 88% gate.
