"""BNP Léa mock retail homepage — FastAPI chat to Studio agent via lea.run_turn."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
VOICE_LIB = ROOT.parent / "voice-talk" / "lib"
sys.path.insert(0, str(VOICE_LIB))

import lea  # noqa: E402
from lea import AGENT_ID, run_turn  # noqa: E402

app = FastAPI(title="BNP Léa mock website (demo)")
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


def _has_api_key() -> bool:
    if os.environ.get("MISTRAL_API_KEY"):
        return True
    try:
        lea.load_api_key()
        return True
    except Exception:  # noqa: BLE001
        return False


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/health")
def health():
    return {
        "ok": True,
        "agent_id": os.environ.get("MISTRAL_AGENT_ID", AGENT_ID),
        "has_key": _has_api_key(),
    }


@app.post("/api/chat")
async def chat(payload: dict):
    text = (payload.get("text") or "").strip()
    conversation_id = payload.get("conversation_id") or None
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)
    if not _has_api_key():
        return JSONResponse(
            {
                "error": "MISTRAL_API_KEY manquante — chargez la clé (source ../scripts/load_mistral_env.sh) puis relancez.",
                "assistant_text": "",
                "reply": "",
                "conversation_id": conversation_id,
            },
            status_code=503,
        )
    authenticated = bool(payload.get("authenticated", True))
    try:
        result = run_turn(text, conversation_id=conversation_id, authenticated=authenticated)
    except Exception as e:  # noqa: BLE001
        return JSONResponse(
            {
                "error": f"Erreur agent: {e}",
                "assistant_text": "",
                "reply": "",
                "conversation_id": conversation_id,
            },
            status_code=502,
        )
    assistant = result.get("assistant_text") or ""
    return {
        "assistant_text": assistant,
        "reply": assistant,
        "conversation_id": result.get("conversation_id"),
        "tool_trace": result.get("tool_trace") or [],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8766")),
        reload=False,
    )
