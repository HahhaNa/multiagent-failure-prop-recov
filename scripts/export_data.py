"""Export run outputs into data/: per-run summary table and compressed full traces."""

import argparse
import json
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
from common import load, runs_root

KEEP = ["task", "suite", "mode", "fault", "kind", "point", "family", "ftype", "ref_seed", "branch_seed", "utility", "security_violation",
        "stalled", "bad", "category", "injected", "read", "tainted_stages", "depth", "channel", "harm", "tainted_irreversible",
        "n_tainted_artifacts", "n_laundered", "irrev_window", "first_irrev", "wasted_calls", "wasted_tokens", "unauthorized_actions",
        "n_llm_calls", "n_live_calls", "prompt_tokens", "completion_tokens", "n_tool_calls", "n_writes", "n_external", "virtual_s",
        "wall_s", "prefix_misses", "overhead", "fault_record", "error", "run_dir"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exps", default="primary,pilot2,screen3")
    ap.add_argument("--traces", action="store_true", help="also pack full traces (events, artifacts, graphs, manifests)")
    a = ap.parse_args()
    out = ROOT / "data"
    out.mkdir(exist_ok=True)
    root = runs_root()
    for exp in a.exps.split(","):
        df = load(exp)
        if df.empty:
            continue
        df = df[[c for c in KEEP if c in df.columns]].copy()
        df["run_dir"] = df["run_dir"].map(lambda p: str(Path(p).relative_to(root)))
        for c in ("tainted_stages", "irrev_window", "first_irrev", "overhead", "fault_record"):
            if c in df:
                df[c] = df[c].map(lambda v: json.dumps(v, default=str) if isinstance(v, (dict, list)) else "")
        df.to_csv(out / f"runs_{exp}.csv.gz", index=False, compression="gzip")
        print(exp, len(df), "rows")
        if a.traces:
            with tarfile.open(out / f"traces_{exp}.tar.gz", "w:gz") as tar:
                for d in sorted((root / exp).glob("*/*/*")):
                    if (d / "DONE").exists():
                        for name in ("events.jsonl", "artifacts.json", "graphs.json", "manifest.json", "summary.json"):
                            if (d / name).exists():
                                tar.add(d / name, arcname=str((d / name).relative_to(root)))
            print("traces ->", out / f"traces_{exp}.tar.gz", round((out / f"traces_{exp}.tar.gz").stat().st_size / 1e6, 1), "MB")


if __name__ == "__main__":
    main()
