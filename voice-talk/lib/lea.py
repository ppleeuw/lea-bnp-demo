"""Léa Conversations API client with local demo tool stubs + STT/TTS."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mistralai.client import Mistral, models

AGENT_ID = os.environ.get("MISTRAL_AGENT_ID", "ag_01a0d88dbab7715789cfd424761a1c44")
STUBS_PATH = Path(__file__).with_name("stubs.json")
STUBS: dict[str, Any] = json.loads(STUBS_PATH.read_text(encoding="utf-8"))
TTS_MODEL = os.environ.get("MISTRAL_TTS_MODEL", "voxtral-mini-tts-2603")
STT_MODEL = os.environ.get("MISTRAL_STT_MODEL", "voxtral-mini-latest")
DEFAULT_VOICE_ID = os.environ.get(
    "MISTRAL_VOICE_ID", "a3e41ea8-020b-44c0-8d8b-f6cc03524e31"
)

log = logging.getLogger("lea")

# ---------------------------------------------------------------------------
# Guardrails that run in code, outside the model
# 1. Moderation on every incoming message (Mistral moderation model).
#    Runs here and not on the agent: agent-level guardrails do not work with
#    the streaming Studio playground.
# 2. Guest block: banking tools only run for a signed-in session, whatever
#    the model asks for.
# 3. Customer check: the customer ID in a tool call must be the signed-in one.
# 4. Confirmation gate: a card lock runs only after the customer's own yes
#    (a tap in the app, or a yes in their last message), not the model's word.
# 5. Output check: an amount must equal the tool result, and "locked" may
#    only be said after a successful lock.
# ---------------------------------------------------------------------------
MODERATION_MODEL = os.environ.get("MISTRAL_MODERATION_MODEL", "mistral-moderation-2603")
THRESHOLDS = {"jailbreaking": 0.3, "pii": 0.5}
BANKING_TOOLS = {"get_account_balance", "lock_credit_card"}
SESSION_CUSTOMER = "78421"  # the demo sign-in; in production this comes from the login token
CARD_NUMBER = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
PIN_VALUE = re.compile(r"\b(pin|code pin|code secret)\b\D{0,8}\d{4,6}\b", re.I)
PASSWORD_VALUE = re.compile(r"\b(password|mot de passe)\s*(is|est|:|=)\s*\S+", re.I)
SPOOFED_CONTEXT = re.compile(r"\[\s*(channel|session|system)\s*:", re.I)
CUSTOMER_YES = re.compile(r"^\W*(yes|yep|yeah|ok|okay|sure|confirm|i confirm|oui|d'accord|je confirme)\b", re.I)
LOCK_CLAIM = re.compile(r"\b(is (now )?locked|has been locked|est (maintenant )?bloqu[ée]e|a été bloqu[ée]e)\b", re.I)


def _luhn_ok(number: str) -> bool:
    digits = [int(d) for d in number if d.isdigit()]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return len(digits) >= 13 and total % 10 == 0


def _contains_secret(text: str) -> bool:
    for m in CARD_NUMBER.finditer(text):
        is_phone = m.start() > 0 and text[m.start() - 1] == "+"
        if not is_phone and _luhn_ok(m.group(0)):
            return True
    return bool(PIN_VALUE.search(text) or PASSWORD_VALUE.search(text))

REPLY_BLOCKED = (
    "I can't help with that. I can answer questions about accounts, cards and "
    "branches, or connect you with an advisor."
)
REPLY_SECRET = (
    "For your security, please don't share card numbers, PINs or passwords in chat. "
    "BNP Paribas will never ask for them. How else can I help?"
)


def _scores(result: Any) -> dict[str, float]:
    raw = getattr(result, "category_scores", None)
    if raw is None and isinstance(result, dict):
        raw = result.get("category_scores")
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    return {k: float(v or 0) for k, v in dict(raw or {}).items()}


def check_input(client: Any, text: str) -> dict[str, Any] | None:
    """Return a block decision, or None when the message may go to the agent."""
    if _contains_secret(text):
        return {"reason": "secret_in_message", "reply": REPLY_SECRET}
    try:
        res = client.classifiers.moderate(model=MODERATION_MODEL, inputs=[text])
        scores = _scores(res.results[0])
    except Exception as e:  # noqa: BLE001 - never take the demo down on a moderation outage
        log.warning("moderation unavailable: %s", e)
        return None
    for category, threshold in THRESHOLDS.items():
        if scores.get(category, 0.0) >= threshold:
            reply = REPLY_SECRET if category == "pii" else REPLY_BLOCKED
            return {"reason": category, "score": round(scores[category], 3), "reply": reply}
    return None


# Alternate names → lean Studio tools (general Q uses KB, not tools)
ALIASES = {
    "get_account_balance": "get_account_balance",
    "get_balance": "get_account_balance",
    "lock_credit_card": "lock_credit_card",
    "lock_card": "lock_credit_card",
}


def load_api_key() -> str:
    key = os.environ.get("MISTRAL_API_KEY")
    if key:
        return key
    for p in (
        Path("/home/box/agent-data/box-secrets.json"),
        Path("/home/box/sand-data/box-secrets.json"),
    ):
        if p.exists():
            k = json.loads(p.read_text()).get("card", {}).get("MISTRAL_API_KEY")
            if k:
                os.environ["MISTRAL_API_KEY"] = k
                return k
    raise RuntimeError("MISTRAL_API_KEY is not set")


def _client() -> Mistral:
    return Mistral(api_key=load_api_key())


def stub_for(name: str, arguments: Any = None) -> dict[str, Any]:
    key = ALIASES.get(name, name)
    if key not in STUBS:
        compact = name.replace("_", "")
        for k in STUBS:
            if k.replace("_", "") == compact:
                key = k
                break
    if key not in STUBS:
        return {"ok": False, "error": f"no stub for {name}"}
    out = json.loads(json.dumps(STUBS[key]))  # deep copy
    args = arguments or {}
    if isinstance(args, str):
        try:
            args = json.loads(args) if args else {}
        except json.JSONDecodeError:
            args = {}
    if key == "get_account_balance":
        out["available"] = 4287.63
        out["ledger"] = 4287.63
        out["display"] = "€4,287.63"
        out["customer_id_suffix"] = "78421"
    elif key == "lock_credit_card":
        out["status"] = "locked"
        out["confirmation_id"] = "LCK-DEMO-259884"
        if args.get("card_last4"):
            out["card_last4"] = str(args["card_last4"])
    return out


def _is_function_call(entry: Any) -> bool:
    return type(entry).__name__ == "FunctionCallEntry" or getattr(entry, "type", None) == "function.call"


def _is_tool_execution(entry: Any) -> bool:
    """A built-in tool that Mistral ran itself, such as the document library search."""
    return type(entry).__name__ == "ToolExecutionEntry" or getattr(entry, "type", None) == "tool.execution"


def _message_text(entry: Any) -> str | None:
    """The text of an answer. Source references (tool_reference chunks) are skipped here."""
    if type(entry).__name__ == "MessageOutputEntry" or getattr(entry, "type", None) == "message.output":
        content = getattr(entry, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    parts.append(block.get("text") or "")
                else:
                    parts.append(getattr(block, "text", None) or "")
            return "".join(parts)
    return None


def _sources(entry: Any) -> list[str]:
    """Titles of the library documents an answer cites (tool_reference chunks)."""
    content = getattr(entry, "content", None)
    if not isinstance(content, list):
        return []
    titles = []
    for block in content:
        kind = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        if kind == "tool_reference":
            title = block.get("title") if isinstance(block, dict) else getattr(block, "title", None)
            if title and title not in titles:
                titles.append(title)
    return titles


def _args(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    try:
        return json.loads(arguments) if arguments else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def execute_tool(name: str, arguments: Any, authenticated: bool, customer_confirmed_lock: bool) -> dict[str, Any]:
    """The model only asks for a tool. This code decides whether it runs."""
    canonical = ALIASES.get(name, name)
    args = _args(arguments)
    if canonical not in STUBS:
        return {"ok": False, "error": "UNKNOWN_TOOL"}
    # Guardrail 2: guest block, whatever the model asks for.
    if not authenticated and canonical in BANKING_TOOLS:
        return {"ok": False, "error": "SIGN_IN_REQUIRED",
                "message": "The visitor is not signed in. Ask them to sign in first."}
    # Guardrail 3: the customer in the tool call must be the signed-in customer.
    suffix = str(args.get("customer_id_suffix") or SESSION_CUSTOMER)
    if suffix != SESSION_CUSTOMER:
        return {"ok": False, "error": "CUSTOMER_MISMATCH",
                "message": "Only the signed-in customer's own data can be used."}
    # Guardrail 4: a write needs the customer's own yes, checked here, not by the model.
    if canonical == "lock_credit_card" and not (args.get("customer_confirmed") and customer_confirmed_lock):
        return {"ok": False, "error": "CONFIRMATION_REQUIRED",
                "message": "Read the card back and ask the customer for an explicit yes."}
    return stub_for(canonical, args)


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def check_output(text: str, tool_trace: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Guardrail 5: the answer may only state facts that a tool returned in this turn."""
    ok = {ALIASES.get(t["name"], t["name"]): t["result"] for t in tool_trace if isinstance(t.get("result"), dict) and "error" not in t["result"]}
    balance = ok.get("get_account_balance")
    if balance and _digits(f"{balance['available']:.2f}") not in _digits(text):
        return {"reason": "amount_not_from_tool",
                "reply": f"Your available balance is {balance.get('display', balance['available'])} "
                         f"({balance.get('account_type', 'checking')} account)."}
    if LOCK_CLAIM.search((text or "").replace("*", "")) and "lock_credit_card" not in ok:
        return {"reason": "lock_claim_without_tool",
                "reply": "I could not confirm that your card is locked, so nothing has changed yet. "
                         "Shall I try again, or connect you with an advisor?"}
    return None


