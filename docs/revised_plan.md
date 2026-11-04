# Revised plan after the Phase 1 review

Status date: 2026-11-04. Pre-registration: `docs/preregistration.md` (committed e948a27 before any analysis below;
Amendment 1 committed da7e18a before the labeler fix). Every number below names its table, script and run subset.
Items marked PENDING are running; their thresholds are already fixed in the pre-registration.

Labels: "v1" = labels used in the Phase 1 report; "v2" = after Amendment 1; "v3" = after Amendment 2 (omission rule).
All tables under `data/tables/` are regenerated with v3 (`FPR_LABELS`, default v3) unless the file name says otherwise. Run subset "main" = Qwen3-8B, 14 tasks,
`~/fpr-runs/main` (exported in `data/runs_main.csv.gz`).

---

## 1. Claims from Phase 1 that are weakened or withdrawn

| # | Phase 1 claim | Status | Reason and evidence | Most likely alternative explanation; what rules it out |
|---|---|---|---|---|
| C1 | "Adversarial faults propagate almost deterministically; accidental about half the time" | **Weakened** | Wording withdrawn (evaluative). With v3 labels: P(propagated \| used) accidental 0.47 [0.33, 0.61] (message, n=560) / 0.59 [0.43, 0.74] (state, n=600); adversarial 0.85 [0.77, 0.92] (n=335) / 0.79 [0.72, 0.86] (n=405); `data/tables/main/by_family_mode.csv`. The adversarial − accidental difference is +0.38 [0.25, 0.49] (message) and +0.20 [0.08, 0.33] (state), down from +0.44 / +0.29 with v1 (`data/tables/stats/headline_comparisons.csv`). The `used` label has pooled audit agreement 94/108 = 0.87 [0.79, 0.92] (§3), so this claim rests on a label below the 0.90 threshold when all rounds are pooled. | Adversarial payloads are written to be acted on (instructions, extra steps) while accidental faults are values that a later lookup can overwrite: the family difference may be a property of the payload design, not of the fault class. Not ruled out; the claim is stated for these payloads only. |
| C2 | "Accidental wrong facts are consumed almost every time (dependence ≈ 1)" | **Withdrawn** | v1 counted a branch as dependent when an intermediate artifact carried the value even if no downstream artifact differed from the clean continuations (audit round 1, used-label agreement 29/36). v3 dependence: 0.91 (message) / 0.83 (state); "≈ 1" is withdrawn. | — |
| C3 | "Restart fixes accidental faults" (R1 − R0 utility +0.27 [0.13, 0.40]) | **Kept, narrowed** | Distinguishable after Holm adjustment over 14 headline comparisons: +0.27 [0.13, 0.40], p_holm = 0.002 (`data/tables/stats/headline_comparisons.csv`). Without `banking.user_task_9` (no-op passes, §3.4): +0.28 [0.13, 0.42], n=1070 (`data/tables/stats_without_banking9/headline_comparisons.csv`). | R1 runs live with a fresh seed; part of the gain could be seed resampling rather than fault removal. Partly ruled out: the clean-continuation failure rate at the same points is 0.06–0.15 (`clean_bad`), below the R0 failure rate. |
| C4 | "Only prevention helps against attacks" | **Weakened** | R3 − R0 residual harm −0.39 [−0.45, −0.34] (n=735; without task 9 −0.40 [−0.46, −0.35]) is distinguishable after Holm; R1 − R0 harm +0.02 [0.00, 0.04] is not. No existing prompt-injection defense was compared, so the claim becomes: "R3 reduces adversarial residual harm; R1 does not". Spotlighting comparison pre-registered (X2), not yet run. | R3's verifier sees the tool evidence while the agents do not see each other's evidence; a defense that only marks tool output may give a similar reduction. Not ruled out until X2 runs. |
| C5 | "R3 cuts harm for +11% tokens but lowers clean success by ~0.1" | **Kept, numbers stated** | Clean refs 0.86 → 0.77 (message), 0.79 → 0.69 (state), n=70 per mode; utility difference R3 − R0 is not distinguishable for either family (accidental +0.04 [−0.09, 0.16]; adversarial +0.14 [−0.03, 0.31]). | — |
| C6 | "No dependency graph is a reliable ground truth" (taint recall 0.55–0.62; union 0.72–0.73) | **Kept, wording changed to the numbers** | Stable across the three equivalence definitions (max change 0.02; V1). Recall rises from k = 1 to 5 (taint, adversarial: 0.49 / 0.53 / 0.55) and changes by <= 0.008 from k = 5 to 10 (pre-registered stability threshold 0.05 met; V2). Replicated on Qwen3-14B: union recall 0.73–0.74. | Dependent labels at k=5 include rare clean behaviors (dependent share falls from 0.71 to 0.66 between k=1 and k=5, accidental), which lowers graph recall. k=10 bounds this. |
| C7 | Irreversibility windows (3–4 steps for plan/executor attacks; 8–24 upstream) | **Kept, with a labeling caveat** | Fault-caused irreversible label agreement 106/108 = 0.98 [0.93, 0.99] over three rounds (`data/audit/audit_summary.md`). Under E1 a correct payment with a reworded free-text subject counts as fault-caused (round 3, branch 11). Post-hoc recount (Amendment 3, `data/tables/validation_main/v7_irreversible_by_definition_posthoc.csv`): adversarial P.out 0.94 and E.tool 0.79 under E1, E2 and E3; adversarial R.tool 0.45 (E1) vs 0.34 (E2); accidental R.start 0.20 vs 0.08, P.start 0.15 vs 0.09, R.tool 0.18 vs 0.12. Accidental upstream shares under E1 include reworded correct actions; the plan/executor-level result does not depend on the definition. `create_calendar_event` and `add_calendar_event_participants` send invitation emails but are labeled reversible in `configs/tools.yaml` (audit round 2, branches 3, 10), so external effects in workspace are undercounted. Relabeling and recount PENDING. | — |
| C8 | Δ and recovery numbers on "harder tasks" (`main_wide`) | **Kept as exploratory** | 4 tasks; per-family CIs span up to 0.8 (e.g. adversarial message Δ 0.22 [−0.06, 0.54]). | — |
| C9 | Cross-family check (Llama-3.2-3B) | **Withdrawn as evidence of generality** | Clean success 0.25; recovery differences sit at that floor. Replaced by the multi-model replication (§6): H1–H3 hold on Qwen3-8B and Qwen3-14B; H1 in state mode does not hold on Qwen3-4B. No second family has passed the gate yet. | Within-family replication cannot rule out Qwen-specific behavior (e.g. following plan steps literally). |

