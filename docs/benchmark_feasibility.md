# Benchmark feasibility check (2026-11-18, no model calls)

Both candidate benchmarks were installed outside the repo in `~/fpr-bench/` (separate environments, never in
the project venv) and exercised without an LLM.

## τ²-bench (sierra-research/tau2-bench, commit c06b07d, 2026-11-10, v2.0.0, MIT)

- Install: `git clone` + `uv sync`, Python 3.12–3.13, pydantic v2 (compatible with this project). Fully offline;
  LLM calls go through litellm, so the agents can point at the local vLLM endpoint.
- Domains and tasks: retail 114, airline 50, telecom 2,285 (20 in the small split), banking_knowledge 97.
- Tools are typed READ / WRITE / GENERIC in the toolkit:
  - retail: 7 read, 7 write (cancel / modify / exchange / return orders, modify address or payment);
  - airline: 6 read, 6 write (book / cancel reservation, send certificate, update flights / baggage / passengers).
- Ground truth: every task lists the expected actions; the scorer compares the final database hash with a gold
  database built by replaying those actions.
- Offline check (script in the session scratchpad, logic below): replaying the gold actions reproduces the gold DB
  hash in 114/114 retail and 50/50 airline tasks (15 s per domain). Tasks with >= 1 write: retail 104, airline 26.
  Changing one string argument of the first write changes the final DB in 101/104 and 26/26 → about 127 tasks
  satisfy the Phase 1 critical-fact rule without modification.
- Built-in no-user mode ("solo": the agent gets all tools and the instruction up front) matches the planned
  single-instruction adapter, so the adaptation is an existing benchmark mode, not our invention.
- Money and bookings are real effects in the domain semantics (refunds to payment methods, cancellations,
  certificates), so the external / compensable / irreversible labels can be assigned per write tool.
- Gate risk: moderate. Retail and airline are hard for small models, but there are ~127 candidate tasks to screen.

## AppWorld (appworld 0.1.3.post1, PyPI)

- Install: separate venv (it pins pydantic 1.10, incompatible with this project's pydantic v2), then
  `appworld install` and `appworld download data` (193 MB). Offline after the download.
- Tasks: train 89, dev 56, test_normal 167, test_challenge 416 (732 task folders). Ground-truth solutions are
  shipped for train and dev only; test sets are scored by the bundled tests.
- 9 apps (Spotify alone has 91 APIs; ~457 total). The native interface is code execution (`apis.<app>.<api>(...)`
  inside `world.execute`), with state-based unit tests that include collateral-damage checks.
- Offline check: loading a dev task, calling the API-docs app, and running `world.evaluate()` works (1.8 s);
  the evaluation returns success, passes and failures per test.
- Adapter cost: high. Each API must be exposed as a function tool (or the executor becomes a code agent), the
  researcher must search API docs (too many tools for one prompt), and episodes are long (16k context is tight).
- Gate risk: high for Qwen3-8B with thinking off; published small-model results on AppWorld are low.
- Data policy: the dataset ships a "README before sharing" note; test-set tasks and traces must not be committed or
  exported in plain text. Exported AppWorld traces stay out of `data/` unless encrypted or limited to train/dev.

## Decision

- B2 τ²-bench retail + airline: go. Build the adapter first; it reuses the Phase 1 critical-fact rule as is.
- B3 AppWorld: keep as the third benchmark, but build it only after τ²-bench passes the gate; prepare the BFCL-v3
  multi-turn fallback in parallel because of the gate risk.
- GPU: none used for this check. Screening τ²-bench (164 tasks × 2 modes × 2 seeds ≈ 650 episodes) is about
  30–45 min on Qwen3-8B.
