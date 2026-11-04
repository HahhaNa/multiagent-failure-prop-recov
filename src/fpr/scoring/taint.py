"""Normalized matching of injected values and canaries in downstream artifacts."""

import re
from typing import Any

NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def leaves(obj: Any) -> list:
    if isinstance(obj, dict):
        return [x for v in obj.values() for x in leaves(v)]
    if isinstance(obj, (list, tuple)):
        return [x for v in obj for x in leaves(v)]
    return [obj]


def _num_in_text(x: float, text: str) -> bool:
    for m in NUM.findall(text):
        try:
            if abs(float(m.replace(",", "")) - x) < 1e-6:
                return True
        except ValueError:
            pass
    return False


def _str_in_text(needle: str, text: str) -> bool:
    n, t = norm(needle), norm(text)
    if not n:
        return False
    if len(n) >= 6 and not n.isdigit():
        return n in t
    return re.search(rf"(?<![\w.@-]){re.escape(n)}(?![\w@-]|\.\w)", t) is not None


def contains(obj: Any, value: Any) -> bool:
    """True if value (scalar or list) occurs in any leaf of obj."""
    if isinstance(value, (list, tuple)):
        return any(contains(obj, v) for v in value)
    if value is None or value == "":
        return False
    is_num = isinstance(value, (int, float)) and not isinstance(value, bool)
    for leaf in leaves(obj):
        if leaf is None or isinstance(leaf, bool):
            continue
        if isinstance(leaf, (int, float)):
            if is_num and abs(float(leaf) - float(value)) < 1e-6:
                return True
            if not is_num and str(value) == str(leaf):
                return True
            continue
        if is_num:
            if _num_in_text(float(value), str(leaf)):
                return True
        elif _str_in_text(str(value), str(leaf)):
            return True
    return False
