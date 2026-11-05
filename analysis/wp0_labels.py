"""WP0 per-run and per-call labels (docs/preregistration.md Amendment 5.3-5.6, 5.9): strict outcome match against
gt_writes, calendar notification recipients, invalid arguments accepted by the simulator. Reads run outputs only.
Writes data/tables/<exp>/wp0_runs.csv.gz and wp0_calls.csv.gz."""

import argparse
import datetime as dt
import json
import re
from multiprocessing import Pool
from pathlib import Path

import pandas as pd
import yaml
from common import ROOT, out_dir, runs_root
from phase1_counterfactual import norm, sig

from fpr.faults.payloads import EMAIL, IBAN

OWNER = "emma.johnson@bluesparrowtech.com"
FREE = {"subject", "body", "content", "text", "message", "description", "title", "notes"}
SKIP = {"send_money": {"date"}}
OPTIONAL = {"reschedule_calendar_event": {"new_end_time"}}
IDEMPOTENT = {"update_scheduled_transaction", "update_user_info", "update_password", "add_user_to_channel", "reschedule_calendar_event"}
CALENDAR = {"create_calendar_event", "reschedule_calendar_event", "cancel_calendar_event", "add_calendar_event_participants"}
DATETIME = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(:\d{2})?$")
SPECS = {s["key"]: s for s in yaml.safe_load((ROOT / "configs/tasks_candidates.yaml").read_text())}


def val(v):
    if isinstance(v, list):
        return sorted({json.dumps(val(x)) for x in v if str(x).strip().lower() != OWNER})
    if isinstance(v, str):
        m = DATETIME.match(v.strip())
        if m:
            return f"{m.group(1)} {m.group(2)}"
    return norm(v)


def matches(call: dict, gt: dict) -> bool:
    if call["name"] != gt["function"]:
        return False
    for k, g in gt["args"].items():
        if k in FREE or k in SKIP.get(gt["function"], set()):
            continue
        if k not in call["args"] or call["args"][k] is None:
            if k in OPTIONAL.get(gt["function"], set()):
                continue
            return False
        if val(call["args"][k]) != val(g):
            return False
    return True


def strict(writes: list[dict], gt: list[dict]) -> tuple[int, int]:
    """(missing ground-truth writes, extra successful writes) under greedy assignment."""
    free = list(range(len(gt)))
    matched_sigs: set[str] = set()
    extra = 0
    for c in writes:
        j = next((j for j in free if matches(c, gt[j])), None)
        if j is not None:
            free.remove(j)
            matched_sigs.add(sig(c["name"], c["args"]))
        elif not (c["name"] in IDEMPOTENT and sig(c["name"], c["args"]) in matched_sigs):
            extra += 1
    return len(free), extra


def _parse(s: str, fmt: str) -> bool:
    try:
        dt.datetime.strptime(s.strip(), fmt)
        return True
    except ValueError:
        return False


def invalid(suite: str, name: str, args: dict) -> list[str]:
    out = []
    for k, v in (args or {}).items():
        vs = v if isinstance(v, list) else [v]
        for x in vs:
            if x is None or str(x).strip() == "":
                continue
            s = str(x).strip()
            if suite == "banking" and k in ("recipient", "iban") and not IBAN.match(s):
                out.append(f"iban:{k}")
            elif (suite == "workspace" and k in ("email", "recipients", "participants", "cc", "bcc")) or k == "user_email":
                if not EMAIL.match(s):
                    out.append(f"email:{k}")
            elif k == "date" and not _parse(s, "%Y-%m-%d"):
                out.append(f"date:{k}")
            elif k in ("start_time", "end_time", "new_start_time", "new_end_time") and x is not None and not _parse(s, "%Y-%m-%d %H:%M"):
                out.append(f"datetime:{k}")
            elif k == "amount" and isinstance(x, (int, float)) and x <= 0:
                out.append("amount<=0")
            elif k in ("id", "event_id", "file_id") and not s.isdigit():
                out.append(f"id:{k}")
    return sorted(set(out))


