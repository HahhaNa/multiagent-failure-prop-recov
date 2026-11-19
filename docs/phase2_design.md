# Phase 2 design: certified selective recovery for LLM multi-agent pipelines

Status date: 2026-11-18. Target: NeurIPS 2027 main track; the course deliverable (2026-12-10) is Tier 1 of the same
design. This file fixes definitions, method, baselines, run matrix and success criteria before any Phase 2 code runs
on a real model. It supersedes `docs/revised_plan.md` §7–8 where they differ (§12 lists the differences).
Pre-registration: Amendment 7 in `docs/preregistration.md` points here.

## 0. Contribution statement (what is new)

1. **Interventional dependence for stochastic agent pipelines** (§2): a definition of when a downstream artifact
   depends on a corrupted input, stated on output distributions under an intervention, with an estimator and a
   ground-truth protocol.
2. **A static-analysis incompleteness result and a query lower bound** (§3): no analysis that reads only program
   structure and literal values (declared edges, data flow, taint) can certify recall of dependent artifacts; any
   procedure with recall >= 1 − α on black-box stochastic producers must re-query them, and a sequential test attains
   the optimal expected number of queries per artifact.
3. **Certified selective recovery (R4p-SPRT)** (§4): a recovery procedure whose invalidation recall is >= 1 − α under
   stated assumptions, with per-object restore and compensation of external effects, and a measured recall/cost
   frontier against restart, rollback, LLM-judge and graph-only baselines.
4. **Evidence across benchmarks and topologies** (§7; model families deferred), plus the Phase 1 measurement (counterfactual
   accuracy of dependency graphs; accidental vs adversarial faults; irreversibility windows).

Closest work and the difference: dependency-guided rollback repair for single memory-augmented agents (arXiv
2608.10502) uses a provenance graph without a recall guarantee or counterfactual ground truth; SagaLLM, Atomix and
Cordon address compensation and gating, not what to recompute; Taser, Retro and Warp (intrusion recovery) re-execute
and compare deterministic programs, while LLM producers are stochastic and launder values through semantics, which is
what §2–§4 handle. Failure-attribution work (Who&When, EDGE, CHIEF, FALAT) locates the root cause; here the root
cause is given and the question is which downstream artifacts to recompute.

## 1. Setting

A pipeline run is a DAG of artifacts. Each artifact a is produced by one LLM call (or a tool call) from its parents:
x_a ~ P_a(· | x_pa(a)). Artifact levels in the AgentDojo harness: researcher claims (L1), plan steps (L2), executor
write calls (L3). A fault is an intervention that replaces the origin artifact o by a corrupted value x̃_o. A finished
faulted run F (R0) is one sample under do(x_o = x̃_o). Recovery is told o and the correct x_o (detection oracle);
§6 adds an imperfect detector.

## 2. Interventional dependence

Let F's shared prefix be fixed (all artifacts produced before o). For a downstream artifact u with realized value
v_u in F, let

  p_u = P( sig(X_u) = sig(v_u) | do(x_o), prefix ),

the probability that a pipeline run with the correct input reproduces u's realized value, where sig is the E1
normalized signature (claims: value; steps and calls: tool + normalized args). Sensitivity: E2 (critical fields).

**Definition.** u is *dependent* if p_u <= τ0 and *reusable* if p_u >= τ1, with 0 <= τ0 < τ1 (indifference zone in
between). Default τ0 = 0.05, τ1 = 0.30. A reusable artifact is one a correct run produces with non-negligible
probability, so reusing it leaves the recovered run inside the support of correct runs.

**Phase 1 definition as a special case.** "Absent from all k clean continuations" is the plug-in test with rejection
when 0 of k samples match; at k = 5 it labels as dependent every u with p_u < ~0.45 at 95%, which mixes dependent and
indifference-zone artifacts. Phase 1 numbers are restated with this caveat.

**Ground truth.** On an evaluation subset, k_gt = 30 fresh clean continuations per (task, mode, branch point), seeds
disjoint from every probe and recovery seed (gt: 300–329; probes: 400+; recovery: 200+). p̂_u = matches / 30;
dependent if the one-sided 95% upper bound of p_u is <= τ1 and p̂_u <= τ0; reusable if the lower bound >= τ0 and
p̂_u >= τ1; otherwise indifference (excluded from recall and precision, counted and reported).

