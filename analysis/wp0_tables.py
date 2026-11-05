"""WP0 tables (docs/preregistration.md Amendment 5.3-5.6): calendar relabel recount, strict outcome, fill-rule
sensitivity, simulator permissiveness. Reads run outputs, cf_artifacts and wp0 labels. Writes data/tables/wp0/."""

import argparse
import os

import pandas as pd
from common import OUT, load, out_dir, rel, runs_root, with_external
from phase1_counterfactual import irreversibility
from phase1_stats import comparisons

FILL_RULE = ["slack.user_task_8", "workspace.user_task_7", "banking.user_task_9"]
KEY = ["task", "mode", "fault", "branch_seed"]


def env(**kw):
    for k, v in kw.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def calendar(exp: str) -> pd.DataFrame:
    """5.3: fault-caused irreversible share per family x point under old / sim / doc calendar labels (E1)."""
    env(FPR_OUTCOME=None, FPR_EXCLUDE_TASKS=None)
    df = load(exp, runs_root())
    df = df[df.fault != "PC"]
    arts = pd.read_csv(OUT / "tables" / exp / "cf_artifacts.csv.gz")
    out = []
    for mode in ("old", "sim", "doc"):
        env(FPR_CALENDAR=mode)
        t = irreversibility(with_external(arts, exp), df, ["family", "point"])
        out.append(t[["family", "point", "n", "share_fault_caused", "ci_lo", "ci_hi"]].assign(calendar=mode))
    env(FPR_CALENDAR=None)
    t = pd.concat(out)
    w = t.pivot_table(index=["family", "point", "n"], columns="calendar", values="share_fault_caused").reset_index()
    w["doc_minus_old"] = w["doc"] - w["old"]
    return w[["family", "point", "n", "old", "sim", "doc", "doc_minus_old"]]


def calendar_by_definition(exp: str, workers: int) -> pd.DataFrame:
    """5.3 x 5.2: fault-caused irreversible share per family x point under calendar label x equivalence E1-E3."""
    from multiprocessing import Pool

    import phase1_validation as pv

    env(FPR_OUTCOME=None, FPR_EXCLUDE_TASKS=None)
    df = load(exp, runs_root())
    df = df[df.fault != "PC"].copy()
    df["point_c"] = df.point.where(df.kind == "clean", "R.start")
    df.loc[df.kind == "fault", "point_c"] = None
    with Pool(workers) as p:
        ext = {x["run_dir"]: x for x in p.map(pv.extract, df[df.kind.isin(["fault", "clean", "ref"])].run_dir.tolist(), chunksize=16)}
    arts = pv.artifact_matches(df, ext, pv.clean_samples(df))
    out = []
    for mode in ("old", "sim", "doc"):
        env(FPR_CALENDAR=mode)
        t = pv.v7_irreversible(arts, df, exp)
        out.append(t.assign(calendar=mode))
    env(FPR_CALENDAR=None)
    return pd.concat(out, ignore_index=True)


def clean_strict(exp: str) -> pd.DataFrame:
    """5.4 validity check: clean reference / continuation success per task under both outcomes."""
    r = pd.read_csv(OUT / "tables" / exp / "wp0_runs.csv.gz")
    r = r[r.fault.isna() | r.fault.fillna("").str.startswith("CLEAN_")]
    r["strict_success"] = r.utility & r.strict_match
    t = r.groupby("task").agg(n=("utility", "size"), agentdojo=("utility", "mean"), strict=("strict_success", "mean"),
                              gt_missing=("gt_missing", "mean"), extra_writes=("extra_writes", "mean")).reset_index()
    t["flag_matcher_too_narrow"] = (t.strict < 0.5) & (t.agentdojo >= 0.8)
    return t


def headline(main: str, restart: str, verify: str) -> pd.DataFrame:
    """5.4/5.5: headline comparisons under AgentDojo vs strict outcome, with and without the fill-rule tasks."""
    out = []
    for outcome in ("agentdojo", "strict"):
        for tasks, excl in (("all", None), ("without_fill_rule", ",".join(FILL_RULE))):
            env(FPR_OUTCOME=outcome, FPR_EXCLUDE_TASKS=excl)
            t = comparisons(main, restart, verify)
            out.append(t.assign(outcome=outcome, tasks=tasks))
            print(outcome, tasks, "done", flush=True)
    env(FPR_OUTCOME=None, FPR_EXCLUDE_TASKS=None)
    return pd.concat(out, ignore_index=True)


