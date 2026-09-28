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

## What runs where

| Piece | Where it runs | What it does |
| --- | --- | --- |
| `mock-website/static/index.html` | Visitor's browser | Demo bank page and chat widget. Sends each message to `/api/chat`. |
| `mock-website/server.py` | Render (Frankfurt), FastAPI | Receives the chat message, calls `run_turn`, returns answer, tools used, sources and a trace ID. |
| `voice-talk/lib/lea.py` | Render, same process | Input checks, Conversations API calls to the Studio agent, tool execution with the demo data in `stubs.json`, output check, one log line per turn. |
| Studio agent `ag_01a0d88d…` | Mistral (EU) | Model `mistral-medium-latest`, instructions, two function tools, document library. |
| Library "BNP Retail Support KB (Demo)" | Mistral (EU) | `kb-retail-support.md`: branch hours, fees, FAQ. Searched by the agent itself. |
| `mistral-moderation-2603` | Mistral (EU) | Scores every customer message (jailbreaking, personal data) before the agent sees it. |

## Guardrails in code (`lea.py`)

1. Input check: card numbers (Luhn), PINs and passwords by pattern; moderation scores for jailbreaking (0.3) and personal data (0.5); fake session notes typed by a guest.
2. Guest block: banking tools refused unless the session is signed in.
3. Customer check: the customer ID in a tool call must be the signed-in customer.
4. Confirmation gate: a card lock runs only after the customer's own yes (the tap in the page, or a typed yes).
5. Output check: a balance must equal the tool result; "locked" only after a successful lock.

## Evaluation

`python eval/eval_live.py` runs the golden set in `eval/golden.json` against the live site (no API key needed). Exit code 1 on any failure.

## Studio agent

`studio/agent_instructions_v12.md` holds the instructions for agent version 12 (library first for every branch question, no hard-coded hours, sign-in only from the server's note, lock immediately after a confirmed tap).
