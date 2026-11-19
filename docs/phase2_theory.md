# Phase 2 theory: proof sketches (design §3)

Setting and notation as in `docs/phase2_design.md` §1–§2. A run is a DAG of artifacts; artifact u is produced by a
stochastic producer P_u(· | x_pa(u)). A fault replaces the origin o by x̃_o. For a downstream artifact u with realized
value v_u in the faulted run F, p_u = P(sig(X_u) = sig(v_u) | do(x_o), prefix). Hypotheses: H0 (dependent) p_u <= τ0;
H1 (reusable) p_u >= τ1, τ0 < τ1. An invalidation procedure outputs, per artifact, *reuse* or *invalidate*; its false-reuse
rate is P(reuse | H0) and its invalidation recall over dependent artifacts is 1 − false-reuse rate.

## Proposition 1 (incompleteness of static analysis)

**Claim.** Let S be any procedure whose output for u is a function of the DAG structure G and the literal artifact
values (x_a)_a of F (declared provenance, data flow and taint are such functions). If S flags every literal copy of
x̃_o (recall 1 on copies), then (i) there exist producers for which u is dependent and S does not flag u, and (ii) there
exist producers with the same G and the same literal values for which u is not dependent; hence no such S has recall
>= 1 − α for every producer family with any α < 1 while also reusing anything.

**Sketch.** Construct two pipelines with identical G and identical realized values in F, differing only in P_u.
Pipeline A: P_u(· | x) outputs f(x) with f(x̃_o) ≠ f(x_o) and f(x̃_o) not containing x̃_o literally (derived value,
paraphrase, omission, an added plan step); then p_u = 0 (u dependent) but no literal copy of x̃_o appears in u.
Pipeline B: P_u ignores x and outputs v_u with probability 1 (u reusable, p_u = 1). S sees the same input (G, x) in
A and B, so it returns the same decision: flagging u costs a false invalidation in B, not flagging misses a dependent
artifact in A. Any S that must be correct on both fails on one; taint (flag iff x̃_o occurs in u) fails on A.
Instantiation: Phase 1 taint recall 0.55–0.62 and declared ∪ taint 0.72–0.73; the Phase 2 channel table
(`analysis/phase2_channels.py`) counts the dependent artifacts taint misses per channel.

## Proposition 2 (query lower bound)

**Claim.** For black-box P_u, any (possibly sequential) test between H0: p = τ0 and H1: p = τ1 based on i.i.d. probe
outcomes Y_i ~ Bern(p) with P(reuse | H0) <= α and P(invalidate | H1) <= β satisfies

E_H0[n] >= ((1−α) log((1−α)/β) + α log(α/(1−β))) / KL(Bern(τ0) ‖ Bern(τ1)),
E_H1[n] >= ((1−β) log((1−β)/α) + β log(β/(1−α))) / KL(Bern(τ1) ‖ Bern(τ0)).

A procedure that issues no probe has E[n] = 0 and so cannot meet both error bounds for every P_u (consistent with
Prop. 1).

**Sketch.** Wald's identity gives E_H0[Λ_n] = E_H0[n] · (−KL(τ0 ‖ τ1)) for the log-likelihood ratio Λ_n of H1 vs H0
at the stopping time. The data-processing inequality applied to the decision (a binary function of the sample path)
gives E_H0[−Λ_n] >= d(1−α ‖ β), the binary KL divergence between the decision distributions under H0 and H1, which is
the numerator above. Same under H1. The bound extends to composite hypotheses p <= τ0, p >= τ1 because the least
favourable pair is (τ0, τ1). With the defaults (α = 0.05, β = 0.20, τ0 = 0.05, τ1 = 0.30): E_H0[n] >= 6.7 and
E_H1[n] >= 5.9 (`fpr.recovery.sprt.wald_lower_bound`); the truncation at n_max = 8 is close to these bounds.

## Proposition 3 (SPRT optimality and recall)

**Claim.** (a) Wald's SPRT with boundaries A = log((1−β)/α), B = log(β/(1−α)) attains the minimum E[n] under both
H0 and H1 among tests with the same or smaller error probabilities (Wald–Wolfowitz 1948); its errors satisfy
α' <= α/(1−β) and β' <= β/(1−α), and in practice α' <= α for these Bernoulli boundaries (verified by simulation in
`tests/test_p2_recovery.py`). (b) Truncation at n_max with "undecided → invalidate" cannot increase the false-reuse
rate. (c) Under A1–A3, the procedure "invalidate declared ∪ taint; test every other data-flow descendant with SPRT"
has expected invalidation recall >= 1 − α' over dependent artifacts, minus the share of dependent artifacts left
unprobed because of the budget.

**Sketch.** (a) is the classical result. (b) The truncated test reuses only on paths that cross A before n_max; these
are a subset of the untruncated test's reuse paths, so P(reuse | H0) does not increase (it raises the false-invalidation
rate instead; the probe calibration control measures it). (c) By A1 every dependent artifact is a data-flow descendant
of o, so it is either flagged (invalidated) or tested; by A2 the probe outcomes for a tested artifact are i.i.d.
Bern(p_u) with p_u <= τ0 under dependence, and the SPRT error probability is non-increasing in p for p <= τ0 (monotone
likelihood ratio), so each tested dependent artifact is reused with probability <= α'. Linearity of expectation over
dependent artifacts gives the recall bound; artifacts never probed are reused without a test and are reported as
"unprobed reuse". Caveat (stated in the report): probes condition on the corrected parents actually used by the
recovery (e.g. the replanned plan for executor probes), while the ground truth p_u is marginal over a full clean
continuation; A2 is an assumption about this conditional sampling, and the calibration table measures the gap.
