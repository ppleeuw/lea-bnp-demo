"""Voxtral STT + TTS helpers."""
from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path

from mistralai.client import Mistral, models

DEFAULT_VOICE_ID = os.environ.get(
    "MISTRAL_TTS_VOICE_ID", "e3596645-b1af-469e-b857-f18ddedc7652"
)  # Oliver - Neutral (en_gb)
TTS_MODEL = os.environ.get("MISTRAL_TTS_MODEL", "voxtral-mini-tts-2603")
STT_MODEL = os.environ.get("MISTRAL_STT_MODEL", "voxtral-mini-latest")


def _client() -> Mistral:
    key = os.environ.get("MISTRAL_API_KEY")
    if not key:
        raise RuntimeError("MISTRAL_API_KEY is not set")
    return Mistral(api_key=key)


def synthesize(text: str, voice_id: str | None = None) -> bytes:
    """Return MP3 bytes for assistant speech."""
    client = _client()
    resp = client.audio.speech.complete(
        model=TTS_MODEL,
        input=text,
        voice_id=voice_id or DEFAULT_VOICE_ID,
        response_format="mp3",
    )
    raw = resp.audio_data
    if isinstance(raw, bytes):
        return raw
    return base64.b64decode(raw)


def transcribe(audio_bytes: bytes, filename: str = "audio.webm") -> str:
    """Transcribe microphone audio to text via Voxtral."""
    client = _client()
    name = Path(filename).name or "audio.webm"
    file_obj = models.File(file_name=name, content=audio_bytes)
    result = client.audio.transcriptions.complete(
        model=STT_MODEL,
        file=file_obj,
        language="fr",
    )
    text = getattr(result, "text", None)
    if text:
        return text.strip()
    if hasattr(result, "model_dump"):
        d = result.model_dump()
        return (d.get("text") or d.get("transcript") or "").strip()
    return str(result).strip()
