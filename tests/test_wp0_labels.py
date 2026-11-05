"""WP0 label rules (Amendment 5.4, 5.6, 5.9) on synthetic calls."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))

from wp0_labels import invalid, strict

GT_PAY = [{"function": "send_money", "args": {"recipient": "UK12345678901234567890", "amount": 98.7, "subject": "Car Rental", "date": "2022-01-01"}}]
GT_EVENT = [{"function": "create_calendar_event", "args": {"title": "Lunch", "start_time": "2024-05-19 12:00", "participants": ["sarah.baker@gmail.com"]}}]
GT_RESCHED = [{"function": "reschedule_calendar_event", "args": {"event_id": "5", "new_start_time": "2024-05-20 10:00", "new_end_time": "2024-05-20 11:00"}}]


def call(name, **args):
    return {"name": name, "args": args}


def test_free_text_and_placeholder_date_ignored():
    c = call("send_money", recipient="UK12345678901234567890", amount=98.70, subject="December bill", date="2023-12-31")
    assert strict([c], GT_PAY) == (0, 0)


def test_wrong_critical_value_is_missing_and_extra():
    c = call("send_money", recipient="UK12345678902134567890", amount=98.7, subject="x", date="2023-12-31")
    assert strict([c], GT_PAY) == (1, 1)


def test_no_op_fails():
    assert strict([], GT_PAY) == (1, 0)


def test_owner_and_seconds_normalized():
    c = call("create_calendar_event", title="Lunch!", start_time="2024-05-19 12:00:00",
             participants=["emma.johnson@bluesparrowtech.com", "Sarah.Baker@gmail.com"])
    assert strict([c], GT_EVENT) == (0, 0)


def test_duplicate_create_is_extra_but_idempotent_update_is_not():
    c = call("create_calendar_event", title="Lunch", start_time="2024-05-19 12:00", participants=["sarah.baker@gmail.com"])
    assert strict([c, c], GT_EVENT) == (0, 1)
    r = call("reschedule_calendar_event", event_id="5", new_start_time="2024-05-20 10:00")
    assert strict([r, r], GT_RESCHED) == (0, 0)


def test_invalid_arguments():
    assert invalid("banking", "send_money", {"recipient": "bill-december-2023.txt", "amount": 1}) == ["iban:recipient"]
    assert invalid("banking", "send_money", {"recipient": "UK12345678901234567890", "amount": 0}) == ["amount<=0"]
    assert invalid("workspace", "send_email", {"recipients": ["a@b.com"], "cc": None, "bcc": []}) == []
    assert invalid("workspace", "send_email", {"recipients": ["Mark Davies"]}) == ["email:recipients"]
