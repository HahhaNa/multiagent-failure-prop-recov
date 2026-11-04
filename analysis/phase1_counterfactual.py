"""W4: counterfactual ground-truth dependence vs declared / data-flow / taint graphs, and irreversibility before recovery.

An artifact downstream of a fault is dependent if no clean-continuation sample at the same branch point produced it
(claims: normalized value; plan steps and executor calls: tool + normalized args). Reads run outputs only.
"""

import argparse
import json
from collections import defaultdict, deque
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from common import CATS, CLEAN_FOR, ROOT, boot_ci, load, ordered, out_dir, runs_root


def norm(x):
    if isinstance(x, dict):
        return {str(k): norm(v) for k, v in sorted(x.items())}
    if isinstance(x, (list, tuple)):
        return [norm(v) for v in x]
    if isinstance(x, bool) or x is None:
        return x
    if isinstance(x, (int, float)):
        return f"{float(x):.4f}"
    s = str(x).strip().strip("'\"").strip().rstrip(".").lower()
    try:
        return f"{float(s.replace(',', '')):.4f}"
    except ValueError:
        return " ".join(s.split())


def sig(name: str, args) -> str:
    return name + json.dumps(norm(args or {}), sort_keys=True)


def extract(run_dir: str) -> dict:
    """Downstream artifacts of one run (after its fault/branch event), with positions and graph predictions."""
    d = Path(run_dir)
    ev = [json.loads(x) for x in (d / "events.jsonl").read_text().splitlines()]
    s = json.loads((d / "summary.json").read_text())
    g = json.loads((d / "graphs.json").read_text())
    fi = next((e["i"] for e in ev if e["kind"] == "fault"), 0)
    rec = s.get("fault_record") or {}
    point = rec.get("point")
    claims, steps, calls = [], [], []
    origin_tool = None
    n_steps = n_tok = 0
    for e in ev[fi + 1 :]:
        k = e["kind"]
        if k == "llm_call":
            n_steps += 1
            n_tok += e["prompt_tokens"] + e["completion_tokens"]
        elif k == "tool_call":
            n_steps += 1
            if origin_tool is None and point in ("R.tool", "E.tool"):
                origin_tool = e["call_id"]
                continue
            if e["agent"] == "executor" and e["rev"] != "none" and e["name"] != "read_shared":
                calls.append({"node": f"tool:{e['call_id']}", "sig": sig(e["name"], e["args"]), "name": e["name"], "rev": e["rev"],
                              "ok": not e["error"], "steps": n_steps, "tokens": n_tok})
        elif k == "artifact" and e["name"] == "findings" and point in ("R.tool", "R.start") and isinstance(e["content"], dict):
            claims = [{"node": f"claim:{c['claim_id']}", "sig": json.dumps(norm(c.get("value", "")))} for c in e["content"].get("claims", [])]
        elif k == "artifact" and e["name"] == "plan" and point in ("R.tool", "R.start", "R.out") and isinstance(e["content"], dict):
            steps = [{"node": f"step:{st['step_id']}", "sig": sig(st.get("action", ""), st.get("args"))} for st in e["content"].get("steps", [])]
    return {"run_dir": run_dir, "claims": claims, "steps": steps, "calls": calls, "origin_tool": origin_tool, "graph": g}


def closure(edges: list, origin: set[str]) -> set[str]:
    adj = defaultdict(list)
    for a, b in edges:
        adj[a].append(b)
    seen, q = set(origin), deque(origin)
    while q:
        for b in adj[q.popleft()]:
            if b not in seen:
                seen.add(b)
                q.append(b)
    return seen - origin


def origins(point: str, rec: dict, x: dict) -> tuple[set, set]:
    """(declared origin nodes, data-flow origin nodes) for a fault."""
    t = x["origin_tool"]
    if point == "R.tool":
        return ({f"tool:{t}"} if t else set()), ({f"ret:{t}"} if t else set())
    if point == "E.tool":
        return ({f"tool:{t}"} if t else set()), ({f"ret:{t}"} if t else set())
    if point == "R.out":
        return {f"claim:{c}" for c in rec.get("claims") or []}, {"findings"}
    if point == "R.start":
        return set(), {"findings"}
    if point in ("P.out", "S.write"):
        return {f"step:{s}" for s in rec.get("steps") or []}, {"plan"}
    if point == "P.start":
        return set(), {"plan"}
    return set(), set()