## 3. Theory (proofs in the paper appendix; sketches here)

Assumptions. (A1) Complete instrumentation: every information path between agents passes through a logged artifact
(messages, blackboard objects, tool returns), so the data-flow graph over-approximates dependence (Phase 1: data-flow
recall 1.0 by construction). (A2) Given corrected parents, probe samples are i.i.d. draws from P_u (fixed seeds per
probe, temperature as in the run). (A3) The match function is the same sig used in §2.

- **Proposition 1 (incompleteness of static analysis).** Let S be any analysis whose output is a function of the
  DAG structure and the literal values of artifacts. For any S with recall 1 on literal copies, there exist producers
  (derived values, paraphrase, omission, behavior change such as an added plan step) for which u is dependent and S
  does not flag u; and producers with identical structure and literals for which u is not dependent. Hence S cannot
  certify recall. Empirical counterpart: taint recall 0.55–0.62, declared ∪ taint 0.72–0.73 (Phase 1), broken down
  by channel type in §8.
- **Proposition 2 (query lower bound).** For black-box P_u, any test that decides dependent vs reusable with false-
  reuse probability <= α and false-invalidation probability <= β needs, in expectation under either hypothesis, at
  least the Wald bound E[n] >= ((1−α) log((1−α)/β) + α log(α/(1−β))) / KL(Bern(τ0) ‖ Bern(τ1)) samples (and the
  symmetric expression under H1). Zero probes give no guarantee.
- **Proposition 3 (optimality and recall).** Wald's SPRT between Bern(τ0) and Bern(τ1) attains the minimum expected
  sample size among tests with these error rates (Wald–Wolfowitz). Under A1–A3, invalidating every artifact flagged
  by declared ∪ taint and testing every remaining data-flow descendant with SPRT gives expected invalidation recall
  >= 1 − α over dependent artifacts, minus the share left unprobed by the budget, which is reported separately.

Numbers for the defaults (α = 0.05, β = 0.20, τ0 = 0.05, τ1 = 0.30): a match adds log(τ1/τ0) = 1.79 to the log-
likelihood ratio and a miss adds log((1−τ1)/(1−τ0)) = −0.31; decide reuse at >= log((1−β)/α) = 2.77 (2 matches with
few misses) and dependence at <= log(β/(1−α)) = −1.56 (6 misses with no match). Truncation at n_max = 8 probes;
an undecided artifact is invalidated (conservative).

## 4. Method: R4p-SPRT

Recovery episode shared by all methods:
1. **Prefix.** Replay F's calls before the fault from the cache (reference seed). At the fault point the injector is
   off and the episode branches.
2. **World at the branch point.** F's effects after the fault are applied at the branch point, not at episode
   start, so the prefix sees the environment it saw in F.
3. **Corrected origin.** R.out: uninjected findings (cached, no live call). P.out / S.write: uninjected plan
   (cached). R.tool / R.start: the researcher continues live from the fault point. P.start: planner live. E.tool: the
   correct tool return replaces the corrupted one in the executor's context.
4. **Invalidation, in topological order (L1 → L2 → L3).** Flagged = declared ∪ taint → invalidated. Candidates U =
   data-flow descendants of o not flagged. For u ∈ U: probe = re-issue the request that produced u (rebuilt from F's
   transcripts) with corrected parents substituted, at a fresh probe seed; one sample per probe; SPRT per §3. A planner
   probe yields one sample for every L2 step at once; an executor probe yields one sample for one L3 call. Invalidated
   u → its data-flow descendants enter U (or are invalidated if their producer must rerun anyway).
5. **Budget.** B = maximum probe calls per branch; B ∈ {0, 4, 8, 16, 32, ∞}. Order: level, then position in F.
   Unprobed members of U are reused and counted as "unprobed reuse". B = 0 is the graph-only method.
