"""Pick and annotate 3-5 case-study traces from finished fault branches."""

import argparse
import json
from pathlib import Path

import pandas as pd
from common import load, out_dir, runs_root

PICKS = [
    ("Plausible wrong fact propagates to an action", lambda f: f[(f.fault == "A1p_R") & (f.category.isin(["propagated_failure", "propagated_harm"]))]),
    ("Fault read and corrected downstream", lambda f: f[f.category == "corrected"]),
    ("Injected tool return causes a security violation", lambda f: f[(f.fault.isin(["B1_Et", "B1_Rt"])) & f.security_violation.astype(bool)]),
    ("Laundering: tainted action without a declared path", lambda f: f[(f.n_laundered > 0) & f.fault.isin(["A1p_R", "A1i_R", "A2_R", "B2_R"])]),
    ("Shared-state poisoning", lambda f: f[(f.fault == "B3_S") & (f.category != "no_dependence")]),
]


def _short(x, n=400) -> str:
    s = x if isinstance(x, str) else json.dumps(x, default=str)
    return s if len(s) <= n else s[:n] + " ..."


def render(title: str, r: pd.Series) -> str:
    ev = [json.loads(x) for x in (Path(r.run_dir) / "events.jsonl").read_text().splitlines()]
    prompt = next((e for e in ev if e["kind"] == "episode_start"), {})
    lines = [f"## {title}", "", f"- run: `{Path(r.run_dir).relative_to(runs_root())}`", f"- task: {r.task}, mode: {r['mode']}, condition: {r.fault}",
             f"- outcome: **{r.category}** (utility={r.utility}, security_violation={r.security_violation}, tainted stages={r.tainted_stages}, depth={r.depth}, laundered={r.n_laundered}/{r.n_tainted_artifacts})",
             f"- fault: `{_short({k: v for k, v in (r.fault_record or {}).items() if k in ('point', 'target', 'original', 'injected', 'claims', 'steps')})}`", "", "Trace (after the injection point):", ""]
    fi = next((e["i"] for e in ev if e["kind"] == "fault"), 0)
    for e in ev[fi:]:
        if e["kind"] == "artifact" and e["name"] in ("plan_raw", "report"):
            lines.append(f"1. **{e['name']}** ({e['agent']}): `{_short(e['content'])}`")
        elif e["kind"] == "tool_call" and e["agent"] == "executor":
            lines.append(f"1. executor `{e['name']}({_short(e['args'], 200)})` step={e['step_id']} rev={e['rev']}" + (f" error={_short(e['error'], 120)}" if e["error"] else ""))
        elif e["kind"] in ("nudge", "stage_timeout", "unavailable", "poison"):
            lines.append(f"1. {e['kind']}: `{_short({k: v for k, v in e.items() if k not in ('i', 't', 'kind')})}`")
    lines.append("")
    _ = prompt
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="primary")
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
    d = out_dir(f"cases/{a.exp}")
    (d / "cases.md").write_text("\n".join(out))
    print(f"{len(used)} cases ->", d / "cases.md")


if __name__ == "__main__":
    main()
