"""W5: recovery baselines under identical fault schedules. R0 = faulted run as-is (main), R1 = full restart (restart),
R3 = verify-all (verify). Tables with cluster-bootstrap CIs, fig7 (recovery by family x stage x mode), fig8 (Pareto)."""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from common import ROOT, boot_ci, load, out_dir, runs_root, style

KEY = ["task", "mode", "fault", "branch_seed"]
METHODS = ["R0", "R1", "R3"]
MCOL = {"R0": "#8f8e88", "R1": "#2a78d6", "R3": "#eb6834"}
STAGES = ["R.tool", "R.start", "R.out", "P.start", "P.out", "S.write", "E.tool"]


def external_tools() -> set[str]:
    t = yaml.safe_load((ROOT / "configs/tools.yaml").read_text())
    return {n for s in t.values() for n, v in s.get("write", {}).items() if v.get("external")} | {n for s in t.values() for n in s.get("read_external", [])}


def fault_caused(exp: str) -> pd.DataFrame:
    """Per branch: counterfactual fault-caused irreversible / compensable external effects (from W4 cf_artifacts)."""
    p = ROOT / "data/tables" / exp / "cf_artifacts.csv.gz"
    a = pd.read_csv(p)
    a = a[(a.level == "call") & a.ok & a.dependent & a.name.isin(external_tools())]
    g = a.groupby(KEY)
    return pd.DataFrame({"fc_irrev": g.apply(lambda x: int((x.rev == "irreversible").sum())), "fc_comp": g.apply(lambda x: int((x.rev == "compensable").sum()))}).reset_index()


def col(df: pd.DataFrame, name: str) -> pd.Series:
    """Column or NaN (runs from older code lack some fields)."""
    return df[name] if name in df else pd.Series(np.nan, index=df.index)


def tokens(df: pd.DataFrame) -> pd.Series:
    return df.prompt_tokens + df.completion_tokens


def branches(root: Path, exps: dict) -> pd.DataFrame:
    main = load(exps["main"], root)
    d = main[(main.kind == "fault") & main.injected].copy()
    d = d.merge(fault_caused(exps["main"]), on=KEY, how="left").fillna({"fc_irrev": 0, "fc_comp": 0})
    r0 = d.assign(method="R0", utility_m=d.utility, harm_m=d.harm.astype(bool), unrec=d.fc_irrev, comp=d.fc_comp, dups=0,
                  recomp_calls=0, recomp_tokens=0, total_tokens=tokens(d), latency=col(d, "llm_latency_s"), extra_tokens=0)
    cols = KEY + ["family", "point", "ftype", "method", "utility_m", "harm_m", "unrec", "comp", "dups", "recomp_calls", "recomp_tokens", "total_tokens", "latency", "extra_tokens"]
    out = [r0[cols]]
    rs = load(exps["restart"], root)
    if not rs.empty:
        rs = rs.copy()
        rs["fault"] = rs.run_dir.map(lambda p: Path(p).name.rsplit("_b", 1)[0])
        rs["branch_seed"] = rs.run_dir.map(lambda p: int(Path(p).name.rsplit("_b", 1)[1]))
        rs = rs.drop(columns=["family", "point", "ftype"]).merge(d.assign(llm_latency_s=col(d, "llm_latency_s"))[KEY + ["family", "point", "ftype", "fc_irrev", "fc_comp", "prompt_tokens", "completion_tokens", "llm_latency_s"]].rename(
            columns={"prompt_tokens": "d_pt", "completion_tokens": "d_ct", "llm_latency_s": "d_lat"}), on=KEY)
        r1 = rs.assign(method="R1", utility_m=rs.utility, harm_m=rs.residual_harm.astype(bool), unrec=rs.fc_irrev, comp=rs.fc_comp, dups=rs.duplicates,
                       recomp_calls=rs.n_llm_calls, recomp_tokens=tokens(rs), total_tokens=tokens(rs) + rs.d_pt + rs.d_ct, latency=col(rs, "llm_latency_s") + rs.d_lat, extra_tokens=tokens(rs))
        out.append(r1[cols])
    vf = load(exps["verify"], root)
    if not vf.empty:
        v = vf[(vf.kind == "fault") & (vf.fault != "PC") & vf.injected].copy()
        if not (ROOT / f"data/tables/{exps['verify']}/cf_artifacts.csv.gz").exists():
            raise SystemExit(f"run analysis/phase1_counterfactual.py --exp {exps['verify']} first")
        v = v.merge(fault_caused(exps["verify"]), on=KEY, how="left").fillna({"fc_irrev": 0, "fc_comp": 0})
        v = v.merge(d[KEY], on=KEY)  # identical fault schedule: injected in both main and verify
        r3 = v.assign(method="R3", utility_m=v.utility, harm_m=v.harm.fillna(v.security_violation).astype(bool), unrec=v.fc_irrev, comp=v.fc_comp, dups=0,
                      recomp_calls=0, recomp_tokens=0, total_tokens=tokens(v), latency=col(v, "llm_latency_s"), extra_tokens=v.verifier_tokens)
        out.append(r3[cols])
    return pd.concat(out, ignore_index=True)


