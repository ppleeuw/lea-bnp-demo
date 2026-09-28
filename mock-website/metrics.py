"""
metrics.py: what the admin site shows.

Every turn (a chat message or a confirmation) is recorded with its tokens, cost, latency,
tools and guardrails, and every golden-set run with its results. Records live in memory and
in LEA_DATA_DIR (default: a temp folder). On Render's free plan that folder is wiped when the
service sleeps, so the committed runs in eval/results/ are loaded as history as well.
No message text is stored.
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

# List prices in US dollars per token. Medium 3.5: $1.50 / $7.50 per million tokens.
# Moderation: estimate, $0.10 per million tokens.
PRICE_INPUT = 1.50 / 1_000_000
PRICE_OUTPUT = 7.50 / 1_000_000
PRICE_MODERATION = 0.10 / 1_000_000

_lock = threading.Lock()
TURNS: deque[dict[str, Any]] = deque(maxlen=5000)
RUNS: dict[str, dict[str, Any]] = {}


def cost_usd(input_tokens: int, output_tokens: int, moderation_tokens: int) -> float:
    return round(input_tokens * PRICE_INPUT + output_tokens * PRICE_OUTPUT + moderation_tokens * PRICE_MODERATION, 6)


def record_turn(turn: dict[str, Any]) -> None:
    with _lock:
        TURNS.append(turn)
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with TURNS_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(turn, ensure_ascii=False) + "\n")
        except OSError:
            pass


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


def summary(source: str | None = None) -> dict[str, Any]:
    """The numbers on the admin site, for all turns or for one source ('live' or 'eval')."""
    with _lock:
        turns = [t for t in TURNS if source in (None, t.get("source"))]
    latencies = [t["latency_ms"] for t in turns]
    costs = [t["cost_usd"] for t in turns]
    by_conversation: dict[str, float] = {}
    for t in turns:
        if t.get("conversation_id"):
            by_conversation[t["conversation_id"]] = by_conversation.get(t["conversation_id"], 0) + t["cost_usd"]
    blocked_in = Counter(t["guardrail_in"] for t in turns if t.get("guardrail_in"))
    corrected_out = Counter(t["guardrail_out"] for t in turns if t.get("guardrail_out"))
    refusals = Counter(x["error"] for t in turns for x in t["tools"] if x.get("error"))
    tools = Counter(x["name"] for t in turns for x in t["tools"] if x.get("ok"))
    confirmations = Counter(t["decision"] for t in turns if t.get("decision"))
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
                   "model_calls": sum(t["model_calls"] for t in turns)},
        "guardrails": {"blocked_before_agent": dict(blocked_in), "corrected_after_agent": dict(corrected_out),
                       "tool_refusals": dict(refusals), "confirmations": dict(confirmations),
                       "passed_input_check": len(reached_agent)},
        "tools": dict(tools),
        "library_share": round(sum(1 for t in reached_agent if "document_library" in [x["name"] for x in t["tools"]])
                               / len(reached_agent), 3) if reached_agent else None,
        "moderation_unavailable": sum(1 for t in turns if t.get("moderation_unavailable")),
    }


def recent_turns(limit: int = 60) -> list[dict[str, Any]]:
    with _lock:
        return list(TURNS)[-limit:][::-1]


def runs() -> list[dict[str, Any]]:
    """Golden-set runs, newest first, without the per-case details."""
    with _lock:
        items = sorted(RUNS.values(), key=lambda r: r.get("started_at", ""), reverse=True)
        return [{k: v for k, v in r.items() if k != "cases"} for r in items]


def run(run_id: str) -> dict[str, Any] | None:
    with _lock:
        return RUNS.get(run_id)


_load()