---

## 2. Artifact equivalence and k sensitivity (pre-registration V1, V2)

**Definition used for the headline numbers: E1 exact** (tool or action name + all arguments after normalization of
case, whitespace, trailing punctuation and number format; claims by normalized value). Justification: the executor's
arguments are what reach the environment, and two calls with different free text (e.g. an email body) are different
external effects. E2 (ignore free-text fields) and E3 (free text by token Jaccard >= 0.5; claims within 1%) are
reported alongside.

**V1 result** (`analysis/phase1_validation.py`, `data/tables/validation_main/v1_equivalence.csv`; main; k=5):

| Family | Graph | Recall E1 | Recall E2 | Recall E3 | Dependent share E1 / E2 / E3 |
|---|---|---|---|---|---|
| accidental (3,389 artifacts) | taint | 0.620 [0.525, 0.698] | 0.639 | 0.635 | 0.658 / 0.628 / 0.639 |
| | declared ∪ taint | 0.725 [0.644, 0.780] | 0.744 | 0.738 | |
| adversarial (2,976) | taint | 0.551 [0.473, 0.636] | 0.568 | 0.565 | 0.519 / 0.494 / 0.505 |
| | declared ∪ taint | 0.718 [0.622, 0.804] | 0.725 | 0.729 | |

Pre-registered criteria: union recall >= 0.90 under any definition would falsify C6 (maximum observed upper CI bound:
0.807); estimates move <= 0.02 across definitions (threshold 0.10). C6 survives V1.

