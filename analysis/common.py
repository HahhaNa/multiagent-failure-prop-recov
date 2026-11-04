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


# ---------- plotting ----------

OUT = ROOT / "data"
CAT_COLORS = {  # ordered: ignored (grays) -> robust (blues) -> propagated (warm)
    "no_dependence": "#c9c8c2",
    "read_not_used": "#8f8e88",
    "corrected": "#86b6ef",
    "contained": "#2a78d6",
    "propagated_failure": "#eb6834",
    "propagated_harm": "#e34948",
    "stall": "#eda100",
}
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
ORDER = ["A1p_R", "A1p_P", "A1i_R", "A2_R", "A3_R", "A4_R", "NC_R", "B1_Rt", "B1_Et", "B2_R", "B2_P", "B3_S", "PC"]


def style():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "figure.facecolor": "#fcfcfb",
            "axes.facecolor": "#fcfcfb",
            "axes.edgecolor": "#8f8e88",
            "axes.labelcolor": "#0b0b0b",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": False,
            "xtick.color": "#52514e",
            "ytick.color": "#52514e",
            "font.size": 9,
            "legend.frameon": False,
            "savefig.dpi": 200,
            "savefig.bbox": "tight",
        }
    )
    return plt


def out_dir(kind: str) -> Path:
    d = OUT / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def ordered(conds) -> list[str]:
    return [c for c in ORDER if c in set(conds)] + sorted(set(conds) - set(ORDER))


# ---------- provenance ----------


def _sig(action, args) -> str:
    return action + json.dumps(args, sort_keys=True, default=str)


def provenance(df: pd.DataFrame) -> pd.DataFrame:
    """Declared vs counterfactual dependence, one row per downstream artifact.

    claim->step for R.out faults (plan steps vs clean R.out continuations);
    step->call for P.out faults (executor calls vs clean P.out continuations).
    An artifact is counterfactually dependent if no clean sample produced it.
    """
    rows = []
    clean = df[df.kind == "clean"]
    for _, r in df[(df.kind == "fault") & df.injected.astype(bool)].iterrows():
        rec = r.get("fault_record") or {}
        if r.point == "R.out" and rec.get("claims"):
            cs = clean[(clean.task == r.task) & (clean["mode"] == r["mode"]) & (clean.point == "R.out")]
            if cs.empty:
                continue
            ref = {_sig(s["action"], s.get("args", {})) for p in cs.plan_raw for s in (p or {}).get("steps", [])}
            for s in (r.plan_raw or {}).get("steps", []):
                rows.append({"level": "claim->step", "fault": r.fault, "mode": r["mode"], "task": r.task,
                             "declared": bool(set(rec["claims"]) & set(s.get("cites", []))),
                             "counterfactual": _sig(s["action"], s.get("args", {})) not in ref})
        if r.point == "P.out" and rec.get("steps") is not None:
            cs = clean[(clean.task == r.task) & (clean["mode"] == r["mode"]) & (clean.point == "P.out")]
            if cs.empty:
                continue
            ref = {_sig(c["name"], c["args"]) for calls in cs.exec_calls for c in calls}
            for c in r.exec_calls:
                rows.append({"level": "step->call", "fault": r.fault, "mode": r["mode"], "task": r.task,
                             "declared": c.get("step_id") in set(rec["steps"]),
                             "counterfactual": _sig(c["name"], c["args"]) not in ref})
    return pd.DataFrame(rows)


def pr(p: pd.DataFrame) -> dict:
    tp = int((p.declared & p.counterfactual).sum())
    fp = int((p.declared & ~p.counterfactual).sum())
    fn = int((~p.declared & p.counterfactual).sum())
    tn = int((~p.declared & ~p.counterfactual).sum())
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": tp / (tp + fp) if tp + fp else float("nan"), "recall": tp / (tp + fn) if tp + fn else float("nan")}
