"""Cross-model comparison of the full Phase 1 matrix. Reads the per-model tables written by phase1_tables,
phase1_counterfactual and phase1_recovery under data/tables/ (run those first for each model)."""

import argparse

import pandas as pd
from common import ROOT, out_dir

LABELS = {
    "qwen3-8b": "Qwen3-8B",
    "llama3.1-8b": "Llama-3.1-8B",
    "granite3.3-8b": "Granite-3.3-8B",
    "ministral-8b": "Ministral-8B",
    "qwen3-4b": "Qwen3-4B",
    "qwen3-14b": "Qwen3-14B-AWQ",
}
FAMILIES = ["accidental", "adversarial"]


def exps(model: str) -> tuple[str, str]:
    return ("main", "recovery") if model == "qwen3-8b" else (f"main_{model}", f"recovery_{model}")


def read(rel: str) -> pd.DataFrame:
    p = ROOT / "data/tables" / rel
    return pd.read_csv(p) if p.exists() else pd.DataFrame()


def ci(v, lo, hi) -> str:
    return f"{v:+.2f} [{lo:+.2f}, {hi:+.2f}]"


def collect(models: list[str]) -> dict[str, pd.DataFrame]:
    prop, clean, rec, rdelta, graphs = [], [], [], [], []
    for m in models:
        main, tag = exps(m)
        lab = {"model": LABELS.get(m, m)}
        c = read(f"{main}/clean.csv")
        if c.empty:
            continue
        c = c[c.suite == "all"] if "all" in set(c.suite) else c
        for mode, g in c.groupby("mode"):
            clean.append({**lab, "mode": mode, "clean_success": (g.clean_success * g.n).sum() / g.n.sum(), "stall": (g.stall * g.n).sum() / g.n.sum(), "n_refs": int(g.n.sum())})
        f = read(f"{main}/by_family_mode.csv")
        f = f[f.family.isin(FAMILIES)]
        for _, r in f.iterrows():
            prop.append({**lab, "family": r.family, "mode": r["mode"], "n": int(r.n), "dependence": r.dependence, "prop_given_dep": r.prop_given_dep,
                         "delta": r.delta, "delta_lo": r.delta_lo, "delta_hi": r.delta_hi, "harm_rate": r.harm_rate, "clean_bad": r.clean_bad})
        g = read(f"{main}/graph_accuracy_family.csv")
        if not g.empty:
            graphs.append(g[g.family.isin(FAMILIES)].assign(**lab))
        r = read(f"{tag}/recovery_family.csv")
        if not r.empty:
            rec.append(r.assign(**lab))
        d = read(f"{tag}/recovery_delta_family.csv")
        if not d.empty:
            rdelta.append(d.assign(**lab))
    cat = lambda xs: pd.concat(xs, ignore_index=True) if xs and isinstance(xs[0], pd.DataFrame) else pd.DataFrame(xs)
    return {"clean": cat(clean), "propagation": cat(prop), "graphs": cat(graphs), "recovery": cat(rec), "recovery_delta": cat(rdelta)}


def summary(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per model x family: the headline numbers of the report."""
    rows = []
    p, rd, g = t["propagation"], t["recovery_delta"], t["graphs"]
    for (m, fam), x in p.groupby(["model", "family"], sort=False):
        row = {"model": m, "family": fam}
        for _, r in x.iterrows():
            row[f"prop|dep {r['mode']}"] = round(r.prop_given_dep, 2)
            row[f"delta {r['mode']}"] = ci(r.delta, r.delta_lo, r.delta_hi)
        if not g.empty:
            gg = g[(g.model == m) & (g.family == fam)].set_index("graph")
            for gr in ("taint", "declared_taint"):
                if gr in gg.index:
                    row[f"recall {gr}"] = round(gg.loc[gr, "recall"], 2)
        if not rd.empty:
            dd = rd[(rd.model == m) & (rd.family == fam)]
            for meth, met in (("R1", "utility"), ("R3", "utility"), ("R3", "harm")):
                y = dd[(dd.method == meth) & (dd.metric == met)]
                if len(y):
                    row[f"{meth}-R0 {met}"] = ci(y.delta_vs_R0.iloc[0], y.lo.iloc[0], y.hi.iloc[0])
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(LABELS))
    a = ap.parse_args()
    t = collect(a.models.split(","))
    t["summary"] = summary(t)
    d = out_dir("tables/models")
    for name, x in t.items():
        if x.empty:
            continue
        x.to_csv(d / f"{name}.csv", index=False, float_format="%.4f")
        (d / f"{name}.md").write_text(x.to_markdown(index=False, floatfmt=".2f"))
    print(t["clean"].round(2).to_string())
    print(t["summary"].to_string())


if __name__ == "__main__":
    main()
