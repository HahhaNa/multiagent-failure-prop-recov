"""Held-out split for Phase 2 (design §9): one task per suite (seeded draw) plus the task with the most injected
branches among the rest; writes configs/phase2_split.yaml. Reads Phase 1 main branch counts (data/runs_restart.csv.gz)."""

import random
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
SEED = 2027


def main() -> None:
    sel = yaml.safe_load((ROOT / "configs/tasks_selected.yaml").read_text())["selected"]
    n = pd.read_csv(ROOT / "data/runs_restart.csv.gz").groupby("task").size().reindex(sel, fill_value=0)
    rng = random.Random(SEED)
    held = [rng.choice(sorted(t for t in sel if t.startswith(s + "."))) for s in ("banking", "workspace", "slack")]
    rest = n.drop(held)
    held.append(min(rest[rest == rest.max()].index))
    out = {
        "rule": "design §9: one task per suite (random.Random(2027).choice over the sorted suite tasks) plus the task with the most "
                "injected Phase 1 main branches among the rest (ties: alphabetical). Held-out tasks choose B; the rest evaluate.",
        "seed": SEED,
        "held_out": held,
        "evaluation": [t for t in sel if t not in held],
        "branches_phase1": {t: int(n[t]) for t in sel},
    }
    (ROOT / "configs/phase2_split.yaml").write_text(yaml.safe_dump(out, sort_keys=False, width=120, allow_unicode=True))
    print(out["held_out"])


if __name__ == "__main__":
    main()
