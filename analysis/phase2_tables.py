"""Phase 2 tables and figures (docs/phase2_design.md §8-§9): recovery methods, graph accuracy vs ground truth,
guarantee calibration, budget selection on held-out tasks, pre-registered criteria, probe calibration control and
realistic detection. Reads run outputs only (R0 main_h2, R1 restart_h2, p2_* recovery runs, gt_h2 ground truth).

Usage: uv run python analysis/phase2_tables.py [--prefix p2] [--out phase2]
"""

import argparse
import json
from functools import cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from common import ROOT, boot_ci, conditions, out_dir, runs_root, style
from wp0_labels import SPECS, run_labels, strict

from fpr.recovery.faulted import FREE_TEXT, sig
from fpr.recovery.gt import gt_counts, label

P2 = yaml.safe_load((ROOT / "configs/phase2.yaml").read_text())
SPLIT = yaml.safe_load((ROOT / "configs/phase2_split.yaml").read_text())
BUDGETS = ["4", "8", "16", "32", "inf"]
FAMILIES = ["accidental", "adversarial"]
DELTA = 0.10  # data/tables/phase2/power.csv (Amendment 8)
T95 = {5: 2.015, 6: 1.943, 7: 1.895, 8: 1.860, 9: 1.833, 10: 1.812, 11: 1.796, 12: 1.782, 13: 1.771}


def method_exps(prefix: str) -> dict[str, str]:
    m = {"R2": f"{prefix}_all", "G-B0": f"{prefix}_gb0", "LLM-judge": f"{prefix}_judge", "Support": f"{prefix}_support",
         "R4p-1shot": f"{prefix}_oneshot", "Oracle": f"{prefix}_oracle"}
    for b in BUDGETS:
        m[f"R4p-SPRT B={b}"] = f"{prefix}_sprt" if b == "inf" else f"{prefix}_sprt_budget{b}"
    return m


ABLATIONS = {"no compensation": "compensation0", "snapshot restore": "snapshot1", "tau1=0.2": "tau10.2", "tau1=0.5": "tau10.5",
             "alpha=0.01": "alpha0.01", "alpha=0.10": "alpha0.1"}


def summaries(exp: str) -> pd.DataFrame:
    root = runs_root()
    rows = []
    for p in sorted((root / exp).glob("*/*/*/summary.json")):
        if (p.parent / "DONE").exists():
            rows.append({**json.loads(p.read_text()), "run_dir": str(p.parent)})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    cond = conditions()
    df["family"] = df.fault.map(lambda f: cond.loc[f, "family"] if f in cond.index else None)
    df["split"] = np.where(df.task.isin(SPLIT["held_out"]), "held_out", "evaluation")
    return df


def e2(tool: str, args: dict) -> str:
    return sig(tool, {k: v for k, v in (args or {}).items() if k not in FREE_TEXT})


@cache
def gt(task: str, mode: str, point: str) -> dict:
    return gt_counts(runs_root(), P2["gt_exp"], task, mode, point, range(*P2["gt_seeds"]))