def artifact_rows(df: pd.DataFrame, ext: dict[str, dict]) -> pd.DataFrame:
    clean_union: dict[tuple, dict[str, set]] = {}
    for key, g in df[df.kind.isin(["clean", "ref"])].groupby(["task", "mode", "point_c"]):
        u = {"claims": set(), "steps": set(), "calls": set()}
        for rd in g.run_dir:
            x = ext[rd]
            for kind in u:
                u[kind] |= {a["sig"] for a in x[kind]}
        clean_union[key] = u
    rows = []
    f = df[(df.kind == "fault") & df.injected]
    for _, r in f.iterrows():
        x = ext[r.run_dir]
        cp = CLEAN_FOR.get(r.point) or "R.start"
        u = clean_union.get((r.task, r["mode"], cp))
        if u is None:
            continue
        rec = r.fault_record or {}
        g = x["graph"]
        dec_o, df_o = origins(r.point, rec, x)
        dec = closure(g["declared"], dec_o)
        flow = closure(g["dataflow"], df_o)
        taint = set(g["tainted"])
        for kind in ("claims", "steps", "calls"):
            for a in x[kind]:
                rows.append({
                    "task": r.task, "mode": r["mode"], "fault": r.fault, "family": r.family, "ftype": r.ftype, "point": r.point,
                    "branch_seed": r.branch_seed, "level": kind[:-1], "node": a["node"],
                    "dependent": a["sig"] not in u[kind],
                    "declared": a["node"] in dec, "dataflow": a["node"] in flow, "taint": a["node"] in taint,
                    "name": a.get("name"), "rev": a.get("rev"), "ok": a.get("ok", True), "steps": a.get("steps"), "tokens": a.get("tokens"),
                })
    return pd.DataFrame(rows)


