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
        "wall_s", "llm_latency_s", "llm_latency_live_s", "prefix_misses", "overhead", "fault_record", "error",
        # R3 verify-all
        "verify", "verifier_calls", "verifier_live_calls", "verifier_tokens", "verifier_latency_s", "verify_stages", "n_blocked",
        # R1 restart
        "recover_from", "recovery", "n_prior_effects", "prior_tainted_irreversible", "prior_tainted_compensable", "duplicates",
        "duplicate_effects", "residual_harm", "run_dir"]
EXPS = ["main", "verify", "restart", "main_wide", "verify_wide", "restart_wide", "cross_llama3.2-3b", "verify_llama3.2-3b",
        "restart2_llama3.2-3b", "t0", "cpu_qwen3-4b-cpu", "latency_qwen3-8b", "verify_pilot2", "primary", "pilot2", "screen3"]
TRACE_FILES = ("DONE", "events.jsonl", "transcripts.json", "artifacts.json", "graphs.json", "manifest.json", "summary.json")
JSON_COLS = ("tainted_stages", "irrev_window", "first_irrev", "overhead", "fault_record", "verify_stages", "duplicate_effects")


def recover_from(run_dir: str, root: Path) -> str:
    m = json.loads((Path(run_dir) / "manifest.json").read_text())
    src = (m.get("run") or {}).get("recover_from")
    return str(Path(src).relative_to(root)) if src else ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exps", default=",".join(EXPS))
    ap.add_argument("--traces", action="store_true", help="also pack full traces (events, transcripts, artifacts, graphs, summaries, manifests)")
    a = ap.parse_args()
    out = ROOT / "data"
    out.mkdir(exist_ok=True)
    root = runs_root()
    for exp in a.exps.split(","):
        df = load(exp)
        if df.empty:
            continue
        df["recover_from"] = df["run_dir"].map(lambda p: recover_from(p, root))
        df = df[[c for c in KEEP if c in df.columns]].copy()
        df["run_dir"] = df["run_dir"].map(lambda p: str(Path(p).relative_to(root)))
        for c in JSON_COLS:
            if c in df:
                df[c] = df[c].map(lambda v: json.dumps(v, default=str) if isinstance(v, (dict, list)) else "")
        df.to_csv(out / f"runs_{exp}.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        print(exp, len(df), "rows")
        if a.traces:  # one archive per task keeps every file small
            tdir = out / "traces" / exp
            tdir.mkdir(parents=True, exist_ok=True)
            total = 0
            for task_dir in sorted(p for p in (root / exp).iterdir() if p.is_dir()):
                dst = tdir / f"{task_dir.name}.tar.gz"
                with tarfile.open(dst, "w:gz") as tar:
                    for d in sorted(task_dir.glob("*/*")):
                        if (d / "DONE").exists():
                            for name in TRACE_FILES:
                                if (d / name).exists():
                                    tar.add(d / name, arcname=str((d / name).relative_to(root)))
                total += dst.stat().st_size
            print("traces ->", tdir, round(total / 1e6, 1), "MB")

if __name__ == "__main__":
    main()
