"""Data for the interactive human audit page (Amendment 5.1). Facts are read mechanically from the raw traces of each
sampled branch and its clean runs; no automatic label is included. Writes data/audit/human_audit_view.json and, with
--html, the page built from analysis/human_audit_page.html."""

import argparse
import json
from pathlib import Path

import yaml
from common import ROOT

FAULT_KEYS = ("cond", "type", "point", "target", "original", "injected", "canary", "dropped", "steps", "claims")


def events(d: Path) -> list[dict]:
    return [json.loads(x) for x in (d / "events.jsonl").read_text().splitlines()]


def run_facts(d: Path, after: int) -> dict:
    ev = events(d)
    a = json.loads((d / "artifacts.json").read_text())
    calls = [
        {"call_id": e["call_id"], "name": e["name"], "args": e["args"], "ok": not e["error"], "error": str(e["error"])[:160] if e["error"] else None}
        for e in ev
        if e["i"] > after and e["kind"] == "tool_call" and e["agent"] == "executor" and e["rev"] != "none" and e["name"] != "read_shared"
    ]
    claims = [{"id": c.get("claim_id"), "text": c.get("text"), "value": c.get("value")} for c in ((a.get("findings") or {}).get("claims") or [])]
    plan = [{"id": s.get("step_id"), "action": s.get("action"), "args": s.get("args")} for s in ((a.get("plan") or {}).get("steps") or [])]
    return {"name": d.name, "claims": claims, "plan": plan, "calls": calls, "report": a.get("report")}


def canon(c: dict) -> str:
    return c["name"] + json.dumps(c["args"], sort_keys=True, ensure_ascii=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", default="sample3.json")
    ap.add_argument("--html", help="write the audit page here")
    a = ap.parse_args()
    specs = {s["key"]: s for s in yaml.safe_load((ROOT / "configs/tasks_candidates.yaml").read_text())}
    tools = yaml.safe_load((ROOT / "configs/tools.yaml").read_text())
    irrev = sorted({n for s in tools.values() for n, v in s.get("write", {}).items() if v.get("external")})
    out = []
    for x in json.loads((ROOT / "data/audit" / a.sample).read_text()):
        d = Path(x["run_dir"])
        ev = events(d)
        f = next((e for e in ev if e["kind"] == "fault"), {})
        branch = run_facts(d, f.get("i", 0))
        clean = []
        for cd in map(Path, x["clean_dirs"]):
            cf = next((e["i"] for e in events(cd) if e["kind"] == "branch"), 0)
            clean.append(run_facts(cd, cf))
        pool = {canon(c) for r in clean for c in r["calls"]}
        for c in branch["calls"]:
            c["literal_match"] = canon(c) in pool
        spec = specs[x["task"]]
        out.append({
            "id": x["id"], "condition": x["condition"], "family": x["family"], "point": x["point"], "mode": x["mode"], "task": x["task"],
            "run_dir": str(d), "prompt": spec["prompt"], "gt_writes": spec["gt_writes"], "injection_goal": spec.get("injection_goal"),
            "fault": {k: f[k] for k in FAULT_KEYS if k in f}, "branch": branch, "clean": clean,
        })
    view = {"sample": a.sample, "irreversible_tools": irrev, "branches": out}
    p = ROOT / "data/audit/human_audit_view.json"
    p.write_text(json.dumps(view, ensure_ascii=False, indent=1))
    print(p, len(out), "branches")
    if a.html:
        tpl = (ROOT / "analysis/human_audit_page.html").read_text()
        Path(a.html).write_text(tpl.replace("/*__DATA__*/null", json.dumps(view, ensure_ascii=False).replace("</", "<\\/")))
        print(a.html)


if __name__ == "__main__":
    main()
