"""V4 label audit sample: 2 injected main branches per condition (one per mode where both exist), seed 2026.
Writes data/audit/sample.json with the run dirs and their clean comparison runs; no automatic labels."""

import json
import random

import yaml
from common import CLEAN_FOR, ROOT, out_dir, runs_root


def main() -> None:
    fc = yaml.safe_load((ROOT / "configs/faults.yaml").read_text())
    tasks = yaml.safe_load((ROOT / "configs/tasks_selected.yaml").read_text())["selected"]
    root = runs_root() / "main"
    rng = random.Random(2026)
    out = []
    for c in fc["conditions"] + [x for x in fc["controls"] if x["id"] != "PC"]:
        modes = c.get("modes") or ["message", "state"]
        picks = modes if len(modes) == 2 else modes * 2
        used = set()
        for mode in picks:
            cands = []
            for t in tasks:
                for d in sorted((root / t / mode).glob(f"{c['id']}_b*")):
                    s = json.loads((d / "summary.json").read_text())
                    if (s.get("fault_record") or {}).get("applied", True) and (d / "DONE").exists() and str(d) not in used:
                        cands.append(d)
            d = rng.choice(cands)
            used.add(str(d))
            cp = CLEAN_FOR.get(c["point"])
            names = [f"CLEAN_{cp.replace('.', '')}_b{b}" for b in range(100, 105)] if cp else [f"ref_s{s}" for s in range(5)]
            out.append({"id": len(out) + 1, "condition": c["id"], "family": c["family"], "point": c["point"], "mode": mode,
                        "task": d.parts[-3], "run_dir": str(d), "clean_dirs": [str(d.parent / n) for n in names]})
    p = out_dir("audit") / "sample.json"
    p.write_text(json.dumps(out, indent=1))
    print(p, len(out))


if __name__ == "__main__":
    main()
