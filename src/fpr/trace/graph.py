"""Per-run dependency graphs: declared provenance, observed data flow and taint."""

from ..scoring.taint import contains


def _claims(content) -> list[dict]:
    return content.get("claims", []) if isinstance(content, dict) else []


def _steps(content) -> list[dict]:
    return content.get("steps", []) if isinstance(content, dict) else []


def build(events: list[dict], taint: list | None = None) -> dict:
    declared: list[tuple[str, str]] = []
    dataflow: list[tuple[str, str]] = []
    nodes: dict[str, dict] = {}
    seen: dict[str, list[str]] = {"researcher": [], "planner": [], "executor": []}
    for e in events:
        k = e["kind"]
        if k == "tool_call":
            nid = f"tool:{e['call_id']}"
            nodes[nid] = {"agent": e["agent"], "name": e["name"], "args": e["args"], "rev": e["rev"]}
            dataflow += [(src, nid) for src in seen[e["agent"]]]
            if e["agent"] == "executor" and e.get("step_id"):
                declared.append((f"step:{e['step_id']}", nid))
            if e["name"] == "read_shared":
                path = e["args"].get("path", "")
                seen[e["agent"]].append("findings" if "findings" in path else "plan")
            else:
                seen[e["agent"]].append(f"ret:{e['call_id']}")
        elif k == "artifact" and e["name"] == "findings":
            nodes["findings"] = {"agent": "researcher"}
            dataflow += [(src, "findings") for src in seen["researcher"]]
            for c in _claims(e["content"]):
                cid = f"claim:{c['claim_id']}"
                nodes[cid] = {"agent": "researcher", "value": c.get("value")}
                declared += [(f"tool:{s}", cid) for s in c.get("source_tool_call_ids", [])]
                dataflow.append(("findings", cid))
        elif k == "artifact" and e["name"] == "plan":
            nodes["plan"] = {"agent": "planner"}
            dataflow += [(src, "plan") for src in seen["planner"]]
            for st in _steps(e["content"]):
                sid = f"step:{st['step_id']}"
                nodes[sid] = {"agent": "planner", "action": st.get("action"), "args": st.get("args")}
                declared += [(f"claim:{c}", sid) for c in st.get("cites", [])]
                dataflow.append(("plan", sid))
        elif k == "message":
            seen[e["to"]].append(e["artifact"])
    tainted = sorted(n for n, d in nodes.items() if taint and contains({k: v for k, v in d.items() if k in ("args", "value")}, taint))
    return {"nodes": nodes, "declared": sorted(set(declared)), "dataflow": sorted(set(dataflow)), "tainted": tainted}
