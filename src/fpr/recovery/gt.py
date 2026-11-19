"""Ground-truth interventional dependence from k_gt clean continuations (design §2)."""

import json
import math
from collections import Counter
from pathlib import Path

from .faulted import sig

CLEAN_FOR = {"R.tool": "R.tool", "R.out": "R.out", "P.start": "R.out", "P.out": "P.out", "S.write": "P.out", "E.tool": "E.tool", "R.start": None}


def _cdf(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))


def _solve(f, target: float) -> float:
    lo, hi = 0.0, 1.0  # f decreasing in p
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) > target else (lo, mid)
    return (lo + hi) / 2


def bounds(k: int, n: int, conf: float = 0.95) -> tuple[float, float]:
    """One-sided Clopper-Pearson lower and upper bounds."""
    a = 1 - conf
    lo = 0.0 if k == 0 else 1 - _solve(lambda q: _cdf(n - k, n, q), a)  # P(X >= k | p) = a, via the complement count
    hi = 1.0 if k == n else _solve(lambda p: _cdf(k, n, p), a)  # P(X <= k | p) = a
    return lo, hi


def label(k: int, n: int, tau0: float = 0.05, tau1: float = 0.30) -> str:
    lo, hi = bounds(k, n)
    p = k / n
    if hi <= tau1 and p <= tau0:
        return "dependent"
    if lo >= tau0 and p >= tau1:
        return "reusable"
    return "indifference"


def run_artifacts(d: Path) -> tuple[set[str], set[str]]:
    """(plan-step sigs, executor non-read call sigs after the branch) of one clean continuation or reference."""
    ev = [json.loads(x) for x in (d / "events.jsonl").read_text().splitlines() if x.strip()]
    art = json.loads((d / "artifacts.json").read_text())
    fi = next((e["i"] for e in ev if e["kind"] == "fault"), -1)
    rec = next((e for e in ev if e["kind"] == "fault"), {})
    skip = None
    if rec.get("point") in ("R.tool", "E.tool"):
        skip = next((e["i"] for e in ev[fi + 1 :] if e["kind"] == "tool_call"), None)
    plan = art.get("plan") if isinstance(art.get("plan"), dict) else {}
    steps = {sig(s.get("action", ""), s.get("args")) for s in plan.get("steps", [])}
    calls = {sig(e["name"], e["args"]) for e in ev[fi + 1 :] if e["kind"] == "tool_call" and e["agent"] == "executor"
             and e["name"] != "read_shared" and e["rev"] != "none" and e["i"] != skip}
    return steps, calls


def gt_counts(root: Path, exp: str, task: str, mode: str, point: str, seeds: range) -> dict:
    """{"n", "steps": Counter, "calls": Counter} over clean continuations at the point mapped by CLEAN_FOR."""
    cp = CLEAN_FOR.get(point)
    base = root / exp / task / mode
    dirs = [base / (f"ref_s{s}" if cp is None else f"CLEAN_{cp.replace('.', '')}_b{s}") for s in seeds]
    dirs = [d for d in dirs if (d / "DONE").exists()]
    st, ca = Counter(), Counter()
    for d in dirs:
        s, c = run_artifacts(d)
        st.update(s)
        ca.update(c)
    return {"n": len(dirs), "steps": dict(st), "calls": dict(ca)}
