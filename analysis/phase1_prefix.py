"""V3 (docs/preregistration.md): prefix variance. Compares core-condition estimates on the seed-0 prefix (`main`,
branch seeds 100-102) with the pooled estimates on the prefixes of reference seeds 1 and 2 (`prefix_s1`,
`prefix_s2`). Run phase1_counterfactual.py on each prefix experiment first (v3 labels need it)."""

import argparse

import pandas as pd
from common import DEPENDENT, PROPAGATED, boot_ci, load, out_dir, with_delta

STATS = {
    "delta": lambda t: (t.bad.astype(float) - t.clean_bad).mean(),
    "prop_given_used": lambda t: t.category.isin(PROPAGATED).sum() / max(1, t.category.isin(DEPENDENT).sum()),
}


def branches(exp: str, core: set[str], seeds: set[int]) -> pd.DataFrame:
    df = load(exp)
    f = with_delta(df)
    return f[f.injected & f.fault.isin(core) & f.branch_seed.isin(seeds) & f.family.isin(["accidental", "adversarial"])]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exps", default="prefix_s1,prefix_s2")
    a = ap.parse_args()
    other = pd.concat([load(e).assign(prefix=e) for e in a.exps.split(",")], ignore_index=True)
    core = set(other[other.kind == "fault"].fault)
    seeds = set(other[other.kind == "fault"].branch_seed)
    base = branches("main", core, seeds).assign(prefix="seed0")
    alt = pd.concat([branches(e, core, seeds).assign(prefix=e) for e in a.exps.split(",")], ignore_index=True)
    rows = []
    for (fam, mode), g0 in base.groupby(["family", "mode"]):
        g1 = alt[(alt.family == fam) & (alt["mode"] == mode)]
        for name, fn in STATS.items():
            lo, hi = boot_ci(g0, fn)
            v1 = fn(g1)
            lo1, hi1 = boot_ci(g1, fn)
            per = {f"{p}": fn(x) for p, x in g1.groupby("prefix")}
            rows.append({"family": fam, "mode": mode, "metric": name, "n_seed0": len(g0), "seed0": fn(g0), "seed0_lo": lo, "seed0_hi": hi,
                         "n_other": len(g1), "other_pooled": v1, "other_lo": lo1, "other_hi": hi1, **per,
                         "inside_seed0_ci": lo <= v1 <= hi})
    t = pd.DataFrame(rows)
    d = out_dir("tables/validation_main")
    t.to_csv(d / "v3_prefix_variance.csv", index=False, float_format="%.4f")
    (d / "v3_prefix_variance.md").write_text(t.to_markdown(index=False, floatfmt=".3f"))
    print(t.round(3).to_string())


if __name__ == "__main__":
    main()
