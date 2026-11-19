"""World actors: legitimate external edits and compensations applied outside the agents (Phase 2)."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agentdojo.default_suites.v1.tools.banking_client import Transaction, next_id


@dataclass
class WorldActor:
    changes: list[Callable[[Any], str]] = field(default_factory=list)

    def apply(self, env: Any) -> list[str]:
        return [change(env) for change in self.changes]


def known_ibans(pre_env: Any) -> set[str]:
    """IBANs in the task's initial transaction history (compensation eligibility, design §4 step 6)."""
    acc = getattr(pre_env, "bank_account", None)
    if acc is None:
        return set()
    own = acc.iban.lower()
    return {x.lower() for t in acc.transactions for x in (t.sender, t.recipient)} - {own}


def refund(task_env: Any, args: dict, writer: str) -> str | None:
    """Recipient of a send_money returns the same amount. Returns an error if the recipient is unknown."""
    rec = str(args.get("recipient", ""))
    if rec.lower() not in known_ibans(task_env.pre_env):
        return f"compensation failed: recipient {rec} not in the initial transaction history"
    acc = task_env.env.bank_account
    acc.transactions.append(Transaction(id=next_id(acc), sender=rec, recipient=acc.iban, amount=float(args.get("amount", 0) or 0),
                                        subject=f"Refund: {args.get('subject', '')}", date=str(args.get("date", "")), recurring=False))
    task_env.store.observe("env:bank_account", acc, writer)
    return None
