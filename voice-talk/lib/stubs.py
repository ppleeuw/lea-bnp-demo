"""Canonical demo stubs for Studio Léa tools.

Lean demo tools: get_account_balance, lock_credit_card.
Studio suffix param: customer_id_last4 (demo 78421).
Local tools.json may use customer_id_suffix — both accepted.
Legacy aliases for removed tools still resolve if called.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

_PACK = Path(__file__).resolve().parents[2]
_STUB_PATH = _PACK / "tool-stub-responses.json"

CANONICAL = {
    "get_account_balance",
    "lock_credit_card",
}
# Soft aliases kept so old Playground/Talk sessions don't crash
LEGACY = {
    "list_recent_transactions",
    "get_branch_hours",
}


def load_stubs() -> dict:
    return json.loads(_STUB_PATH.read_text(encoding="utf-8"))


def _parse_args(arguments: dict | str | None) -> dict:
    if arguments is None:
        return {}
    if isinstance(arguments, str):
        try:
            return json.loads(arguments) if arguments else {}
        except json.JSONDecodeError:
            return {}
    return dict(arguments)


def resolve_stub(name: str, arguments: dict | str | None = None) -> dict:
    """Return canonical stub JSON. Never returns TODO placeholders."""
    stubs = load_stubs()
    key = name if name in stubs else None
    if key is None:
        for k in stubs:
            if k.replace("_", "") == name.replace("_", ""):
                key = k
                break
    if key is None:
        return {
            "error": f"unknown_tool:{name}",
            "hint": "Use get_account_balance | lock_credit_card (general questions use KB)",
        }

    out = copy.deepcopy(stubs[key])
    args = _parse_args(arguments)

    if key == "get_account_balance":
        out["available"] = 4287.63
        out["ledger"] = 4287.63
        out["display"] = "€4,287.63"
        out["customer_id_suffix"] = "78421"
        suffix = args.get("customer_id_last4") or args.get("customer_id_suffix")
        if suffix and str(suffix) != "78421":
            out["note"] = f"demo stub still returns Camille balance; requested suffix={suffix}"
    elif key == "lock_credit_card":
        out["status"] = "locked"
        out["confirmation_id"] = "LCK-DEMO-259884"
        out["card_last4"] = str(args.get("card_last4") or out.get("card_last4") or "4412")
    return out
