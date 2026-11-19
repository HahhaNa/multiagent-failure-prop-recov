"""Channel analysis for Proposition 1 (design §8): every ground-truth-dependent artifact that taint misses is classified
by fixed rules on the pair (F's artifact, closest clean-continuation artifact with the same tool or action):

- added step: no clean continuation ever uses this tool / action at this level;
- paraphrase: only free-text fields differ;
- format change: differing values are equal after stripping punctuation, case, list order and number format;
- omission: the artifact lacks an argument, or a list argument has fewer items, than the closest clean artifact;
- derived value: a differing non-free-text value is a number or date (computed from the corrupted input);
- other.
Reads main_h2 faulted runs and gt_h2 clean continuations. Output: data/tables/phase2/channels*.csv.
"""

import json
import re
from collections import Counter
from functools import cache

import pandas as pd
import yaml
from common import ROOT, conditions, out_dir, runs_root

from fpr.recovery.faulted import FREE_TEXT, FaultedRun, norm, sig
from fpr.recovery.gt import CLEAN_FOR, label

P2 = yaml.safe_load((ROOT / "configs/phase2.yaml").read_text())
NUMDATE = re.compile(r"^-?[\d.,:\- ]+$")


@cache
def clean_arts(task: str, mode: str, point: str) -> tuple[int, dict, dict]:
    """(n, {level: Counter(sig)}, {level: [(name, args)]}) over the k_gt clean runs."""
    cp = CLEAN_FOR.get(point)
    base = runs_root() / P2["gt_exp"] / task / mode
    seeds = range(*P2["gt_seeds"])
    dirs = [base / (f"ref_s{s}" if cp is None else f"CLEAN_{cp.replace('.', '')}_b{s}") for s in seeds]
    dirs = [d for d in dirs if (d / "DONE").exists()]
    counts, items = {2: Counter(), 3: Counter()}, {2: [], 3: []}
    for d in dirs:
        R = FaultedRun.load(d)
        fi = R.fi if any(e["kind"] == "fault" for e in R.events) else -1
        plan = R.artifacts.get("plan") if isinstance(R.artifacts.get("plan"), dict) else {}
        st = [(s.get("action", ""), s.get("args") or {}) for s in plan.get("steps", [])]
        o = R.origin_call()
        ca = [(e["name"], e["args"]) for e in R.events[fi + 1 :] if e["kind"] == "tool_call" and e["agent"] == "executor"
              and e["name"] != "read_shared" and e["rev"] != "none" and (o is None or e["i"] != o["i"])]
        for lvl, xs in ((2, st), (3, ca)):
            counts[lvl].update({sig(n, a) for n, a in xs})
            items[lvl] += xs
    return len(dirs), counts, items


def _loose(v) -> str:
    if isinstance(v, list):
        return "|".join(sorted(_loose(x) for x in v))
    return re.sub(r"[^a-z0-9]", "", str(norm(v)).lower())


def classify(name: str, args: dict, clean: list[tuple[str, dict]]) -> str:
    same = [a for n, a in clean if n == name]
    if not same:
        return "added step"
    args = {k: v for k, v in args.items() if k != "step_id"}

    def score(c):
        return sum(1 for k in set(args) | set(c) if norm(args.get(k)) == norm(c.get(k)))

    c = max(same, key=score)
    diff = [k for k in set(args) | set(c) if norm(args.get(k)) != norm(c.get(k))]
    if not diff:
        return "other"
    if all(k in FREE_TEXT for k in diff):
        return "paraphrase"
    hard = [k for k in diff if k not in FREE_TEXT]
    if all(_loose(args.get(k)) == _loose(c.get(k)) for k in hard):
        return "format change"
    if any(k not in args or (isinstance(c.get(k), list) and isinstance(args.get(k), list) and len(args[k]) < len(c[k])) for k in hard):
        return "omission"
    if any(NUMDATE.match(str(args.get(k, ""))) and NUMDATE.match(str(c.get(k, ""))) for k in hard):
        return "derived value"
    return "other"


def main() -> None:
    cond = conditions()
    rows = []
    for p in sorted((runs_root() / P2["src"]).glob("*/*/*/summary.json")):
        s = json.loads(p.read_text())
        f = s.get("fault")
        if not f or f.startswith("CLEAN_") or not s.get("injected") or not (p.parent / "DONE").exists():
            continue
        F = FaultedRun.load(p.parent)
        n, counts, items = clean_arts(s["task"], s["mode"], F.point)
        if not n:
            continue
        flags = F.flags()
        for a in F.steps() + F.calls():
            lab = label(counts[a.level].get(a.sig, 0), n, P2["sprt"]["tau0"], P2["sprt"]["tau1"])
            if lab != "dependent":
                continue
            tainted = F.tainted(a)
            rows.append({"task": s["task"], "mode": s["mode"], "fault": f, "family": cond.loc[f, "family"], "point": F.point, "level": a.level,
                         "tainted": tainted, "declared_or_taint": a.id in flags,
                         "channel": "literal (taint)" if tainted else classify(a.name, a.args, items[a.level])})
    df = pd.DataFrame(rows)
    d = out_dir("tables/phase2")
    df.to_csv(d / "channels_artifacts.csv.gz", index=False)
    t = df.groupby(["family", "channel"]).size().rename("n").reset_index()
    t["share"] = t.n / t.groupby("family").n.transform("sum")
    t2 = df[~df.declared_or_taint].groupby(["family", "channel"]).size().rename("n_missed_by_union").reset_index()
    t = t.merge(t2, on=["family", "channel"], how="left").fillna({"n_missed_by_union": 0})
    t.to_csv(d / "channels.csv", index=False)
    (d / "channels.md").write_text(t.to_markdown(index=False, floatfmt=".3f"))
    print(t.to_string(index=False))


if __name__ == "__main__":
    main()
