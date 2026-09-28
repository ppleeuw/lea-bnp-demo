"""
demo_bank.py: the stand-in for BNP's API gateway and core banking in this demo.

Nothing here calls a bank. When the model asks for a tool, lea.py hands the request to
check() and, if allowed, to run(), which answers with the standard data in demo_bank.json.
In production each branch of run() becomes one call to BNP's API gateway, with the
customer taken from the login token instead of from the tool call.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

DATA: dict[str, Any] = json.loads(Path(__file__).with_name("demo_bank.json").read_text(encoding="utf-8"))
SIGNED_IN_CUSTOMER = DATA["customer"]["customer_id_suffix"]

READ_TOOLS = {"get_account_balance"}
WRITE_TOOLS = {"lock_credit_card"}          # run only after the customer confirms in the app
BANKING_TOOLS = READ_TOOLS | WRITE_TOOLS    # need a signed-in session
OPEN_TOOLS = {"transfer_to_advisor"}        # allowed for guests too


def refusal(error: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": error, "message": message}


def check(name: str, args: dict[str, Any], signed_in: bool) -> dict[str, Any] | None:
    """Return a refusal, or None when the tool may run. The model only asks; this decides."""
    if name not in BANKING_TOOLS | OPEN_TOOLS:
        return refusal("UNKNOWN_TOOL", "This tool does not exist.")
    if name in BANKING_TOOLS:
        if not signed_in:
            return refusal("SIGN_IN_REQUIRED", "The visitor is not signed in. Ask them to sign in first.")
        if str(args.get("customer_id_suffix") or SIGNED_IN_CUSTOMER) != SIGNED_IN_CUSTOMER:
            return refusal("CUSTOMER_MISMATCH", "Only the signed-in customer's own data can be used.")
    if name == "get_account_balance" and (args.get("account_type") or "checking") not in DATA["accounts"]:
        return refusal("ACCOUNT_NOT_FOUND", "This customer has no such account.")
    if name == "lock_credit_card" and str(args.get("card_last4")) not in DATA["cards"]:
        return refusal("CARD_NOT_FOUND", "No card with these last four digits belongs to this customer.")
    return None


def card(last4: str) -> dict[str, Any]:
    """What the confirmation card in the page shows about the card to lock."""
    return {"card_last4": last4, "card_type": DATA["cards"].get(last4, {}).get("type", "Card")}


def run(name: str, args: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
    """Execute a tool that passed check() and, for a write, the customer's confirmation."""
    if name == "get_account_balance":
        account_type = args.get("account_type") or "checking"
        account = DATA["accounts"][account_type]
        return {"ok": True, "account_type": account_type, "available": account["available"],
                "currency": account["currency"], "display": f"€{account['available']:,.2f}",
                "account_masked": account["account_masked"], "as_of": DATA["as_of"]}
    if name == "lock_credit_card":
        # The same key always gives the same receipt, so a retry never locks twice.
        reference = "LCK-DEMO-" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:6].upper()
        return {"ok": True, "status": "locked", **card(str(args["card_last4"])),
                "reason": args.get("reason", "customer_request"), "confirmation_id": reference}
    if name == "transfer_to_advisor":
        return {"ok": True, "status": "queued", **DATA["advisor_queue"]}
    return refusal("UNKNOWN_TOOL", "This tool does not exist.")
