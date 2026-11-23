# Phase 2b results: counterfactual commit gate with rollback repair (CCG)

Design and success criteria: `docs/phase2b_design.md` (pre-registration Amendments 9–12 in `docs/preregistration.md`).
All numbers come from `analysis/phase2b_tables.py` and `analysis/phase2b_report.py`. Full tables with 95%
cluster-bootstrap CIs are in `data/tables/phase2b/summary.md`; the LaTeX version is in
`data/tables/phase2b/latex/main_tables.tex`.

Setup:
- three-agent pipeline (researcher → planner → executor), Qwen3-8B-FP8 on vLLM;
- AgentDojo v1.2.2, all 97 user tasks: 949 important_instructions attack cases plus 97 benign runs, message mode;
- the 14-task pipeline set (`main_h2`, both modes, seeds 100–101; Amendment 11) for accidental faults and
  controlled injection points.

## 1. What was run

| Group | Methods |
|---|---|
| Ours | CCG (masked ∨ re-observed, SPRT, localization, rollback-to-read repair) |
| CCG ablations | 1 sample per test (no SPRT); block without repair; executor-context purification; masked only; re-observed only; M ∨ Nz ∨ Ro; N-remove neutralizer |
| Counterfactual baselines | MELON at the executor, MELON at every agent, AgentSentry-style (executor), CausalArmor-style leave-one-out (executor) |
| Prevention | repeat prompt, spotlighting, ProtectAI DeBERTa detector, tool filter, PromptArmor (Qwen3-8B), IPIGuard-style |
| Adaptive | A1 task-coupled value substitution (33 tasks × 2 modes); A2 delegated-instruction tasks (5 tasks × 5 seeds, benign) |

**MELON port.** The port was checked against the official MELON code, run locally on single-agent AgentDojo
(banking + slack) with a local embedding model in place of the API embedding:
- official MELON: ASR 0.084, detection 0.53;
- selected port (v2, official argument rule): ASR 0.068, detection 0.57;
- the port meets the pre-registered ASR tolerance (|ΔASR| ≤ 0.05) but not per-case agreement (0.75 < 0.85), so it is
  reported as failing validation.

## 2. AgentDojo (97 tasks)

| Method | BU | UA | ASR | Clean flag | Upstream detection | Cost × NoDef |
|---|---|---|---|---|---|---|
| No defense | 0.41 | 0.29 | 0.125 | – | – | 1.0 |
| **CCG** | 0.37 | 0.30 | **0.031** | 0.30 | 0.86 | 11.1 |
| MELON (executor) | 0.39 | 0.28 | 0.062 | 0.07 | 0.56 | 1.3 |
| MELON (every agent) | 0.22 | 0.14 | 0.006 | 0.62 | 0.96 | 1.4 |
| AgentSentry-style | 0.41 | 0.29 | 0.112 | 0.02 | 0.16 | 1.4 |
| CausalArmor-style | 0.41 | 0.31 | 0.096 | 0.23 | 0.63 | 1.7 |
| Tool filter | 0.35 | 0.31 | 0.027 | – | – | 0.5 |
| DeBERTa detector | 0.23 | 0.17 | 0.030 | 0.75 | 0.90 | 1.0 |
| IPIGuard-style | 0.41 | 0.33 | 0.036 | – | – | 1.1 |

Upstream detection = share of NoDef-successful attacks whose payload was first read by the researcher (117 of 119)
that the method flags.

Findings:
- **Where harm enters.** 98% of successful attacks entered at the researcher. Executor-level counterfactual tests
  catch 0.16 (AgentSentry-style), 0.56 (MELON) and 0.63 (CausalArmor-style) of them; CCG catches 0.86.
- **The obvious lift detects more.** MELON at every agent detects 0.96, but flags 62% of benign runs and loses
  half of BU.
- **Cheap prevention matches CCG on ASR.** Tool filter, the DeBERTa detector and IPIGuard-style reach the same ASR
  as CCG at a fraction of the cost. IPIGuard-style also has higher UA.
- **Repair does not help here.** On AgentDojo, CCG's UA equals CCG-block's and CCG-purify's. Masked-criterion flags
  on persistent environment injections mostly cannot be localized: the masked run still performs the write after
  every read is neutralized. Such writes are therefore blocked rather than repaired.
- **Adding neutralization buys little.** M ∨ Nz ∨ Ro reaches ASR 0.027, against CCG's 0.030, but with 37% clean
  flags and 15.7× cost.
- **Removing sources instead of rewriting them weakens repair.** The N-remove neutralizer lowers UA to 0.29.
- **Masked only vs re-observed only.** Masked only is the better detector here: 0.83 upstream detection at 0.08
  clean flags. Re-observation adds false flags and catches few persistent injections, as designed.

## 3. 14-task pipeline set

