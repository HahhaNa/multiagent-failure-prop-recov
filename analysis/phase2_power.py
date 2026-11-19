"""Power simulation for the utility non-inferiority margin (design §9), on Phase 1 branch-level R1 data.

Model: accidental R1 branches of the evaluation tasks (configs/phase2_split.yaml). Under a true difference of 0,
both arms draw each branch's outcome from its cell probability (cell = task x mode x condition, shrunk to the task
mean with weight 5 / (5 + 5)); arms independent given the cell (conservative: positive pairing lowers variance).
Analysis as pre-registered for the criterion: paired branch differences, random intercept for task, approximated by the
mean difference with a task-cluster standard error and t(T-1) critical value (no GLMM library in the environment).
Non-inferior if the one-sided 95% lower bound exceeds -delta. Output: data/tables/phase2/power.csv.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
T95 = {5: 2.015, 6: 1.943, 7: 1.895, 8: 1.860, 9: 1.833, 10: 1.812, 11: 1.796, 12: 1.782, 13: 1.771}  # one-sided t quantiles


def lower_bound(d: np.ndarray, task: np.ndarray) -> float:
    tasks = np.unique(task)
    means = np.array([d[task == t].mean() for t in tasks])
    w = np.array([(task == t).sum() for t in tasks], dtype=float)
    est = float((means * w).sum() / w.sum())
    resid = (means - est) * w / w.mean()
    se = float(np.sqrt((resid**2).sum() / (len(tasks) * (len(tasks) - 1))))
    return est - T95[len(tasks) - 1] * se


def main(n_sim: int = 2000, seed: int = 0) -> None:
    split = yaml.safe_load((ROOT / "configs/phase2_split.yaml").read_text())
    fam = {c["id"]: c["family"] for c in yaml.safe_load((ROOT / "configs/faults.yaml").read_text())["conditions"]}
    r1 = pd.read_csv(ROOT / "data/runs_restart.csv.gz")
    r1["fault"] = r1.recover_from.str.split("/").str[-1].str.rsplit("_b", n=1).str[0]  # export leaves `fault` empty for R1
    r1 = r1[(r1.fault.map(fam) == "accidental") & r1.task.isin(split["evaluation"])].copy()
    r1["u"] = r1.utility.astype(float)
    tm = r1.groupby("task").u.mean()
    cell = r1.groupby(["task", "mode", "fault"]).u.agg(["mean", "size"]).reset_index()
    cell["p"] = (cell["mean"] * cell["size"] + tm.reindex(cell.task).values * 5) / (cell["size"] + 5)
    r1 = r1.merge(cell[["task", "mode", "fault", "p"]], on=["task", "mode", "fault"])
    p, task = r1.p.values, r1.task.values
    rng = np.random.default_rng(seed)
    rows = []
    for delta in (0.05, 0.10):
        ok = 0
        for _ in range(n_sim):
            d = (rng.random(len(p)) < p).astype(float) - (rng.random(len(p)) < p).astype(float)
            ok += lower_bound(d, task) > -delta
        rows.append({"delta": delta, "power": ok / n_sim, "n_branches": len(p), "n_tasks": len(np.unique(task)), "n_sim": n_sim,
                     "r1_utility": round(float(r1.u.mean()), 3)})
    out = pd.DataFrame(rows)
    chosen = next((r["delta"] for r in rows if r["power"] >= 0.8), None)
    out["chosen"] = out.delta == chosen
    d = ROOT / "data/tables/phase2"
    d.mkdir(parents=True, exist_ok=True)
    out.to_csv(d / "power.csv", index=False)
    print(out.to_string(index=False))
    print("chosen delta:", chosen if chosen is not None else "none (utility criterion secondary)")


if __name__ == "__main__":
    main()
