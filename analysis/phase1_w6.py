"""W6 optional analyses: T=0 noise floor vs T=0.3, CPU latency sample, cross-family recovery. Reads run outputs only."""

import argparse
import json

import numpy as np
import pandas as pd
from common import boot_ci, load, out_dir, runs_root


def _sig_set(calls) -> str:
    return json.dumps(sorted(c["name"] + json.dumps(c["args"], sort_keys=True, default=str) for c in calls or []))


def noise_floor(exp_t0: str = "t0", exp_ref: str = "main") -> pd.DataFrame:
    """Spread among clean samples at each branch point: outcome disagreement and distinct action sets, T=0 vs T=0.3."""
    rows = []
    t0 = load(exp_t0)
    if t0.empty:
        return pd.DataFrame()
    tasks = set(t0.task)
    for name, df in ((f"T=0 ({exp_t0})", t0), (f"T=0.3 ({exp_ref})", load(exp_ref))):
        df = df[df.task.isin(tasks) & df.kind.isin(["clean", "ref"])].copy()
        df["pt"] = df.point.where(df.kind == "clean", "ref")
        df["acts"] = df.exec_calls.map(_sig_set)
        g = df.groupby(["task", "mode", "pt"])
        per = pd.DataFrame({
            "n": g.size(),
            "bad_rate": g.bad.mean(),
            "disagree": g.bad.apply(lambda b: min(b.mean(), 1 - b.mean())),  # share deviating from the majority outcome
            "distinct_actions": g.acts.nunique(),
        }).reset_index()
        for pt, x in per.groupby("pt"):
            lo, hi = boot_ci(x, lambda t: t.disagree.mean())
            rows.append({"setting": name, "point": pt, "groups": len(x), "samples_per_group": x.n.mean(), "bad_rate": x.bad_rate.mean(),
                         "outcome_disagreement": x.disagree.mean(), "ci_lo": lo, "ci_hi": hi, "distinct_action_sets": x.distinct_actions.mean()})
    return pd.DataFrame(rows)


def cpu_latency(exp: str = "cpu_qwen3-4b-cpu", gpu_exp: str = "main") -> pd.DataFrame:
    c = load(exp)
    if c.empty:
        return pd.DataFrame()
    g = load(gpu_exp)
    g = g[(g.kind == "ref") & (g.ref_seed == 0) & g.task.isin(set(c.task))]
    sets = [(f"CPU llama.cpp Qwen3-4B Q4 ({exp}), 2 slots", c, False), (f"GPU vLLM Qwen3-8B FP8 ({gpu_exp} refs seed 0; latencies as recorded, 64 concurrent episodes)", g, True)]
    single = load("latency_qwen3-8b")
    if not single.empty:
        sets.append(("GPU vLLM Qwen3-8B FP8 (latency_qwen3-8b, single stream, seed 9)", single, False))
    rows = []
    for name, df, replayed in sets:
        rows.append({
            "setting": name, "runs": len(df), "clean_success": df.utility.mean(), "stall": df.stalled.mean(),
            "llm_calls": df.n_llm_calls.mean(), "prompt_tokens": df.prompt_tokens.mean(), "completion_tokens": df.completion_tokens.mean(),
            # replayed runs have no meaningful wall time; recorded per-call latency comes from the original live calls
            "wall_s_median": np.nan if replayed else df.wall_s.median(), "wall_s_p90": np.nan if replayed else df.wall_s.quantile(0.9),
            "llm_s_per_episode": df.llm_latency_s.median(),
            "llm_latency_per_call_s": (df.llm_latency_s / df.n_llm_calls.replace(0, np.nan)).median(),
            "completion_tok_per_s": (df.completion_tokens / df.llm_latency_s.replace(0, np.nan)).median(),
        })
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.parse_args()
    d = out_dir("tables/w6")
    for name, t in (("noise_floor_t0", noise_floor()), ("cpu_latency", cpu_latency())):
        if t.empty:
            print("skip", name)
            continue
        t.to_csv(d / f"{name}.csv", index=False, float_format="%.4f")
        (d / f"{name}.md").write_text(t.to_markdown(index=False, floatfmt=".3f"))
        print(t.round(3).to_string())
    print("->", d, runs_root())


if __name__ == "__main__":
    main()
