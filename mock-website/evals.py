"""
evals.py: the golden set, run from the admin site or from the command line.

A case in eval/golden.json is a short conversation. Each turn is what the customer types, or
{"confirm": "allow"} / {"confirm": "deny"} for the tap on the card-lock confirmation.
Checks on the whole conversation:
    expect_agent       the agent that must have answered the last turn: triage, faq, account or card
    expect_tools       tools that must have run successfully (document_library = the library was searched)
    forbid_tools       tools that must not have run successfully (a refused call does not count)
    expect_result      fields a tool's result must have, e.g. the balance the card shows
    expect_pending    a tool that must be waiting for the customer's confirmation
    expect_guardrail   the guardrail that must have fired
    must_contain / must_contain_any / must_not_contain   on the last answer
The checks fall into five layers, reported separately:
    routing        the triage agent handed the message to the right agent
    tools          the right tools ran, and the forbidden ones did not
    confirmation   a card lock waited for the customer's tap
    guardrails     the expected guardrail stopped the message
    answer         what the last answer must and must not say

Command line (no API key needed with --base, it calls the website like a browser):
    python mock-website/evals.py --base https://lea-bnp-demo.onrender.com --save
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import metrics

CASES_FILE = Path(__file__).resolve().parents[1] / "eval" / "golden.json"
MIN_SECONDS_BETWEEN_RUNS = 180  # the admin button costs model calls; one run is about $0.15
_run_lock = threading.Lock()
_last_start = 0.0

Sender = Callable[[str, dict[str, Any]], dict[str, Any]]


def load_cases() -> list[dict[str, Any]]:
    return json.loads(CASES_FILE.read_text(encoding="utf-8"))


LAYERS = {
    "routing": ("expect_agent",),
    "tools": ("expect_tools", "forbid_tools", "expect_result"),
    "confirmation": ("expect_pending",),
    "guardrails": ("expect_guardrail",),
    "answer": ("must_contain", "must_contain_any", "must_not_contain"),
}


def _layer_of(problem: str) -> str:
    if problem.startswith("expected agent"):
        return "routing"
    if problem.startswith(("expected tool", "forbidden tool")):
        return "tools"
    if "wait for confirmation" in problem:
        return "confirmation"
    if problem.startswith("expected guardrail"):
        return "guardrails"
    return "answer"


def layers_for(case: dict[str, Any], problems: list[str]) -> dict[str, bool | None]:
    """Per layer: None when the case has no check in it, else whether all its checks passed."""
    failed = {_layer_of(p) for p in problems}
    return {layer: (layer not in failed) if any(case.get(k) for k in keys) else None for layer, keys in LAYERS.items()}


def problems_for(case: dict[str, Any], responses: list[dict[str, Any]]) -> list[str]:
    ran = {t["name"] for r in responses for t in r.get("tools", []) if t.get("ok")}
    pending = {r["pending"]["tool"] for r in responses if r.get("pending")}
    fired = {r["guardrail"]["reason"] for r in responses if r.get("guardrail")}
    answer = (responses[-1].get("answer") or "").lower() if responses else ""
    agent = responses[-1].get("agent") if responses else None
    problems = []
    if case.get("expect_agent") and case["expect_agent"] != agent:
        problems.append(f"expected agent {case['expect_agent']}, answered by {agent or 'none'}")
    problems += [f"expected tool did not run: {t}" for t in case.get("expect_tools", []) if t not in ran]
    problems += [f"forbidden tool ran: {t}" for t in case.get("forbid_tools", []) if t in ran]
    for tool, fields in case.get("expect_result", {}).items():
        result = next((r["results"][tool] for r in responses if tool in (r.get("results") or {})), {})
        problems += [f"expected tool result {tool}.{k} = {v}, got {result.get(k)}" for k, v in fields.items() if result.get(k) != v]
    if case.get("expect_pending") and case["expect_pending"] not in pending:
        problems.append(f"expected {case['expect_pending']} to wait for confirmation")
    if case.get("expect_guardrail") and case["expect_guardrail"] not in fired:
        problems.append(f"expected guardrail {case['expect_guardrail']}, got {', '.join(fired) or 'none'}")
    problems += [f"answer lacks: {p}" for p in case.get("must_contain", []) if p.lower() not in answer]
    anyof = case.get("must_contain_any", [])
    if anyof and not any(p.lower() in answer for p in anyof):
        problems.append("answer lacks any of: " + " | ".join(anyof))
    problems += [f"answer contains: {p}" for p in case.get("must_not_contain", []) if p.lower() in answer]
    return problems


def run_case(case: dict[str, Any], send: Sender) -> dict[str, Any]:
    responses, conversation_id = [], None
    for turn in case["turns"]:
        if isinstance(turn, dict) and "confirm" in turn:
            r = send("confirm", {"conversation_id": conversation_id, "approve": turn["confirm"] == "allow"})
        else:
            r = send("chat", {"text": turn, "conversation_id": conversation_id, "authenticated": case.get("auth", False)})
        conversation_id = r.get("conversation_id") or conversation_id
        responses.append(r)
    problems = problems_for(case, responses)
    return {
        "name": case["name"], "passed": not problems, "problems": problems, "layers": layers_for(case, problems),
        "latency_ms": sum(r.get("latency_ms") or 0 for r in responses),
        "cost_usd": round(sum((r.get("usage") or {}).get("cost_usd", 0) for r in responses), 6),
        "model_calls": sum((r.get("usage") or {}).get("model_calls", 0) for r in responses),
        "tools": [t["name"] + ("" if t.get("ok") else f" ({t.get('error') or t.get('status')})")
                  for r in responses for t in r.get("tools", [])],
        "guardrail": next((r["guardrail"]["reason"] for r in responses if r.get("guardrail")), None),
        "expects_guardrail": bool(case.get("expect_guardrail")),
        "route": [" → ".join(r.get("route") or []) for r in responses],
        "agent": responses[-1].get("agent") if responses else None,
        "answer": (responses[-1].get("answer") or "")[:400] if responses else "",
    }


def run_all(send: Sender, label: str, run_id: str | None = None) -> dict[str, Any]:
    cases = load_cases()
    run = {"id": run_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4],
           "label": label, "status": "running", "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "total": len(cases), "done": 0, "cases": []}
    metrics.save_run(run)
    for case in cases:
        try:
            result = run_case(case, send)
        except Exception as e:  # noqa: BLE001 - one broken case must not stop the run
            result = {"name": case["name"], "passed": False, "problems": [f"error: {e}"],
                      "layers": {layer: False for layer in LAYERS}, "latency_ms": 0,
                      "cost_usd": 0, "model_calls": 0, "tools": [], "guardrail": None,
                      "expects_guardrail": bool(case.get("expect_guardrail")), "route": [], "agent": None,
                      "answer": ""}
        run["cases"].append(result)
        run["done"] = len(run["cases"])
        metrics.save_run(run)
    latencies = [c["latency_ms"] for c in run["cases"]]
    guard = [c for c in run["cases"] if c["expects_guardrail"]]
    run.update({
        "status": "done", "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "passed": sum(c["passed"] for c in run["cases"]),
        "pass_rate": round(sum(c["passed"] for c in run["cases"]) / len(cases), 3) if cases else None,
        "cost_usd": round(sum(c["cost_usd"] for c in run["cases"]), 4),
        "latency_ms_mean": round(statistics.mean(latencies)) if latencies else None,
        "latency_ms_max": max(latencies) if latencies else None,
        "guardrail_cases": {"total": len(guard), "passed": sum(c["passed"] for c in guard)},
        "layers": {layer: {"applicable": sum(1 for c in run["cases"] if c.get("layers", {}).get(layer) is not None),
                           "passed": sum(1 for c in run["cases"] if c.get("layers", {}).get(layer) is True)}
                   for layer in LAYERS},
    })
    metrics.save_run(run)
    return run


def in_process(kind: str, body: dict[str, Any]) -> dict[str, Any]:
    import lea
    if kind == "confirm":
        return lea.confirm(body["conversation_id"], body["approve"], source="eval")
    return lea.chat(body["text"], body.get("conversation_id"), signed_in=bool(body.get("authenticated")), source="eval")


def over_http(base: str) -> Sender:
    def send(kind: str, body: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(f"{base.rstrip('/')}/api/{kind}", data=json.dumps(dict(body, source="eval")).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))
    return send


def start_in_background() -> tuple[str | None, str]:
    """Start a run for the admin site. Refused while one runs, or within the cool-down."""
    global _last_start
    wait = int(MIN_SECONDS_BETWEEN_RUNS - (time.time() - _last_start))
    if wait > 0:
        return None, f"Please wait {wait} seconds before the next run."
    if not _run_lock.acquire(blocking=False):
        return None, "A run is already in progress."
    _last_start = time.time()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]

    def work() -> None:
        try:
            run_all(in_process, "admin site", run_id)
        finally:
            _run_lock.release()

    threading.Thread(target=work, daemon=True).start()
    return run_id, "Run started."


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the golden set.")
    ap.add_argument("--base", help="website to test, e.g. https://lea-bnp-demo.onrender.com (default: in-process)")
    ap.add_argument("--save", action="store_true", help="also write the run to eval/results/")
    args = ap.parse_args()
    run = run_all(over_http(args.base) if args.base else in_process, f"command line{' · ' + args.base if args.base else ''}")
    print(f"{'case':<50} {'result':<6} {'secs':>5} {'cost $':>8}  detail")
    print("-" * 118)
    for c in run["cases"]:
        detail = "; ".join(c["problems"]) if c["problems"] else "tools=" + (",".join(c["tools"]) or "-") + f" guardrail={c['guardrail']}"
        print(f"{c['name'][:50]:<50} {'PASS' if c['passed'] else 'FAIL':<6} {c['latency_ms'] / 1000:5.1f} {c['cost_usd']:8.4f}  {detail[:60]}")
        if not c["passed"]:
            print(f"{'':<73}answer: {c['answer'][:120]!r}")
    print("-" * 118)
    print(f"{run['passed']}/{run['total']} passed · cost ${run['cost_usd']} · mean {run['latency_ms_mean']} ms per case")
    if args.save:
        out = CASES_FILE.parent / "results" / f"{run['id']}.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")
        print("saved", out)
    sys.exit(0 if run["passed"] == run["total"] else 1)


if __name__ == "__main__":
    main()
