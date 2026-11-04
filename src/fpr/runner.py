"""Resumable experiment runner. Each run is an immutable directory; DONE marks completion.

Usage: python -m fpr.runner <stage> [--model qwen3-8b] [--mock] [--limit N]
Stages: screen0 screen1 screen_all select pilot primary main main_wide t0 cpu latency verify_pilot verify restart cross status
"""

import argparse
import asyncio
import hashlib
import importlib.metadata as md
import json
import os
import subprocess
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from .env.agentdojo_adapter import ROOT
from .faults.spec import FaultSpec, clean_point, conditions
from .llm.cache import CachedLLM, DiskCache
from .llm.client import OpenAIClient
from .orchestrator import Episode, EpisodeConfig
from .scoring.recovery import mark_tainted, taint_from_record
from .tasks import TaskSpec, load_specs


@dataclass(frozen=True)
class RunSpec:
    exp: str
    task: str
    mode: str
    fault: str | None
    ref_seed: int
    branch_seed: int
    recover_from: str | None = None  # R1: faulted run directory to restart from

    @property
    def name(self) -> str:
        return f"ref_s{self.ref_seed}" if self.fault is None else f"{self.fault}_b{self.branch_seed}"

    def path(self, root: Path) -> Path:
        return root / self.exp / self.task / self.mode / self.name


def load_cfg() -> dict:
    return yaml.safe_load((ROOT / "configs/phase1.yaml").read_text())


def runs_root(cfg: dict) -> Path:
    return Path(os.path.expanduser(os.environ.get("FPR_RUNS", cfg["runs_root"])))


def config_hash() -> str:
    h = hashlib.sha1()
    for p in sorted((ROOT / "configs").glob("*.yaml")):
        h.update(p.name.encode() + p.read_bytes())
    return h.hexdigest()[:12]


