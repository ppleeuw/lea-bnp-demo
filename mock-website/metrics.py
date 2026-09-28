"""
metrics.py: what the evals console shows.

Two kinds of record:
  - a turn record per request (a chat message or a confirmation): tokens, cost, latency, tools,
    guardrails. No message text. Kept in memory and in LEA_DATA_DIR (default: a temp folder).
  - a trace per request: the same plus the question, the answer and every step. Memory only,
    the last 50, never written to disk or to the log.
Golden-set runs are kept in memory and in LEA_DATA_DIR; the committed runs in eval/results/
are loaded as history. On Render's free plan the temp folder is wiped when the service sleeps.
"""
from __future__ import annotations

import json
import os
import statistics
import tempfile
import threading
from collections import Counter, deque
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.environ.get("LEA_DATA_DIR", Path(tempfile.gettempdir()) / "lea-data"))
COMMITTED_RUNS = Path(__file__).resolve().parents[1] / "eval" / "results"
TURNS_FILE = DATA_DIR / "turns.jsonl"

# List prices in US dollars, from https://mistral.ai/pricing/api, read on 28 September 2026.
PRICE_SOURCE = "https://mistral.ai/pricing/api"
PRICES_CHECKED_ON = "2026-09-28"
PRICE_INPUT = 1.50 / 1_000_000       # Mistral Medium 3.5, per input token
PRICE_OUTPUT = 7.50 / 1_000_000      # Mistral Medium 3.5, per output token
PRICE_LIBRARY_CALL = 0.01            # per library search call
PRICES = [
    {"item": "Mistral Medium 3.5", "id": "mistral-medium-latest", "unit": "per million tokens",
     "input": 1.50, "output": 7.50, "used_for": "the agent: every model call"},
    {"item": "Mistral Moderation 2", "id": "mistral-moderation-2603", "unit": "per million tokens",
     "input": 0.0, "output": 0.0, "used_for": "the input check on every typed message (free)"},
    {"item": "Library search", "id": "document_library", "unit": "per call",
     "input": 0.01, "output": None, "used_for": "each search the agent runs in the library"},
    {"item": "Library indexing", "id": "libraries", "unit": "per million tokens",
     "input": 1.00, "output": None, "used_for": "once, when kb-retail-support.md was uploaded"},
]

_lock = threading.Lock()
TURNS: deque[dict[str, Any]] = deque(maxlen=5000)
TRACES: deque[dict[str, Any]] = deque(maxlen=50)
RUNS: dict[str, dict[str, Any]] = {}


def cost_usd(input_tokens: int, output_tokens: int, library_calls: int = 0) -> float:
    return round(input_tokens * PRICE_INPUT + output_tokens * PRICE_OUTPUT + library_calls * PRICE_LIBRARY_CALL, 6)


def record_turn(turn: dict[str, Any]) -> None:
    with _lock:
        TURNS.append(turn)
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with TURNS_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(turn, ensure_ascii=False) + "\n")
        except OSError:
            pass


def record_trace(trace: dict[str, Any]) -> None:
    with _lock:
        TRACES.append(trace)


def save_run(run: dict[str, Any]) -> None:
    with _lock:
        RUNS[run["id"]] = run
        if run.get("status") == "done":
            try:
                (DATA_DIR / "runs").mkdir(parents=True, exist_ok=True)
                (DATA_DIR / "runs" / f"{run['id']}.json").write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass


def _load() -> None:
    if TURNS_FILE.exists():
        for line in TURNS_FILE.read_text(encoding="utf-8").splitlines()[-TURNS.maxlen:]:
            try:
                TURNS.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    for folder, committed in ((COMMITTED_RUNS, True), (DATA_DIR / "runs", False)):
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            try:
                run = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            run["committed"] = committed
            RUNS.setdefault(run["id"], run)


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


def use_case(turn: dict[str, Any]) -> str:
    """Which of the demo's use cases a turn belongs to, from what happened in it."""
    names = {t["name"] for t in turn.get("tools", [])}
    if turn.get("guardrail_in"):
        return "Blocked before the agent"
    if "lock_credit_card" in names:
        return "Card lock"
    if "get_account_balance" in names:
        return "Balance inquiry"
    if "transfer_to_advisor" in names:
        return "Advisor handoff"
    if "document_library" in names:
        return "General question (library)"
    return "Other answer (no tool)"


