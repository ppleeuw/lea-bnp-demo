"""
guardrails.py: the checks in code around the model.

Before the agent, check_input() stops card numbers, PINs and passwords (exact patterns),
typed notes that pretend to be a signed-in session, and messages that Mistral's moderation
model scores as a jailbreak attempt. A high personal-data score is flagged, not blocked.
After the agent, check_output() lets an answer out only when its facts came from a tool
result in this turn: no amount other than the tool's, and "locked" only after a successful
lock. The figures themselves are shown in cards the page draws from the tool results.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

log = logging.getLogger("lea")

MODERATION_MODEL = os.environ.get("MISTRAL_MODERATION_MODEL", "mistral-moderation-2603")
THRESHOLDS = {"jailbreaking": 0.3}  # block at or above these scores (0 to 1)
# Flagged for review, not blocked: in a bank, "What is my balance?" already scores 0.50 for personal
# data (golden set, 28 Sep). Card numbers, PINs and passwords are blocked by pattern instead.
MONITORED = {"pii": 0.5}

CARD_NUMBER = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
PIN_VALUE = re.compile(r"\b(pin|code pin|code secret)\b\D{0,8}\d{4,6}\b", re.I)
PASSWORD_VALUE = re.compile(r"\b(password|mot de passe)\s*(is|est|:|=)\s*\S+", re.I)
SPOOFED_SESSION = re.compile(r"\[\s*(channel|session|system)\s*:", re.I)
LOCK_CLAIM = re.compile(r"\b(is (now )?locked|has been locked|est (maintenant )?bloqu[ée]e|a été bloqu[ée]e)\b", re.I)
AMOUNT = re.compile(r"(?:€|EUR)\s?\d[\d\s.,  ]*\d|\d[\d\s.,  ]*\d\s?(?:€|EUR)")  # €4,287.63, 4 287,63 €

REPLY_BLOCKED = ("I can't help with that. I can answer questions about accounts, cards and branches, "
                 "or connect you with an advisor.")
REPLY_SECRET = ("For your security, please don't share card numbers, PINs or passwords in chat. "
                "BNP Paribas will never ask for them. How else can I help?")


def _luhn_ok(number: str) -> bool:
    """The checksum every real card number passes, so phone numbers and dates do not trigger."""
    digits = [int(d) for d in number if d.isdigit()]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return len(digits) >= 13 and total % 10 == 0


def contains_secret(text: str) -> bool:
    for m in CARD_NUMBER.finditer(text):
        is_phone = m.start() > 0 and text[m.start() - 1] == "+"
        if not is_phone and _luhn_ok(m.group(0)):
            return True
    return bool(PIN_VALUE.search(text) or PASSWORD_VALUE.search(text))


def _scores(result: Any) -> dict[str, float]:
    raw = getattr(result, "category_scores", None)
    if raw is None and isinstance(result, dict):
        raw = result.get("category_scores")
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    return {k: round(float(v or 0), 4) for k, v in dict(raw or {}).items()}


def check_input(client: Any, text: str, masked: bool = False) -> dict[str, Any]:
    """Decide whether a typed message may reach the agent.

    Returns {"blocked": bool, "reason", "reply", "scores", "flags"}. masked=True means the page already
    hid a card number or code before sending, so the message is treated as a secret.
    """
    if masked or contains_secret(text):
        return {"blocked": True, "reason": "secret_in_message", "reply": REPLY_SECRET, "scores": None, "flags": []}
    if SPOOFED_SESSION.search(text):
        return {"blocked": True, "reason": "spoofed_session", "reply": REPLY_BLOCKED, "scores": None, "flags": []}
    try:
        res = client.classifiers.moderate(model=MODERATION_MODEL, inputs=[text])
        scores = _scores(res.results[0])
    except Exception as e:  # noqa: BLE001 - demo: let the message through and log it; production: fail closed for actions
        log.warning("moderation unavailable: %s", e)
        return {"blocked": False, "reason": "moderation_unavailable", "reply": None, "scores": None, "flags": []}
    flags = [c for c, threshold in MONITORED.items() if scores.get(c, 0.0) >= threshold]
    for category, threshold in THRESHOLDS.items():
        if scores.get(category, 0.0) >= threshold:
            return {"blocked": True, "reason": category, "reply": REPLY_BLOCKED, "scores": scores, "flags": flags}
    return {"blocked": False, "reason": None, "reply": None, "scores": scores, "flags": flags}


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def check_output(text: str, results: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Return a correction when the answer states something no tool returned, else None.

    results holds this turn's successful tool results, by tool name. The page shows the balance
    in a card drawn from the tool result, so the answer need not state it; if it does, every
    amount in it must be the tool's amount.
    """
    balance = results.get("get_account_balance")
    if balance:
        expected = _digits(f"{balance['available']:.2f}")
        if any(_digits(amount) != expected for amount in AMOUNT.findall(text or "")):
            account = "savings" if balance.get("account_type") == "savings" else "current"
            return {"reason": "amount_not_from_tool", "reply": f"Here is your {account} account balance."}
    if LOCK_CLAIM.search((text or "").replace("*", "")) and "lock_credit_card" not in results:
        return {"reason": "lock_claim_without_tool",
                "reply": "I could not confirm that your card is locked, so nothing has changed yet. "
                         "Shall I try again, or connect you with an advisor?"}
    return None