def _sh(cmd: str) -> str:
    try:
        return subprocess.run(cmd, check=False, shell=True, capture_output=True, text=True, cwd=ROOT, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def base_manifest(cfg: dict, model_alias: str) -> dict:
    models = yaml.safe_load((ROOT / "configs/models.yaml").read_text())
    m = models.get(model_alias, {})
    serve = ROOT / f"logs/serve_cmd_{model_alias}.txt"
    serve = serve if serve.exists() else ROOT / "logs/serve_cmd.txt"
    return {
        "git_commit": _sh("git rev-parse HEAD"),
        "git_dirty": bool(_sh("git status --porcelain --untracked-files=no")),
        "config_hash": config_hash(),
        "model_alias": model_alias,
        "model_repo": m.get("repo"),
        "model_revision": m.get("revision"),
        "quantization": m.get("quantization"),
        "vllm_version": md.version("vllm"),
        "agentdojo_version": md.version("agentdojo"),
        "benchmark_version": cfg["benchmark_version"],
        "driver_version": _sh("nvidia-smi --query-gpu=driver_version --format=csv,noheader"),
        "cuda_version": _sh("nvidia-smi | grep -o 'CUDA Version: [0-9.]*' | cut -d' ' -f3"),
        "serve_command": serve.read_text().strip() if serve.exists() else None,
        "temperature": cfg["temperature"],
        "backend": m.get("backend", "vllm"),
    }


# ---------- matrix ----------


def expand(exp: str, tasks: list[str], modes: list[str], ref_seeds: list[int], conds: list[FaultSpec], clean_pts: list[str], branch_seeds: list[int]) -> list[RunSpec]:
    out = []
    for t in tasks:
        for m in modes:
            out += [RunSpec(exp, t, m, None, s, s) for s in ref_seeds]
            for c in conds:
                if m in c.modes:
                    out += [RunSpec(exp, t, m, c.id, 0, b) for b in branch_seeds]
            for p in clean_pts:
                out += [RunSpec(exp, t, m, clean_point(p).id, 0, b) for b in branch_seeds]
    return out


def primary_matrix(tasks: list[str], k: int = 3, core_only: bool = False, modes: list[str] | None = None, exp: str = "primary") -> list[RunSpec]:
    cfg = load_cfg()
    fc = yaml.safe_load((ROOT / "configs/faults.yaml").read_text())
    conds = [c for c in conditions() if c.core or not core_only]
    return expand(exp, tasks, modes or cfg["modes"], cfg["ref_seeds"][:k], conds, fc["clean_points"], cfg["branch_seeds"][:k])


RECOVERY_SEED0 = 200


def prior_effects(run_dir: Path) -> list[dict]:
    """External effects of a finished faulted run, in order, with taint flags."""
    s = json.loads((run_dir / "summary.json").read_text())
    effects = s.get("external_effects")
    if effects is None:  # runs from before summaries recorded effects: read the external-world events
        ev = [json.loads(x) for x in (run_dir / "events.jsonl").read_text().splitlines()]
        effects = [{"tool": e["tool"], "args": e["args"], "rev": e["rev"]} for e in ev if e["kind"] == "external_effect"]
    return mark_tainted(effects, taint_from_record(s.get("fault_record") or {}))


def restart_matrix(root: Path, src_exp: str = "main", exp: str = "restart", faults: list[str] | None = None) -> list[RunSpec]:
    """One R1 run per injected fault branch of src_exp."""
    out = []
    for s in summaries(root, src_exp):
        f = s.get("fault")
        if not f or f.startswith("CLEAN_") or not s.get("injected") or (faults and f not in faults):
            continue
        out.append(RunSpec(exp, s["task"], s["mode"], f, s["ref_seed"], s["branch_seed"], recover_from=s["run_dir"]))
    return out


# ---------- execution ----------


class Runner:
    def __init__(self, llm, cfg: dict, specs: dict[str, TaskSpec], model_alias: str, model_name: str, concurrency: int, root: Path, verify: bool = False):
        self.llm, self.cfg, self.specs = llm, cfg, specs
        self.verify = verify
        self.model_alias, self.model_name = model_alias, model_name
        self.root = root
        self.sem = asyncio.Semaphore(concurrency)
        self.manifest = {**base_manifest(cfg, model_alias), "verify": verify}
        models = yaml.safe_load((ROOT / "configs/models.yaml").read_text())
        self.single_tool_calls = bool(models.get(model_alias, {}).get("single_tool_calls", False))
        self.stats = {"done": 0, "skipped": 0, "failed": 0, "live_calls": 0, "t0": time.time()}
        self.executed: list[str] = []

    def is_done(self, r: RunSpec) -> bool:
        return (r.path(self.root) / "DONE").exists()

    def _prepare_dir(self, d: Path) -> None:
        if d.exists():  # leftover from a crashed or failed attempt: keep it, labeled
            n = 0
            while d.with_name(f"{d.name}.incomplete{n}").exists():
                n += 1
            d.rename(d.with_name(f"{d.name}.incomplete{n}"))
        d.mkdir(parents=True)

    async def run_one(self, r: RunSpec) -> dict | None:
        if self.is_done(r):
            self.stats["skipped"] += 1
            return None
        async with self.sem:
            d = r.path(self.root)
            self._prepare_dir(d)
            c = self.cfg
            ec = EpisodeConfig(
                spec=self.specs[r.task], mode=r.mode, fault_id=r.fault, ref_seed=r.ref_seed, branch_seed=r.branch_seed,
                model=self.model_name, temperature=c["temperature"], max_tokens=c["max_tokens"], version=c["benchmark_version"],
                turns=c["turns"], clock=c["clock"], tool_output_chars=c["tool_output_chars"], expect_replay=r.fault is not None,
                single_tool_calls=self.single_tool_calls, verify=self.verify,
            )
            if r.recover_from:
                ec.fault_id, ec.expect_replay = None, False
                ec.ref_seed = ec.branch_seed = RECOVERY_SEED0 + (r.branch_seed - self.cfg["branch_seeds"][0])
                ec.prior_effects = prior_effects(Path(r.recover_from))
            man = {**self.manifest, "run": r.__dict__, "seed": r.ref_seed, "branch_seed": r.branch_seed, "start": datetime.now().isoformat()}
            (d / "manifest.json").write_text(json.dumps(man, indent=1))
            try:
                s = await Episode(ec, self.llm, d).run()
            except Exception:  # noqa: BLE001 - harness bug: keep the run dir, labeled FAILED
                (d / "FAILED").write_text(traceback.format_exc())
                self.stats["failed"] += 1
                return None
            man["end"] = datetime.now().isoformat()
            (d / "manifest.json").write_text(json.dumps(man, indent=1))
            (d / "DONE").write_text(man["end"])
            self.stats["done"] += 1
            self.stats["live_calls"] += s["n_live_calls"]
            self.executed.append(str(d))
            return s

    async def run_all(self, runs: list[RunSpec], log: Path | None = None) -> None:
        refs = [r for r in runs if r.fault is None]
        branches = [r for r in runs if r.fault is not None]
        for phase in (refs, branches):  # branches replay the seed-0 reference prefix
            tasks = [asyncio.create_task(self.run_one(r)) for r in phase]
            for n, fut in enumerate(asyncio.as_completed(tasks), 1):
                await fut
                if log and (n % 20 == 0 or n == len(tasks)):
                    self.log_progress(log, len(runs))

    def throughput(self) -> float:
        h = max((time.time() - self.stats["t0"]) / 3600, 1e-9)
        return self.stats["live_calls"] / 12 / h

    def log_progress(self, log: Path, total: int) -> None:
        row = {**self.stats, "total": total, "now": datetime.now().isoformat(), "run_eq_per_h": round(self.throughput(), 1)}
        with open(log, "a") as f:
            f.write(json.dumps(row) + "\n")
        print(json.dumps(row), flush=True)


# ---------- stages ----------


def summaries(root: Path, exp: str) -> list[dict]:
    out = []
    for p in sorted((root / exp).glob("*/*/*/summary.json")):
        if (p.parent / "DONE").exists():
            s = json.loads(p.read_text())
            s["run_dir"] = str(p.parent)
            out.append(s)
    return out


def select_tasks(root: Path, specs: dict[str, TaskSpec], n: int = 20, exp: str = "screen") -> dict:
    rows = summaries(root, exp)
    ok: dict[str, dict] = {}
    for s in rows:
        ok.setdefault(s["task"], {})[(s["mode"], s["ref_seed"])] = bool(s["utility"]) and not s["security_violation"]
    full = [t for t, v in ok.items() if all(v.get((m, k)) for m in ("message", "state") for k in (0, 1))]
    rate = {t: sum(v.values()) / len(v) for t, v in ok.items()}
    # fallback when fewer than n pass 4/4: highest clean pass rate over all screening samples, at least 0.5
    partial = sorted((t for t in ok if t not in full and rate[t] >= 0.5), key=lambda t: (-rate[t], t))
    by_suite: dict[str, list[str]] = {}
    for t in [t for t in specs if t in full]:
        by_suite.setdefault(t.split(".")[0], []).append(t)
    chosen: list[str] = []
    while len(chosen) < n and any(by_suite.values()):
        for s in list(by_suite):
            if by_suite[s] and len(chosen) < n:
                chosen.append(by_suite[s].pop(0))
    fill = [t for t in partial][: max(0, n - len(chosen))]
    return {"selected": chosen + fill, "full_pass": sorted(full), "filled_from_partial": fill, "pass_rate": {t: round(rate[t], 3) for t in sorted(rate)}, "screen_results": {t: {f"{m}_s{k}": v for (m, k), v in d.items()} for t, d in ok.items()}}


def make_llm(args, cfg, specs):
    cache = DiskCache(runs_root(cfg) / "cache" / ("mock" if args.mock else args.model))
    if args.mock:
        from .llm.mock import MockLLM

        return CachedLLM(MockLLM(specs), cache, "record"), "mock"
    models = yaml.safe_load((ROOT / "configs/models.yaml").read_text())
    m = models[args.model]
    client = OpenAIClient(base_url=m.get("base_url", "http://127.0.0.1:8000/v1"), concurrency=m.get("concurrency", cfg["concurrency"]))
    return CachedLLM(client, cache, "record"), m["repo"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage")
    ap.add_argument("--model", default="qwen3-8b")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--k", type=int, default=0)
    ap.add_argument("--core-only", action="store_true")
    ap.add_argument("--modes", default="")
    ap.add_argument("--exp", default="")
    ap.add_argument("--concurrency", type=int, default=0)
    ap.add_argument("--task-set", default="selected", choices=["selected", "wide"])
    ap.add_argument("--src", default="main", help="restart: faulted experiment to recover")
    a = ap.parse_args()
    cfg = load_cfg()
    root = runs_root(cfg)
    if a.mock:
        root = root / "mock"
    specs = load_specs(ROOT / "configs/tasks_candidates.yaml")
    sel_path = root / "tasks_selected.yaml" if a.mock else ROOT / "configs/tasks_selected.yaml"
    modes = a.modes.split(",") if a.modes else cfg["modes"]

    if a.stage == "select":
        res = select_tasks(root, specs, exp=a.exp or "screen")
        res["screen_exp"] = a.exp or "screen"
        sel_path.write_text(yaml.safe_dump(res, sort_keys=False))
        print(json.dumps({k: v for k, v in res.items() if k != "screen_results"}, indent=1))
        return
    if a.stage == "status":
        for exp in sorted(p.name for p in root.iterdir() if p.is_dir() and p.name != "cache"):
            n_done = len(list((root / exp).glob("*/*/*/DONE")))
            n_fail = len(list((root / exp).glob("*/*/*/FAILED")))
            print(exp, "done", n_done, "failed", n_fail)
        return

    sel = yaml.safe_load(sel_path.read_text()) if sel_path.exists() else {}
    selected = sel.get("selected", [])
    if a.task_set == "wide":  # robustness set: clean pass rate in [1/3, 0.5)
        selected = sorted(t for t, v in sel.get("pass_rate", {}).items() if 0.3 <= v < 0.5)
    if a.stage == "screen0":
        runs = expand(a.exp or "screen", list(specs), modes, [0], [], [], [])
    elif a.stage == "screen_all":
        runs = expand(a.exp or "screen", list(specs), modes, [1, 2], [], [], [])
    elif a.stage == "screen1":
        rows = summaries(root, a.exp or "screen")
        passed = sorted({s["task"] for s in rows if s["ref_seed"] == 0 and s["utility"]})
        runs = expand(a.exp or "screen", passed, modes, [1], [], [], [])
    elif a.stage == "pilot":
        tasks = selected[:: max(1, len(selected) // 5)][:5]
        fc = yaml.safe_load((ROOT / "configs/faults.yaml").read_text())
        runs = expand(a.exp or "pilot", tasks, modes, [0], conditions(include_controls=True), fc["clean_points"], [cfg["branch_seeds"][0]])
    elif a.stage == "primary":
        runs = primary_matrix(selected, k=a.k or 3, core_only=a.core_only, modes=modes, exp=a.exp or "primary")
    elif a.stage == "main":
        runs = primary_matrix(selected, k=5, modes=modes, exp=a.exp or "main")
    elif a.stage == "main_wide":  # W6.2 robustness set: tasks with clean pass rate in [1/3, 0.5), not pooled with main
        wide = sorted(t for t, v in sel.get("pass_rate", {}).items() if 0.3 <= v < 0.5)
        runs = primary_matrix(wide, k=5, modes=modes, exp=a.exp or "main_wide")
    elif a.stage == "t0":  # W6.3 noise floor at temperature 0: refs + clean continuations, 5 tasks, k=5
        fc = yaml.safe_load((ROOT / "configs/faults.yaml").read_text())
        cfg = {**cfg, "temperature": 0.0}
        runs = expand(a.exp or "t0", selected[:: max(1, len(selected) // 5)][:5], modes, cfg["ref_seeds"][:5], [], fc["clean_points"], cfg["branch_seeds"][:5])
    elif a.stage == "cpu":  # W6.4 CPU latency sample: 10 tasks x 2 modes, clean, seed 0
        runs = expand(a.exp or f"cpu_{a.model}", selected[:10], modes, [0], [], [], [])
    elif a.stage == "latency":  # single-stream latency on the serving backend: same 20 tasks as cpu, uncached seed 9
        runs = expand(a.exp or f"latency_{a.model}", selected[:10], modes, [9], [], [], [])
    elif a.stage == "verify_pilot":  # PLAN2 4: mini pilot, 5 tasks, k=1, clean refs only
        runs = expand(a.exp or "verify_pilot", selected[:: max(1, len(selected) // 5)][:5], modes, [0], [], [], [])
    elif a.stage == "verify":
        fc = yaml.safe_load((ROOT / "configs/faults.yaml").read_text())
        k = a.k or 5
        conds = [c for c in conditions(include_controls=not a.core_only) if c.core or not a.core_only]
        runs = expand(a.exp or "verify", selected, modes, cfg["ref_seeds"][:k], conds, fc["clean_points"], cfg["branch_seeds"][:k])
    elif a.stage == "restart":
        core = [c.id for c in conditions() if c.core] if a.core_only else None
        runs = restart_matrix(root, a.src, a.exp or "restart", core)
    elif a.stage == "refs":  # clean go/no-go sample for a new model: selected tasks x modes, ref seeds 0-1
        runs = expand(a.exp or f"refs_{a.model}", selected, modes, cfg["ref_seeds"][: a.k or 2], [], [], [])
    elif a.stage == "cross":
        runs = primary_matrix(selected, k=2, core_only=True, modes=["message"], exp=a.exp or f"cross_{a.model}")
    else:
        raise SystemExit(f"unknown stage {a.stage}")
    if a.limit:
        runs = runs[: a.limit]
    llm, model_name = make_llm(a, cfg, specs)
    conc = a.concurrency or yaml.safe_load((ROOT / "configs/models.yaml").read_text()).get(a.model, {}).get("concurrency", cfg["concurrency"])
    r = Runner(llm, cfg, specs, "mock" if a.mock else a.model, model_name, conc, root, verify=a.stage.startswith("verify"))
    (root / "logs").mkdir(parents=True, exist_ok=True)
    print(f"{a.stage}: {len(runs)} runs, {sum(1 for x in runs if r.is_done(x))} already done", flush=True)
    log_name = a.exp or ("screen" if a.stage.startswith("screen") else a.stage)
    asyncio.run(r.run_all(runs, root / "logs" / f"{log_name}.progress.jsonl"))
    print("finished", json.dumps(r.stats), flush=True)


if __name__ == "__main__":
    main()