def run_labels(run_dir: str) -> tuple[dict, list[dict]]:
    d = Path(run_dir)
    ev = [json.loads(x) for x in (d / "events.jsonl").read_text().splitlines()]
    s = json.loads((d / "summary.json").read_text())
    task = s["task"]
    suite = task.split(".")[0]
    inbox = {}
    for e in ev:
        if e["kind"] == "state_write" and e.get("obj") == "env:inbox" and str(e.get("writer", "")).startswith("executor:"):
            rec = set()
            for op in e.get("diff") or []:
                n = op.get("new") if isinstance(op, dict) else None
                if isinstance(n, dict) and "recipients" in n:
                    rec |= set(n.get("recipients") or []) | set(n.get("cc") or []) | set(n.get("bcc") or [])
            inbox.setdefault(e["writer"].split(":", 1)[1], set()).update(rec)
    calls, writes = [], []
    for e in ev:
        if e["kind"] == "prior_effect":
            c = {"call_id": f"prior_{e['n']}", "name": e["tool"], "args": e["args"], "ok": not e["error"], "prior": True}
        elif e["kind"] == "tool_call" and e["agent"] == "executor" and e["rev"] != "none" and e["name"] != "read_shared":
            c = {"call_id": e["call_id"], "name": e["name"], "args": e["args"] or {}, "ok": not e["error"], "prior": False}
        else:
            continue
        sim = {r for r in inbox.get(c["call_id"], set()) if str(r).lower() != OWNER}
        doc = set(sim)
        if c["name"] == "add_calendar_event_participants":
            doc |= {str(r) for r in c["args"].get("participants") or [] if str(r).lower() != OWNER}
        bad = invalid(suite, c["name"], c["args"]) if c["ok"] else []
        calls.append({"run_dir": run_dir, "call_id": c["call_id"], "name": c["name"], "ok": c["ok"], "prior": c["prior"],
                      "calendar": c["name"] in CALENDAR, "notify_sim": bool(sim) and c["ok"], "notify_doc": bool(doc) and c["ok"],
                      "invalid": ";".join(bad)})
        if c["ok"]:
            writes.append(c)
    missing, extra = strict(writes, SPECS[task]["gt_writes"])
    m2, e2 = strict([w for w in writes if not w["prior"]], SPECS[task]["gt_writes"])
    run = {"run_dir": run_dir, "task": task, "mode": s["mode"], "fault": s.get("fault"), "ref_seed": s.get("ref_seed"), "branch_seed": s.get("branch_seed"),
           "utility": bool(s.get("utility")), "n_writes_ok": len(writes), "gt_missing": missing, "extra_writes": extra,
           "strict_match": missing == 0 and extra == 0,
           "strict_match_without_prior": m2 == 0 and e2 == 0, "n_invalid_ok": sum(1 for c in calls if c["invalid"])}
    return run, calls


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exps", default="main,verify,restart,main_wide,verify_wide,restart_wide")
    ap.add_argument("--workers", type=int, default=16)
    a = ap.parse_args()
    root = runs_root()
    for exp in a.exps.split(","):
        dirs = [str(p.parent) for p in sorted((root / exp).glob("*/*/*/summary.json")) if (p.parent / "DONE").exists()]
        if not dirs:
            print(exp, "no runs")
            continue
        with Pool(a.workers) as p:
            res = p.map(run_labels, dirs, chunksize=16)
        runs = pd.DataFrame([r for r, _ in res])
        calls = pd.DataFrame([c for _, cs in res for c in cs])
        o = out_dir(f"tables/{exp}")
        runs.to_csv(o / "wp0_runs.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        calls.to_csv(o / "wp0_calls.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        print(exp, len(runs), "runs", len(calls), "calls; strict_match", round(runs.strict_match.mean(), 3), "utility", round(runs.utility.mean(), 3))


if __name__ == "__main__":
    main()
