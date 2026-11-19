"""A finished faulted run F: its events, transcripts and downstream artifacts (design §1, §4)."""

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..agents.schemas import Findings
from ..scoring.recovery import taint_from_record
from ..scoring.taint import contains

FREE_TEXT = {"subject", "body", "content", "description", "title", "text", "message", "location"}


def norm(x):
    """E1 normalization (same as analysis/phase1_counterfactual.py)."""
    if isinstance(x, dict):
        return {str(k): norm(v) for k, v in sorted(x.items())}
    if isinstance(x, (list, tuple)):
        return [norm(v) for v in x]
    if isinstance(x, bool) or x is None:
        return x
    if isinstance(x, (int, float)):
        return f"{float(x):.4f}"
    s = str(x).strip().strip("'\"").strip().rstrip(".").lower()
    try:
        return f"{float(s.replace(',', '')):.4f}"
    except ValueError:
        return " ".join(s.split())


def sig(name: str, args) -> str:
    args = {k: v for k, v in (args or {}).items() if k != "step_id"}
    return name + json.dumps(norm(args), sort_keys=True)


def effect_key(name: str, args: dict) -> str:
    return sig(name, args)


@dataclass
class Artifact:
    id: str  # step:<id> or call:<call_id>
    level: int  # 2 plan step, 3 executor write call
    sig: str
    name: str
    args: dict
    step_id: str | None = None
    cites: list[str] = field(default_factory=list)
    call_id: str | None = None
    ok: bool = True
    rev: str = "none"
    pos: int = 0  # position in F (event index)