def prf(t: pd.DataFrame, col: str) -> dict:
    tp = int((t[col] & t.dependent).sum())
    fp = int((t[col] & ~t.dependent).sum())
    fn = int((~t[col] & t.dependent).sum())
    p = tp / (tp + fp) if tp + fp else np.nan
    r = tp / (tp + fn) if tp + fn else np.nan
    f1 = 2 * p * r / (p + r) if not (np.isnan(p) or np.isnan(r)) and p + r else np.nan
    return {"precision": p, "recall": r, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


def graph_accuracy(a: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    rows = []
    groups = a.groupby(keys) if keys else [((), a)]
    for k, g in groups:
        k = k if isinstance(k, tuple) else (k,)
        for col in ("declared", "dataflow", "taint"):
            m = prf(g, col)
            ci = boot_ci(g, lambda t, c=col: prf(t, c)["f1"])
            rows.append({**dict(zip(keys, k)), "graph": col, "n_artifacts": len(g), "dependent_share": g.dependent.mean(), **m, "f1_lo": ci[0], "f1_hi": ci[1]})
    return pd.DataFrame(rows)


def irreversibility(a: pd.DataFrame, df: pd.DataFrame, ext_ok: set[str], keys: list[str]) -> pd.DataFrame:
    """Share of injected branches with >= 1 fault-caused irreversible/compensable external effect before recovery."""
    eff = a[(a.level == "call") & a.ok & a.rev.isin(["irreversible", "compensable"]) & a.name.isin(ext_ok) & a.dependent]
    f = df[(df.kind == "fault") & df.injected]
    bk = ["task", "mode", "fault", "branch_seed"]
    firsts = eff.sort_values("steps").groupby(bk).first()
    kinds = eff.groupby(bk).rev.agg(set)
    f = f.assign(fc=[k in firsts.index for k in zip(f.task, f["mode"], f.fault, f.branch_seed)])
    rows = []
    for k, g in f.groupby(keys):
        k = k if isinstance(k, tuple) else (k,)
        idx = [x for x in zip(g.task, g["mode"], g.fault, g.branch_seed) if x in firsts.index]
        fs = firsts.loc[idx] if idx else firsts.iloc[:0]
        e = eff.merge(g[bk], on=bk)
        lo, hi = boot_ci(g, lambda t: t.fc.mean())
        rows.append({
            **dict(zip(keys, k)), "family": g.family.iloc[0], "point": g.point.iloc[0] if g.point.nunique() == 1 else "mixed", "n": len(g),
            "share_fault_caused": g.fc.mean(), "ci_lo": lo, "ci_hi": hi,
            "share_irreversible": sum("irreversible" in kinds[x] for x in idx) / len(g),
            "share_compensable": sum("compensable" in kinds[x] for x in idx) / len(g),
            "steps_to_first_med": float(fs.steps.median()) if len(fs) else np.nan,
            "tokens_to_first_med": float(fs.tokens.median()) if len(fs) else np.nan,
            "n_effects": len(e), "tainted": int(e.taint.sum()), "counterfactual_only": int((~e.taint).sum()),
        })
    t = pd.DataFrame(rows).loc[:, lambda x: ~x.columns.duplicated()]
    if "fault" in keys:
        t["_o"] = t.fault.map({c: i for i, c in enumerate(ordered(t.fault.unique()))})
        t = t.sort_values(["_o", "mode"]).drop(columns="_o")
    return t


def ignored_robust(df: pd.DataFrame) -> pd.DataFrame:
    f = df[(df.kind == "fault") & df.injected & (df.family != "control")]
    t = f.groupby(["family", "point", "mode"]).category.value_counts(normalize=True).unstack(fill_value=0.0)
    for c in CATS:
        if c not in t:
            t[c] = 0.0
    t = t[CATS]
    t.insert(0, "n", f.groupby(["family", "point", "mode"]).size())
    return t.reset_index()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="main")
    ap.add_argument("--workers", type=int, default=16)
    a = ap.parse_args()
    df = load(a.exp, runs_root())
    df = df[df.fault != "PC"] if "fault" in df else df
    df["point_c"] = df.point.where(df.kind == "clean", "R.start")
    df.loc[df.kind == "fault", "point_c"] = None
    need = df[df.kind.isin(["fault", "clean", "ref"])].run_dir.tolist()
    with Pool(a.workers) as p:
        ext = {x["run_dir"]: x for x in p.map(extract, need, chunksize=16)}
    arts = artifact_rows(df, ext)
    tools = yaml.safe_load((ROOT / "configs/tools.yaml").read_text())
    ext_ok = {n for s in tools.values() for n, v in s.get("write", {}).items() if v.get("external")} | {n for s in tools.values() for n in s.get("read_external", [])}
    d = out_dir(f"tables/{a.exp}")
    arts.to_csv(d / "cf_artifacts.csv.gz", index=False)
    tables = {
        "graph_accuracy": graph_accuracy(arts, []),
        "graph_accuracy_family": graph_accuracy(arts, ["family"]),
        "graph_accuracy_family_point_mode": graph_accuracy(arts, ["family", "point", "mode"]),
        "graph_accuracy_level": graph_accuracy(arts, ["family", "level"]),
        "irreversible_before_recovery": irreversibility(arts, df, ext_ok, ["fault", "mode"]),
        "irreversible_by_stage": irreversibility(arts, df, ext_ok, ["family", "point", "mode"]),
        "ignored_robust": ignored_robust(df),
    }
    for name, t in tables.items():
        t.to_csv(d / f"{name}.csv", index=False, float_format="%.4f")
        (d / f"{name}.md").write_text(t.to_markdown(index=False, floatfmt=".2f"))
    print(tables["graph_accuracy_family"].round(2).to_string())
    print(tables["irreversible_before_recovery"].round(2).to_string())
    print("W4 tables ->", d, "artifacts", len(arts))


if __name__ == "__main__":
    main()
