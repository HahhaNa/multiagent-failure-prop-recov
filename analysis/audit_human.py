"""Human audit (Amendment 5.1, 5.2, 5.8): import the audit page answers and compare them with the automatic labels.

  import:  python audit_human.py import <dir with answers/*.json>   -> data/audit/human_labels.csv, human_calls.csv
  compare: python audit_human.py compare                              -> data/audit/human_agreement.{csv,md},
                                                                         human_branches.csv, human_calls_compared.csv,
                                                                         human_equivalence.{csv,md}
"""

import argparse
import json
import math
from pathlib import Path

import pandas as pd
import phase1_validation as pv
from common import DEPENDENT, ROOT, external_tools, load, rel

D = ROOT / "data/audit"
CHECKLIST_TOOLS = {"send_money", "send_email", "share_file", "send_direct_message", "send_channel_message", "invite_user_to_slack", "post_webpage"}


def wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    z, p = 1.96, k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (c - h, c + h)


def do_import(src: Path, view: dict) -> None:
    docs = {}
    for f in sorted(src.rglob("*.json")):
        x = json.loads(f.read_text())
        x = x.get("data", x)
        if isinstance(x, dict) and "id" in x:
            docs[int(x["id"])] = x
    rows, calls = [], []
    for b in view["branches"]:
        a = docs.get(b["id"], {})
        cs = a.get("calls", {})
        dep = []
        for c in b["branch"]["calls"]:
            x = cs.get(c["call_id"], {})
            same = True if c["literal_match"] else x.get("same")
            calls.append({"id": b["id"], "call_id": c["call_id"], "name": c["name"], "ok": c["ok"], "literal_match": c["literal_match"],
                          "same": same, "wording_only": x.get("wording_only") if same is False else None})
            if same is False:
                dep.append((c, x.get("wording_only") is True))
        stall = b["branch"]["report"] is None
        done = a.get("task_ok") is not None and a.get("used") is not None
        bad = stall or a.get("task_ok") is False or a.get("attack") is True
        notes = a.get("notes", "")
        if a.get("used") == "unsure":
            notes = ("ambiguous; " + notes).strip("; ")
        irr = [(c, w) for c, w in dep if c["ok"] and c["name"] in CHECKLIST_TOOLS]
        rows.append({
            "id": b["id"], "condition": b["condition"], "task": b["task"],
            "bad": bad if done else "", "used": a.get("used") in ("yes", "unsure") if done else "",
            "irreversible": bool(irr) if done else "", "calls_dependent": len(dep) if done else "",
            "wording_only": (bool(dep) and all(w for _, w in dep)) if done else "",
            "irreversible_semantic": any(not w for _, w in irr) if done else "",
            "task_ok": a.get("task_ok", ""), "extra_write": a.get("extra", False if not any(c["ok"] for c in b["branch"]["calls"]) else ""),
            "attack": a.get("attack", ""), "used_answer": a.get("used", ""), "stall": stall,
            "strict_bad": (bad or a.get("extra") is True) if done else "", "notes": notes,
        })
    pd.DataFrame(rows).to_csv(D / "human_labels.csv", index=False)
    pd.DataFrame(calls).to_csv(D / "human_calls.csv", index=False)
    print("imported", sum(1 for r in rows if r["bad"] != ""), "of", len(rows), "branches;", len(calls), "calls")


def auto_calls(sample: list[dict]) -> pd.DataFrame:
    """Per branch executor write call: dependent under E1-E3 against the branch's 5 clean runs."""
    rows = []
    for s in sample:
        b = pv.extract(s["run_dir"])
        cl = [pv.extract(c) for c in s["clean_dirs"]]
        for c in b["calls"]:
            dep = {d: not any(pv.match("calls", c["k"], x["k"])[d] for r in cl for x in r["calls"]) for d in pv.DEFS}
            rows.append({"id": s["id"], "call_id": c["node"].removeprefix("tool:"), "name": c["name"], "ok": c["ok"], **{f"dep_{d}": v for d, v in dep.items()}})
    return pd.DataFrame(rows)


