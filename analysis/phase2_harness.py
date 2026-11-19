"""Old vs fixed harness (design §7.1): clean success, Δ bad and graph-accuracy headline for Qwen3-8B `main` (Phase 1
harness) vs `main_h2` (disallowed tool calls return the error text), plus the count of disallowed calls.
Reads data/tables/{main,main_h2} (from phase1_tables / phase1_counterfactual) and the raw events.
Output: data/tables/phase2/harness_comparison.{csv,md}.
"""

import pandas as pd
from common import OUT, out_dir, runs_root


def disallowed(exp: str) -> dict:
    n_runs = n_hit = n_calls = 0
    for p in (runs_root() / exp).glob("*/*/ref_s*/events.jsonl"):
        n_runs += 1
        k = sum(1 for x in p.read_text().splitlines() if '"ToolNotFoundError' in x and '"kind": "tool_call"' in x)
        n_calls += k
        n_hit += k > 0
    return {"refs": n_runs, "refs_with_disallowed": n_hit, "disallowed_calls": n_calls}


def main() -> None:
    rows = []
    for exp, harness in (("main", "phase 1 (empty tool message)"), ("main_h2", "fixed (error text)")):
        t = OUT / "tables" / exp
        clean = pd.read_csv(t / "clean.csv")
        row = {"exp": exp, "harness": harness, **disallowed(exp)}
        for m in ("message", "state"):
            c = clean[clean["mode"] == m]
            row[f"clean_success_{m}"] = round(float((c.clean_success * c.n).sum() / c.n.sum()), 4)
        fm = pd.read_csv(t / "by_family_mode.csv")
        for r in fm[fm.family.isin(["accidental", "adversarial"])].itertuples():
            row[f"delta_{r.family}_{r.mode}"] = f"{r.delta:.3f} [{r.delta_lo:.3f}, {r.delta_hi:.3f}]"
        ga = pd.read_csv(t / "graph_accuracy_family.csv")
        for r in ga[ga.graph.isin(["taint", "declared_taint"])].itertuples():
            row[f"recall_{r.graph}_{r.family}"] = round(r.recall, 4)
        rows.append(row)
    df = pd.DataFrame(rows).set_index("exp").T.reset_index().rename(columns={"index": "metric"})
    d = out_dir("tables/phase2")
    df.to_csv(d / "harness_comparison.csv", index=False)
    (d / "harness_comparison.md").write_text(df.to_markdown(index=False))
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