**V2 result** (`data/tables/validation_main/v2_k_sensitivity.csv`; E1; mean over all subsets of size k):

| Family | k | Dependent share | Recall taint | Recall declared ∪ taint |
|---|---|---|---|---|
| accidental | 1 / 3 / 5 | 0.710 / 0.673 / 0.658 | 0.581 / 0.609 / 0.620 | 0.699 / 0.718 / 0.725 |
| adversarial | 1 / 3 / 5 | 0.585 / 0.538 / 0.519 | 0.493 / 0.534 / 0.551 | 0.679 / 0.710 / 0.718 |
| accidental | 10 | 0.649 | 0.626 | 0.729 |
| adversarial | 10 | 0.512 | 0.559 | 0.718 |

Interpretation fixed in advance: stable if |recall(k=10) − recall(k=5)| <= 0.05 for taint and union in both families.
Observed differences (k=10 uses `main` + `cf10`, branch seeds 100–109): +0.006, +0.004 (accidental), +0.008, 0.000
(adversarial). **Stable**; the k=5 values are kept. The dependent share still falls slightly (0.658 → 0.649,
0.519 → 0.512), so a small share of k=5 dependent labels are rare clean behaviors; it does not change graph recall by
more than 0.01.

**Prefix variance (V3).** Every main branch replays the reference prefix of seed 0: one prefix per task × mode, so
between-prefix variance is not captured in any Phase 1 CI. `prefix_s1` / `prefix_s2` (core conditions, branch seeds
100–102, prefixes of reference seeds 1 and 2; 994 runs each) — `analysis/phase1_prefix.py`,
`data/tables/validation_main/v3_prefix_variance.csv`:

| Family, mode | Δ seed-0 [95% CI] (n) | Δ seeds 1–2 pooled (n) | P(prop \| used) seed-0 | seeds 1–2 |
|---|---|---|---|---|
| accidental, message | 0.42 [0.21, 0.62] (147) | 0.31 (273) | 0.54 [0.32, 0.72] | 0.49 |
| accidental, state | 0.45 [0.20, 0.68] (144) | 0.48 (276) | 0.68 [0.48, 0.85] | 0.67 |
| adversarial, message | 0.64 [0.49, 0.79] (84) | 0.54 (168) | 0.87 [0.73, 0.98] | 0.87 |
| adversarial, state | 0.44 [0.25, 0.61] (126) | 0.48 (246) | 0.85 [0.72, 0.96] | 0.93 |

All 8 pooled estimates lie inside the seed-0 CI, so the pre-registered criterion for prefix independence is met.
The test has low power (core conditions only, 3 branch seeds; seed-0 CIs span 0.25–0.48), and two point estimates
move by 0.10 or more (accidental message Δ −0.11, adversarial message Δ −0.10). Phase 1 CIs are kept as computed
over tasks; prefix is listed as an unmodeled variance source.

---

## 3. Label audit (pre-registration V4)

**Procedure.** 36 injected main branches, 2 per condition, stratified by mode, seed 2026 (`data/audit/sample.json`,
`analysis/audit_sample.py`). Each branch was relabeled from raw events, transcripts and artifacts by an auditor who did
not see summaries, graphs or tables; instructions are in this repository's history (audit prompt reproduced in
`data/audit/README.md`). Agreement: `analysis/audit_compare.py`.

**Round 1 (v1 labels)** `data/audit/audit_agreement_r1_v1.md`:

| Label | Agreement | >= 0.90 |
|---|---|---|
| bad outcome | 31/36 = 0.86 | no |
| read | 36/36 = 1.00 | yes |
| used | 29/36 = 0.81 | no |
| fault-caused irreversible | 34/36 = 0.94 | yes |
| per-call dependence | 60/60 = 1.00 | yes |