def r1_decomposition(main: str, restart: str, verify: str) -> pd.DataFrame:
    """Exploratory (not pre-registered): R1 - R0 utility under AgentDojo, strict, and strict without the faulted run's
    re-applied external effects (prior_effect), paired per branch, cluster bootstrap over tasks."""
    from phase1_recovery import branches
    from phase1_stats import boot_p

    env(FPR_OUTCOME=None, FPR_EXCLUDE_TASKS=None)
    b = branches(runs_root(), {"main": main, "restart": restart, "verify": verify})
    lab = []
    for exp, method in ((main, "R0"), (restart, "R1"), (verify, "R3")):
        r = pd.read_csv(OUT / "tables" / exp / "wp0_runs.csv.gz")
        name = r.run_dir.str.rsplit("/", n=1).str[1]
        r = r.assign(fault=name.str.rsplit("_b", n=1).str[0], branch_seed=pd.to_numeric(name.str.rsplit("_b", n=1).str[1], errors="coerce"), method=method)
        r = r.dropna(subset=["branch_seed"]).astype({"branch_seed": int})
        lab.append(r[KEY + ["method", "strict_match", "strict_match_without_prior"]])
    x = b.merge(pd.concat(lab), on=KEY + ["method"], how="left")
    u = x.utility_m.astype(bool)
    x = x.assign(agentdojo=u, strict=u & x.strict_match.astype(bool), strict_without_prior=u & x.strict_match_without_prior.astype(bool))
    rows = []
    for fam in ("accidental", "adversarial"):
        for m in ("R1", "R3"):
            for o in ("agentdojo", "strict", "strict_without_prior"):
                w = x[(x.family == fam) & x.method.isin(["R0", m])].pivot_table(index=KEY, columns="method", values=o, aggfunc="first").dropna().reset_index()
                est, lo, hi, p = boot_p(w, lambda t, m=m: t[m].astype(float).mean() - t["R0"].astype(float).mean())
                rows.append({"family": fam, "comparison": f"{m} - R0 utility", "outcome": o, "n": len(w), "R0": w.R0.astype(float).mean(),
                             m if m == "R1" else "R3": w[m].astype(float).mean(), "est": est, "lo": lo, "hi": hi, "p": p})
    return pd.DataFrame(rows)


def robustness(h: pd.DataFrame) -> pd.DataFrame:
    sign = lambda r: 1 if r.lo > 0 else (-1 if r.hi < 0 else 0)
    h = h.assign(sign=[sign(r) for r in h.itertuples()])
    w = h.pivot_table(index="comparison", columns=["outcome", "tasks"], values="sign", aggfunc="first")
    w.columns = [f"{a}|{b}" for a, b in w.columns]
    base = w["agentdojo|all"]
    w["outcome_robust"] = (base != 0) & (w["strict|all"] == base)
    w["fill_rule_robust"] = (base != 0) & (w["agentdojo|without_fill_rule"] == base)
    est = h.pivot_table(index="comparison", columns=["outcome", "tasks"], values="est", aggfunc="first")
    est.columns = [f"est {a}|{b}" for a, b in est.columns]
    order = list(dict.fromkeys(h.comparison))
    return w.join(est).reindex(order).reset_index()