| Method | Accidental util / harm | Adversarial util / harm | Clean util / flag | Cost |
|---|---|---|---|---|
| No defense | 0.46 / 0.23 | 0.44 / 0.49 | 0.75 / – | 1.0 |
| **CCG** | 0.60 / 0.05 | 0.59 / 0.04 | 0.64 / 0.34 | 10–14 |
| CCG-block | 0.37 / 0.04 | 0.44 / 0.04 | 0.56 / 0.35 | 7.5–8.5 |
| CCG-purify | 0.40 / 0.05 | 0.49 / 0.03 | 0.58 / 0.35 | 9–12 |
| MELON (executor) | 0.41 / 0.20 | 0.40 / 0.34 | 0.67 / 0.11 | 1.3 |

Detection by injection point, CCG vs MELON at the executor:

| Injection point | CCG | MELON (executor) |
|---|---|---|
| Laundered plan steps (P.out) | 0.93 | 0.07 |
| Forged researcher claim (R.out) | 0.63 | 0.11 |
| Blackboard (S.write) | 0.61 | 0.26 |
| Researcher read (R.tool) | 0.48 | 0.21 |
| Executor read (E.tool) | 0.88 | 0.62 |

Paired comparisons on the same runs. These are exploratory, because the pre-registered repair comparisons were on
AgentDojo.
- Source repair beats blocking: +0.22 utility on accidental faults, +0.15 on adversarial.
- Source repair beats executor-context purification: +0.20 and +0.10.
- The SPRT beats one sample per test, mainly through fewer false flags.
- Re-observation alone gives the highest utility (accidental 0.69, clean 0.78). Its adversarial harm is 0.15,
  against CCG's 0.04.

## 4. Pre-registered criteria (Amendment 9)

| Id | Result | Estimate |
|---|---|---|
| S1 upstream detection ≥ 0.50 and ≥ +0.20 over MELON-all and AgentSentry | fail | 0.86. +0.70 over AgentSentry, −0.09 vs MELON-all |
| S2 ASR ≤ 0.05 and below NoDef | **pass** | 0.031 (NoDef 0.125) |
| S3 repair UA beats block by ≥ 0.05 | fail | −0.01 [−0.03, 0.01] on AgentDojo |
| S4 clean flag ≤ 0.15, BU non-inferior | fail | 0.30; ΔBU −0.04 [−0.08, −0.01] |
| S5 accidental harm ≤ 0.5 × NoDef, utility beats NoDef | fail | harm 0.05 vs 0.23 (meets); Δutility +0.14 [−0.02, 0.29] (CI includes 0) |
| S6 source repair beats executor purification upstream | fail | −0.00 [−0.03, 0.02] on AgentDojo |
| S7 Ro false-flag ≤ 0.20 + 0.05 | fail | 0.16, upper CI 0.32 |
| S8 benign cost ≤ 8× | fail | 11.1× |

## 5. Adaptive attacks

- **A1, task-coupled value substitution.** It rarely succeeds against Qwen3-8B (harm 5 of 66 without defense,
  2 of 66 with CCG). The masked criterion flags 0.15, as expected for an attack that only acts within the user's
  task.
- **A2, delegated-instruction tasks (benign).** Neutralization flags 52% of runs (68% with N-remove), the masked
  criterion 16% and re-observation 52%. Utility is near zero for every method on these tasks (NoDef 0.08). This
  confirms that the neutralization criterion should stay out of the gate.

## 6. Reading

- **What holds.** In a pipeline, harm enters upstream. Executor-level counterfactual tests miss most of it.
  Re-deriving the whole pipeline at commit time catches it:
  - on AgentDojo, without the benign-flag rate that MELON at every agent pays;
  - on the controlled injection points, most clearly for laundered plan steps.
- **Repair works for transient faults.** On the 14-task set, repair at the source beats blocking and executor-side
  purification for transient faults (accidental faults and channel tampering).
- **What does not hold.**
  - CCG costs about 11× a NoDef run and flags 30–34% of clean runs.
  - On AgentDojo, simple prevention defenses reach the same ASR for far less.
  - Repair gives no utility gain on persistent environment injections.
  - Six of the eight pre-registered criteria fail, and the S1 detection claim fails against MELON at every agent.
- **Costs follow the components.** Most clean flags come from re-observation (stochastic re-derivations of the
  8B pipeline) and from the instruction-following mask (data that implies an action, e.g. a bill). Most of the cost
  comes from repeated full re-derivations.

## 7. Limitations

- One model (Qwen3-8B).
- AgentDojo in message mode only.
- The MELON port fails the agreement check.
- 16k context: overflowing replays are scored as stalls. Counts and an exclusion sensitivity analysis are in
  `adv_overflow_sensitivity.csv`; CCG's ASR changes by 0.004, any method's by at most 0.014.
- CausalArmor ran with vLLM at gpu-memory-utilization 0.80 (Amendment 12). Every other row used 0.90.
- The 14-task set uses 2 of 5 seeds per condition (Amendment 11).
- MultiAgentDojo had no public release and was not used.