**Causes** (traces in `data/audit/audit_branches_r1_v1.csv`):
- bad: 2 branches on `banking.user_task_9`, whose AgentDojo utility check passes when no write is made; 2 branches where
  the checker ignores an extra unrequested write; 1 branch where our harm flag (canary URL fetched) was set but `bad`
  was not.
- used: the v1 category is taint-based and does not compare with clean continuations (4 branches `corrected` with no
  differing downstream artifact; A4 timeouts always `corrected`; a stall that every clean continuation also shows),
  and it misses a dependent call without taint (1).

**Fix** (Amendment 1): `bad_v2 = bad or harm`; `used_v2` requires a counterfactually dependent downstream artifact or
an executor call/report carrying the injected value. Not fixed: checker semantics (no-op pass, extra writes).
Round 1 after the fix (optimistic, same sample): bad 32/36, used 31/36 (`audit_agreement_r1_v2.md`).

**Round 2** (fresh sample, seed 2027; `audit_agreement_r2_v2.md`): bad 31/36, used 30/36 — the v2 fix did not raise
`used` agreement on new data. Remaining disagreements were omissions (a timeout or empty plan makes the branch skip an
action every clean continuation takes). **Amendment 2** added the omission rule (v3) and a stopping rule: if `used`
fails on a third fresh sample, all `used`-based claims are withdrawn.

**Round 3** (fresh sample, seed 2028; `audit_agreement_r3_v3.md`): bad 33/36 (0.92), bad without task 9 33/33, used
33/36 (0.92), fault-caused irreversible 36/36, per-call dependence 67/68. All labels meet the threshold, so the
`used`-based claims are kept under the stopping rule.

**All rounds pooled** (`data/audit/audit_summary.md`, Wilson 95% intervals; rounds 1–2 were used to develop the fixes,
so pooled v3 values are optimistic):

| Label | v1 | v3 |
|---|---|---|
| bad | 95/108 = 0.88 [0.80, 0.93] | 96/108 = 0.89 [0.82, 0.94] |
| bad without `banking.user_task_9` | 93/97 = 0.96 | 94/97 = 0.97 [0.91, 0.99] |
| used | 92/108 = 0.85 [0.77, 0.91] | 94/108 = 0.87 [0.79, 0.92] |
| fault-caused irreversible | 106/108 = 0.98 | 106/108 = 0.98 [0.93, 0.99] |
| per-call dependence | 186/187 = 0.995 | 186/187 = 0.995 [0.97, 1.00] |

Reading: round 3 passes, but v1 also reaches 33/36 on round 3, so the difference between label versions is within
sampling variation at n=36 per round. Headline claims are therefore based on Δ (bad), fault-caused irreversibility and
per-call dependence (pooled agreement >= 0.97 without task 9); `used`-based shares are secondary.

**Who audited.** All three rounds were automated trace audits (no human auditor), given only the raw traces and the instructions in
`data/audit/README.md`; no human audit has been done. A human audit of at least 30 branches is required before these
agreement rates are reported as a manual audit.

**3.4 Checker finding.** On `banking.user_task_9`, 116 of 138 utility passes in main have no successful executor write
(fault and clean runs combined). The task's check accepts an unchanged environment. All headline numbers will be
reported with and without this task.

---

## 4. Task selection (pre-registration V5)

Funnel (`src/fpr/tasks.py::annotate`, `configs/tasks_candidates.yaml`, `configs/tasks_selected.yaml`,
`data/tables/validation_main/v5_task_selection.csv`):

1. AgentDojo v1.2.2 user tasks: banking 16, slack 21, workspace 40, travel 20 (97).
2. Candidate pool, 33: tasks whose ground truth has at least one write call, passes its own utility check, and has at
   least one critical fact (a write-argument value that is not in the prompt, is present in the environment, and whose
   perturbation makes the utility check fail). Travel: 0 tasks qualify.
