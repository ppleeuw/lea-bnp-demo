"""
server.py: the web server of the demo (FastAPI, run by uvicorn on Render).

Pages   /            the demo bank website with the Léa chat
        /admin       the evals console, a separate site for the admin
API     POST /api/chat        one customer message        → lea.chat
        POST /api/confirm     tap on the card-lock card    → lea.confirm
        GET  /api/admin/...   the console's data: summary, runs, traces, cost, agents, health
        POST /api/admin/runs  start a golden-set run
        GET  /health

Endpoints are plain "def", not "async def": FastAPI runs each in a worker thread, so one
slow model call does not hold up other visitors.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import evals
import guardrails
import lea
import metrics

STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="BNP Léa demo")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


def _source(payload: dict) -> str:
    """'eval' when the golden set calls over HTTP, so its turns are not counted as visitors."""
    return "eval" if payload.get("source") == "eval" else "live"


def _no_key() -> JSONResponse:
    return JSONResponse({"error": "MISTRAL_API_KEY is not set on the server.", "answer": ""}, status_code=503)


# ----------------------------------------------------------------------------- pages
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/admin")
def admin():
    return FileResponse(STATIC / "admin" / "index.html")


@app.get("/health")
def health():
    return {"ok": True, "agents": {role: a["id"] for role, a in lea.AGENTS.items()}, "has_key": lea.has_api_key()}


# ----------------------------------------------------------------------------- the chat
@app.post("/api/chat")
def chat(payload: dict):
    text = (payload.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "empty text", "answer": ""}, status_code=400)
    if not lea.has_api_key():
        return _no_key()
    try:
        return lea.chat(text, conversation_id=payload.get("conversation_id") or None,
                        signed_in=bool(payload.get("authenticated", False)),  # default: guest
                        masked=bool(payload.get("masked", False)), source=_source(payload))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"agent error: {e}", "answer": ""}, status_code=502)


@app.post("/api/confirm")
def confirm(payload: dict):
    if not payload.get("conversation_id"):
        return JSONResponse({"error": "conversation_id is required", "answer": ""}, status_code=400)
    if not lea.has_api_key():
        return _no_key()
    try:
        return lea.confirm(payload["conversation_id"], approve=bool(payload.get("approve")), source=_source(payload))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"agent error: {e}", "answer": ""}, status_code=502)


# ----------------------------------------------------------------------------- the evals console
@app.get("/api/admin/summary")
def admin_summary():
    return {
        "agents": [{"role": role, **{k: a[k] for k in ("id", "label", "model", "tools")}} for role, a in lea.AGENTS.items()],
        "moderation": guardrails.MODERATION_MODEL,
        "live": metrics.summary("live"), "eval": metrics.summary("eval"),
        "recent": metrics.recent_turns(60), "runs": metrics.runs(),
        "thresholds": guardrails.THRESHOLDS, "monitored": guardrails.MONITORED,
    }


@app.get("/api/admin/runs/{run_id}")
def admin_run(run_id: str):
    run = metrics.run(run_id)
    return run if run else JSONResponse({"error": "unknown run"}, status_code=404)


@app.post("/api/admin/runs")
def admin_start_run():
    if not lea.has_api_key():
        return _no_key()
    run_id, message = evals.start_in_background()
    return JSONResponse({"run_id": run_id, "message": message}, status_code=202 if run_id else 429)


@app.get("/api/admin/cases")
def admin_cases():
    return evals.load_cases()


@app.get("/api/admin/traces")
def admin_traces():
    return metrics.traces()


@app.get("/api/admin/traces/{trace_id}")
def admin_trace(trace_id: str):
    trace = metrics.trace(None if trace_id == "latest" else trace_id)
    return trace if trace else JSONResponse({"error": "No trace yet: chat with Léa or run the golden set."}, status_code=404)


@app.get("/api/admin/cost")
def admin_cost():
    last_run = next((r for r in metrics.runs() if r.get("status") == "done" and r.get("cost_usd") is not None), None)
    return {"prices": metrics.PRICES, "source": metrics.PRICE_SOURCE, "checked_on": metrics.PRICES_CHECKED_ON,
            "live": metrics.summary("live"), "eval": metrics.summary("eval"),
            "last_trace": metrics.trace(), "last_run": last_run}


# Live facts from Mistral, cached: the agent's configuration and the health of each dependency.
_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def _cached(key: str, seconds: int, build) -> Any:
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < seconds:
            return hit[1]
    value = build()
    with _cache_lock:
        _cache[key] = (time.time(), value)
    return value


def _dump(obj: Any) -> Any:
    return obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj


def _library_ids(agents: list[dict[str, Any]]) -> list[str]:
    return sorted({i for agent in agents for t in agent.get("tools") or [] if t.get("type") == "document_library"
                   for i in t.get("library_ids") or []})


def _studio_agents() -> list[dict[str, Any]]:
    """The four agents as Studio has them now, with their role and the roles they hand off to."""
    agents = []
    for role, config in lea.AGENTS.items():
        agent = _dump(lea.client().beta.agents.get(agent_id=config["id"]))
        agent["role"], agent["label"] = role, config["label"]
        agent["hands_off_to"] = [lea.ROLE_BY_ID.get(i, i) for i in agent.get("handoffs") or []]
        agents.append(agent)
    return agents


def _agent_facts() -> dict[str, Any]:
    agents = _studio_agents()
    libraries = []
    for library_id in _library_ids(agents):
        library = _dump(lea.client().beta.libraries.get(library_id=library_id))
        documents = _dump(lea.client().beta.libraries.documents.list(library_id=library_id))
        library["documents"] = [{k: d.get(k) for k in ("name", "size", "process_status", "last_processed_at")}
                                for d in (documents.get("data") or [])]
        libraries.append(library)
    return {"agents": agents, "libraries": libraries}


@app.get("/api/admin/agents")
def admin_agent():
    if not lea.has_api_key():
        return _no_key()
    try:
        return _cached("agent", 60, _agent_facts)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"Could not read the agents from Mistral: {e}"}, status_code=502)


def _health() -> dict[str, Any]:
    checks = [{"name": "API key on the server", "ok": lea.has_api_key(),
               "detail": "MISTRAL_API_KEY is set" if lea.has_api_key() else "set MISTRAL_API_KEY in Render"}]
    if lea.has_api_key():
        agents = []
        try:
            agents = _studio_agents()
            checks.append({"name": "Studio agents reachable", "ok": True,
                           "detail": " · ".join(f"{a['label']} v{a.get('version')} {a.get('model')}" for a in agents)})
            wrong = [a["label"] for a in agents
                     if sorted(a["hands_off_to"]) != sorted(lea.AGENTS[a["role"]]["hands_off_to"])]
            checks.append({"name": "Handoffs as in studio/agents.json", "ok": not wrong,
                           "detail": "triage → FAQ, account, card; each back to triage" if not wrong
                           else "different in Studio: " + ", ".join(wrong)})
        except Exception as e:  # noqa: BLE001
            checks.append({"name": "Studio agents reachable", "ok": False, "detail": str(e)[:200]})
        for library_id in _library_ids(agents):
            try:
                docs = _dump(lea.client().beta.libraries.documents.list(library_id=library_id))
                checks.append({"name": "Library readable with the server's key", "ok": True,
                               "detail": f"{len(docs.get('data') or [])} document(s) in {library_id[:8]}…"})
            except Exception as e:  # noqa: BLE001
                checks.append({"name": "Library readable with the server's key", "ok": False,
                               "detail": "share the library with the workspace in Studio: " + str(e)[:160]})
        try:
            lea.client().classifiers.moderate(model=guardrails.MODERATION_MODEL, inputs=["health check"])
            checks.append({"name": "Moderation model", "ok": True, "detail": guardrails.MODERATION_MODEL})
        except Exception as e:  # noqa: BLE001
            checks.append({"name": "Moderation model", "ok": False, "detail": str(e)[:200]})
    failed = sum(not c["ok"] for c in checks)
    return {"status": "ok" if not failed else "down" if not checks[0]["ok"] else "degraded", "checks": checks}


@app.get("/api/admin/health")
def admin_health():
    return _cached("health", 60, _health)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8766")))