@dataclass
class FaultedRun:
    dir: Path
    events: list[dict]
    transcripts: dict
    artifacts: dict
    summary: dict
    manifest: dict

    @classmethod
    def load(cls, d: str | Path) -> "FaultedRun":
        d = Path(d)
        ev = [json.loads(x) for x in (d / "events.jsonl").read_text().splitlines() if x.strip()]
        man = json.loads((d / "manifest.json").read_text()) if (d / "manifest.json").exists() else {}
        return cls(d, ev, json.loads((d / "transcripts.json").read_text()), json.loads((d / "artifacts.json").read_text()),
                   json.loads((d / "summary.json").read_text()), man)

    # ---- fault ----
    @property
    def record(self) -> dict:
        return self.summary.get("fault_record") or {}

    @property
    def point(self) -> str:
        return self.record.get("point", "")

    @property
    def mode(self) -> str:
        return self.summary["mode"]

    @property
    def fi(self) -> int:
        return next((e["i"] for e in self.events if e["kind"] == "fault"), len(self.events))

    @property
    def taint(self) -> list:
        return taint_from_record(self.record)

    def origin_call(self) -> dict | None:
        """R.tool / E.tool: the tool call whose return was corrupted (logged right after the fault event)."""
        if self.point not in ("R.tool", "E.tool"):
            return None
        return next((e for e in self.events[self.fi + 1 :] if e["kind"] == "tool_call"), None)

    # ---- env calls ----
    def env_calls(self) -> list[dict]:
        return [e for e in self.events if e["kind"] == "tool_call" and e["name"] != "read_shared" and not e["error"]]

    def pre_calls(self) -> list[dict]:
        o = self.origin_call()
        return [e for e in self.env_calls() if e["i"] < self.fi or (o is not None and e["i"] == o["i"])]

    def post_calls(self) -> list[dict]:
        pre = {e["i"] for e in self.pre_calls()}
        return [e for e in self.env_calls() if e["i"] not in pre]

    # ---- artifacts ----
    def findings_seen(self) -> dict | None:
        """Findings as the planner saw them (post-injection artifact)."""
        return self.artifacts.get("findings")

    def findings_uninjected(self) -> dict:
        """R.out: the researcher's own output before injection (its last guided call)."""
        calls = [e for e in self.events[: self.fi] if e["kind"] == "llm_call" and e["agent"] == "researcher"]
        if not calls:
            return {"claims": []}
        try:
            return Findings.model_validate_json(calls[-1]["content"] or "{}").model_dump()
        except ValueError:
            return {"claims": []}

    def plan_seen(self) -> dict | None:
        """The plan as the executor saw it (S.write: the poisoned blackboard version)."""
        if self.point == "S.write" and self.record.get("applied"):
            for e in self.events:
                if e["kind"] == "tool_call" and e["agent"] == "executor" and e["name"] == "read_shared" and "plan" in str(e["args"].get("path")):
                    try:
                        return json.loads(e["result"])
                    except (TypeError, ValueError):
                        break
        return self.artifacts.get("plan")

    def steps(self) -> list[Artifact]:
        if self.point not in ("R.tool", "R.start", "R.out"):
            return []
        plan = self.artifacts.get("plan")
        if not isinstance(plan, dict):
            return []
        pos = next((e["i"] for e in self.events if e["kind"] == "artifact" and e["name"] == "plan"), 0)
        return [Artifact(f"step:{s['step_id']}", 2, sig(s.get("action", ""), s.get("args")), s.get("action", ""), s.get("args") or {},
                         step_id=s["step_id"], cites=list(s.get("cites") or []), pos=pos) for s in plan.get("steps", [])]

    def calls(self) -> list[Artifact]:
        """Executor non-read calls after the fault (successful or not), excluding the origin call."""
        o = self.origin_call()
        out = []
        for e in self.events[self.fi + 1 :]:
            if e["kind"] != "tool_call" or e["agent"] != "executor" or e["name"] == "read_shared" or e["rev"] == "none":
                continue
            if o is not None and e["i"] == o["i"]:
                continue
            out.append(Artifact(f"call:{e['call_id']}", 3, sig(e["name"], e["args"]), e["name"], e["args"], step_id=e.get("step_id"),
                                call_id=e["call_id"], ok=not e["error"], rev=e["rev"], pos=e["i"]))
        return out

    def tainted(self, a: Artifact) -> bool:
        t = self.taint
        return bool(t) and contains(a.args, t)

    def declared_flags(self, steps: list[Artifact], calls: list[Artifact]) -> set[str]:
        """Declared-graph descendants of the origin: tool -> claim (sources) -> step (cites) -> call (step_id)."""
        rec, point = self.record, self.point
        bad_claims: set[str] = set()
        bad_steps: set[str] = set()
        if point == "R.out":
            bad_claims = set(rec.get("claims") or [])
        elif point == "R.tool":
            o = self.origin_call()
            f = self.findings_seen() or {}
            if o is not None and isinstance(f, dict):
                bad_claims = {c["claim_id"] for c in f.get("claims", []) if o["call_id"] in (c.get("source_tool_call_ids") or [])}
        elif point in ("P.out", "S.write"):
            bad_steps = set(rec.get("steps") or [])
        flagged = {s.id for s in steps if set(s.cites) & bad_claims}
        bad_steps |= {s.step_id for s in steps if s.id in flagged}
        flagged |= {c.id for c in calls if c.step_id and c.step_id in bad_steps}
        return flagged

    def flags(self) -> set[str]:
        """Flagged = declared ∪ taint (design §4 step 4)."""
        steps, calls = self.steps(), self.calls()
        return self.declared_flags(steps, calls) | {a.id for a in steps + calls if self.tainted(a)}

    def truncate(self, t: int) -> "FaultedRun":
        """F as seen by a detector that fires at event t: events from t on never happened (design §6, Amendment 8)."""
        import copy

        ev = self.events[:t]
        art = copy.deepcopy(self.artifacts)
        for name in ("plan_raw", "plan", "report"):
            pos = next((e["i"] for e in self.events if e["kind"] == "artifact" and e["name"] == name), None)
            if pos is not None and pos >= t:
                art.pop(name, None)
        ids = {e["call_id"] for e in ev if e["kind"] == "tool_call" and not e["error"]}
        eff = [e for e in ev if e["kind"] == "external_effect" and e["call_id"] in ids]
        summ = {**self.summary, "report": None, "external_effects": [{"tool": e["tool"], "args": e["args"], "rev": e["rev"]} for e in eff],
                "detect_at": t}
        return FaultedRun(self.dir, ev, self.transcripts, art, summ, self.manifest)

    def final_env_dump(self) -> dict:
        """Last stored version of every env object (for the 'none' policy check)."""
        import gzip

        with gzip.open(self.dir / "snapshots.json.gz", "rt") as fh:
            snaps = json.load(fh)
        return {k: v[-1]["value"] for k, v in snaps.items() if k.startswith("env:")}