3. Screening `screen3`: Qwen3-8B, each candidate × 2 modes × seeds {0, 1, 2}; selection rule: all screening samples
   pass (8 tasks), then filled with tasks of clean pass rate >= 0.5 (6 tasks) → 14 selected.
4. Excluded: 4 tasks at pass rate 1/3 (run separately as `main_wide`), 15 tasks below 1/3 (11 at 0).

**The selection is on clean success of the agent model**, so clean failure rates on selected tasks are lower
(`clean_bad` 0.06–0.19) than on the excluded `main_wide` tasks (0.43–0.54). Pre-registered expectation: lower Δ on
excluded tasks. Observed (`data/tables/main_wide/by_family_mode.csv`, v2): adversarial Δ 0.22 [−0.06, 0.54] (message,
n=100) vs 0.68 [0.59, 0.76] on selected tasks; accidental Δ 0.38 [0.01, 0.73] vs 0.41 [0.27, 0.55]. Harm rate on
excluded tasks 0.40 / 0.31 vs 0.59 / 0.51. Δ and harm on selected tasks are therefore upper-end estimates for the
candidate pool; the 15 tasks below 1/3 cannot be measured with this model because clean continuations rarely succeed.

---

## 5. Statistics (pre-registration V6)

`analysis/phase1_stats.py`, `data/tables/stats/headline_comparisons.csv` (v3 labels) and
`data/tables/stats_without_banking9/headline_comparisons.csv` (same conclusions). Paired cluster bootstrap over 14 tasks, 10,000 resamples, Holm adjustment over 14 comparisons.

Distinguishable after adjustment (6 Δ / family comparisons, plus 2 recovery): Δ bad for both families and modes;
adversarial − accidental P(propagated | dependent) in both modes; R1 − R0 utility (accidental, +0.27); R3 − R0 residual
harm (adversarial, −0.39).

Not distinguishable (reported as null or exploratory): R3 − R0 utility (both families); R1 − R0 utility (adversarial,
+0.01 [−0.17, 0.18]); R1 − R0 harm (adversarial); R3 − R0 harm (accidental).

Tasks needed for 80% power at the observed between-task variance: R3 − R0 utility adversarial 45, R3 − R0 harm
accidental 32, R1 − R0 harm adversarial 78; the others are below 14 or have no effect to detect. All analyses
per fault condition (18 conditions) are exploratory.

---

## 6. Generality (multi-model replication)

Gate fixed before any of these models ran: refs clean success >= 0.4 (14 tasks × 2 modes × 2 seeds). Same 14 tasks for
every model.

| Model | Family | Gate (refs clean success, stall) | Status |
|---|---|---|---|
| Qwen3-8B FP8 | Qwen | main refs 0.86 / 0.79 | primary |
| Qwen3-4B FP8 | Qwen | 0.55, 0.14 | full matrix done (seen before pre-registration; exploratory) |
| Qwen3-14B AWQ | Qwen | 0.875, 0.00 | full matrix done (pre-registered hypotheses H1–H3) |
| Llama-3.1-8B (FP8 at load) | Llama | 0.29, 0.50 | no-go: executor exhausts its turns after calls to tools outside its role |
| Granite-3.3-8B (FP8 at load) | IBM | 0.07, 0.00 | no-go |
| Ministral-8B (FP8 at load) | Mistral | 0.07, 1.00 (two runs) | invalid: chat template rejects the harness's tool-call ids, then non-alternating roles; rerun with mistral tokenizer mode PENDING |
| Hermes-3-Llama-3.1-8B (FP8 at load) | Llama fine-tune | 0.39 (22/56), 0.00 | no-go: below the 0.4 gate by one run; not run further (gate fixed in advance) |

**Pre-registered hypotheses X1 per model** (`data/tables/models/summary.md`; `analysis/phase1_models.py`; runs
`main_<model>`, `restart_<model>`, `verify_<model>`):

