"""Nodes of a run (inputs an agent receives from outside itself) and its pending writes (design §1-§2)."""

from dataclasses import dataclass, field

from ..recovery.faulted import FREE_TEXT, sig


def e2(name: str, args: dict | None) -> str:
    """E2 signature: tool + normalized arguments without free-text fields."""
    return sig(name, {k: v for k, v in (args or {}).items() if k not in FREE_TEXT})


MELON_ARGS = {"send_money": ("recipient", "amount"), "send_email": ("recipients",)}


def m_sig(name: str, args: dict | None) -> str:
    """Masked-recurrence signature (Amendment 12): the official MELON argument rule (send_money: recipient and amount;
    send_email: recipients), E2 for every other tool."""
    keep = MELON_ARGS.get(name)
    if keep is None:
        return e2(name, args)
    return sig(name, {k: v for k, v in (args or {}).items() if k in keep})


def tool_key(name: str, args: dict | None) -> str:
    """Identity of a source read (same tool, same arguments) for quarantine."""
    return sig(name, args)


@dataclass
class Node:
    j: int  # 0 = start (fresh run), 1..n = nodes in event order
    kind: str  # start | rtool | findings | plan | etool
    sub: int = 0  # index within its kind
    name: str = ""
    args: dict = field(default_factory=dict)
    text: str = ""

    @property
    def is_tool(self) -> bool:
        return self.kind in ("rtool", "etool")

    @property
    def key(self) -> str:
        return tool_key(self.name, self.args)


def node_list(events: list[dict]) -> list[Node]:
    out = [Node(0, "start")]
    rt = [e for e in events if e["kind"] == "tool_call" and e["agent"] == "researcher"]
    for k, e in enumerate(rt):
        out.append(Node(len(out), "rtool", k, e["name"], e["args"], str(e["result"])))
    out.append(Node(len(out), "findings"))
    out.append(Node(len(out), "plan"))
    et = [e for e in events if e["kind"] == "tool_call" and e["agent"] == "executor" and e["name"] != "read_shared"]
    for k, e in enumerate(et):
        out.append(Node(len(out), "etool", k, e["name"], e["args"], str(e["result"])))
    return out


@dataclass
class Write:
    call_id: str
    name: str
    args: dict
    rev: str
    i: int

    @property
    def sig(self) -> str:
        return e2(self.name, self.args)

    @property
    def msig(self) -> str:
        return m_sig(self.name, self.args)


def pending(events: list[dict]) -> list[Write]:
    """Every successful, non-deduplicated executor write of a run (the staged set W)."""
    return [Write(e["call_id"], e["name"], e["args"], e["rev"], e["i"]) for e in events
            if e["kind"] == "tool_call" and e["agent"] == "executor" and e["rev"] != "none" and not e["error"]
            and not e.get("dedup") and e["name"] != "read_shared"]


def write_sigs(events: list[dict], masked: bool = False) -> set[str]:
    return {w.msig if masked else w.sig for w in pending(events)}
