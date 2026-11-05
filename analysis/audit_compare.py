"""V4 label audit: agreement between the blind trace audit (data/audit/manual_*.json) and the automatic labels
(run summaries, refined categories, main cf_artifacts). Threshold from docs/preregistration.md: >= 90% per label."""

import argparse
import json

import pandas as pd
import yaml
from common import DEPENDENT, ROOT, load, out_dir


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", default="", help="'' (round 1), '2', '3', or 'human' (data/audit/human_labels.csv)")
    args = ap.parse_args()
    if args.round == "human":  # Amendment 5.1: per-call answers and E1-E3 comparison
        import audit_human

        audit_human.compare()
        return
    d = ROOT / "data/audit"
    sample = {x["id"]: x for x in json.loads((d / f"sample{'' if args.round == 'human' else args.round}.json").read_text())} if args.round != "human" else {}
    if args.round == "human":  # human labels for the round-3 sample, filled in data/audit/human_labels.csv
        sample = {x["id"]: x for x in json.loads((d / "sample3.json").read_text())}
        tf = {"true": True, "false": False, "1": True, "0": False, "yes": True, "no": False}
        h = pd.read_csv(d / "human_labels.csv", dtype=str).fillna("")
        h = h[h.bad.str.lower().isin(tf)]
        manual = {int(r.id): {"bad": tf[r.bad.lower()], "read": True, "used": tf.get(r.used.lower(), False),
                              "fault_caused_irreversible": tf.get(r.irreversible.lower(), False), "calls": [], "notes": r.notes} for r in h.itertuples()}
    else:
        manual = {x["id"]: x for f in sorted(d.glob(f"manual{args.round}_*.json")) for x in json.loads(f.read_text())}
    if not manual:
        raise SystemExit("no filled rows (data/audit/human_labels.csv)" if args.round == "human" else "no audit files")
    df = load("main").set_index("run_dir")
    arts = pd.read_csv(ROOT / "data/tables/main/cf_artifacts.csv.gz")
    tools = yaml.safe_load((ROOT / "configs/tools.yaml").read_text())
    ext = {n for s in tools.values() for n, v in s.get("write", {}).items() if v.get("external")}
    ext_read = {n for s in tools.values() for n in s.get("read_external", [])}
    rows, calls = [], []
    for i, m in sorted(manual.items()):
        s = sample[i]
        r = df.loc[s["run_dir"]]
        bk = (arts.task == s["task"]) & (arts["mode"] == s["mode"]) & (arts.fault == s["condition"]) & (arts.branch_seed == int(s["run_dir"].rsplit("_b", 1)[1]))
        a = arts[bk & (arts.level == "call")]
        eff = a[a.ok & a.rev.isin(["irreversible", "compensable"]) & a.dependent]
        auto = {
            "bad": bool(r.bad),
            "read": r.category != "no_dependence",
            "used": r.category in DEPENDENT,
            "fault_caused_irreversible": bool(eff.name.isin(ext | ext_read).any()),
            "fault_caused_irreversible_writes_only": bool(eff.name.isin(ext).any()),
        }
        man = {k: bool(m[k]) for k in ("bad", "read", "used", "fault_caused_irreversible")}
        man["fault_caused_irreversible_writes_only"] = man["fault_caused_irreversible"]
        rows.append({"id": i, "condition": s["condition"], "mode": s["mode"], "task": s["task"], "category": r.category, "confidence": m.get("confidence"),
                     **{f"auto_{k}": v for k, v in auto.items()}, **{f"manual_{k}": v for k, v in man.items()}, "notes": m.get("notes", "")})
        dep = dict(zip(a.node.str.removeprefix("tool:"), a.dependent))
        for c in m.get("calls", []):
            calls.append({"id": i, "call_id": c["call_id"], "name": c["name"], "manual_dependent": bool(c["dependent"]), "auto_dependent": dep.get(c["call_id"])})
    t = pd.DataFrame(rows)
    c = pd.DataFrame(calls)
    summary = []
    for k in ("bad", "read", "used", "fault_caused_irreversible", "fault_caused_irreversible_writes_only"):
        agree = t[f"auto_{k}"] == t[f"manual_{k}"]
        summary.append({"label": k, "n": len(t), "agree": int(agree.sum()), "agreement": agree.mean(), "pass_90": agree.mean() >= 0.9,
                        "disagreeing_ids": ",".join(str(x) for x in t.id[~agree])})
    x = t[t.task != "banking.user_task_9"]
    for k in ("bad", "used"):
        agree = x[f"auto_{k}"] == x[f"manual_{k}"]
        summary.append({"label": f"{k} (without banking.user_task_9)", "n": len(x), "agree": int(agree.sum()), "agreement": agree.mean(),
                        "pass_90": agree.mean() >= 0.9, "disagreeing_ids": ",".join(str(i) for i in x.id[~agree])})
    cc = c.dropna(subset=["auto_dependent"])
    agree = cc.auto_dependent.astype(bool) == cc.manual_dependent
    summary.append({"label": "call_dependent", "n": len(cc), "agree": int(agree.sum()), "agreement": agree.mean() if len(cc) else float("nan"),
                    "pass_90": bool(len(cc) and agree.mean() >= 0.9), "disagreeing_ids": ",".join(sorted({str(x) for x in cc.id[~agree]})),
                    })
    s = pd.DataFrame(summary)
    o = out_dir("audit")
    t.to_csv(o / f"audit_branches{args.round}.csv", index=False)
    c.to_csv(o / f"audit_calls{args.round}.csv", index=False)
    s.to_csv(o / f"audit_agreement{args.round}.csv", index=False, float_format="%.3f")
    (o / f"audit_agreement{args.round}.md").write_text(s.to_markdown(index=False, floatfmt=".3f"))
    print(s.to_string())
    print("calls without automatic label:", int(c.auto_dependent.isna().sum()))


if __name__ == "__main__":
    main()