def summary(source: str | None = None) -> dict[str, Any]:
    """The numbers on the console, for all turns or for one source ('live' or 'eval')."""
    with _lock:
        turns = [t for t in TURNS if source in (None, t.get("source"))]
    latencies = [t["latency_ms"] for t in turns]
    costs = [t["cost_usd"] for t in turns]
    by_conversation: dict[str, float] = {}
    for t in turns:
        if t.get("conversation_id"):
            by_conversation[t["conversation_id"]] = by_conversation.get(t["conversation_id"], 0) + t["cost_usd"]
    groups: dict[str, list[dict[str, Any]]] = {}
    for t in turns:
        groups.setdefault(use_case(t), []).append(t)
    per_use_case = [{
        "use_case": name, "turns": len(items),
        "model_calls": round(statistics.mean(t["model_calls"] for t in items), 1),
        "library_calls": round(statistics.mean(t.get("library_calls", 0) for t in items), 1),
        "cost_usd": round(statistics.mean(t["cost_usd"] for t in items), 5),
        "latency_ms": round(statistics.mean(t["latency_ms"] for t in items)),
    } for name, items in sorted(groups.items())]
    reached_agent = [t for t in turns if not t.get("guardrail_in")]
    return {
        "turns": len(turns),
        "conversations": len(by_conversation),
        "reached_agent": len(reached_agent),
        "latency_ms": {"p50": _pct(latencies, 50), "p95": _pct(latencies, 95),
                       "mean": round(statistics.mean(latencies)) if latencies else None},
        "cost_usd": {"total": round(sum(costs), 4),
                     "per_turn": round(statistics.mean(costs), 5) if costs else None,
                     "per_conversation": round(statistics.mean(by_conversation.values()), 5) if by_conversation else None},
        "tokens": {"input": sum(t["input_tokens"] for t in turns), "output": sum(t["output_tokens"] for t in turns),
                   "model_calls": sum(t["model_calls"] for t in turns),
                   "library_calls": sum(t.get("library_calls", 0) for t in turns)},
        "guardrails": {
            "blocked_before_agent": dict(Counter(t["guardrail_in"] for t in turns if t.get("guardrail_in"))),
            "corrected_after_agent": dict(Counter(t["guardrail_out"] for t in turns if t.get("guardrail_out"))),
            "flagged_not_blocked": dict(Counter(f for t in turns for f in (t.get("flags") or []))),
            "tool_refusals": dict(Counter(x["error"] for t in turns for x in t["tools"] if x.get("error"))),
            "confirmations": dict(Counter(t["decision"] for t in turns if t.get("decision"))),
            "passed_input_check": len(reached_agent)},
        "tools": dict(Counter(x["name"] for t in turns for x in t["tools"] if x.get("ok"))),
        "per_use_case": per_use_case,
        "library_share": round(sum(1 for t in reached_agent if "document_library" in [x["name"] for x in t["tools"]])
                               / len(reached_agent), 3) if reached_agent else None,
        "moderation_unavailable": sum(1 for t in turns if t.get("moderation_unavailable")),
    }


def recent_turns(limit: int = 60) -> list[dict[str, Any]]:
    with _lock:
        return list(TURNS)[-limit:][::-1]


def traces() -> list[dict[str, Any]]:
    """The kept traces, newest first, without the steps."""
    with _lock:
        return [{k: t[k] for k in ("id", "ts", "source", "kind", "signed_in", "question", "outcome", "totals", "guardrails")}
                for t in reversed(TRACES)]


def trace(trace_id: str | None = None) -> dict[str, Any] | None:
    with _lock:
        if not TRACES:
            return None
        if trace_id is None:
            return TRACES[-1]
        return next((t for t in TRACES if t["id"] == trace_id), None)


def runs() -> list[dict[str, Any]]:
    """Golden-set runs, newest first, without the per-case details."""
    with _lock:
        items = sorted(RUNS.values(), key=lambda r: r.get("started_at", ""), reverse=True)
        return [{k: v for k, v in r.items() if k != "cases"} for r in items]


def run(run_id: str) -> dict[str, Any] | None:
    with _lock:
        return RUNS.get(run_id)


_load()