STATS = {
    "utility": lambda t: t.utility_m.mean(),
    "residual_harm": lambda t: t.harm_m.mean(),
    "unrecoverable_share": lambda t: (t.unrec > 0).mean(),
}


def summarize(b: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    rows = []
    for k, g in b.groupby(keys + ["method"]):
        k = k if isinstance(k, tuple) else (k,)
        row = dict(zip(keys + ["method"], k))
        row["n"] = len(g)
        for name, fn in STATS.items():
            row[name] = fn(g)
            row[f"{name}_lo"], row[f"{name}_hi"] = boot_ci(g, fn)
        row.update({
            "unrecoverable_mean": g.unrec.mean(), "compensable_share": (g.comp > 0).mean(), "duplicates_mean": g.dups.mean(),
            "duplicate_share": (g.dups > 0).mean(), "recomp_calls": g.recomp_calls.mean(), "recomp_tokens": g.recomp_tokens.mean(),
            "extra_tokens": g.extra_tokens.mean(), "total_tokens": g.total_tokens.mean(), "llm_latency_s": g.latency.mean(),
        })
        rows.append(row)
    return pd.DataFrame(rows)


def paired_delta(b: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Utility and harm differences vs R0 on the same branches, cluster-bootstrap CI over tasks."""
    w = b.pivot_table(index=KEY + ["family", "point"], columns="method", values=["utility_m", "harm_m"], aggfunc="first")
    w.columns = [f"{a}_{m}" for a, m in w.columns]
    w = w.reset_index()
    rows = []
    for m in ("R1", "R3"):
        if f"utility_m_{m}" not in w:
            continue
        x = w.dropna(subset=[f"utility_m_{m}", "utility_m_R0"])
        for k, g in x.groupby(keys) if keys else [((), x)]:
            k = k if isinstance(k, tuple) else (k,)
            for met in ("utility_m", "harm_m"):
                fn = lambda t, a=f"{met}_{m}", c=f"{met}_R0": t[a].astype(float).mean() - t[c].astype(float).mean()
                lo, hi = boot_ci(g, fn)
                rows.append({**dict(zip(keys, k)), "method": m, "metric": met.replace("_m", ""), "n": len(g), "delta_vs_R0": fn(g), "lo": lo, "hi": hi})
    return pd.DataFrame(rows)


def clean_overhead(root: Path, exps: dict) -> pd.DataFrame:
    m, v = load(exps["main"], root), load(exps["verify"], root)
    if v.empty:
        return pd.DataFrame()
    rows = []
    for name, x in ((exps["main"], m), (exps["verify"], v)):
        r = x[x.kind == "ref"]
        for mode, g in r.groupby("mode"):
            lo, hi = boot_ci(g, lambda t: t.utility.mean())
            rows.append({"exp": name, "mode": mode, "n": len(g), "clean_success": g.utility.mean(), "lo": lo, "hi": hi, "tokens": tokens(g).mean(),
                         "verifier_tokens": g.get("verifier_tokens", pd.Series(0, index=g.index)).fillna(0).mean(), "llm_calls": g.n_llm_calls.mean()})
    return pd.DataFrame(rows)


def fig7(s: pd.DataFrame, d: Path) -> None:
    plt = style()
    fams = ["accidental", "adversarial"]
    metrics = [("utility", "recovered correctness (utility)"), ("residual_harm", "residual harm"), ("unrecoverable_share", "branches with unrecoverable effect")]
    fig, axes = plt.subplots(len(metrics), 2, figsize=(12, 9), sharey="row")
    for j, fam in enumerate(fams):
        x = s[s.family == fam]
        stages = [p for p in STAGES if p in set(x.point)]
        for i, (met, label) in enumerate(metrics):
            ax = axes[i, j]
            w = 0.8 / (len(METHODS) * 2)
            for mi, m in enumerate(METHODS):
                for hi_, mode in enumerate(("message", "state")):
                    y = x[(x.method == m) & (x["mode"] == mode)].set_index("point").reindex(stages)
                    pos = np.arange(len(stages)) + (mi * 2 + hi_ - (len(METHODS) * 2 - 1) / 2) * w
                    err = np.vstack([(y[met] - y[f"{met}_lo"]).clip(lower=0), (y[f"{met}_hi"] - y[met]).clip(lower=0)])
                    ax.bar(pos, y[met], w * 0.95, color=MCOL[m], alpha=1.0 if mode == "message" else 0.55, yerr=err, error_kw={"lw": 0.6, "ecolor": "#52514e"},
                           label=f"{m} {mode}" if (i == 0 and j == 0) else None)
            ax.set_xticks(np.arange(len(stages)), stages)
            ax.set_ylim(0, 1.05)
            if j == 0:
                ax.set_ylabel(label)
            if i == 0:
                ax.set_title(fam.capitalize(), loc="left")
    fig.legend(loc="lower center", ncol=6, bbox_to_anchor=(0.5, -0.02))
    fig.suptitle("Recovery baselines by fault stage (R0 none, R1 full restart, R3 verify-all; solid = message, light = state; 95% cluster-bootstrap CI)", x=0.01, ha="left")
    fig.savefig(d / "fig7_recovery.png")
    plt.close(fig)


def fig8(s: pd.DataFrame, d: Path) -> None:
    plt = style()
    fig, ax = plt.subplots(figsize=(7, 4.5))
    marks = {"accidental": "o", "adversarial": "s"}
    for fam, mk in marks.items():
        for m in METHODS:
            y = s[(s.family == fam) & (s.method == m)]
            if y.empty:
                continue
            ax.errorbar(y.total_tokens, y.utility, yerr=[y.utility - y.utility_lo, y.utility_hi - y.utility], fmt=mk, color=MCOL[m], ms=7, lw=0.8,
                        label=f"{m} {fam}")
            ax.annotate(m, (y.total_tokens.iloc[0], y.utility.iloc[0]), textcoords="offset points", xytext=(6, 4), fontsize=8, color="#52514e")
    ax.set_xlabel("inference tokens per faulted branch (R0: faulted run; R1: faulted run + restart; R3: verified run)")
    ax.set_ylabel("recovered correctness (utility)")
    ax.set_ylim(0, 1)
    ax.legend(ncol=2, fontsize=8)
    ax.set_title("Correctness vs inference cost (R3 pays at run time, R1 after the fault)", loc="left")
    fig.savefig(d / "fig8_pareto.png")
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="")
    ap.add_argument("--main", default="main")
    ap.add_argument("--restart", default="restart")
    ap.add_argument("--verify", default="verify")
    ap.add_argument("--tag", default="recovery")
    a = ap.parse_args()
    root = Path(a.root) if a.root else runs_root()
    exps = {"main": a.main, "restart": a.restart, "verify": a.verify}
    b = branches(root, exps)
    b = b[b.family != "control"]
    dt, df_ = out_dir(f"tables/{a.tag}"), out_dir(f"figures/{a.tag}")
    tables = {
        "recovery_family": summarize(b, ["family"]),
        "recovery_family_mode": summarize(b, ["family", "mode"]),
        "recovery_family_stage_mode": summarize(b, ["family", "point", "mode"]),
        "recovery_condition": summarize(b, ["fault"]),
        "recovery_delta_family": paired_delta(b, ["family"]),
        "recovery_delta_family_stage": paired_delta(b, ["family", "point"]),
        "clean_overhead": clean_overhead(root, exps),
    }
    for name, t in tables.items():
        t.to_csv(dt / f"{name}.csv", index=False, float_format="%.4f")
        (dt / f"{name}.md").write_text(t.to_markdown(index=False, floatfmt=".2f"))
    cov = b.groupby(["method", "family"]).size().unstack(fill_value=0)
    (dt / "coverage.md").write_text(cov.to_markdown())
    fig7(tables["recovery_family_stage_mode"], df_)
    fam = summarize(b, ["family"])
    fig8(fam, df_)
    print(fam[["family", "method", "n", "utility", "utility_lo", "utility_hi", "residual_harm", "unrecoverable_share", "duplicates_mean", "total_tokens"]].round(3).to_string())
    print(tables["recovery_delta_family"].round(3).to_string())
    print("->", dt, df_)


if __name__ == "__main__":
    main()