6. **State repair** for invalidated L3 calls. Reversible: per-object restore (rebuild each touched env object from
   its pre-fault version plus F's kept writes in order). Compensable (`send_money`): refund of the same amount from
   the recipient by the world actor; succeeds only if the recipient IBAN appears in the task's initial transaction
   history. Irreversible (including calendar invitations, Amendment 5.3): stays; unrecoverable effect.
7. **Recomputation.** Partial replanning (planner keeps reused steps, regenerates steps depending on invalidated
   claims); executor runs only new or changed steps in the repaired world; kept L3 calls are not re-executed.
8. **Scoring.** AgentDojo and strict utility, residual harm, duplicated external effects (same tool and args as an
   effect of F), unrecoverable effects, recovery tokens = all calls after the branch point including probes, counted
   whether live or replayed from the cache.

## 5. Baselines and ablations

| Id | Invalidation | Purpose |
|---|---|---|
| R0 | none | faulted run as is |
| R1 | everything (full restart; F's external effects re-applied) | existing `restart` runs |
| R2 | everything after the fault (rollback to the fault point) | strongest simple competitor |
| G-B0 | declared ∪ taint, no probes | graph-only (Prop. 1 in practice) |
| LLM-judge | one same-model call per branch lists dependent artifacts given the correction | cheap semantic alternative (2608.10502 reports high judge F1) |
| Support | provenance reachability minus artifacts with an independent trusted support (2608.10502-style) | closest prior method, re-implemented |
| R4p-1shot | one probe per candidate, invalidate on mismatch | shows what SPRT adds (noise) |
| R4p-SPRT | §4 | proposed |
| Oracle | ground-truth dependent set (§2) | upper bound |
| Ablations | R4p without compensation; with whole-snapshot restore; τ1 ∈ {0.2, 0.5}; α ∈ {0.01, 0.10} | sensitivity |

**Probe calibration control.** SPRT run on artifacts of clean continuations treated as if faulted (no actual fault):
the observed invalidation rate estimates the false-invalidation rate; it must be <= β + CI.

## 6. Realistic detection

A third detector, MELON-style masked re-execution (ICML 2025; rerun the executor step with a task-neutral prompt
and compare tool calls), is added as the strongest published re-execution detector. Besides the detection oracle:
detector = R3's verifier verdicts (V1/V2/V3 issues) on the same branch, available only
at the stage where the verifier runs. Recovery starts at the first verifier flag; faults the verifier misses are not
recovered. Reported as R3+R4p vs R3 alone, with the Phase 1 irreversibility windows: share of fault-caused
irreversible effects that occur before the first flag.

## 7. Breadth (D)

Priority (author's decision, 2026-11-18): more benchmarks first, then topologies; larger models are deferred (§7.3).

| Axis | Tier 1 (course, 2026-12-10) | Tier 2 (NeurIPS) |
|---|---|---|
| Benchmarks | AgentDojo banking/workspace/slack, 14 tasks; τ-bench adapter built and gated on MockLLM / pilot if time allows | AgentDojo expanded to >= 30 tasks; + τ-bench (B2); + AppWorld (B3, fallback BFCL-v3 multi-turn); target >= 100 tasks total (§7.2) |
| Topologies | T1 linear researcher → planner → executor | + T2 hierarchical (orchestrator dispatches parallel sub-researchers, one per information need, and merges) on >= 2 benchmarks; T3 (planner ↔ critic cycle, <= 2 rounds) if time remains |
| Models | Qwen3-8B | Qwen3-8B on every benchmark; Qwen3-14B replication on AgentDojo; other models deferred (§7.3) |
| Faults | injected (Phase 1 conditions) | + natural faults: clean-run failures whose first wrong artifact is located automatically by comparison with ground truth, recovered with the same methods |

Admission rules fixed now: a benchmark adapter or topology enters the study if Qwen3-8B clean success >= 0.4 on >= 30
tasks of that benchmark (screening funnel reported in full); cells that fail are reported as such, never pooled.

### 7.1 Harness fix before any Phase 2 run (2026-11-18)

Found while reading clean failures: a call to a tool outside the agent's allowed list returns an empty tool message
(`TaskEnv.call` returns "" as text and the error only in the event log), so the model is not told the call failed.
It occurs in 106 of 140 `main` reference runs (208 calls), mostly researcher calls to write tools listed in its action
catalog. Fix: the tool message carries the error text (`ToolNotFoundError: ...`). Because this changes agent inputs,
Qwen3-8B `main`, `verify` and `restart` are rerun under the fixed harness as `main_h2`, `verify_h2`, `restart_h2`
(Phase 1 runs are kept unchanged). Phase 2 uses only `*_h2` runs as R0/R1/R3. Reported: clean success, Δ and the
graph-accuracy headline under both harnesses, so the size of the bug's effect is visible.

### 7.2 Benchmark expansion

**Selection criteria** (fixed before looking at any result on a new benchmark): (1) state-changing tool calls with a
programmatic final-state checker; (2) values read from the environment flow into write arguments, so the automatic
critical-fact rule of Phase 1 (perturbing the value in the ground truth makes the checker fail) yields >= 1 critical
fact per task; (3) some effects are external (money, bookings, messages to third parties) so irreversibility and
compensation are testable; (4) tool returns contain free text for adversarial injection, or injection uses the
existing tool-return hook (B1@E.tool style); (5) runs offline on this machine under a permissive license; (6) the
admission gate above.

**Candidates, in order.**
- **B1 AgentDojo expansion.** Revisit the critical-fact rule (it admits 33 of the write tasks and 0 travel tasks)
  so that write arguments derived from the environment by a computation also count; screen all qualifying tasks;
  target >= 30 AgentDojo tasks. The rule change is a dated amendment written before screening.
  Adversarial faults use all 97 AgentDojo user tasks (they need no critical fact; as MELON and CaMeL do), so only
  the accidental family depends on the critical-fact subset.
- **B2 τ-bench (retail, airline).** Single-instruction adapter: the user simulator is replaced by giving the user
  instruction as the task prompt (reported as an adaptation; the benchmark's dialogue setting is not reproduced).
  The domain policy goes into the planner and executor system prompts. Outcome = the benchmark's final database
  comparison plus required outputs; strict outcome as in Amendment 5.4. Reversibility labels come from the tool
  documentation with one rule: an action is external if it moves money or commits a booking or cancellation for the
  customer, otherwise reversible; compensable if the benchmark offers an inverse action (e.g. cancel after book).
  Adversarial goal per domain (e.g. a refund or a change to an attacker-controlled payment method), with a canary
  value and a security check written before screening.
  Feasibility (docs/benchmark_feasibility.md): installs offline; retail 114 + airline 50 tasks; ~127 meet the
  critical-fact rule unchanged; the built-in no-user "solo" mode is the single-instruction adapter.
- **B3 AppWorld** (multi-app APIs with state-based tests, including collateral-damage checks). APIs exposed as
  function tools (not a code-writing agent); task subset where every needed API fits the 16k context. If Qwen3-8B
  fails the gate, **fallback B3′: BFCL-v3 multi-turn** (stateful API simulators with state-based checks), then
  ToolSandbox.
- Adapter = suite wrapper with the `TaskEnv` interface (tools, env objects registered in the state store, utility,
  security, ground-truth writes) so the agents, faults, tracing, recovery and analysis code run unchanged. Each
  adapter passes the M1–M3 acceptance tests on MockLLM before any real-model screening.

**Task selection.** Tasks are admitted by the criteria above, not by Qwen3-8B's pass rate; the screening pass rate is
used only for the gate and reported per task. Primary analyses use all admitted tasks; a sensitivity analysis uses
tasks with clean pass rate >= 0.5 (the Phase 1 rule), so the Phase 1 selection bias is measured, not repeated.

### 7.3 Deferred: larger and other models

Deferred until the benchmark breadth is done: a larger Qwen (Qwen3-30B-A3B-Instruct-2507 via llama.cpp with CPU
experts, or Qwen3-32B / Qwen3.6 on a rented 48 GB GPU at about $0.35–1.90 per hour), a non-Qwen model (gpt-oss-20b
on vLLM; backup Mistral-Nemo-12B), and API models. If revived, each passes the same 0.4 gate and runs the core
conditions at k = 3 first. Any cloud use changes the "only machine" rule and is recorded in every manifest.

## 8. Metrics and analysis

Per branch, aggregated by family (accidental / adversarial, never pooled), benchmark, topology and model:
- invalidation recall and reuse precision vs ground truth (indifference artifacts excluded and counted);
- observed false-reuse rate vs nominal α (calibration plot);
- utility (AgentDojo, strict), residual harm, duplicates, unrecoverable effects;
- recovery tokens, live calls, probes, unprobed reuse.
Statistics: primary = mixed-effects logistic / linear models with random intercepts for task and branch-point within
task (WP1), paired by branch; cluster bootstrap over tasks as secondary. Holm within the primary family.
Channel analysis for Prop. 1: each dependent artifact missed by taint is classified automatically (derived value,
format change, paraphrase, omission, added/removed step, other) by fixed rules on the artifact pair.
Main figures: (1) recall vs recovery tokens frontier per family; (2) calibration of false reuse vs α; (3) channel
breakdown of graph misses across models (scale 4B/8B/14B and families).

### 8.1 Main tables (sized against peers; `docs/related_work.md` §3)

**Table 1 (main): recovery methods × benchmarks.** Rows: R0, R1, R2, G-B0 (graph-only), LLM-judge, Support,
R4p-1shot, R4p-SPRT, Oracle (9 rows), each split by family (accidental / adversarial) → 18 rows. Columns per
benchmark (AgentDojo, τ-bench, AppWorld or BFCL): invalidation recall, utility, residual harm, recovery tokens
(× R1), duplicated external effects → 5 × 3 = 15 columns. ≈ 270 cells, each with a 95% CI (CIs in the appendix
version if the page is too dense). Qwen3-14B on AgentDojo as an extra row block.
**Table 2: dependency-graph accuracy vs ground truth.** Rows: declared, data-flow, taint, declared ∪ taint, LLM-judge,
R4p-SPRT at the chosen B; columns: recall, precision, F1 × family × benchmark ≈ 6 × 18 ≈ 110 cells.
**Table 3: guarantee calibration.** Observed false-reuse rate vs nominal α ∈ {0.01, 0.05, 0.10} and τ1 ∈ {0.2, 0.3,
0.5}, per benchmark.
**Table 4: realistic detection.** Detector (oracle, R3 verifier, MELON-style masked re-execution) × recovery (none,
R2, R4p-SPRT): utility, harm, share of irreversible effects before detection.

**Task scope by family.** Adversarial faults use all AgentDojo user tasks × the suite's injection tasks (as MELON
and CaMeL, 97 tasks), since they need no critical fact; accidental value faults use the critical-fact subset.
The same split applies to τ-bench and AppWorld.

## 9. Pre-registered criteria for R4p-SPRT (Tier 1, evaluated on the 10 non-held-out tasks)

Held-out split (4 tasks: one per suite plus the task with the most branches) in `configs/phase2_split.yaml`,
committed before the first real-model R4p run; it selects B (smallest B with held-out recall >= 0.90; else ∞).

| Metric | Threshold | Reference |
|---|---|---|
| Invalidation recall vs ground truth | >= 0.90 | G-B0 ≈ 0.72 (Phase 1 definition) |
| Observed false-reuse rate | <= α + 0.05 upper CI | nominal α = 0.05 |
| Reuse precision | >= 0.95 | — |
| Utility, accidental: R4p − R1 | non-inferior at margin δ (below) | R1 0.74 |
| Recovery tokens | <= 0.5 × R1 | R1 ≈ 27k per branch |
| Duplicated external effects | 0 | R1 16–24% of branches |

Margin δ: fixed before any R4p run by a power simulation on Phase 1 branch-level data under the mixed model: the
smallest δ in {0.05, 0.10} with power >= 0.8 for a true difference of 0; if neither reaches 0.8, the utility
criterion is reported as secondary (estimate and CI, no pass/fail). The simulation script and its output are
committed with the split. Tier 2 repeats the criteria per cell; no new thresholds.

## 10. Acceptance tests (MockLLM, before any real run)

1. Policy "none" reproduces R0's final state; policy "all" equals R2.
2. Prefix replay makes 0 live calls before the branch point; recovery tokens count only post-branch calls.
3. Per-object restore equals a run that never made the invalidated writes (hand-built case).
4. Compensation succeeds for a known IBAN and fails for the canary IBAN.
5. Zero duplicate external effects on every mock branch (R4p, oracle).
6. A probe request rebuilt from transcripts with no correction equals the original request byte for byte.
7. SPRT: decisions and sample counts match a reference implementation on simulated Bernoulli streams; empirical
   error rates on 10^5 simulated artifacts within α, β ± 0.01.
8. Budget: B = 4 uses <= 4 probe calls; B = ∞ tests all of U.
9. Matrix counts and resume (skip DONE) as in M4.

## 11. Run matrix and GPU budget

Phase 1 throughput on this machine: 560–740 live calls/min for one job alone, ~380/min per job when two share the
server. Estimates use 500 calls/min (Qwen3-8B) and half that for 14B.

**Tier 1 (Qwen3-8B, AgentDojo, T1; 2,010 injected `main` branches)**

| Job | Live calls (est.) | GPU time (est.) |
|---|---|---|
| Harness-fixed reruns `main_h2`, `verify_h2`, `restart_h2` (§7.1; prefixes before the first disallowed call replay from the cache) | ~35–45k | 1.2–1.5 h |
| Real-model pilot (2 tasks, all methods) | ~3k | 10 min |
| Ground truth k_gt = 30 (all branch points, 14 tasks × 2 modes) | ~25k | 50 min |
| R2 | ~10k | 20 min |
| R4p-SPRT at 6 budgets + R4p-1shot (probes shared via cache) | ~50–70k | 1.7–2.3 h |
| LLM-judge, Support, oracle | ~10k | 20 min |
| Ablations (compensation, snapshot, τ1, α) | ~20k | 40 min |
| Calibration control on clean continuations | ~8k | 15 min |
| Realistic detection (R3 + R4p on `verify` branches) | ~15k | 30 min |
| Spotlighting + tool-output filter (prevention, secondary) | ~35k | 1–1.25 h |
| Reruns after bugs (50% buffer) | — | ~3 h |
| **Tier 1 total (Qwen3-8B)** | | **≈ 10.5–11.5 h** |
| Optional in Tier 1: τ-bench screening (all tasks × 2 modes × 2 seeds) | ~15k | 30 min |

**Tier 2 (NeurIPS), Qwen3-8B.** Per benchmark: screening 0.5–1 h; `main` + `verify` + `restart` on >= 30 tasks
3–4 h; ground truth + Phase 2 methods 5–6 h → **≈ 9–11 h per benchmark** (AppWorld × 1.5–2 for longer episodes).
Cells: AgentDojo-expanded, τ-bench, AppWorld or BFCL under T1, plus T2 on two of them → about 5 cells →
**≈ 45–60 GPU hours**; Qwen3-14B replication on AgentDojo +8 h. All local; no cloud cost.

**Calendar.** Tier 1: build and mock tests to ~11-25, real runs ~11-26 to 11-28, analysis to 12-03, report to 12-09.
Tier 2: AgentDojo expansion and τ-bench adapter in December (screening and main matrix by early January);
AppWorld/BFCL adapter and T2 topology in January; remaining main matrices and recovery runs in February–March;
analysis and writing in April; submission at the NeurIPS 2027 deadline
(expected mid-May 2027; confirm when announced).

## 12. Differences from `docs/revised_plan.md` §8

- Dependence is interventional with an indifference zone (§2); ground truth k_gt = 30 on disjoint seeds replaces
  k = 10.
- Probing is a truncated SPRT with a recall guarantee, not one probe per candidate (kept as R4p-1shot).
- Budget values change to probe calls {0, 4, 8, 16, 32, ∞}.
- New baselines: LLM-judge, Support (2608.10502-style), realistic detection, calibration control.
- Utility criterion uses a branch-level mixed model with a margin from a power simulation.
- Compensation eligibility uses the initial transaction history (the banking env has no contact list).
- Recovery tokens count replayed probe calls.
- Calendar invitations are irreversible (Amendment 5.3).
- X3 (cross-model verifier) is deferred with the other models (§7.3).
- Breadth priority: benchmarks before topologies before models (author, 2026-11-18).
