"""Pre-registered validation of counterfactual dependence (docs/preregistration.md V1, V2, V5).
V1: three artifact-equivalence definitions. V2: number of clean continuations k (subsets of the existing five; k=10
when `cf10` exists). V5: task selection funnel. Reads run outputs only."""

import argparse
import json
import re
from collections import defaultdict
from itertools import combinations
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from common import CLEAN_FOR, ROOT, boot_ci, load, out_dir, runs_root
from phase1_counterfactual import closure, norm, origins

FREE = {"subject", "body", "content", "text", "message", "description", "title", "notes"}
DEFS = ["E1", "E2", "E3"]
GRAPHS = ["declared", "dataflow", "taint", "declared_taint"]


def tokens(x) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(x).lower()))


def jaccard(a, b) -> float:
    ta, tb = tokens(a), tokens(b)
    return 1.0 if not ta and not tb else len(ta & tb) / max(1, len(ta | tb))


def keys(name: str, args: dict) -> tuple[str, str, dict]:
    args = args or {}
    e1 = name + json.dumps(norm(args), sort_keys=True)
    e2 = name + json.dumps(norm({k: v for k, v in args.items() if k not in FREE}), sort_keys=True)
    return e1, e2, {k: v for k, v in args.items() if k in FREE}


def claim_keys(value) -> tuple[str, str, float | None]:
    k = json.dumps(norm(value))
    try:
        num = float(str(value).replace(",", "").strip())
    except ValueError:
        num = None
    return k, k, num


def extract(run_dir: str) -> dict:
    """Downstream artifacts with E1/E2/E3 match keys (same artifact scope as phase1_counterfactual.extract)."""
    d = Path(run_dir)
    ev = [json.loads(x) for x in (d / "events.jsonl").read_text().splitlines()]
    s = json.loads((d / "summary.json").read_text())
    g = json.loads((d / "graphs.json").read_text())
    fi = next((e["i"] for e in ev if e["kind"] == "fault"), 0)
    point = (s.get("fault_record") or {}).get("point")
    out = {"claims": [], "steps": [], "calls": []}
    origin_tool = None
    for e in ev[fi + 1 :]:
        k = e["kind"]
        if k == "tool_call":
            if origin_tool is None and point in ("R.tool", "E.tool"):
                origin_tool = e["call_id"]
                continue
            if e["agent"] == "executor" and e["rev"] != "none" and e["name"] != "read_shared":
                out["calls"].append({"node": f"tool:{e['call_id']}", "k": keys(e["name"], e["args"])})
        elif k == "artifact" and e["name"] == "findings" and point in ("R.tool", "R.start") and isinstance(e["content"], dict):
            out["claims"] = [{"node": f"claim:{c['claim_id']}", "k": claim_keys(c.get("value", ""))} for c in e["content"].get("claims", [])]
        elif k == "artifact" and e["name"] == "plan" and point in ("R.tool", "R.start", "R.out") and isinstance(e["content"], dict):
            out["steps"] = [{"node": f"step:{st['step_id']}", "k": keys(st.get("action", ""), st.get("args"))} for st in e["content"].get("steps", [])]
    return {"run_dir": run_dir, **out, "origin_tool": origin_tool, "graph": g}


def match(kind: str, a, b) -> dict[str, bool]:
    if kind == "claims":
        e1 = a[0] == b[0]
        num = a[2] is not None and b[2] is not None and abs(a[2] - b[2]) <= 0.01 * max(abs(a[2]), abs(b[2]), 1e-9)
        return {"E1": e1, "E2": e1, "E3": e1 or num}
    e2 = a[1] == b[1]
    e3 = e2 and all(jaccard(a[2].get(f, ""), b[2].get(f, "")) >= 0.5 for f in set(a[2]) | set(b[2]))
    return {"E1": a[0] == b[0], "E2": e2, "E3": e3}


def clean_samples(df: pd.DataFrame) -> dict[tuple, list[str]]:
    """(task, mode, point_c) -> clean run dirs ordered by seed."""
    out = defaultdict(list)
    c = df[df.kind.isin(["clean", "ref"])].copy()
    c["seed"] = c.branch_seed.where(c.kind == "clean", c.ref_seed)
    for key, g in c.sort_values("seed").groupby(["task", "mode", "point_c"]):
        out[key] = g.run_dir.tolist()
    return out


def artifact_matches(df: pd.DataFrame, ext: dict, samples: dict) -> pd.DataFrame:
    """One row per downstream artifact: graph predictions and, per definition, which clean samples contain a match."""
    rows = []
    for _, r in df[(df.kind == "fault") & df.injected].iterrows():
        x = ext[r.run_dir]
        runs = samples.get((r.task, r["mode"], CLEAN_FOR.get(r.point) or "R.start"))
        if not runs:
            continue
        g = x["graph"]
        dec_o, df_o = origins(r.point, r.fault_record or {}, x)
        dec, flow, taint = closure(g["declared"], dec_o), closure(g["dataflow"], df_o), set(g["tainted"])
        for kind in ("claims", "steps", "calls"):
            for a in x[kind]:
                m = {d: [] for d in DEFS}
                for i, rd in enumerate(runs):
                    hit = {d: False for d in DEFS}
                    for b in ext[rd][kind]:
                        for d, v in match(kind, a["k"], b["k"]).items():
                            hit[d] = hit[d] or v
                    for d in DEFS:
                        if hit[d]:
                            m[d].append(i)
                rows.append({"task": r.task, "mode": r["mode"], "fault": r.fault, "family": r.family, "point": r.point, "level": kind[:-1],
                             "n_clean": len(runs), "declared": a["node"] in dec, "dataflow": a["node"] in flow, "taint": a["node"] in taint,
                             "declared_taint": a["node"] in dec or a["node"] in taint, **{f"match_{d}": m[d] for d in DEFS}})
    return pd.DataFrame(rows)