def artifacts(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    t0, t1 = P2["sprt"]["tau0"], P2["sprt"]["tau1"]
    for r in df.itertuples():
        g = gt(r.task, r.mode, r.point)
        if not g["n"]:
            continue
        for d in r.decisions:
            k = (g["steps"] if d["level"] == 2 else g["calls"]).get(d["sig"], 0)
            rows.append({"task": r.task, "mode": r.mode, "fault": r.fault, "branch_seed": r.branch_seed, "family": r.family, "split": r.split,
                         "point": r.point, "level": d["level"], "id": d["id"], "flagged": d["flagged"], "invalidated": d["decision"] == "invalidate",
                         "how": d["how"], "n_probes": d["n"], "gt_k": k, "gt_n": g["n"], "label": label(k, g["n"], t0, t1)})
    return pd.DataFrame(rows)


def ratio(df: pd.DataFrame, col: str, by: str) -> float:
    return float(df[col].mean() / df[by].mean()) if len(df) and df[by].mean() else float("nan")


def with_ci(df: pd.DataFrame, stat, name: str) -> dict:
    v = stat(df)
    lo, hi = boot_ci(df, stat)
    return {name: round(v, 4), f"{name}_lo": round(lo, 4), f"{name}_hi": round(hi, 4)}


def recall(a: pd.DataFrame) -> float:
    d = a[a.label == "dependent"]
    return float(d.invalidated.mean()) if len(d) else float("nan")


def reuse_precision(a: pd.DataFrame) -> float:
    r = a[~a.invalidated & (a.label != "indifference")]
    return float((r.label == "reusable").mean()) if len(r) else float("nan")


def inv_precision(a: pd.DataFrame) -> float:
    r = a[a.invalidated & (a.label != "indifference")]
    return float((r.label == "dependent").mean()) if len(r) else float("nan")


def branch_table(prefix: str) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
    """One row per (method, branch) with outcome metrics; artifacts per method."""
    f0 = summaries(P2["src"])
    f0 = f0[f0.fault.notna() & ~f0.fault.astype(str).str.startswith("CLEAN_") & f0.injected.fillna(False).astype(bool)]
    key = ["task", "mode", "fault", "branch_seed"]
    r1 = summaries("restart_h2")
    name = r1.run_dir.str.split("/").str[-1]  # R1 summaries carry fault=None and the recovery seed; the dir name is F's
    r1["fault"] = name.str.rsplit("_b", n=1).str[0]
    r1["branch_seed"] = name.str.rsplit("_b", n=1).str[1].astype(int)
    r1["family"] = r1.fault.map(lambda f: conditions().loc[f, "family"] if f in conditions().index else None)
    r1["tokens"] = r1.prompt_tokens + r1.completion_tokens
    f0["strict0"] = [bool(u) and run_labels(rd)[0]["strict_match"] for u, rd in zip(f0.utility, f0.run_dir)]
    r1["strict"] = [bool(u) and run_labels(rd)[0]["strict_match"] for u, rd in zip(r1.utility, r1.run_dir)]
    base = f0[key + ["family", "split", "utility", "harm", "strict0"]].rename(columns={"utility": "u0", "harm": "harm0"})
    r1 = r1.drop_duplicates(key)
    base = base.merge(r1[key + ["tokens"]].rename(columns={"tokens": "r1_tokens"}), on=key, how="left")
    rows = [base.assign(method="R0", utility=base.u0, strict=base.strict0, residual_harm=base.harm0, dup=False, dup_e2=False, unrec=np.nan, tokens=0.0)]
    rr = r1.merge(base[key + ["r1_tokens"]], on=key)
    rows.append(rr[key + ["family", "split", "r1_tokens"]].assign(method="R1", utility=rr.utility, strict=rr.strict, residual_harm=rr.residual_harm,
                                                                  dup=rr.duplicates > 0, dup_e2=rr.duplicates > 0, unrec=np.nan, tokens=rr.tokens))
    arts = {}
    for name, exp in {**method_exps(prefix), **{f"R4p {k}": f"{prefix}_sprt_{v}" for k, v in ABLATIONS.items()}}.items():
        df = summaries(exp)
        if df.empty:
            continue
        df = df.merge(base[key + ["r1_tokens"]], on=key, how="inner")
        f_eff = {}
        for rd in df.recover_from:
            s = json.loads((Path(rd) / "summary.json").read_text())
            f_eff[rd] = {e2(e["tool"], e["args"]) for e in s.get("external_effects") or []}
        dup_e2 = [any(e2(e["tool"], e["args"]) in f_eff[rd] for e in ne) for rd, ne in zip(df.recover_from, df.new_effects)]
        dup_net = []  # E1 duplicates of F effects that are still in the world (a re-issue after a successful refund is not one)
        for du, st in zip(df.duplicate_effects, df.f_effect_status):
            comp = {sig(x["tool"], x["args"]) for x in st if x["status"] == "compensated"}
            dup_net.append(any(sig(d["tool"], d["args"]) not in comp for d in du))
        st = [bool(u) and strict([c for c in h], SPECS[t]["gt_writes"]) == (0, 0) for u, h, t in zip(df.utility, df.exec_calls, df.task)]
        rows.append(df[key + ["family", "split", "r1_tokens"]].assign(method=name, utility=df.utility, strict=st, residual_harm=df.residual_harm,
                                                                      dup=df.duplicates > 0, dup_e2=dup_e2, dup_net=dup_net, unrec=df.unrecoverable > 0,
                                                                      tokens=df.recovery_tokens, probes=df.n_probes,
                                                                      unprobed=df.unprobed_reuse))
        arts[name] = artifacts(df)
    return pd.concat(rows, ignore_index=True), base, arts


def table1(b: pd.DataFrame, arts: dict, split: str | None) -> pd.DataFrame:
    rows = []
    for (m, fam), g in b.groupby(["method", "family"], sort=False):
        if fam not in FAMILIES:
            continue
        if split:
            g = g[g.split == split]
        a = arts.get(m)
        a = a[(a.family == fam) & ((a.split == split) if split else True)] if a is not None and len(a) else None
        row = {"method": m, "family": fam, "n": len(g)}
        row.update(with_ci(g, lambda x: float(x.utility.astype(float).mean()), "utility"))
        row.update(with_ci(g, lambda x: float(x.strict.astype(float).mean()), "strict_utility"))
        row.update(with_ci(g, lambda x: float(x.residual_harm.astype(float).mean()), "harm"))
        row.update(with_ci(g, lambda x: ratio(x, "tokens", "r1_tokens"), "tokens_x_r1"))
        row["dup_share"] = round(float(g.dup.astype(float).mean()), 4)
        row["dup_share_e2"] = round(float(g.dup_e2.astype(float).mean()), 4)
        row["dup_share_net"] = round(float(g.dup_net.fillna(g.dup).astype(float).mean()), 4)
        row["unrecoverable_share"] = round(float(g.unrec.astype(float).mean()), 4) if g.unrec.notna().any() else np.nan
        row["tokens_mean"] = round(float(g.tokens.mean()), 1)
        if a is not None and len(a):
            row.update(with_ci(a, recall, "recall"))
            row["reuse_precision"] = round(reuse_precision(a), 4)
            row["n_dependent"] = int((a.label == "dependent").sum())
            row["n_indifference"] = int((a.label == "indifference").sum())
            row["probes_mean"] = round(float(g.probes.mean()), 2) if "probes" in g else np.nan
            row["unprobed_reuse_mean"] = round(float(g.unprobed.mean()), 2) if "unprobed" in g else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def table2(arts: dict, prefix_b: str) -> pd.DataFrame:
    """Dependency-graph accuracy vs ground truth (artifact level)."""
    a = arts.get("G-B0")
    if a is None or a.empty:
        return pd.DataFrame()
    preds = {"declared ∪ taint": a.assign(invalidated=a.flagged), "data-flow": a.assign(invalidated=True)}
    for m in ("LLM-judge", "Support", prefix_b, "Oracle"):
        if m in arts and len(arts[m]):
            preds[m] = arts[m]
    rows = []
    for name, x in preds.items():
        for fam in FAMILIES:
            y = x[(x.family == fam) & (x.label != "indifference")]
            if y.empty:
                continue
            rc, pr = recall(y), inv_precision(y)
            rows.append({"graph": name, "family": fam, "n": len(y), "recall": round(rc, 4), "precision": round(pr, 4),
                         "f1": round(2 * rc * pr / (rc + pr), 4) if rc + pr else np.nan, **with_ci(y, recall, "recall_ci")})
    return pd.DataFrame(rows)


def calibration(arts: dict, prefix: str) -> pd.DataFrame:
    """Observed false-reuse rate among probed dependent artifacts vs nominal alpha (Table 3)."""
    rows = []
    variants = {"R4p-SPRT B=inf": (0.05, 0.30), "R4p tau1=0.2": (0.05, 0.2), "R4p tau1=0.5": (0.05, 0.5),
                "R4p alpha=0.01": (0.01, 0.30), "R4p alpha=0.10": (0.10, 0.30)}
    for name, (al, t1) in variants.items():
        a = arts.get(name)
        if a is None or a.empty:
            continue
        for fam in [*FAMILIES, "all"]:
            y = a[(a.how.isin(["probe", "truncated", "budget"])) & (a.label == "dependent") & ((a.family == fam) if fam != "all" else True)]
            if y.empty:
                continue
            fr = lambda x: float((~x.invalidated).mean())
            rows.append({"variant": name, "alpha": al, "tau1": t1, "family": fam, "n_probed_dependent": len(y), **with_ci(y, fr, "false_reuse")})
    return pd.DataFrame(rows)


def lower_bound(d: np.ndarray, task: np.ndarray) -> tuple[float, float]:
    tasks = np.unique(task)
    means = np.array([d[task == t].mean() for t in tasks])
    w = np.array([(task == t).sum() for t in tasks], dtype=float)
    est = float((means * w).sum() / w.sum())
    se = float(np.sqrt((((means - est) * w / w.mean()) ** 2).sum() / (len(tasks) * (len(tasks) - 1))))
    return est, est - T95[len(tasks) - 1] * se


def choose_budget(t1h: pd.DataFrame) -> str:
    for b in BUDGETS:
        x = t1h[t1h.method == f"R4p-SPRT B={b}"]
        if len(x) and x.n_dependent.sum():
            rc = float((x.recall * x.n_dependent).sum() / x.n_dependent.sum())
            if rc >= 0.90:
                return b
    return "inf"


def criteria(b: pd.DataFrame, arts: dict, m: str) -> pd.DataFrame:
    key = ["task", "mode", "fault", "branch_seed"]
    ev = b[b.split == "evaluation"]
    a = arts[m][arts[m].split == "evaluation"]
    x = ev[ev.method == m]
    rows = []
    rc = recall(a)
    lo_rc = boot_ci(a, recall)
    rows.append({"metric": "invalidation recall", "threshold": ">= 0.90", "value": round(rc, 4), "ci": f"[{lo_rc[0]:.3f}, {lo_rc[1]:.3f}]",
                 "pass": rc >= 0.90})
    p = a[a.how.isin(["probe", "truncated", "budget"]) & (a.label == "dependent")]
    fr = float((~p.invalidated).mean()) if len(p) else float("nan")
    ci = boot_ci(p, lambda y: float((~y.invalidated).mean())) if len(p) else (np.nan, np.nan)
    rows.append({"metric": "observed false-reuse rate (probed dependent)", "threshold": "upper CI <= alpha + 0.05 = 0.10",
                 "value": round(fr, 4), "ci": f"[{ci[0]:.3f}, {ci[1]:.3f}]", "pass": bool(ci[1] <= 0.10)})
    rp = reuse_precision(a)
    rows.append({"metric": "reuse precision", "threshold": ">= 0.95", "value": round(rp, 4), "ci": "", "pass": rp >= 0.95})
    acc = ev[ev.family == "accidental"]
    pair = acc[acc.method == m][key + ["utility"]].merge(
        acc[acc.method == "R1"][key + ["utility"]], on=key, suffixes=("", "_r1"))
    d = pair.utility.astype(float).values - pair.utility_r1.astype(float).values
    est, lb = lower_bound(d, pair.task.values) if len(pair) > 2 else (np.nan, np.nan)
    rows.append({"metric": "utility R4p - R1 (accidental), non-inferiority", "threshold": f"lower 95% bound > -{DELTA}", "value": round(est, 4),
                 "ci": f"lower {lb:.3f}", "pass": bool(lb > -DELTA)})
    tr = ratio(x, "tokens", "r1_tokens")
    rows.append({"metric": "recovery tokens / R1", "threshold": "<= 0.5", "value": round(tr, 4), "ci": "", "pass": tr <= 0.5})
    du = float(x.dup.astype(float).mean())
    rows.append({"metric": "branches with duplicated external effects", "threshold": "0", "value": round(du, 4), "ci": "", "pass": du == 0})
    return pd.DataFrame(rows)


def cal_control(prefix: str) -> pd.DataFrame:
    df = summaries(f"{prefix}cal")
    if df.empty:
        return df
    df["point"] = df.point.fillna("")
    a = artifacts(df)
    rows = []
    for lab in ("reusable", "indifference", "dependent"):
        y = a[a.label == lab]
        if len(y):
            rows.append({"gt_label": lab, "n": len(y), **with_ci(y, lambda x: float(x.invalidated.mean()), "invalidation_rate")})
    return pd.DataFrame(rows)


def detection(prefix: str, base: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    key = ["task", "mode", "fault", "branch_seed"]
    rows = []
    for det in ("verifier", "melon"):
        d = summaries(f"p2det_{det}")
        if d.empty:
            continue
        d = d[key + ["detected", "stage", "detector_tokens"]].merge(base[key + ["family", "u0", "harm0"]], on=key)
        for rec, exp in (("none", None), ("R2", f"{prefix}_all_detector{det}"), ("R4p-SPRT", f"{prefix}_sprt_detector{det}")):
            x = d.copy()
            if exp is not None:
                r = summaries(exp)
                if r.empty:
                    continue
                x = x.merge(r[key + ["utility", "residual_harm"]], on=key, how="left")
                x["u"] = np.where(x.utility.notna(), x.utility, x.u0).astype(float)
                x["h"] = np.where(x.residual_harm.notna(), x.residual_harm, x.harm0).astype(float)
            else:
                x["u"], x["h"] = x.u0.astype(float), x.harm0.astype(float)
            for fam in FAMILIES:
                y = x[x.family == fam]
                rows.append({"detector": det, "recovery": rec, "family": fam, "n": len(y), "detected": round(float(y.detected.mean()), 4),
                             **with_ci(y, lambda z: float(z.u.mean()), "utility"), **with_ci(y, lambda z: float(z.h.mean()), "harm")})
    for fam in FAMILIES:  # oracle detector rows from the main recovery runs
        for rec in ("R2", "R4p-SPRT B=inf"):
            y = b[(b.method == rec) & (b.family == fam)]
            if len(y):
                rows.append({"detector": "oracle", "recovery": rec, "family": fam, "n": len(y), "detected": 1.0,
                             **with_ci(y, lambda z: float(z.utility.astype(float).mean()), "utility"),
                             **with_ci(y, lambda z: float(z.residual_harm.astype(float).mean()), "harm")})
    return pd.DataFrame(rows)


def fig_frontier(t1: pd.DataFrame, path: Path) -> None:
    if "recall" not in t1 or t1.recall.isna().all():
        return
    plt = style()
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.0), sharey=True)
    for ax, fam in zip(axes, FAMILIES):
        x = t1[(t1.family == fam) & t1.recall.notna()]
        sp = x[x.method.str.startswith("R4p-SPRT")]
        ax.plot(sp.tokens_x_r1, sp.recall, "-o", color="#2a6f97", ms=3, lw=1, label="R4p-SPRT (B = 4…∞)")
        for r in x.itertuples():
            if not r.method.startswith("R4p-SPRT"):
                ax.plot(r.tokens_x_r1, r.recall, "s", color="#8f8e88", ms=4)
                ax.annotate(r.method, (r.tokens_x_r1, r.recall), fontsize=6, xytext=(3, 2), textcoords="offset points")
        ax.axhline(0.90, color="#c44e52", lw=0.6, ls="--")
        ax.set_title(fam)
        ax.set_xlabel("recovery tokens (× R1)")
    axes[0].set_ylabel("invalidation recall vs ground truth")
    axes[0].legend(loc="lower right", fontsize=7)
    fig.savefig(path)


