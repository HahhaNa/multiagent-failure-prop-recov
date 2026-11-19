"""tau2-bench screening: selection funnel, per-task clean success and the admission gate.

Usage: uv run python analysis/tau2_screening.py [--exp tau2_screen]
Writes data/tables/tau2_screening/{funnel.csv,tasks.csv,gate.json} and configs/tau2_selected.yaml.
"""

import argparse
import json

import pandas as pd
import yaml
from common import ROOT, load

GATE_RATE, GATE_TASKS = 0.4, 30


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="tau2_screen")
    a = ap.parse_args()
    out = ROOT / "data/tables/tau2_screening"
    out.mkdir(parents=True, exist_ok=True)
    specs = yaml.safe_load((ROOT / "configs/tau2_candidates.yaml").read_text())
    funnel = yaml.safe_load((ROOT / "configs/tau2_funnel.yaml").read_text())
    df = load(a.exp)
    df = df[df["fault"].isna()]
    df["ok"] = df["utility"].astype(bool) & ~df["security_violation"].astype(bool)
    df["stalled"] = df["stalled"].astype(bool)
    per = df.groupby("task").agg(suite=("suite", "first"), runs=("ok", "size"), clean_success=("ok", "mean"), stalls=("stalled", "sum"),
                                 tokens=("prompt_tokens", "mean"), writes=("n_writes", "mean")).reset_index()
    modes = df.pivot_table(index="task", columns="mode", values="ok", aggfunc="mean").add_prefix("success_").reset_index()
    per = per.merge(modes, on="task")
    per["n_critical_facts"] = per["task"].map({s["key"]: len(s["critical_facts"]) for s in specs})
    per["n_gold_writes"] = per["task"].map({s["key"]: len(s["gt_writes"]) for s in specs})
    per = per.sort_values(["suite", "clean_success", "task"], ascending=[True, False, True])
    per.round(3).to_csv(out / "tasks.csv", index=False)
    rows = []
    for sn, f in funnel.items():
        p = per[per["suite"] == sn]
        rows.append({
            "suite": sn, "tasks": f["tasks"], "with_action_calls": f["with_action_calls"], "gold_replay_passes": f["gold_replay_passes"],
            "with_critical_fact": f["with_critical_fact"], "screened": len(p), "complete_4_runs": int((p["runs"] == 4).sum()),
            "success_ge_0.4": int((p["clean_success"] >= GATE_RATE).sum()), "success_ge_0.5": int((p["clean_success"] >= 0.5).sum()),
            "success_1.0": int((p["clean_success"] == 1).sum()), "mean_clean_success": round(p["clean_success"].mean(), 3),
            "mean_success_message": round(p["success_message"].mean(), 3), "mean_success_state": round(p["success_state"].mean(), 3),
        })
    fdf = pd.DataFrame(rows)
    tot = {c: (fdf[c].sum() if not c.startswith("mean") else None) for c in fdf.columns if c != "suite"}
    tot.update(suite="all", mean_clean_success=round(per["clean_success"].mean(), 3), mean_success_message=round(per["success_message"].mean(), 3),
               mean_success_state=round(per["success_state"].mean(), 3))
    fdf = pd.concat([fdf, pd.DataFrame([tot])], ignore_index=True)
    fdf.to_csv(out / "funnel.csv", index=False)
    n_pass = int((per["clean_success"] >= GATE_RATE).sum())
    gate = {"exp": a.exp, "rule": f"clean success >= {GATE_RATE} on >= {GATE_TASKS} tasks", "tasks_passing": n_pass, "passed": n_pass >= GATE_TASKS,
            "runs": len(df), "stalled_runs": int(df["stalled"].sum())}
    (out / "gate.json").write_text(json.dumps(gate, indent=1))
    rate = {t: round(float(v), 3) for t, v in zip(per["task"], per["clean_success"])}
    sel = {"selected": sorted(rate, key=lambda t: (t.split(".")[0], int(t.split(".")[1]))), "sensitivity_ge_0.5": sorted(t for t, v in rate.items() if v >= 0.5),
           "pass_rate": rate, "screen_exp": a.exp, "gate": gate}
    (ROOT / "configs/tau2_selected.yaml").write_text(yaml.safe_dump(sel, sort_keys=False))
    print(fdf.to_string(index=False))
    print(json.dumps(gate))


if __name__ == "__main__":
    main()
