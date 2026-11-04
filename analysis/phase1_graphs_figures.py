"""W5: fig9 graph accuracy (declared / data-flow / taint vs counterfactual) and fig10 irreversible-before-recovery by stage.
Reads the W4 tables written by phase1_counterfactual.py."""

import argparse

import numpy as np
import pandas as pd
from common import ROOT, SERIES, out_dir, style

GRAPHS = [("declared", "declared"), ("dataflow", "data-flow"), ("taint", "taint")]
STAGES = ["R.tool", "R.start", "R.out", "P.start", "P.out", "S.write", "E.tool"]


def fig9(exp: str) -> None:
    t = pd.read_csv(ROOT / f"data/tables/{exp}/graph_accuracy_family.csv")
    plt = style()
    fams = ["accidental", "adversarial"]
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), sharey=True)
    for ax, fam in zip(axes, fams):
        x = t[t.family == fam].set_index("graph")
        mets = ["precision", "recall", "f1"]
        w = 0.26
        for gi, (g, label) in enumerate(GRAPHS):
            vals = [x.loc[g, m] for m in mets]
            pos = np.arange(len(mets)) + (gi - 1) * w
            ax.bar(pos, vals, w * 0.95, color=SERIES[gi], label=label)
            ax.errorbar(pos[2], x.loc[g, "f1"], yerr=[[x.loc[g, "f1"] - x.loc[g, "f1_lo"]], [x.loc[g, "f1_hi"] - x.loc[g, "f1"]]], color="#52514e", lw=0.8, capsize=2)
            for p, v in zip(pos, vals):
                ax.text(p, v + 0.02, f"{v:.2f}", ha="center", fontsize=7, color="#52514e")
        ax.set_xticks(np.arange(len(mets)), mets)
        ax.set_ylim(0, 1.1)
        n = int(x["n_artifacts"].iloc[0])
        ax.set_title(f"{fam.capitalize()} (n={n} artifacts, {x['dependent_share'].iloc[0]:.0%} dependent)", loc="left")
    axes[0].set_ylabel("score vs counterfactual dependence")
    axes[0].legend(loc="lower left", fontsize=8)
    fig.suptitle("Dependency graphs as predictors of counterfactually dependent artifacts (F1 with 95% cluster-bootstrap CI)", x=0.01, ha="left")
    fig.savefig(out_dir(f"figures/{exp}") / "fig9_graph_accuracy.png")
    plt.close(fig)


def fig10(exp: str) -> None:
    t = pd.read_csv(ROOT / f"data/tables/{exp}/irreversible_by_stage.csv")
    t = t[t.family != "control"]
    plt = style()
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), sharey=True)
    for ax, fam in zip(axes, ["accidental", "adversarial"]):
        x = t[t.family == fam]
        stages = [s for s in STAGES if s in set(x.point)]
        for mi, mode in enumerate(("message", "state")):
            y = x[x["mode"] == mode].set_index("point").reindex(stages)
            pos = np.arange(len(stages)) + (mi - 0.5) * 0.38
            err = np.vstack([(y.share_fault_caused - y.ci_lo).clip(lower=0).fillna(0), (y.ci_hi - y.share_fault_caused).clip(lower=0).fillna(0)])
            ax.bar(pos, y.share_fault_caused, 0.36, color=SERIES[mi], label=mode, yerr=err, error_kw={"lw": 0.6, "ecolor": "#52514e"})
            for p, v, med in zip(pos, y.share_fault_caused, y.steps_to_first_med):
                if not (np.isnan(v) or np.isnan(med)):
                    ax.text(p, min(1.0, v) + 0.03, f"{med:.0f}", ha="center", fontsize=6, color="#52514e")
        ax.set_xticks(np.arange(len(stages)), stages)
        ax.set_ylim(0, 1.05)
        ax.set_title(fam.capitalize(), loc="left")
    axes[0].set_ylabel("branches with >= 1 fault-caused\nirreversible/compensable effect")
    axes[0].legend(fontsize=8)
    fig.suptitle("Fault-caused irreversible effects before recovery (counterfactual; 95% cluster-bootstrap CI; label = median steps to first)", x=0.01, ha="left")
    fig.savefig(out_dir(f"figures/{exp}") / "fig10_irreversible_by_stage.png")
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="main")
    a = ap.parse_args()
    fig9(a.exp)
    fig10(a.exp)
    print("figs 9-10 ->", out_dir(f"figures/{a.exp}"))


if __name__ == "__main__":
    main()