def recall_prec(t: pd.DataFrame, dep: pd.Series, col: str) -> tuple[float, float]:
    tp = (t[col] & dep).sum()
    return (tp / dep.sum() if dep.sum() else np.nan, tp / t[col].sum() if t[col].sum() else np.nan)


def v1(a: pd.DataFrame, k: int) -> pd.DataFrame:
    rows = []
    for fam, g in a[a.family.isin(["accidental", "adversarial"])].groupby("family"):
        for d in DEFS:
            dep = g[f"match_{d}"].map(lambda m: not any(i < k for i in m))
            g2 = g.assign(dependent=dep)
            for col in GRAPHS:
                rec, prec = recall_prec(g2, g2.dependent, col)
                lo, hi = boot_ci(g2, lambda t, c=col: recall_prec(t, t.dependent, c)[0])
                rows.append({"family": fam, "definition": d, "graph": col, "n_artifacts": len(g2), "dependent_share": dep.mean(),
                             "recall": rec, "recall_lo": lo, "recall_hi": hi, "precision": prec})
    return pd.DataFrame(rows)


def v2(a: pd.DataFrame, kmax: int) -> pd.DataFrame:
    """Every subset of size k of the first kmax clean samples (same positions in every group); E1."""
    rows = []
    f = a[a.family.isin(["accidental", "adversarial"])]
    for k in sorted({1, 3, 5, kmax}):
        if k > kmax:
            continue
        subs = list(combinations(range(kmax), k)) if k < kmax else [tuple(range(kmax))]
        if len(subs) > 60:
            subs = [subs[i] for i in np.random.default_rng(0).choice(len(subs), 60, replace=False)]
        for fam, g in f.groupby("family"):
            res = defaultdict(list)
            for s in subs:
                s = set(s)
                dep = g.match_E1.map(lambda m, s=s: not (set(m) & s))
                res["dependent_share"].append(dep.mean())
                for col in ("taint", "declared_taint", "declared"):
                    res[col].append(recall_prec(g, dep, col)[0])
            for metric, v in res.items():
                rows.append({"family": fam, "k": k, "metric": metric if metric == "dependent_share" else f"recall_{metric}",
                             "mean": np.mean(v), "min": np.min(v), "max": np.max(v), "n_subsets": len(subs)})
    return pd.DataFrame(rows)


def v5() -> pd.DataFrame:
    sel = yaml.safe_load((ROOT / "configs/tasks_selected.yaml").read_text())
    rate = sel["pass_rate"]
    chosen = set(sel["selected"])
    wide = {t for t, v in rate.items() if 0.3 <= v < 0.5}
    rows = [{"task": t, "suite": t.split(".")[0], "screen_pass_rate": v,
             "status": "selected" if t in chosen else ("excluded, run as main_wide" if t in wide else "excluded")} for t, v in sorted(rate.items())]
    return pd.DataFrame(rows).sort_values(["status", "screen_pass_rate"], ascending=[False, False])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="main")
    ap.add_argument("--cf10", default="cf10")
    ap.add_argument("--workers", type=int, default=16)
    a = ap.parse_args()
    df = load(a.exp, runs_root())
    df = df[df.fault != "PC"]
    extra = load(a.cf10, runs_root())
    kmax = 5
    if not extra.empty:
        df = pd.concat([df, extra], ignore_index=True)
        kmax = 10
    df["point_c"] = df.point.where(df.kind == "clean", "R.start")
    df.loc[df.kind == "fault", "point_c"] = None
    with Pool(a.workers) as p:
        ext = {x["run_dir"]: x for x in p.map(extract, df[df.kind.isin(["fault", "clean", "ref"])].run_dir.tolist(), chunksize=16)}
    arts = artifact_matches(df, ext, clean_samples(df))
    d = out_dir(f"tables/validation_{a.exp}")
    t1, t2, t5 = v1(arts, 5), v2(arts, kmax), v5()
    for name, t in (("v1_equivalence", t1), ("v2_k_sensitivity", t2), ("v5_task_selection", t5)):
        t.to_csv(d / f"{name}.csv", index=False, float_format="%.4f")
        (d / f"{name}.md").write_text(t.to_markdown(index=False, floatfmt=".3f"))
    print(t1.round(3).to_string())
    print(t2.round(3).to_string())
    print(t5.status.value_counts().to_string(), "| kmax", kmax)


if __name__ == "__main__":
    main()