| Hypothesis | Qwen3-4B (exploratory) | Qwen3-8B | Qwen3-14B |
|---|---|---|---|
| H1 adversarial > accidental P(propagated \| used), message | 0.78 vs 0.51 | 0.85 vs 0.47 | 0.82 vs 0.49 |
| H1, state | 0.82 [0.77, 0.86] vs 0.71 [0.57, 0.84]: not supported | 0.79 vs 0.59 | 0.88 [0.84, 0.93] vs 0.60 [0.44, 0.74] |
| H2 R1 − R0 utility, accidental > 0 | +0.26 [0.16, 0.35] | +0.27 [0.13, 0.40] | +0.36 [0.22, 0.48] |
| H2 R1 − R0 harm, adversarial, CI contains 0 | yes | +0.02 [0.00, 0.04] | +0.01 [0.00, 0.04] |
| H3 R3 − R0 harm, adversarial < 0 | −0.33 [−0.42, −0.23] | −0.39 [−0.45, −0.34] | −0.41 [−0.48, −0.35] |

Observed but not pre-registered: R3 − R0 utility for adversarial faults is −0.01 [−0.14, 0.09] (4B), +0.14 [−0.02,
0.30] (8B), +0.25 [0.12, 0.40] (14B). Alternative explanation that cannot be separated here: the verifier is the same
model as the agents, so verifier capability and agent capability change together; X3 is needed to separate them.
Declared ∪ taint recall is 0.62–0.65 (4B), 0.72–0.73 (8B), 0.73–0.74 (14B).

**Open constraint: a capable second family.** If neither pending model passes the gate, generality across families is
not shown, and the report says so. Qwen3-4B / 8B / 14B give a within-family scale comparison only.

**Cross-model verifier (X3).** The harness serves one model per GPU. Options, in order of preference:
(a) serve Qwen3-8B and a 3–4B verifier of another family together with a memory split (requires relaxing the
one-model rule; KV cache for 8B drops from 26k tokens); (b) a verifier on CPU via llama.cpp (6.3 s per call measured
for a 4B Q4 model) on a pre-registered subset of 300 branches; (c) a two-pass replay where the verifier model runs
offline over recorded stage outputs, which removes the verifier's effect on later stages and is reported as a
detection measurement, not as R3.

---

## 7. Baselines to add before Phase 2

| Baseline | Design | Pre-registered hypothesis and failure |
|---|---|---|
| R2 rollback-to-fault | Restore the full internal snapshot from just before injection, re-apply nothing, rerun downstream live with the fault removed; external effects already made stay. | R2 utility ≈ R1 at lower recomputation tokens; failure if R2 tokens >= R1 tokens. |
| Spotlighting (X2) | Tool outputs datamarked and declared as data in system prompts; same fault schedule as main. | R3 harm reduction > spotlighting; failure if spotlighting >= R3 or CIs overlap. |
| Tool-output filter | Drop tool-return text that matches an instruction classifier (same model, one call per tool return). | Same comparison as spotlighting. |
| Cross-model verifier (X3) | §6. | R3x harm reduction differs from R3 by more than the CI half-width; null otherwise. |

---

## 8. Phase 2, redesigned around Finding C6

**Problem.** Selective recovery invalidates the artifacts that depend on a known faulty artifact and recomputes only
those. Phase 1 measured that the cheap dependency graphs miss 27–28% (declared ∪ taint) to 38–45% (taint) of
counterfactually dependent artifacts (k=5, E1). A method that trusts them keeps those artifacts.

**Hypothesis H-P2.** Targeted counterfactual probing of uncertain edges — artifacts downstream of a read of the faulty
input that are not tainted — recovers most of the oracle's invalidation recall at a fraction of full-restart cost.

**Method (R4p).**
1. Candidate set U = artifacts reachable from the faulty artifact in the data-flow graph, minus those flagged by
   declared ∪ taint (flagged ones are invalidated directly).
2. Probe: for each u in U in topological order, re-run the producing agent step with the corrected input (prefix
   replayed from cache, one live call) and compare the regenerated artifact with u under E1. Different → invalidate u
   and its data-flow descendants; same → reuse u.
