"""Exact operating characteristic of the truncated SPRTs (phase2b design §5) -> data/tables/phase2b/sprt_oc.csv."""

import csv
from pathlib import Path

from fpr.recovery.sprt import SPRTParams, operating_characteristic

OUT = Path(__file__).resolve().parents[1] / "data/tables/phase2b/sprt_oc.csv"

# Ro / Nz / Phase 2 R4p: H1 = reproduced (commit / reuse). M: H1 = recurs under masking (flag); undecided -> flag.
TESTS = {
    "Ro": {"tau0": 0.05, "tau1": 0.30, "alpha": 0.05, "beta": 0.20, "on_trunc": "invalidate"},
    "M": {"tau0": 0.05, "tau1": 0.30, "alpha": 0.20, "beta": 0.10, "on_trunc": "reuse"},
}


def main() -> None:
    rows = []
    for name, kw in TESTS.items():
        for n_max in (8, 12, 16, 20, 24, 30, 10_000):
            p = SPRTParams(n_max=n_max, **kw)
            for q in (0.0, 0.05, 0.10, 0.30, 0.50, 0.80):
                oc = operating_characteristic(p, q)
                rows.append({"test": name, "n_max": n_max if n_max < 10_000 else "inf", "p": q, "P_H1": round(oc["reuse"], 4),
                             "P_H0": round(oc["invalidate"], 4), "P_truncated": round(oc["truncated"], 4), "E_n": round(oc["E_n"], 2)})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        if r["n_max"] in (8, 24) and r["p"] in (0.05, 0.30):
            print(r)


if __name__ == "__main__":
    main()
