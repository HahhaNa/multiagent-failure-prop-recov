"""Cross-family comparison: core conditions, message mode, primary vs secondary model."""

import argparse

import pandas as pd
from common import condition_table, load, ordered, out_dir, style


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--primary", default="primary")
    ap.add_argument("--cross", default="cross_llama3.2-3b")
    a = ap.parse_args()
    rows = []
    for label, exp in [("Qwen3-8B", a.primary), ("Llama-3.2-3B", a.cross)]:
        df = load(exp)
        if df.empty:
            continue
        df = df[df["mode"] == "message"]
        t = condition_table(df)
        t["model"] = label
        refs = df[df.kind == "ref"]
        t["clean_success"] = refs.utility.mean()
        rows.append(t)
    t = pd.concat(rows, ignore_index=True)
    core = set(load(a.cross).fault.dropna()) if rows and len(rows) > 1 else set(t.fault)
    t = t[t.fault.isin(core)]
    d = out_dir("tables/crossfamily")
    t.to_csv(d / "core_message.csv", index=False, float_format="%.4f")
    (d / "core_message.md").write_text(t.to_markdown(index=False, floatfmt=".2f"))
    plt = style()
    conds = ordered([c for c in t.fault.unique() if not c.startswith("CLEAN")])
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), sharey=True)
    for ax, col, lab in zip(axes, ["dependence", "delta"], ["dependence rate", "Delta P(bad | fault) - P(bad | clean)"]):
        for j, m in enumerate(t.model.unique()):
            v = t[t.model == m].set_index("fault").reindex(conds)[col]
            ax.barh([i + (j - 0.5) * 0.38 for i in range(len(conds))], v.values, height=0.36, color=["#2a78d6", "#eb6834"][j], label=m)
        ax.set_yticks(range(len(conds)), conds)
        ax.invert_yaxis()
        ax.axvline(0, color="#8f8e88", lw=0.8)
        ax.set_xlabel(lab)
    axes[0].legend(fontsize=7)
    fig.suptitle("Core conditions, message handoff: primary vs cross-family model", x=0.01, ha="left", fontsize=11)
    fig.savefig(out_dir("figures/crossfamily") / "fig6_crossfamily.png")
    print(t[["model", "fault", "n", "dependence", "prop_given_dep", "delta", "clean_success"]].round(2).to_string())


if __name__ == "__main__":
    main()
