"""Phase 1 figures (PLAN 12, items 1-5) from finished runs."""

import argparse

import numpy as np
import pandas as pd
from common import (
    CAT_COLORS,
    CATS,
    conditions,
    load,
    ordered,
    out_dir,
    pr,
    provenance,
    runs_root,
    style,
)

plt = style()
INK, INK2, SURF = "#0b0b0b", "#52514e", "#fcfcfb"


def fig_heatmap(f: pd.DataFrame, d) -> None:
    cond = conditions()
    panels = [("Accidental + controls", ["accidental", "control"]), ("Adversarial", ["adversarial"])]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), gridspec_kw={"width_ratios": [1, 1]})
    for ax, (title, fams) in zip(axes, panels):
        sub = f[f.fault.map(cond["family"]).isin(fams)]
        rows = ordered(sub.fault.unique())
        m = pd.crosstab(sub.fault, sub.category, normalize="index").reindex(index=rows, columns=CATS, fill_value=0)
        n = sub.groupby("fault").size().reindex(rows)
        ax.imshow(m.values, cmap="Blues", vmin=0, vmax=1, aspect="auto")
        for i in range(m.shape[0]):
            for j in range(m.shape[1]):
                v = m.values[i, j]
                if v > 0:
                    ax.text(j, i, f"{v:.0%}", ha="center", va="center", fontsize=7, color="white" if v > 0.55 else INK)
        ax.set_xticks(range(len(CATS)), [c.replace("_", "\n") for c in CATS], fontsize=7)
        ax.set_yticks(range(len(rows)), [f"{r} (n={n[r]})" for r in rows])
        ax.set_title(title, loc="left", fontsize=10, color=INK)
        for s in ax.spines.values():
            s.set_visible(False)
        ax.tick_params(length=0)
    fig.suptitle("Outcome category by fault condition (share of injected fault branches, both handoff modes)", x=0.01, ha="left", fontsize=11)
    fig.savefig(d / "fig1_propagation_heatmap.png")
    plt.close(fig)


def _deepest(stages) -> str:
    for s in ("effect", "executor", "planner", "researcher"):
        if s in (stages or []):
            return s
    return "none"


def _alluvial(ax, df: pd.DataFrame, cols: list[str], orders: list[list[str]], colors: dict) -> None:
    total = len(df)
    gap = 0.02
    x = np.arange(len(cols)) * 1.0
    pos: list[dict] = []
    for ci, c in enumerate(cols):
        counts = df[c].value_counts()
        levels = [lv for lv in orders[ci] if lv in counts.index]
        y, p = 1.0, {}
        for lv in levels:
            h = counts[lv] / total * (1 - gap * (len(levels) - 1))
            p[lv] = [y - h, y]
            y -= h + gap
        pos.append(p)
        for lv, (a, b) in p.items():
            ax.add_patch(plt.Rectangle((x[ci] - 0.04, a), 0.08, b - a, color="#52514e", lw=0))
            ax.text(x[ci] + 0.07, (a + b) / 2, f"{lv} ({counts[lv]})", va="center", fontsize=7, color=INK)
    for ci in range(len(cols) - 1):
        src_off = {k: v[1] for k, v in pos[ci].items()}
        dst_off = {k: v[1] for k, v in pos[ci + 1].items()}
        flows = df.groupby([cols[ci], cols[ci + 1], cols[-1]]).size()
        for (a, b, outc), n in flows.items():
            h = n / total * (1 - gap * (len(pos[ci]) - 1))
            h2 = n / total * (1 - gap * (len(pos[ci + 1]) - 1))
            y0, y1 = src_off[a], dst_off[b]
            src_off[a] -= h
            dst_off[b] -= h2
            t = np.linspace(0, 1, 30)
            sm = 3 * t**2 - 2 * t**3
            xs = x[ci] + 0.04 + (x[ci + 1] - x[ci] - 0.08) * t
            top = y0 + (y1 - y0) * sm
            bot = (y0 - h) + ((y1 - h2) - (y0 - h)) * sm
            ax.fill_between(xs, bot, top, color=colors.get(outc, "#c9c8c2"), alpha=0.55, lw=0)
    ax.set_xlim(-0.2, x[-1] + 0.8)
    ax.set_ylim(-0.05, 1.05)
    ax.axis("off")
    for ci, c in enumerate(cols):
        ax.text(x[ci], 1.06, c, ha="center", fontsize=8, color=INK2)


