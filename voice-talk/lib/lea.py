"""Léa Conversations API client with local demo tool stubs + STT/TTS."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from mistralai.client import Mistral, models

AGENT_ID = os.environ.get("MISTRAL_AGENT_ID", "ag_01a0d88dbab7715789cfd424761a1c44")
STUBS_PATH = Path(__file__).with_name("stubs.json")
STUBS: dict[str, Any] = json.loads(STUBS_PATH.read_text())
TTS_MODEL = os.environ.get("MISTRAL_TTS_MODEL", "voxtral-mini-tts-2603")
STT_MODEL = os.environ.get("MISTRAL_STT_MODEL", "voxtral-mini-latest")
DEFAULT_VOICE_ID = os.environ.get(
    "MISTRAL_VOICE_ID", "a3e41ea8-020b-44c0-8d8b-f6cc03524e31"
)

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


def _message_text(entry: Any) -> str | None:
    if type(entry).__name__ == "MessageOutputEntry" or getattr(entry, "type", None) == "message.output":
        content = getattr(entry, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                else:
                    parts.append(getattr(block, "text", None) or str(block))
            return "".join(parts)
    return None



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
) -> dict[str, Any]:
    """Send one user message; resolve tool calls with stubs; return assistant text + meta."""
    client = _client()
    tool_trace: list[dict[str, Any]] = []

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

    for _ in range(max_tool_rounds):
        calls = [o for o in (resp.outputs or []) if _is_function_call(o)]
        texts = [t for t in (_message_text(o) for o in (resp.outputs or [])) if t]
        if not calls:
            return {
                "conversation_id": conversation_id,
                "assistant_text": texts[-1] if texts else "",
                "tool_trace": tool_trace,
            }
        results = []
        for call in calls:
            name = call.name
            stub = stub_for(name, getattr(call, "arguments", None))
            tcid = call.tool_call_id or getattr(call, "id", None)
            tool_trace.append(
                {
                    "name": name,
                    "arguments": getattr(call, "arguments", None),
                    "tool_call_id": tcid,
                    "result": stub,
                }
            )
            results.append(
                models.FunctionResultEntry(
                    tool_call_id=tcid,
                    result=json.dumps(stub, ensure_ascii=False),
                )
            )
        resp = client.beta.conversations.append(
            conversation_id=conversation_id,
            inputs=results,
        )

    texts = [t for t in (_message_text(o) for o in (resp.outputs or [])) if t]
    return {
        "conversation_id": conversation_id,
        "assistant_text": texts[-1] if texts else "(no assistant text after tool rounds)",
        "tool_trace": tool_trace,
    }


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
