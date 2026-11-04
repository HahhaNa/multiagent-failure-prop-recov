"""Compact print figures for the writeup (7 in wide, 8 pt text). Reads tables under data/tables/ only."""

import pandas as pd
from common import ROOT, out_dir, style

FAM = {"accidental": "#2a78d6", "adversarial": "#eb6834"}
STAGES = ["R.tool", "R.out", "P.out", "E.tool"]


def main() -> None:
    plt = style()
    plt.rcParams.update({"font.size": 8, "axes.titlesize": 8.5, "axes.labelsize": 8, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
                         "legend.fontsize": 7.5, "figure.facecolor": "white", "axes.facecolor": "white"})
    irr = pd.read_csv(ROOT / "data/tables/main/irreversible_by_stage.csv")
    rec = pd.read_csv(ROOT / "data/tables/recovery/recovery_family.csv")
    fig, axes = plt.subplots(1, 3, figsize=(7, 2.15), gridspec_kw={"width_ratios": [1.35, 1, 1]})
    w = 0.38

    ax = axes[0]
    m = irr[(irr["mode"] == "message") & irr.point.isin(STAGES)]
    for j, (fam, c) in enumerate(FAM.items()):
        x = m[m.family == fam].set_index("point").reindex(STAGES)
        pos = [i + (j - 0.5) * w for i in range(len(STAGES))]
        ax.bar(pos, x.share_fault_caused, w * 0.92, color=c, label=fam)
        ax.errorbar(pos, x.share_fault_caused, [x.share_fault_caused - x.ci_lo, x.ci_hi - x.share_fault_caused], fmt="none", ecolor="#444", lw=0.6)
        if fam == "adversarial":
            for p, v, s in zip(pos, x.ci_hi, x.steps_to_first_med):
                ax.text(p, min(v, 1.0) + 0.03, f"{s:.0f}", ha="center", fontsize=6.5, color="#333")
    ax.set_xticks(range(len(STAGES)), STAGES)
    ax.set_ylim(0, 1.12)
    ax.set_ylabel("share of branches")
    ax.set_title("(a) Fault-caused irreversible effect\n(message mode; label = median steps)", loc="left")
    ax.legend(frameon=False, loc="upper left")

    methods = ["R0", "R1", "R3"]
    for ax, col, title in ((axes[1], "utility", "(b) Utility after recovery"), (axes[2], "residual_harm", "(c) Residual harm")):
        for j, (fam, c) in enumerate(FAM.items()):
            x = rec[rec.family == fam].set_index("method").reindex(methods)
            pos = [i + (j - 0.5) * w for i in range(len(methods))]
            ax.bar(pos, x[col], w * 0.92, color=c)
            ax.errorbar(pos, x[col], [x[col] - x[f"{col}_lo"], x[f"{col}_hi"] - x[col]], fmt="none", ecolor="#444", lw=0.6)
        ax.set_xticks(range(len(methods)), ["R0 none", "R1 restart", "R3 verify"])
        ax.set_ylim(0, 1.0)
        ax.set_title(title, loc="left")
    fig.tight_layout(pad=0.3, w_pad=1.0)
    d = out_dir("figures/paper")
    fig.savefig(d / "fig_phase1_summary.png", dpi=300)
    print(d / "fig_phase1_summary.png")


if __name__ == "__main__":
    main()