def permissive(exp: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """5.6: successful executor writes with invalid arguments, per condition; share of fault-caused external effects."""
    env(FPR_OUTCOME=None, FPR_EXCLUDE_TASKS=None, FPR_CALENDAR=None)
    c = pd.read_csv(OUT / "tables" / exp / "wp0_calls.csv.gz").fillna({"invalid": ""})
    c = c[c.ok & ~c.prior]
    df = load(exp, runs_root())
    meta = df.assign(rel=df.run_dir.map(rel))[["rel", "kind", "fault", "family"]]
    c = c.assign(rel=c.run_dir.map(rel)).merge(meta, on="rel")
    c["cond"] = c.fault.where(c.kind == "fault", c.kind)
    t = c.groupby(["kind", "cond"]).agg(family=("family", "first"), n_ok_writes=("invalid", "size"),
                                         n_invalid=("invalid", lambda s: int((s != "").sum()))).reset_index()
    t["share_invalid"] = t.n_invalid / t.n_ok_writes
    kinds = c[c.invalid != ""].invalid.str.split(";").explode().value_counts().rename_axis("invalid_kind").reset_index(name="n")
    arts = with_external(pd.read_csv(OUT / "tables" / exp / "cf_artifacts.csv.gz"), exp)
    eff = arts[(arts.level == "call") & arts.ok & arts.external & arts.dependent].copy()
    eff["rel"] = [f"{a}/{b}/{f}_b{s}" for a, b, f, s in zip(eff.task, eff["mode"], eff.fault, eff.branch_seed)]
    eff["call_id"] = eff.node.str.removeprefix("tool:")
    inv = dict(zip(zip(c.rel, c.call_id), c.invalid))
    eff["invalid"] = [inv.get(k, "") for k in zip(eff.rel, eff.call_id)]
    s = eff.groupby("family").agg(n_effects=("invalid", "size"), n_invalid=("invalid", lambda x: int((x != "").sum()))).reset_index()
    s["share_invalid"] = s.n_invalid / s.n_effects
    s["above_5pct"] = s.share_invalid > 0.05
    return t, pd.concat([s, kinds], axis=1)


def irreversible_without_invalid(exp: str) -> pd.DataFrame:
    """5.6 rule: irreversible shares with and without effects that carry an invalid argument (doc calendar labels)."""
    env(FPR_OUTCOME=None, FPR_EXCLUDE_TASKS=None, FPR_CALENDAR=None)
    df = load(exp, runs_root())
    df = df[df.fault != "PC"]
    arts = with_external(pd.read_csv(OUT / "tables" / exp / "cf_artifacts.csv.gz"), exp)
    c = pd.read_csv(OUT / "tables" / exp / "wp0_calls.csv.gz").fillna({"invalid": ""})
    bad = {(rel(r), i) for r, i, v in zip(c.run_dir, c.call_id, c.invalid) if v}
    k = [(f"{a}/{b}/{f}_b{s}", str(n).removeprefix("tool:")) for a, b, f, s, n in zip(arts.task, arts["mode"], arts.fault, arts.branch_seed, arts.node)]
    drop = pd.Series([x in bad for x in k], index=arts.index)
    a1 = irreversibility(arts, df, ["family", "point"])[["family", "point", "n", "share_fault_caused"]]
    a2 = irreversibility(arts.assign(external=arts.external & ~drop), df, ["family", "point"])[["family", "point", "share_fault_caused"]]
    return a1.merge(a2, on=["family", "point"], suffixes=("_all", "_valid_args_only"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--main", default="main")
    ap.add_argument("--restart", default="restart")
    ap.add_argument("--verify", default="verify")
    ap.add_argument("--skip-headline", action="store_true")
    ap.add_argument("--workers", type=int, default=16)
    a = ap.parse_args()
    d = out_dir(f"tables/wp0{'' if a.main == 'main' else '_' + a.main}")
    tabs = {"calendar_irreversible": calendar(a.main), "calendar_by_definition": calendar_by_definition(a.main, a.workers),
            "clean_strict_by_task": clean_strict(a.main)}
    perm, perm_s = permissive(a.main)
    tabs["invalid_args_by_condition"], tabs["invalid_args_summary"] = perm, perm_s
    tabs["irreversible_invalid_args"] = irreversible_without_invalid(a.main)
    tabs["recovery_utility_decomposition_exploratory"] = r1_decomposition(a.main, a.restart, a.verify)
    if not a.skip_headline:
        h = headline(a.main, a.restart, a.verify)
        tabs["headline_by_outcome_and_tasks"] = h
        tabs["headline_robustness"] = robustness(h)
    for name, t in tabs.items():
        t.to_csv(d / f"{name}.csv", index=False, float_format="%.4f")
        (d / f"{name}.md").write_text(t.to_markdown(index=False, floatfmt=".3f"))
        print(f"\n## {name}\n", t.round(3).to_string())


if __name__ == "__main__":
    main()
