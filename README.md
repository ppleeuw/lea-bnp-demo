# Léa BNP mock demo

Demonstration retail homepage + Léa chat widget (Mistral Studio agent). **Not affiliated with BNP Paribas.**

## Local

```bash
export MISTRAL_API_KEY=...
cd mock-website
python -m venv .venv && source .venv/bin/activate
pip install -r ../requirements.txt
python server.py
```

Open http://127.0.0.1:8766/

## Demo arc

1. Guest homepage — Léa FAQ / branch hours only
2. Sign in → Camille (demo)
3. Signed-in — balance + card lock with SCA mock

## Deploy (Render)

Connect this repo on Render (Blueprint from `render.yaml`) and set `MISTRAL_API_KEY` in the dashboard. Never commit the key.