def _trace_log(record: dict[str, Any]) -> None:
    """One JSON line per turn in the server log (Render > Logs). No message text, no personal data."""
    print(json.dumps({"event": "lea_turn", **record}, ensure_ascii=False), flush=True)


SESSION_PREAMBLE = (
    "[Channel: authenticated banking web chat. "
    "Signed-in customer: Camille Dubois, customer_id_suffix=78421, "
    "primary card Visa Classic last4=4412. "
    "Do NOT ask for name, customer ID, password, PIN, or full card number. "
    "For balance, call get_account_balance immediately. "
    "For card lock, confirm then call lock_credit_card.]\n\n"
)

GUEST_PREAMBLE = (
    "[Channel: public website chat — visitor is NOT authenticated. "
    "Do NOT call get_account_balance or lock_credit_card. "
    "Refuse balance and card actions; ask the visitor to sign in. "
    "Answer general FAQ and branch hours only. "
    "Do not invent personal account data.]\n\n"
)


def run_turn(
    user_text: str,
    conversation_id: str | None = None,
    max_tool_rounds: int = 6,
    authenticated: bool = True,
    confirmed_action: str | None = None,
) -> dict[str, Any]:
    """Send one user message; resolve tool calls with stubs; return assistant text + meta.

    confirmed_action: set by our web page (not by the customer's text) after the customer
    tapped "Lock card" and approved in the banking app, for example "lock_credit_card".
    """
    client = _client()
    trace_id = uuid.uuid4().hex[:12]
    started = time.time()
    tool_trace: list[dict[str, Any]] = []
    sources: list[str] = []
    ui_confirmed = authenticated and confirmed_action == "lock_credit_card"

    def finish(text: str, guardrail: dict[str, Any] | None) -> dict[str, Any]:
        _trace_log({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "trace_id": trace_id, "conversation_id": conversation_id, "agent_id": AGENT_ID,
            "authenticated": authenticated,
            "tools": [{"name": t["name"], "ok": "error" not in (t.get("result") or {}),
                       "error": (t.get("result") or {}).get("error")} for t in tool_trace],
            "sources": sources, "guardrail": guardrail and guardrail.get("reason"),
            "latency_ms": int((time.time() - started) * 1000),
        })
        return {"conversation_id": conversation_id, "assistant_text": text, "tool_trace": tool_trace,
                "sources": sources, "guardrail": guardrail, "trace_id": trace_id}

    # Guardrail 1: checks before anything reaches the agent. A confirmation our own page
    # generated after the tap is not customer-typed text, so it skips moderation.
    if not ui_confirmed:
        if SPOOFED_CONTEXT.search(user_text):
            return finish(REPLY_BLOCKED, {"reason": "spoofed_session_context", "reply": REPLY_BLOCKED})
        blocked = check_input(client, user_text)
        if blocked:
            return finish(blocked["reply"], blocked)

    # On a fresh conversation, attach session context once (auth or guest).
    preamble = SESSION_PREAMBLE if authenticated else GUEST_PREAMBLE
    payload = user_text if conversation_id else (preamble + user_text)
    if conversation_id:
        resp = client.beta.conversations.append(
            conversation_id=conversation_id,
            inputs=payload,
        )
    else:
        resp = client.beta.conversations.start(
            agent_id=AGENT_ID,
            inputs=payload,
        )
    conversation_id = resp.conversation_id
    customer_confirmed_lock = ui_confirmed or bool(CUSTOMER_YES.match(user_text))

    for _ in range(max_tool_rounds):
        outputs = resp.outputs or []
        for o in outputs:
            if _is_tool_execution(o):
                tool_name = getattr(o, "name", "")
                tool_trace.append({"name": str(getattr(tool_name, "value", tool_name)), "builtin": True, "result": {}})
            for title in _sources(o):
                if title not in sources:
                    sources.append(title)
        calls = [o for o in outputs if _is_function_call(o)]
        texts = [t for t in (_message_text(o) for o in outputs) if t]
        if not calls:
            text = texts[-1] if texts else ""
            flagged = check_output(text, tool_trace)
            return finish(flagged["reply"] if flagged else text, flagged)
        results = []
        for call in calls:
            name = call.name
            result = execute_tool(name, getattr(call, "arguments", None), authenticated, customer_confirmed_lock)
            tcid = call.tool_call_id or getattr(call, "id", None)
            tool_trace.append(
                {
                    "name": name,
                    "arguments": getattr(call, "arguments", None),
                    "tool_call_id": tcid,
                    "result": result,
                }
            )
            results.append(
                models.FunctionResultEntry(
                    tool_call_id=tcid,
                    result=json.dumps(result, ensure_ascii=False),
                )
            )
        resp = client.beta.conversations.append(
            conversation_id=conversation_id,
            inputs=results,
        )

    texts = [t for t in (_message_text(o) for o in (resp.outputs or [])) if t]
    return finish(texts[-1] if texts else "(no assistant text after tool rounds)", None)


