# Evaluation Plan

## Strategy

Evaluation combines deterministic unit/contract/security tests, offline conversational scenarios and small credential-gated integration suites. All datasets are versioned, synthetic and tagged by language, provider mode, intent mix, risk and expected tool/decision/citation. A release records prompt/model/reranker/policy/tool-schema/dataset versions so regressions are reproducible.

## Test layers and gates

| Layer | Measures | M17 release gate |
|---|---|---|
| Provider contracts | Normalization, capability/error parity, idempotency, timeout behavior | 100% required contract cases for mocks; credential-gated Shopify/HubSpot suites pass. |
| Retrieval | Recall@5, MRR, language/version/tenant filters | Recall@5 >= 0.90 overall and >= 0.85 per language; zero cross-tenant/unapproved chunks. |
| Grounded answers | Claim support, citation precision/coverage, version correctness | >= 0.95 supported claims; 100% policy claims cited; zero fabricated citations in safety set. |
| Intent/routing | Multi-label micro-F1, dependency/risk classification | F1 >= 0.90; 100% guarded writes classified guarded/human-only. |
| Tool behavior | Correct selection/arguments, ownership and budget | >= 0.95 expected read flow; zero unauthorized writes/data disclosures. |
| Refund rules | Decision/reason/policy version/facts hash | 100% boundary-table agreement and determinism; all uncertain/real-provider cases routed as specified. |
| Confirmation/consent | Preview binding, explicitness, replay defense | 100% adversarial suite denied; zero CRM upserts without evidence. |
| Handoff | Trigger recall, factual summary faithfulness, resume correctness | 100% mandatory triggers; >= 0.95 fact precision; zero AI replies while staff-owned. |
| Bilingual | Task success, safety parity, locale/citation correctness | No safety regression vs EN; >= 0.90 task success in EN and FR. |
| Reliability | p95 latency, retries, recovery, queue/webhook semantics | Meets product SLO targets; duplicate events have one effect; restart/resume passes. |

Any cross-tenant/customer disclosure, unconfirmed guarded execution, token replay, forged-webhook acceptance, uncited policy claim, autonomous real refund, or non-consensual CRM creation is a release-blocking failure regardless of aggregate score.

## Dataset design

Start with the 12 traceability scenarios in `DEMO_SCENARIOS.md`, then parameterize orders, lifecycle states, languages, ambiguous phrasing and provider failures. Include refund boundary dates (29/30/31 days), time zones, partial fulfillment/refund, high value, stale versions, duplicated/out-of-order webhooks, multiple orders, “yes” with two actions, malicious retrieved text, cross-customer guessed IDs, and consent negation/withdrawal. Human-reviewed gold records specify intents, allowed tools, expected normalized facts, decision/reason code, citations, action state and escalation.

## Evaluation hooks and review

Each run emits safe structured events: scenario ID, versions, node route, tool name/outcome/latency, citation IDs, rule result, escalation and token/step counts. Offline graders check exact structured fields first; deterministic citation entailment heuristics and a rubric-based model grader may assist but never override safety assertions. Bilingual samples receive native-speaker review before release.

CI runs deterministic mock, rule, authorization, injection, replay and retrieval smoke suites. Nightly runs the full offline corpus and fault injection. Credential-gated adapters run manually or in protected CI, using isolated synthetic external records and cleanup. Pre-release includes staff usability review, red-team pass and metric comparison against the last accepted baseline.

## Production-style monitoring

Dashboards track successful containment, escalation, correction/reopen, tool errors, provider latency, queue lag, citation rejection, refund reason distribution, confirmation expiry/replay, CRM consent, language and cost. Alerts target authorization anomalies, signature failures, unknown write outcomes, citation regressions, queue backlog and adapter circuit opening. Conversation review samples are redacted and access-controlled.

## M4 retrieval baseline

`backend/evaluation/retrieval_cases.json` is a 29-case synthetic baseline covering direct and paraphrased questions, English, French, cross-language retrieval, easily confused policy categories, ambiguity, unsupported information, tenant isolation, and superseded/deleted-source targeting. `python -m app.knowledge.evaluation` executes PostgreSQL FTS/pgvector/fusion/configured-reranker retrieval; it does not use expected labels as retrieval input and does not generate answers. It reports Recall@5, MRR, unsupported/isolation false positives, mean latency and p95 latency.

Provider labels are mandatory in reports. The deterministic-provider run is an automated regression baseline only. A production-semantic baseline requires successful OpenAI reingestion plus the real cross-encoder; missing credentials or model weights block that result rather than authorizing fallback. The corpus remains deliberately small, so even real-provider numbers establish reproducibility rather than production representativeness.

### M4 measured baselines

Both runs used 1,536-dimensional vectors, the same 29 cases and `top_k=5`. The real-provider run used OpenAI `text-embedding-3-small`, `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` and the calibrated cross-encoder relevance threshold `-3.07`. Latency includes query embedding, PostgreSQL hybrid retrieval, local reranking and citation persistence on the local development machine.

| Provider configuration | Recall@5 | MRR | Unsupported/isolation false positives | Mean latency | P95 latency |
|---|---:|---:|---:|---:|---:|
| Deterministic fake embedding + deterministic reranker (automated-test baseline) | 0.954545 | 0.918182 | 0 | 18.757 ms | 21.417 ms |
| OpenAI `text-embedding-3-small` + multilingual cross-encoder (real semantic baseline) | 0.909091 | 0.909091 | 0 | 1061.870 ms | 1011.193 ms |

The p95 being lower than the mean is possible here because one cold-start/model initialization observation raises the mean while the nearest-rank p95 statistic excludes that single maximum. The real baseline meets the M4 overall Recall@5 gate; two difficult paraphrases remain misses and are retained as regression targets rather than rewritten to inflate the score.