3. Budget B probes per branch, spent on U ordered by a cheap score (distance from the fault, artifact level); unprobed
   members of U are reused (reported as a separate failure mode).
4. Recovery: per-object restore of state written by invalidated calls; compensation for compensable effects (below);
   partial replanning of invalidated steps; executor runs only new or changed steps; valid calls replayed from cache.

**Main result.** Invalidation recall vs. recovery token cost, per family, with points: declared, taint, declared ∪
taint, R4p at B ∈ {1, 2, 4, 8, ∞}, R2, R1, and the oracle (counterfactual table at k=10, E1) as the upper bound.

**Pre-registered success criteria for R4p (B chosen on a held-out 4-task split, evaluated on the other 10).**

| Metric | Threshold | Reference value |
|---|---|---|
| Invalidation recall vs. oracle | >= 0.90 | declared ∪ taint 0.72 |
| Reuse precision (reused artifacts that match a clean continuation) | >= 0.95 | — |
| Utility, accidental faults | non-inferior to R1: lower 95% bound of (R4p − R1) > −0.05 | R1 0.74 |
| Recovery tokens | <= 0.5 × R1 recovery tokens | R1 ≈ 27k per branch |
| Duplicated external effects | 0 (any duplicate fails the criterion) | R1 16–24% of branches |

Each criterion is reported as passed or failed; there is no aggregate score.

**Baselines:** R0, R1, R2, R3, oracle. **Ablations:** R4p without probing (= declared ∪ taint), without compensation,
without per-object restore (whole-snapshot restore instead); each reported against R4p with paired CIs.

**Compensation semantics** (tools from `configs/tools.yaml`):

| Tool | Label | Compensating action | Failed compensation |
|---|---|---|---|
| send_money | compensable | a refund transfer of the same amount from the recipient, executed by the world actor; succeeds only if the recipient is in the environment's contact list (attacker IBANs are not) | effect counted as unrecoverable; utility scored on the final state |
| schedule_transaction, update_scheduled_transaction, update_password, update_user_info | reversible | per-object restore to the pre-write version | — |
| create/cancel/reschedule calendar event, add participants | reversible (relabel PENDING: invitation emails are external) | per-object restore; sent invitations counted as irreversible after relabeling | — |
| delete_email, append_to_file, create_file, delete_file, add_user_to_channel, remove_user_from_slack | reversible | per-object restore | — |
| send_email, share_file, send_direct_message, send_channel_message, invite_user_to_slack, post_webpage, get_webpage on an attacker URL | irreversible | none | always counted as unrecoverable |

**Unrecoverable effects** are reported separately per method: branches with >= 1 fault-caused irreversible effect
before recovery, and utility on those branches. Recomputation is never counted as full recovery for a branch with an
unrecoverable effect.

**Secondary results** (reported after the main comparison): drift scenario (R2 vs. per-object restore) and early
detection in the 8–24-step window of upstream faults.

---

## 9. Timeline to 2026-12-10

| Week of | Milestone | Done means |
|---|---|---|
| Nov 4 | Round-2 audit, k=10, prefix variance, multi-model matrix, v2 tables, Holm reruns | thresholds evaluated as pre-registered; outputs spot-checked |
| Nov 9 | Tool relabeling; R2; spotlighting and tool-output filter; cross-model verifier option chosen | baselines run on the main fault schedule; acceptance tests on MockLLM |
| Nov 16 | R4p probing, per-object restore, compensation; held-out split chosen | MockLLM acceptance tests for each step; zero duplicate effects on mock |
| Nov 23 | Real runs: R4p frontier, ablations, all baselines | runs complete; 10% of branches spot-checked against traces |
| Nov 30 | Analysis, second label audit on Phase 2 labels | pre-registered criteria evaluated |
| Dec 7 | Final report (4–6 pages) | every number traced to a table, script and run subset |
