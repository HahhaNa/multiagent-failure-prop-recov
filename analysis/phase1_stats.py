"""V6 (docs/preregistration.md): headline comparisons with paired cluster-bootstrap p-values over tasks, Holm
adjustment across the family of headline comparisons, and tasks needed for 80% power. Reads run outputs only."""

import argparse
from statistics import NormalDist

import numpy as np
import pandas as pd
from common import DEPENDENT, PROPAGATED, load, out_dir, runs_root, with_delta
from phase1_recovery import branches

B = 10_000


def per_task(x: pd.DataFrame, fn) -> pd.Series:
    return x.groupby("task").apply(fn, include_groups=False).dropna()


def boot_p(x: pd.DataFrame, stat) -> tuple[float, float, float, float]:
    """Point estimate, 95% CI and two-sided p for H0: stat = 0, resampling tasks."""
    groups = [g for _, g in x.groupby("task")]
    rng = np.random.default_rng(0)
    est = stat(x)
    vals = np.array([stat(pd.concat([groups[i] for i in rng.integers(0, len(groups), len(groups))])) for _ in range(B)])
    vals = vals[~np.isnan(vals)]
    p = min(1.0, 2 * min((vals <= 0).mean(), (vals >= 0).mean()))
    return est, float(np.quantile(vals, 0.025)), float(np.quantile(vals, 0.975)), p


def tasks_needed(d: pd.Series) -> float:
    """Tasks for 80% power (two-sided 0.05) given per-task differences d (normal approximation)."""
    if len(d) < 2 or d.std(ddof=1) == 0 or d.mean() == 0:
        return np.nan
    return float(np.ceil(((NormalDist().inv_cdf(0.975) + NormalDist().inv_cdf(0.8)) * d.std(ddof=1) / abs(d.mean())) ** 2))


def holm(p: list[float]) -> list[float]:
    order = np.argsort(p)
    adj = np.empty(len(p))
    run = 0.0
    for rank, i in enumerate(order):
        run = max(run, (len(p) - rank) * p[i])
        adj[i] = min(1.0, run)
    return adj.tolist()


def comparisons(main: str, restart: str, verify: str) -> pd.DataFrame:
    rows = []
    df = load(main, runs_root())
    f = with_delta(df[df.fault != "PC"])
    f = f[f.injected]
    for fam in ("accidental", "adversarial"):
        for mode in ("message", "state"):
            x = f[(f.family == fam) & (f["mode"] == mode)]
            stat = lambda t: (t.bad.astype(float) - t.clean_bad).mean()
            rows.append({"comparison": f"Delta bad, {fam}, {mode}", "n": len(x), **dict(zip(["est", "lo", "hi", "p"], boot_p(x, stat))),
                         "tasks_for_80pct_power": tasks_needed(per_task(x, stat))})
    for mode in ("message", "state"):
        x = f[(f["mode"] == mode) & f.family.isin(["accidental", "adversarial"]) & f.category.isin(DEPENDENT)]

        def stat(t):
            p = t.category.isin(PROPAGATED)
            a, c = p[t.family == "adversarial"], p[t.family == "accidental"]
            return a.mean() - c.mean() if len(a) and len(c) else np.nan

        rows.append({"comparison": f"P(prop|dep) adversarial - accidental, {mode}", "n": len(x), **dict(zip(["est", "lo", "hi", "p"], boot_p(x, stat))),
                     "tasks_for_80pct_power": tasks_needed(per_task(x, stat))})
    b = branches(runs_root(), {"main": main, "restart": restart, "verify": verify})
    key = ["task", "mode", "fault", "branch_seed"]
    w = b.pivot_table(index=key + ["family"], columns="method", values=["utility_m", "harm_m"], aggfunc="first")
    w.columns = [f"{a}_{m}" for a, m in w.columns]
    w = w.reset_index()
    for fam in ("accidental", "adversarial"):
        for m in ("R1", "R3"):
            for met, lab in (("utility_m", "utility"), ("harm_m", "residual harm")):
                x = w[(w.family == fam)].dropna(subset=[f"{met}_{m}", f"{met}_R0"])
                stat = lambda t, a=f"{met}_{m}", c=f"{met}_R0": t[a].astype(float).mean() - t[c].astype(float).mean()
                rows.append({"comparison": f"{m} - R0 {lab}, {fam}", "n": len(x), **dict(zip(["est", "lo", "hi", "p"], boot_p(x, stat))),
                             "tasks_for_80pct_power": tasks_needed(per_task(x, stat))})
    t = pd.DataFrame(rows)
    t["p_holm"] = holm(t.p.tolist())
    t["ci_excludes_0"] = (t.lo > 0) | (t.hi < 0)
    t["distinguishable_after_holm"] = t.p_holm < 0.05
    return t


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--main", default="main")
    ap.add_argument("--restart", default="restart")
    ap.add_argument("--verify", default="verify")
    ap.add_argument("--tag", default="stats")
    a = ap.parse_args()
    t = comparisons(a.main, a.restart, a.verify)
    d = out_dir(f"tables/{a.tag}")
    t.to_csv(d / "headline_comparisons.csv", index=False, float_format="%.4f")
    (d / "headline_comparisons.md").write_text(t.to_markdown(index=False, floatfmt=".3f"))
    print(t.round(3).to_string())


if __name__ == "__main__":
    main()