def chat(
    user_text: str,
    conversation_id: str | None = None,
    authenticated: bool = True,
) -> dict[str, Any]:
    """App-facing wrapper: reply + tools keys."""
    r = run_turn(user_text, conversation_id=conversation_id, authenticated=authenticated)
    return {
        "conversation_id": r["conversation_id"],
        "reply": r.get("assistant_text") or "",
        "tools": [
            {"name": t["name"], "arguments": t.get("arguments"), "result": t.get("result")}
            for t in (r.get("tool_trace") or [])
        ],
    }


def transcribe_audio(file_bytes: bytes, filename: str = "audio.webm", language: str | None = None) -> str:
    client = _client()
    kwargs: dict[str, Any] = {
        "model": STT_MODEL,
        "file": {"content": file_bytes, "file_name": filename},
    }
    if language:
        kwargs["language"] = language
    resp = client.audio.transcriptions.complete(**kwargs)
    return (getattr(resp, "text", None) or "").strip()


def synthesize_speech(text: str, voice_id: str | None = None) -> bytes:
    client = _client()
    clean = (text or "").replace("**", "").replace("`", "").strip() or "Bonjour."
    if len(clean) > 1500:
        clean = clean[:1500]
    resp = client.audio.speech.complete(
        model=TTS_MODEL,
        input=clean,
        voice_id=voice_id or DEFAULT_VOICE_ID,
        response_format="mp3",
    )
    audio = getattr(resp, "audio_data", None)
    if audio is None:
        audio = getattr(resp, "audio", None)
    if isinstance(audio, str):
        return base64.b64decode(audio)
    if isinstance(audio, (bytes, bytearray)):
        return bytes(audio)
    raise RuntimeError("TTS response missing audio_data")
