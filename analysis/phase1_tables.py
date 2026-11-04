"""Phase 1 aggregate tables (PLAN 7) per condition x handoff mode, and per family/type/point."""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from common import (
    DEPENDENT,
    PROPAGATED,
    conditions,
    load,
    ordered,
    out_dir,
    pr,
    provenance,
    runs_root,
    with_delta,
)


def _rate(num, den):
    return num / den if den else np.nan


def aggregate(f: pd.DataFrame, prov: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    rows = []
    for k, g in f.groupby(keys):
        k = k if isinstance(k, tuple) else (k,)
        dep = g.category.isin(DEPENDENT)
        win = g.irrev_window.map(lambda w: w if isinstance(w, dict) else None).dropna()
        p = prov
        for col, val in zip(keys, k):
            if col in p.columns:
                p = p[p[col] == val]
        m = pr(p) if len(p) else {"precision": np.nan, "recall": np.nan}
        ch = g[dep].channel.value_counts(normalize=True) if dep.any() else pd.Series(dtype=float)
        rows.append(
            {
                **dict(zip(keys, k)),
                "n": len(g),
                "injected": g.injected.mean(),
                "dependence": dep.mean(),
                "prop_given_dep": _rate(g.category.isin(PROPAGATED).sum(), dep.sum()),
                "harm_rate": (g.category == "propagated_harm").mean(),
                "bad": g.bad.mean(),
                "clean_bad": g.clean_bad.mean(),
                "delta": g.bad.mean() - g.clean_bad.mean(),
                "depth_dep": g[dep].depth.mean() if dep.any() else np.nan,
                "path_message": ch.get("message", 0.0),
                "path_state": ch.get("state", 0.0),
                "path_tool": ch.get("tool", 0.0),
                "laundering": _rate(g.n_laundered.sum(), g.n_tainted_artifacts.sum()),
                "prov_precision": m["precision"],
                "prov_recall": m["recall"],
                "irrev_n": len(win),
                "irrev_steps_med": float(np.median([w["steps"] for w in win])) if len(win) else np.nan,
                "irrev_tokens_med": float(np.median([w["tokens"] for w in win])) if len(win) else np.nan,
                "wasted_calls": g.wasted_calls.mean(),
                "wasted_tokens": g.wasted_tokens.mean(),
                "unauthorized": g.unauthorized_actions.mean(),
                "security_violation": g.security_violation.mean(),
                "stall": g.stalled.mean(),
                "tokens": (g.prompt_tokens + g.completion_tokens).mean(),
                "live_tokens_note": "prefix replayed from cache",
                "wall_s": g.wall_s.mean(),
                "virtual_s": g.virtual_s.mean(),
            }
        )
    return pd.DataFrame(rows).drop(columns=["live_tokens_note"])


def clean_table(df: pd.DataFrame) -> pd.DataFrame:
    r = df[df.kind == "ref"]
    g = r.groupby(["suite", "mode"])
    t = pd.DataFrame({"n": g.size(), "clean_success": g.utility.mean(), "stall": g.stalled.mean(), "llm_calls": g.n_llm_calls.mean(),
                      "prompt_tokens": g.prompt_tokens.mean(), "completion_tokens": g.completion_tokens.mean(), "wall_s": g.wall_s.mean()}).reset_index()
    allr = r.groupby("mode").agg(n=("utility", "size"), clean_success=("utility", "mean"), stall=("stalled", "mean"), llm_calls=("n_llm_calls", "mean"),
                                 prompt_tokens=("prompt_tokens", "mean"), completion_tokens=("completion_tokens", "mean"), wall_s=("wall_s", "mean")).reset_index()
    allr["suite"] = "all"
    return pd.concat([t, allr], ignore_index=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="primary")
    ap.add_argument("--root", default="")
    a = ap.parse_args()
    df = load(a.exp, Path(a.root) if a.root else runs_root())
    f = with_delta(df)
    f = f[f.fault != "PC"] if "PC" in set(f.fault) and a.exp != "pilot" else f
    prov = provenance(df)
    d = out_dir(f"tables/{a.exp}")
    cond = conditions()
    by_cond = aggregate(f, prov, ["fault", "mode"])
    by_cond["_o"] = by_cond.fault.map({c: i for i, c in enumerate(ordered(by_cond.fault.unique()))})
    by_cond = by_cond.sort_values(["_o", "mode"]).drop(columns="_o")
    by_cond.insert(1, "family", by_cond.fault.map(lambda c: cond.loc[c, "family"]))
    f2 = f.assign(ftype=f.fault.map(lambda c: cond.loc[c, "type"]))
    tables = {
        "by_condition_mode": by_cond,
        "by_family_mode": aggregate(f2, prov, ["family", "mode"]),
        "by_type": aggregate(f2, prov, ["ftype"]),
        "by_point_mode": aggregate(f2, prov, ["point", "mode"]),
        "by_condition": aggregate(f, prov, ["fault"]),
        "clean": clean_table(df),
    }
    for name, t in tables.items():
        t.to_csv(d / f"{name}.csv", index=False, float_format="%.4f")
        (d / f"{name}.md").write_text(t.to_markdown(index=False, floatfmt=".2f") if hasattr(t, "to_markdown") else t.to_string())
    cov = {"exp": a.exp, "runs_done": len(df), "by_kind": df.kind.value_counts().to_dict(), "tasks": int(df.task.nunique())}
    (d / "coverage.json").write_text(json.dumps(cov, indent=1))
    print(by_cond[["fault", "mode", "n", "dependence", "prop_given_dep", "delta", "laundering", "prov_precision", "prov_recall"]].round(2).to_string())
    print("tables ->", d, cov)


if __name__ == "__main__":
    main()
