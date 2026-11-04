"""R3 attribution: where along the verified pipeline the injected value or canary disappears (V1 findings, V2 plan, V3 guard),
per family x stage x mode. Reads verify run outputs only."""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
from common import ROOT, boot_ci, load, out_dir

sys.path.insert(0, str(ROOT / "src"))
from fpr.scoring.recovery import taint_from_record
from fpr.scoring.taint import contains

STAGES = ["in_findings", "after_V1", "in_plan", "after_V2", "proposed_write", "executed_write"]


def reach(run_dir: str, rec: dict) -> dict | None:
    marks = taint_from_record(rec)
    if not marks or not all(marks):
        return None
    d = Path(run_dir)
    art = json.loads((d / "artifacts.json").read_text())
    ev = [json.loads(x) for x in (d / "events.jsonl").read_text().splitlines()]
    fi = next((e["i"] for e in ev if e["kind"] == "fault"), 0)
    writes = [e for e in ev[fi:] if e["kind"] == "tool_call" and e["agent"] == "executor" and e["rev"] != "none"]
    # S.write faults live in the blackboard version the plan verifier reads
    poisoned = [e for e in ev if e["kind"] == "poison"]
    return {
        "in_findings": contains(art.get("findings"), marks),
        "after_V1": contains(art.get("findings_verified"), marks),
        "in_plan": contains(art.get("plan"), marks) or bool(poisoned),
        "after_V2": contains(art.get("plan_verified"), marks),
        "proposed_write": any(contains(e["args"], marks) for e in writes),
        "executed_write": any(contains(e["args"], marks) and not e["error"] for e in writes),
        "blocked_tainted": any(contains(e["args"], marks) and e["error"] == "blocked_by_verifier" for e in writes),
    }


def clean_losses(main_exp: str, exp: str) -> pd.DataFrame:
    """Clean references that pass in main but fail under verify-all, and which verifier stage intervened."""
    k = ["task", "mode", "ref_seed"]
    m = load(main_exp)
    m = m[m.kind == "ref"][[*k, "utility"]]
    v = load(exp)
    x = v[v.kind == "ref"].merge(m, on=k, suffixes=("", "_main"))
    changed = lambda st: x.verify_stages.map(lambda s: (s or {}).get(st, {}).get("changed", 0) > 0)
    x = x.assign(V1_changed=changed("V1"), V2_changed=changed("V2"), V3_blocked=x.n_blocked > 0)
    rows = []
    for name, g in (("all refs", x), ("pass in main", x[x.utility_main]), ("lost under verify", x[x.utility_main & ~x.utility])):
        rows.append({"set": name, "n": len(g), "V1_changed": int(g.V1_changed.sum()), "V2_changed": int(g.V2_changed.sum()),
                     "V3_blocked": int(g.V3_blocked.sum()), "none": int((~(g.V1_changed | g.V2_changed | g.V3_blocked)).sum())})
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="verify")
    ap.add_argument("--main", default="main")
    a = ap.parse_args()
    v = load(a.exp)
    f = v[(v.kind == "fault") & v.injected & ~v.family.isin(["control"])]
    rows = []
    for _, r in f.iterrows():
        x = reach(r.run_dir, r.fault_record or {})
        if x is not None:
            rows.append({"task": r.task, "mode": r["mode"], "fault": r.fault, "family": r.family, "point": r.point, **x, "bad": r.bad, "harm": bool(r.harm)})
    t = pd.DataFrame(rows)
    out = []
    for k, g in t.groupby(["family", "point", "mode"]):
        row = dict(zip(["family", "point", "mode"], k), n=len(g))
        for c in [*STAGES, "blocked_tainted"]:
            row[c] = g[c].mean()
        row["executed_lo"], row["executed_hi"] = boot_ci(g, lambda s: s.executed_write.mean())
        out.append(row)
    res = pd.DataFrame(out)
    d = out_dir(f"tables/{a.exp}")
    res.to_csv(d / "verifier_taint_reach.csv", index=False, float_format="%.4f")
    (d / "verifier_taint_reach.md").write_text(res.to_markdown(index=False, floatfmt=".2f"))
    print(res.round(2).to_string())
    cl = clean_losses(a.main, a.exp)
    cl.to_csv(d / "verifier_clean_losses.csv", index=False)
    (d / "verifier_clean_losses.md").write_text(cl.to_markdown(index=False))
    print(cl.to_string())


if __name__ == "__main__":
    main()
