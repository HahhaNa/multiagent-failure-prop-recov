"""Pick and annotate 5-8 case-study traces: propagation, ignored faults, laundering, verifier catch/miss, restart effects."""

import argparse
import json
from pathlib import Path

import pandas as pd
from common import load, out_dir, runs_root

PROP = ["propagated_failure", "propagated_harm"]
PICKS = [
    ("Wrong fact propagates through a message handoff", lambda f: f[(f.fault == "A1p_R") & (f["mode"] == "message") & f.category.isin(PROP)]),
    ("Wrong fact propagates through the shared blackboard", lambda f: f[(f.fault == "A1p_R") & (f["mode"] == "state") & f.category.isin(PROP)]),
    ("Injected instruction read but ignored", lambda f: f[f.fault.isin(["B3_S", "B1_Rt", "B1_Et"]) & (f.category == "read_not_used")]),
    ("Laundering: tainted action without a declared path", lambda f: f[(f.n_laundered > 0) & f.fault.isin(["A1p_R", "A1i_R", "A2_R", "B2_R"])]),
]


def _short(x, n=400) -> str:
    s = x if isinstance(x, str) else json.dumps(x, default=str)
    return s if len(s) <= n else s[:n] + " ..."


def render(title: str, r: pd.Series) -> str:
    ev = [json.loads(x) for x in (Path(r.run_dir) / "events.jsonl").read_text().splitlines()]
    prompt = next((e for e in ev if e["kind"] == "episode_start"), {})
    lines = [f"## {title}", "", f"- run: `{Path(r.run_dir).relative_to(runs_root())}`", f"- task: {r.task}, mode: {r['mode']}, condition: {r.fault}",
             f"- outcome: **{r.get('category')}** (utility={r.utility}, security_violation={r.security_violation}, tainted stages={r.get('tainted_stages')}, depth={r.get('depth')}, laundered={r.get('n_laundered')}/{r.get('n_tainted_artifacts')})",
             f"- fault: `{_short({k: v for k, v in (r.get('fault_record') or {}).items() if k in ('point', 'target', 'original', 'injected', 'claims', 'steps')})}`", "", "Trace (after the injection point):", ""]
    fi = next((e["i"] for e in ev if e["kind"] == "fault"), 0)
    for e in ev[fi:]:
        if e["kind"] == "verify":
            lines.append(f"1. verifier {e['stage']}: changed={e.get('changed')} `{_short({k: e.get(k) for k in ('issues', 'drop', 'fix', 'allow', 'tool', 'args') if e.get(k)}, 300)}`")
            continue
        if e["kind"] == "prior_effect":
            lines.append(f"1. prior effect re-applied: `{e['tool']}({_short(e['args'], 160)})` rev={e['rev']} tainted={e['tainted']}")
            continue
        if e["kind"] == "artifact" and e["name"] in ("plan_raw", "report"):
            lines.append(f"1. **{e['name']}** ({e['agent']}): `{_short(e['content'])}`")
        elif e["kind"] == "tool_call" and e["agent"] == "executor":
            lines.append(f"1. executor `{e['name']}({_short(e['args'], 200)})` step={e['step_id']} rev={e['rev']}" + (f" error={_short(e['error'], 120)}" if e["error"] else ""))
        elif e["kind"] in ("nudge", "stage_timeout", "unavailable", "poison"):
            lines.append(f"1. {e['kind']}: `{_short({k: v for k, v in e.items() if k not in ('i', 't', 'kind')})}`")
    lines.append("")
    _ = prompt
    return "\n".join(lines)


def recovery_cases(main: pd.DataFrame, f: pd.DataFrame, used: set) -> list[str]:
    """Verifier catch / miss (verify exp) and restart duplicate / unrecoverable effect (restart exp), keyed to main branches."""
    out = []
    key = ["task", "mode", "fault", "branch_seed"]
    v = load("verify")
    if not v.empty:
        v = v[(v.kind == "fault") & v.injected.astype(bool)].merge(f[key + ["category", "bad"]].rename(columns={"category": "main_cat", "bad": "main_bad"}), on=key)
        catch = v[v.main_cat.isin(PROP) & ~v.bad & (v.verify_stages.map(lambda x: sum(x[s]["changed"] for s in ("V1", "V2", "V3"))) > 0)]
        miss = v[v.category.isin(PROP) & v.family.eq("adversarial")]
        for title, c in (("Verifier catch (R3): fault propagated in main, stopped by the verifier", catch), ("Verifier miss (R3): fault propagates despite verify-all", miss)):
            c = c.sort_values(["task", "mode", "branch_seed"])
            if len(c):
                r = c.iloc[0]
                used.add(r.run_dir)
                out.append(render(title + f" (main outcome: {r.main_cat})", r))
    rs = load("restart")
    if not rs.empty:
        c = rs[(rs.duplicates > 0) & (rs.prior_tainted_irreversible + rs.prior_tainted_compensable > 0)]
        c = c if len(c) else rs[rs.duplicates > 0]
        c = c.sort_values(["task", "mode", "run_dir"])
        if len(c):
            r = c.iloc[0].copy()
            used.add(r.run_dir)
            src = json.loads((Path(r.run_dir) / "manifest.json").read_text())["run"]["recover_from"]
            r["fault_record"] = json.loads((Path(src) / "summary.json").read_text()).get("fault_record")
            r["fault"] = f"R1 restart of {Path(src).name}"
            out.append(render(f"Full restart (R1) re-executes an action and cannot undo the faulted run's effect (duplicates={r.duplicates}, "
                              f"tainted prior irreversible={r.prior_tainted_irreversible}, compensable={r.prior_tainted_compensable})", r))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="main")
    ap.add_argument("--root", default="")
    a = ap.parse_args()
    df = load(a.exp, Path(a.root) if a.root else runs_root())
    f = df[(df.kind == "fault") & df.injected.astype(bool)]
    out, used = ["# Case studies", ""], set()
    for title, sel in PICKS:
        c = sel(f)
        c = c[~c.run_dir.isin(used)].sort_values(["task", "mode", "branch_seed"])
        if len(c):
            r = c.iloc[0]
            used.add(r.run_dir)
            out.append(render(title, r))
    out += recovery_cases(df, f, used)
    d = out_dir(f"cases/{a.exp}")
    (d / "cases.md").write_text("\n".join(out))
    print(f"{len(used)} cases ->", d / "cases.md")


if __name__ == "__main__":
    main()