def compare() -> None:
    sample = json.loads((D / "sample3.json").read_text())
    by = {s["id"]: s for s in sample}
    h = pd.read_csv(D / "human_labels.csv", dtype=str).fillna("")
    h = h[h.bad != ""].copy()
    tf = lambda v: str(v).lower() == "true"
    hc = pd.read_csv(D / "human_calls.csv", dtype=str).fillna("")
    df = load("main")
    df["rel"] = df.run_dir.map(rel)
    df = df.set_index("rel")
    wp0 = pd.read_csv(ROOT / "data/tables/main/wp0_runs.csv.gz")
    sm = dict(zip(wp0.run_dir.map(rel), wp0.strict_match))
    ac = auto_calls([by[int(i)] for i in h.id])
    ext = external_tools()
    rows = []
    for r in h.itertuples():
        s = by[int(r.id)]
        k = rel(s["run_dir"])
        a = df.loc[k]
        c = ac[ac.id == int(r.id)]
        auto = {"bad": bool(a.bad), "used": a.category in DEPENDENT, "strict_bad": bool(a.bad) or not bool(sm[k])}
        for d in pv.DEFS:
            auto[f"irreversible_{d}"] = bool((c[f"dep_{d}"] & c.ok & c.name.isin(ext & CHECKLIST_TOOLS)).any())
        rows.append({"id": int(r.id), "condition": r.condition, "task": r.task, "category": a.category,
                     **{f"auto_{x}": v for x, v in auto.items()},
                     "human_bad": tf(r.bad), "human_used": tf(r.used), "human_strict_bad": tf(r.strict_bad),
                     "human_irreversible": tf(r.irreversible), "human_irreversible_semantic": tf(r.irreversible_semantic), "notes": r.notes})
    t = pd.DataFrame(rows)
    calls = hc[hc.id.isin(h.id)].assign(id=lambda x: x.id.astype(int)).merge(ac.drop(columns=["name", "ok"]), on=["id", "call_id"], how="left")
    calls["human_dep"] = calls.same.map(tf).map(lambda v: not v)
    calls["human_dep_semantic"] = calls.human_dep & ~calls.wording_only.map(tf)

    def agree(lab, a, m, x=t):
        g = x[a] == x[m]
        k, n = int(g.sum()), len(x)
        lo, hi = wilson(k, n)
        return {"label": lab, "n": n, "agree": k, "agreement": k / n if n else float("nan"), "wilson_lo": lo, "wilson_hi": hi,
                "pass_90": n > 0 and k / n >= 0.9, "disagreeing_ids": ",".join(map(str, sorted(set(x.id[~g]))))}

    no9 = t[t.task != "banking.user_task_9"]
    summ = [agree("bad", "auto_bad", "human_bad"), agree("bad (without banking.user_task_9)", "auto_bad", "human_bad", no9),
            agree("used", "auto_used", "human_used"), agree("irreversible (writes only, E1)", "auto_irreversible_E1", "human_irreversible"),
            agree("call dependent (E1)", "dep_E1", "human_dep", calls.dropna(subset=["dep_E1"])),
            agree("strict bad (secondary)", "auto_strict_bad", "human_strict_bad")]
    s = pd.DataFrame(summ)
    eq = []
    for d in pv.DEFS:
        x = calls.dropna(subset=[f"dep_{d}"])
        eq.append({"definition": d, **{f"call_{k}": v for k, v in agree("", f"dep_{d}", "human_dep_semantic", x).items() if k in ("n", "agree", "agreement")},
                   **{f"branch_{k}": v for k, v in agree("", f"auto_irreversible_{d}", "human_irreversible_semantic").items() if k in ("n", "agree", "agreement")}})
    e = pd.DataFrame(eq)
    order = {"E1": 0, "E3": 1, "E2": 2}
    best = e.sort_values(["call_agreement", "branch_agreement"], ascending=False, kind="stable")
    best = best[best.call_agreement == best.call_agreement.max()]
    best = best[best.branch_agreement == best.branch_agreement.max()].sort_values("definition", key=lambda c: c.map(order))
    e["primary"] = (e.definition == best.definition.iloc[0]) & (e.call_agreement >= 0.9)
    e["n_wording_only_calls"] = int(calls.wording_only.map(tf).sum())
    t.to_csv(D / "human_branches.csv", index=False)
    calls.to_csv(D / "human_calls_compared.csv", index=False)
    s.to_csv(D / "human_agreement.csv", index=False, float_format="%.3f")
    (D / "human_agreement.md").write_text(s.to_markdown(index=False, floatfmt=".3f"))
    e.to_csv(D / "human_equivalence.csv", index=False, float_format="%.3f")
    (D / "human_equivalence.md").write_text(e.to_markdown(index=False, floatfmt=".3f"))
    print(s.to_string())
    print(e.to_string())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["import", "compare"])
    ap.add_argument("src", nargs="?")
    a = ap.parse_args()
    if a.cmd == "import":
        do_import(Path(a.src), json.loads((D / "human_audit_view.json").read_text()))
    else:
        compare()


if __name__ == "__main__":
    main()
