# Mock website — BNP Léa (interview demo)

Placeholder retail-banking homepage with a floating chat widget that talks to the Mistral Studio agent **Léa** via the Conversations API (same `voice-talk/lib/lea.py` client + stubs as voice-talk). No TTS on this site.

## Run

```bash
source ../scripts/load_mistral_env.sh   # or export MISTRAL_API_KEY
cd mock-website && pip install -r requirements.txt
python server.py
# open http://127.0.0.1:8766
```

If system `pip` is PEP 668-blocked, use a local venv:

```bash
source ../scripts/load_mistral_env.sh
cd mock-website
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python server.py
```

Port defaults to **8766** (`PORT` env overrides). Host is `0.0.0.0`.

## Endpoints

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/` | Static homepage |
| GET | `/health` | `{ok, agent_id, has_key}` |
| POST | `/api/chat` | `{text, conversation_id?}` → `{assistant_text, reply, conversation_id, tool_trace?}` |
| GET | `/static/...` | Static assets |

The site loads without an API key; chat returns a clear 503 if the key is missing.

## Smoke

```bash
curl -s http://127.0.0.1:8766/health
curl -s -X POST http://127.0.0.1:8766/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"text":"Quels sont les horaires de l'\''agence Paris Opéra ?"}'
```