def fig_sankey(f: pd.DataFrame, d) -> None:
    g = f[f.injected.astype(bool) & (f.fault != "PC")].copy()
    g["injection point"] = g.point
    g["deepest tainted stage"] = g.tainted_stages.map(_deepest)
    g["outcome"] = g.category
    orders = [["R.start", "R.tool", "R.out", "P.start", "P.out", "S.write", "E.tool"], ["none", "researcher", "planner", "executor", "effect"], CATS]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    for ax, mode in zip(axes, ["message", "state"]):
        sub = g[g["mode"] == mode]
        if len(sub):
            _alluvial(ax, sub, ["injection point", "deepest tainted stage", "outcome"], orders, CAT_COLORS)
        ax.set_title(f"{mode} handoff (n={len(sub)})", loc="left", fontsize=10, color=INK, pad=14)
    handles = [plt.Rectangle((0, 0), 1, 1, color=CAT_COLORS[c], alpha=0.7) for c in CATS]
    fig.legend(handles, CATS, loc="lower center", ncol=7, fontsize=7)
    fig.suptitle("Fault paths: injection point -> deepest tainted stage -> outcome (bands colored by outcome)", x=0.01, ha="left", fontsize=11)
    fig.savefig(d / "fig2_path_sankey.png")
    plt.close(fig)


def fig_window(f: pd.DataFrame, d) -> None:
    g = f[f.irrev_window.map(lambda w: isinstance(w, dict))].copy()
    g["steps"] = g.irrev_window.map(lambda w: w["steps"])
    g["tokens"] = g.irrev_window.map(lambda w: w["tokens"])
    rows = ordered(g.fault.unique())
    fig, axes = plt.subplots(1, 2, figsize=(10, 0.35 * max(4, len(rows)) + 1.2), sharey=True)
    rng = np.random.default_rng(0)
    for ax, col, label in zip(axes, ["steps", "tokens"], ["steps (LLM + tool calls)", "tokens"]):
        for i, c in enumerate(rows):
            for j, mode in enumerate(["message", "state"]):
                v = g[(g.fault == c) & (g["mode"] == mode)][col].to_numpy()
                if len(v) == 0:
                    continue
                yy = i + (j - 0.5) * 0.35
                ax.scatter(v, yy + rng.uniform(-0.08, 0.08, len(v)), s=10, color=["#2a78d6", "#eb6834"][j], alpha=0.6, lw=0)
                ax.plot([np.median(v)] * 2, [yy - 0.15, yy + 0.15], color=INK, lw=2)
        ax.set_xlabel(f"injection -> first tainted irreversible or compensable action, {label}")
        ax.set_yticks(range(len(rows)), rows)
        ax.invert_yaxis()
    axes[0].scatter([], [], color="#2a78d6", label="message")
    axes[0].scatter([], [], color="#eb6834", label="state")
    axes[0].legend(loc="lower right", fontsize=7)
    fig.suptitle(f"Irreversibility window (n={len(g)} branches with a tainted irreversible or compensable action; bar = median)", x=0.01, ha="left", fontsize=11)
    fig.savefig(d / "fig3_irreversibility_window.png")
    plt.close(fig)


