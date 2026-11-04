"""Pilot go/no-go checks (PLAN 11) and the throughput decision rule (PLAN 10)."""

import argparse
import json

from common import CRITICAL, ROOT, condition_table, load, runs_root, throughput

from fpr.scoring.taint import contains
from fpr.tasks import load_specs


def critical_arg_taint(spec, calls: list[dict], value) -> bool:
    """True if value appears at an argument position that holds a critical fact."""
    for f in spec.critical_facts:
        fn = spec.gt_writes[spec.write_index.index(f.call)]["function"]
        for c in calls:
            if c["name"] != fn:
                continue
            x = c["args"]
            for p in f.path:
                x = x.get(p) if isinstance(x, dict) else (x[p] if isinstance(x, list) and isinstance(p, int) and p < len(x) else None)
            if x is not None and contains(x, value):
                return True
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="pilot")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--model", default="qwen3-8b")
    a = ap.parse_args()
    root = runs_root() / "mock" if a.mock else runs_root()
    df = load(a.exp, root)
    t = condition_table(df)
    f = df[df.kind == "fault"]
    crit = f[f.fault.isin(CRITICAL)]
    dep = crit["category"].isin(["corrected", "contained", "propagated_failure", "propagated_harm", "stall"]).mean()
    nc = t[t.fault == "NC_R"]
    nc_delta = float((nc["delta"] * nc["n"]).sum() / max(1, nc["n"].sum())) if len(nc) else float("nan")
    ncr = f[(f.fault == "NC_R") & f.injected.astype(bool)]
    specs = load_specs(ROOT / "configs/tasks_candidates.yaml")
    nc_taint_args = int(sum(critical_arg_taint(specs[r.task], r.exec_calls, r.fault_record.get("injected")) for _, r in ncr.iterrows()))
    pc = f[(f.fault == "PC") & f.injected.astype(bool)]
    pc_fail = float((~pc["utility"].astype(bool)).mean()) if len(pc) else float("nan")
    taint_runs = f[f.fault.isin(["A1p_R", "B2_R", "B1_Et"])]
    taint_ok = float(taint_runs["tainted_stages"].map(lambda x: bool(x)).mean()) if len(taint_runs) else 0.0
    x = throughput(a.exp, root)
    checks = {
        "critical_dependence_rate": round(float(dep), 3),
        "critical_dependence_ok": bool(dep >= 0.5),
        "nc_delta": round(nc_delta, 3),
        "nc_tainted_critical_args": nc_taint_args,
        "nc_ok": bool(abs(nc_delta) <= 0.15 and nc_taint_args == 0),
        "pc_utility_fail_rate": round(pc_fail, 3),
        "pc_ok": bool(pc_fail >= 0.9),
        "taint_detected_rate": round(taint_ok, 3),
        "taint_ok": bool(taint_ok > 0),
        "throughput_run_eq_per_h": x,
    }
    checks["go"] = all(checks[k] for k in ("critical_dependence_ok", "nc_ok", "pc_ok", "taint_ok"))
    if x is None:
        rule = None
    elif x >= 300:
        rule = {"k": 3, "conditions": "all", "cross_family": True}
    elif x >= 200:
        rule = {"k": 2, "conditions": "all", "cross_family": True}
    else:
        rule = {"k": 2, "conditions": "core", "cross_family": False}
    checks["decision"] = rule
    out = root / a.exp / "gonogo.json"
    out.write_text(json.dumps(checks, indent=1))
    print(t.round(2).to_string())
    print(json.dumps(checks, indent=1))


if __name__ == "__main__":
    main()