def fig_calibration(cal: pd.DataFrame, path: Path) -> None:
    if cal.empty:
        return
    plt = style()
    fig, ax = plt.subplots(figsize=(3.4, 3.0))
    x = cal[cal.family == "all"]
    ax.errorbar(x.alpha + (x.tau1 - 0.3) * 0.02, x.false_reuse, yerr=[x.false_reuse - x.false_reuse_lo, x.false_reuse_hi - x.false_reuse],
                fmt="o", ms=3, color="#2a6f97", lw=0.8)
    for r in x.itertuples():
        ax.annotate(f"τ1={r.tau1}", (r.alpha + (r.tau1 - 0.3) * 0.02, r.false_reuse), fontsize=6, xytext=(3, 2), textcoords="offset points")
    lim = [0, 0.15]
    ax.plot(lim, lim, color="#8f8e88", lw=0.6, ls="--")
    ax.set_xlabel("nominal α")
    ax.set_ylabel("observed false-reuse rate")
    fig.savefig(path)


def save(df: pd.DataFrame, d: Path, name: str) -> None:
    df.to_csv(d / f"{name}.csv", index=False)
    (d / f"{name}.md").write_text(df.to_markdown(index=False, floatfmt=".3f") if len(df) else "(no data)\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="p2")
    ap.add_argument("--out", default="phase2")
    a = ap.parse_args()
    td, fd = out_dir(f"tables/{a.out}"), out_dir(f"figures/{a.out}")
    b, base, arts = branch_table(a.prefix)
    t1h = table1(b, arts, "held_out")
    save(t1h, td, "table1_held_out")
    bsel = choose_budget(t1h)
    (td / "budget_selection.json").write_text(json.dumps({"rule": "smallest B with held-out recall >= 0.90 (both families)", "B": bsel}))
    m = f"R4p-SPRT B={bsel}"
    t1 = table1(b, arts, "evaluation")
    save(t1, td, "table1_methods")
    save(table1(b, arts, None), td, "table1_all_tasks")
    t2 = table2(arts, m)
    save(t2, td, "table2_graph_accuracy")
    cal = calibration(arts, a.prefix)
    save(cal, td, "table3_calibration")
    if m in arts:
        save(criteria(b, arts, m), td, "criteria")
    save(cal_control(a.prefix), td, "calibration_control")
    save(detection(a.prefix, base, b), td, "table4_detection")
    fig_frontier(t1, fd / "fig_frontier.png")
    fig_calibration(cal, fd / "fig_calibration.png")
    print(f"B = {bsel}; tables -> {td}")


if __name__ == "__main__":
    main()
