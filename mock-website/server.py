"""
server.py: the web server of the demo (FastAPI, run by uvicorn on Render).

Pages   /            the demo bank website with the Léa chat
        /admin       evals and monitoring, for the admin
API     POST /api/chat        one customer message        → lea.chat
        POST /api/confirm     tap on the card-lock card    → lea.confirm
        GET  /api/admin/summary, GET /api/admin/runs/{id}, POST /api/admin/runs
        GET  /health

Endpoints are plain "def", not "async def": FastAPI runs each in a worker thread, so one
slow model call does not hold up other visitors.
"""
from __future__ import annotations

import os
from pathlib import Path

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


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/admin")
def admin():
    return FileResponse(STATIC / "admin.html")


@app.get("/health")
def health():
    return {"ok": True, "agent_id": lea.AGENT_ID, "agent_version": lea.AGENT_VERSION or "latest",
            "has_key": lea.has_api_key()}


def _no_key() -> JSONResponse:
    return JSONResponse({"error": "MISTRAL_API_KEY is not set on the server.", "answer": ""}, status_code=503)


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
                        masked=bool(payload.get("masked", False)))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"agent error: {e}", "answer": ""}, status_code=502)


@app.post("/api/confirm")
def confirm(payload: dict):
    if not payload.get("conversation_id"):
        return JSONResponse({"error": "conversation_id is required", "answer": ""}, status_code=400)
    if not lea.has_api_key():
        return _no_key()
    try:
        return lea.confirm(payload["conversation_id"], approve=bool(payload.get("approve")))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"agent error: {e}", "answer": ""}, status_code=502)


@app.get("/api/admin/summary")
def admin_summary():
    return {
        "agent": {"id": lea.AGENT_ID, "version": lea.AGENT_VERSION or "latest", "model": "mistral-medium-latest",
                  "moderation": guardrails.MODERATION_MODEL},
        "prices": {"input_per_million": metrics.PRICE_INPUT * 1e6, "output_per_million": metrics.PRICE_OUTPUT * 1e6},
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8766")))
