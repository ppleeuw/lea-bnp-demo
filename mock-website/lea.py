"""
lea.py: the conversation with Léa, the Studio agent, through Mistral's Conversations API.

For every customer message, chat() does five things:
  1. guardrails.check_input: patterns and Mistral moderation, before the agent sees anything
  2. conversations.start or .append: the agent answers, searches its library, or asks for a tool
  3. a tool request goes to demo_bank.check, then demo_bank.run with the demo data; a card lock
     is held back and returned to the page as "pending" until the customer confirms in the app,
     which comes back through confirm()
  4. guardrails.check_output on the final answer
  5. metrics.record_turn: tokens, cost, latency, tools and guardrails, for the admin site
The model proposes; this code decides.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from mistralai.client import Mistral, models

import demo_bank
import guardrails
import metrics

AGENT_ID = os.environ.get("MISTRAL_AGENT_ID", "ag_01a0d88dbab7715789cfd424761a1c44")
AGENT_VERSION = os.environ.get("MISTRAL_AGENT_VERSION") or None  # None = latest; pin it in production
MAX_TOOL_ROUNDS = 6

# Added by the server to the first message of a conversation. The customer cannot set this:
# the page only sends a signed-in flag, and typed notes like this are blocked by the guardrails.
SIGNED_IN_NOTE = ("[Channel: authenticated banking web chat. Signed-in customer: Camille Dubois, "
                  "customer_id_suffix=78421, primary card Visa Classic last4=4412. Do not ask for name, "
                  "customer ID, password, PIN or full card number.]\n\n")
GUEST_NOTE = ("[Channel: public website chat. The visitor is NOT signed in. Do not call get_account_balance "
              "or lock_credit_card; for balance or card actions ask them to sign in. Answer general "
              "questions from the library.]\n\n")

_pending: dict[str, dict[str, Any]] = {}  # card locks waiting for confirmation; production: a session store
_client: Mistral | None = None
_client_lock = threading.Lock()


def client() -> Mistral:
    global _client
    with _client_lock:
        if _client is None:
            key = os.environ.get("MISTRAL_API_KEY")
            if not key:
                raise RuntimeError("MISTRAL_API_KEY is not set")
            _client = Mistral(api_key=key)
        return _client


def has_api_key() -> bool:
    return bool(os.environ.get("MISTRAL_API_KEY"))


# ----------------------------------------------------------------------------- one request
class Turn:
    """Everything that happens for one request: for the answer to the page and for the metrics."""

    def __init__(self, kind: str, signed_in: bool, source: str, conversation_id: str | None):
        self.kind, self.signed_in, self.source, self.conversation_id = kind, signed_in, source, conversation_id
        self.trace_id = uuid.uuid4().hex[:12]
        self.started = time.time()
        self.model_calls = self.input_tokens = self.output_tokens = self.moderation_tokens = 0
        self.tools: list[dict[str, Any]] = []        # every tool asked for, with its outcome
        self.results: dict[str, dict[str, Any]] = {}  # successful results, by tool name
        self.sources: list[str] = []
        self.guardrail_in = self.guardrail_out = self.decision = self.error = None
        self.scores: dict[str, float] | None = None
        self.flags: list[str] = []
        self.pending: dict[str, Any] | None = None

    def send(self, inputs: Any) -> Any:
        """One call to the agent: start the conversation, or append to it."""
        if self.conversation_id:
            resp = client().beta.conversations.append(conversation_id=self.conversation_id, inputs=inputs)
        else:
            extra = {"agent_version": AGENT_VERSION} if AGENT_VERSION else {}
            resp = client().beta.conversations.start(agent_id=AGENT_ID, inputs=inputs, **extra)
            self.conversation_id = resp.conversation_id
        self.model_calls += 1
        usage = getattr(resp, "usage", None)
        self.input_tokens += (getattr(usage, "prompt_tokens", 0) or 0) + (getattr(usage, "connector_tokens", 0) or 0)
        self.output_tokens += getattr(usage, "completion_tokens", 0) or 0
        for entry in resp.outputs or []:
            if _is_tool_execution(entry):  # a built-in tool Mistral ran itself: the library search
                name = getattr(entry, "name", "")
                self.tools.append({"name": str(getattr(name, "value", name)), "ok": True, "builtin": True})
            for title in _sources(entry):
                if title not in self.sources:
                    self.sources.append(title)
        return resp

    def tool(self, name: str, args: dict[str, Any], result: dict[str, Any]) -> None:
        self.tools.append({"name": name, "args": args, "ok": bool(result.get("ok")),
                           "error": result.get("error"), "status": result.get("status")})
        if result.get("ok"):
            self.results[name] = result

    def finish(self, answer: str) -> dict[str, Any]:
        latency_ms = int((time.time() - self.started) * 1000)
        cost = metrics.cost_usd(self.input_tokens, self.output_tokens, self.moderation_tokens)
        guardrail = ({"stage": "input", "reason": self.guardrail_in} if self.guardrail_in else
                     {"stage": "output", "reason": self.guardrail_out} if self.guardrail_out else None)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "trace_id": self.trace_id,
            "source": self.source, "kind": self.kind, "conversation_id": self.conversation_id,
            "signed_in": self.signed_in, "model_calls": self.model_calls, "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens, "moderation_tokens": self.moderation_tokens, "cost_usd": cost,
            "latency_ms": latency_ms, "sources": self.sources, "scores": self.scores, "flags": self.flags,
            "guardrail_in": self.guardrail_in, "guardrail_out": self.guardrail_out,
            "moderation_unavailable": self.error == "moderation_unavailable",
            "pending": self.pending and self.pending["tool"], "decision": self.decision,
            "tools": [{k: t.get(k) for k in ("name", "ok", "error", "status")} for t in self.tools],
        }
        metrics.record_turn(record)
        print(json.dumps({"event": "lea_turn", **record}, ensure_ascii=False), flush=True)  # Render Logs
        return {
            "conversation_id": self.conversation_id, "answer": answer, "tools": self.tools,
            "results": self.results, "pending": self.pending, "sources": self.sources, "guardrail": guardrail,
            "trace_id": self.trace_id, "latency_ms": latency_ms,
            "usage": {"model_calls": self.model_calls, "input_tokens": self.input_tokens,
                      "output_tokens": self.output_tokens, "cost_usd": cost},
        }


# ----------------------------------------------------------------------------- entry points
def chat(text: str, conversation_id: str | None = None, signed_in: bool = False,
         masked: bool = False, source: str = "live") -> dict[str, Any]:
    """One customer message in, one answer out (or a card lock waiting for confirmation)."""
    turn = Turn("chat", signed_in, source, conversation_id)
    check = guardrails.check_input(client(), text, masked)
    turn.scores, turn.flags = check["scores"], check["flags"]
    if check["scores"] is not None:
        turn.moderation_tokens = len(text) // 4 + 1
    if check["reason"] == "moderation_unavailable":
        turn.error = "moderation_unavailable"
    if check["blocked"]:
        turn.guardrail_in = check["reason"]
        return turn.finish(check["reply"])

    if conversation_id in _pending:  # the customer moved on without tapping: nothing is locked
        held = _pending.pop(conversation_id)
        result = {"ok": False, "status": "not_confirmed",
                  "message": "The customer did not confirm in the app. Nothing was changed."}
        turn.tool(held["name"], held["args"], result)
        turn.decision = "not_confirmed"
        turn.send(_with_held_result(held, result))

    note = "" if conversation_id else (SIGNED_IN_NOTE if signed_in else GUEST_NOTE)
    return _tool_loop(turn, turn.send(note + text))


def confirm(conversation_id: str, approve: bool, source: str = "live") -> dict[str, Any]:
    """The customer tapped "Lock card" (and approved in the app) or "Keep card active"."""
    held = _pending.pop(conversation_id, None)
    turn = Turn("confirm", bool(held and held["signed_in"]), source, conversation_id)
    if not held:
        turn.error = "nothing_pending"
        return turn.finish("There is nothing waiting for your confirmation.")
    turn.decision = "allowed" if approve else "denied"
    if approve:
        key = f"{conversation_id}:{held['args'].get('card_last4')}"  # same card, same conversation: same receipt
        result = demo_bank.run(held["name"], held["args"], idempotency_key=key)
    else:
        result = {"ok": False, "status": "cancelled",
                  "message": "The customer chose to keep the card active. Nothing was changed."}
    turn.tool(held["name"], held["args"], result)
    return _tool_loop(turn, turn.send(_with_held_result(held, result)))


# ----------------------------------------------------------------------------- the tool loop
def _tool_loop(turn: Turn, resp: Any) -> dict[str, Any]:
    """Until the agent answers: run the tools it asks for, or hold a card lock for confirmation."""
    for _ in range(MAX_TOOL_ROUNDS):
        outputs = resp.outputs or []
        calls = [o for o in outputs if _is_function_call(o)]
        if not calls:
            return _checked_answer(turn, _text(outputs))
        results, held = [], None
        for call in calls:
            args = _args(call.arguments)
            refused = demo_bank.check(call.name, args, turn.signed_in)
            if refused is None and call.name in demo_bank.WRITE_TOOLS and held is None:
                held = {"tool_call_id": call.tool_call_id, "name": call.name, "args": args, "signed_in": turn.signed_in}
                continue
            result = refused or demo_bank.run(call.name, args, idempotency_key=f"{turn.conversation_id}:{call.tool_call_id}")
            turn.tool(call.name, args, result)
            results.append(_result_entry(call.tool_call_id, result))
        if held:
            # A write waits for the customer's own confirmation in the app, whatever the model says.
            held["other_results"] = results
            _pending[turn.conversation_id] = held
            turn.tools.append({"name": held["name"], "args": held["args"], "ok": False, "error": None,
                               "status": "pending_confirmation"})
            turn.pending = {"tool": held["name"], "reason": held["args"].get("reason"),
                            **demo_bank.card(str(held["args"].get("card_last4")))}
            return turn.finish(_text(outputs))
        resp = turn.send(results)
    return _checked_answer(turn, "Sorry, I could not finish that. Would you like to talk to an advisor?")


def _checked_answer(turn: Turn, text: str) -> dict[str, Any]:
    correction = guardrails.check_output(text, turn.results)
    if correction:
        turn.guardrail_out = correction["reason"]
        text = correction["reply"]
    return turn.finish(text)


# ----------------------------------------------------------------------------- reading Mistral's replies
def _result_entry(tool_call_id: str, result: dict[str, Any]) -> Any:
    return models.FunctionResultEntry(tool_call_id=tool_call_id, result=json.dumps(result, ensure_ascii=False))


def _with_held_result(held: dict[str, Any], result: dict[str, Any]) -> list[Any]:
    return held["other_results"] + [_result_entry(held["tool_call_id"], result)]


def _is_function_call(entry: Any) -> bool:
    return getattr(entry, "type", None) == "function.call" or type(entry).__name__ == "FunctionCallEntry"


def _is_tool_execution(entry: Any) -> bool:
    return getattr(entry, "type", None) == "tool.execution" or type(entry).__name__ == "ToolExecutionEntry"


def _chunks(entry: Any) -> list[Any]:
    if getattr(entry, "type", None) != "message.output" and type(entry).__name__ != "MessageOutputEntry":
        return []
    content = getattr(entry, "content", None)
    return [content] if isinstance(content, str) else list(content or [])


def _get(chunk: Any, key: str) -> Any:
    return chunk.get(key) if isinstance(chunk, dict) else getattr(chunk, key, None)


def _text(outputs: list[Any]) -> str:
    """The last answer's text. Source references (tool_reference chunks) are not text."""
    texts = []
    for entry in outputs:
        parts = [c if isinstance(c, str) else (_get(c, "text") or "") for c in _chunks(entry)]
        if any(parts):
            texts.append("".join(parts))
    return texts[-1] if texts else ""


def _sources(entry: Any) -> list[str]:
    """Titles of the library documents an answer cites."""
    return [_get(c, "title") for c in _chunks(entry)
            if not isinstance(c, str) and _get(c, "type") == "tool_reference" and _get(c, "title")]


def _args(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    try:
        return json.loads(arguments) if arguments else {}
    except (TypeError, json.JSONDecodeError):
        return {}