def fig_provenance(df: pd.DataFrame, f: pd.DataFrame, d) -> None:
    p = provenance(df)
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.6), gridspec_kw={"width_ratios": [1, 1, 1.6]})
    for ax, level in zip(axes[:2], ["claim->step", "step->call"]):
        sub = p[p.level == level] if len(p) else p
        if len(sub) == 0:
            ax.axis("off")
            ax.set_title(f"{level}: no data", loc="left", fontsize=9)
            continue
        m = pr(sub)
        mat = np.array([[m["tp"], m["fp"]], [m["fn"], m["tn"]]])
        ax.imshow(mat, cmap="Blues", vmin=0)
        for i in range(2):
            for j in range(2):
                ax.text(j, i, str(mat[i, j]), ha="center", va="center", color="white" if mat[i, j] > mat.max() * 0.55 else INK)
        ax.set_xticks([0, 1], ["dependent", "not dependent"])
        ax.set_yticks([0, 1], ["declared", "not declared"])
        ax.set_xlabel("counterfactual (vs clean continuation)")
        ax.set_title(f"{level}\nprecision {m['precision']:.2f}, recall {m['recall']:.2f}", loc="left", fontsize=9)
        ax.tick_params(length=0)
        for s in ax.spines.values():
            s.set_visible(False)
    ax = axes[2]
    g = f[f.injected.astype(bool) & (f.n_tainted_artifacts > 0)]
    rows = ordered(g.fault.unique())
    for j, mode in enumerate(["message", "state"]):
        vals = [g[(g.fault == c) & (g["mode"] == mode)].pipe(lambda s: s.n_laundered.sum() / s.n_tainted_artifacts.sum() if s.n_tainted_artifacts.sum() else np.nan) for c in rows]
        ax.barh(np.arange(len(rows)) + (j - 0.5) * 0.38, vals, height=0.36, color=["#2a78d6", "#eb6834"][j], label=mode)
    ax.set_yticks(range(len(rows)), rows)
    ax.invert_yaxis()
    ax.set_xlim(0, 1)
    ax.set_xlabel("laundering rate (tainted artifacts without a declared path to the fault)")
    ax.legend(fontsize=7)
    fig.suptitle("Declared provenance vs counterfactual dependence, and laundering", x=0.01, ha="left", fontsize=11)
    fig.savefig(d / "fig4_provenance_laundering.png")
    plt.close(fig)


def fig_ignored(f: pd.DataFrame, d) -> None:
    rows = ordered(f.fault.unique())
    fig, axes = plt.subplots(1, 2, figsize=(11, 0.32 * len(rows) + 1.6), sharey=True)
    for ax, mode in zip(axes, ["message", "state"]):
        sub = f[f["mode"] == mode]
        m = pd.crosstab(sub.fault, sub.category, normalize="index").reindex(index=rows, columns=CATS, fill_value=0)
        left = np.zeros(len(rows))
        for c in CATS:
            ax.barh(range(len(rows)), m[c].values, left=left, color=CAT_COLORS[c], height=0.7, edgecolor=SURF, linewidth=1, label=c)
            left += m[c].values
        ax.set_yticks(range(len(rows)), rows)
        ax.invert_yaxis()
        ax.set_xlim(0, 1)
        ax.set_title(f"{mode} handoff", loc="left", fontsize=10)
        ax.set_xlabel("share of fault branches")
    axes[0].legend(loc="upper center", bbox_to_anchor=(1.05, -0.12), ncol=7, fontsize=7)
    fig.suptitle("Ignored is not robust: never read / read but unused vs corrected / contained vs propagated", x=0.01, ha="left", fontsize=11)
    fig.savefig(d / "fig5_ignored_vs_robust.png")
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="primary")
    ap.add_argument("--root", default="")
    a = ap.parse_args()
    from pathlib import Path

    df = load(a.exp, Path(a.root) if a.root else runs_root())
    f = df[(df.kind == "fault") & df.injected]  # category shares are over injected branches
    d = out_dir(f"figures/{a.exp}")
    fig_heatmap(f, d)
    fig_sankey(f, d)
    fig_window(f, d)
    fig_provenance(df, f, d)
    fig_ignored(f, d)
    print("figures ->", d)


if __name__ == "__main__":
    main()
