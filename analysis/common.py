"""Shared loading and metric helpers for analysis scripts. Reads run outputs only."""

import json
import os
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CATS = ["no_dependence", "read_not_used", "corrected", "contained", "propagated_failure", "propagated_harm", "stall"]
DEPENDENT = ["corrected", "contained", "propagated_failure", "propagated_harm", "stall"]
PROPAGATED = ["propagated_failure", "propagated_harm"]
CRITICAL = ["A1p_R", "A1p_P", "A1i_R", "A2_R", "A3_R"]


def runs_root() -> Path:
    cfg = yaml.safe_load((ROOT / "configs/phase1.yaml").read_text())
    return Path(os.path.expanduser(os.environ.get("FPR_RUNS", cfg["runs_root"])))


def conditions() -> pd.DataFrame:
    fc = yaml.safe_load((ROOT / "configs/faults.yaml").read_text())
    return pd.DataFrame(fc["conditions"] + fc["controls"]).set_index("id")


def load(exp: str, root: Path | None = None) -> pd.DataFrame:
    root = root or runs_root()
    rows = []
    for p in sorted((root / exp).glob("*/*/*/summary.json")):
        if not (p.parent / "DONE").exists():
            continue
        s = json.loads(p.read_text())
        s["run_dir"] = str(p.parent)
        rows.append(s)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["kind"] = df["fault"].map(lambda f: "ref" if not isinstance(f, str) else ("clean" if str(f).startswith("CLEAN_") else "fault"))
    cond = conditions()
    df["point"] = df["fault"].map(lambda f: _point(f, cond))
    df["family"] = df["fault"].map(lambda f: cond.loc[f, "family"] if isinstance(f, str) and f in cond.index else None)
    df["ftype"] = df["fault"].map(lambda f: cond.loc[f, "type"] if isinstance(f, str) and f in cond.index else None)
    df["bad"] = df["bad"].astype(bool)
    return df


def _point(f, cond) -> str | None:
    if not isinstance(f, str):
        return None
    if f.startswith("CLEAN_"):
        p = f[len("CLEAN_") :]
        return p[0] + "." + p[1:]
    return cond.loc[f, "point"] if f in cond.index else None


CLEAN_FOR = {"R.tool": "R.tool", "R.out": "R.out", "P.out": "P.out", "S.write": "P.out", "E.tool": "E.tool", "E.args": "E.tool", "R.start": None}


def clean_bad(df: pd.DataFrame) -> pd.DataFrame:
    """Noise floor: P(bad) of clean continuations per (task, mode, point); R.start uses references."""
    c = df[df.kind == "clean"].groupby(["task", "mode", "point"])["bad"].mean().rename("clean_bad").reset_index()
    r = df[df.kind == "ref"].groupby(["task", "mode"])["bad"].mean().rename("clean_bad").reset_index()
    r["point"] = "R.start"
    return pd.concat([c, r], ignore_index=True)


def with_delta(df: pd.DataFrame) -> pd.DataFrame:
    f = df[df.kind == "fault"].copy()
    f["clean_point"] = f["point"].map(lambda p: CLEAN_FOR.get(p) or "R.start")
    cb = clean_bad(df).rename(columns={"point": "clean_point"})
    return f.merge(cb, on=["task", "mode", "clean_point"], how="left")


def condition_table(df: pd.DataFrame) -> pd.DataFrame:
    f = with_delta(df)
    if f.empty:
        return f
    g = f.groupby(["fault", "mode"])
    t = pd.DataFrame(
        {
            "n": g.size(),
            "injected": g["injected"].mean(),
            "dependence": g["category"].apply(lambda c: c.isin(DEPENDENT).mean()),
            "prop_given_dep": g["category"].apply(lambda c: c.isin(PROPAGATED).sum() / max(1, c.isin(DEPENDENT).sum())),
            "bad": g["bad"].mean(),
            "clean_bad": g["clean_bad"].mean(),
            "harm": g["harm"].mean() if "harm" in f else 0.0,
            "security_violation": g["security_violation"].mean(),
            "utility": g["utility"].mean(),
            "depth": g["depth"].mean(),
        }
    )
    t["delta"] = t["bad"] - t["clean_bad"]
    return t.reset_index()


def throughput(exp: str, root: Path | None = None) -> float | None:
    p = (root or runs_root()) / "logs" / f"{exp}.progress.jsonl"
    if not p.exists():
        return None
    rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    return rows[-1]["run_eq_per_h"] if rows else None
