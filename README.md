# Léa BNP mock demo

A demo retail-banking website with Léa, a customer support assistant built on a Mistral Studio agent, and an evals console for the admin. **Not affiliated with BNP Paribas.** All data is synthetic.

- Demo site: https://lea-bnp-demo.onrender.com
- Evals console: https://lea-bnp-demo.onrender.com/admin

## What runs where

| Piece | Where it runs | What it does |
| --- | --- | --- |
| `mock-website/static/index.html` | Visitor's browser | The demo bank page and chat widget. Sends every message to `/api/chat`; draws the cards from what comes back. Sign-in and the in-app approval are simulated. |
| `mock-website/static/admin.html` | Admin's browser | Evals console: golden-set runs, guardrails, cost, latency, recent turns. |
| `mock-website/server.py` | Render (Frankfurt), FastAPI | The pages and the API: `/api/chat`, `/api/confirm`, `/api/admin/*`, `/health`. |
| `mock-website/lea.py` | Render | The conversation with the Studio agent (Conversations API): input check, tool loop, card-lock hold, output check, metrics. |
| `mock-website/guardrails.py` | Render | Input checks (patterns + Mistral moderation) and output checks. |
| `mock-website/demo_bank.py` + `demo_bank.json` | Render | Stand-in for BNP's API gateway and core banking: checks each tool request and answers with standard demo data. No real bank is called. |
| `mock-website/metrics.py`, `evals.py` | Render | Records every turn and every golden-set run for the evals console. |
| Studio agent `ag_01a0d88d…` | Mistral (EU) | Model `mistral-medium-latest`, instructions (`studio/agent_instructions_v12.md`), tools (`studio/tools_v12.json`), document library. |
| Library "BNP Retail Support KB (Demo)" | Mistral (EU) | `kb-retail-support.md`: branch hours, fees, FAQ. The agent searches it itself. |
| `mistral-moderation-2603` | Mistral (EU) | Scores every typed message (jailbreaking, personal data) before the agent sees it. |

## The rule: the model proposes, code decides

1. **Input check** (`guardrails.check_input`): card numbers (Luhn), PINs, passwords, typed fake session notes; moderation scores for jailbreaking (≥ 0.3) and personal data (≥ 0.5).
2. **Guest block** (`demo_bank.check`): banking tools only for a signed-in session.
3. **Customer check**: the customer ID in a tool call must be the signed-in customer.
4. **Confirmation hold** (`lea._tool_loop`): a card lock is not run when the model asks; it waits as "pending" until the customer taps "Lock card" and approves in the app (`/api/confirm`).
5. **Output check** (`guardrails.check_output`): the balance in the answer must equal the tool result; "locked" only after a successful lock.

## Run locally

```bash
pip install -r requirements.txt
export MISTRAL_API_KEY=...
cd mock-website && python server.py      # http://127.0.0.1:8766 and /admin
```

## Evaluation

The golden set is `eval/golden.json` (16 cases). Run it from the evals console, or from the command line against any deployment (no API key needed):

```bash
python mock-website/evals.py --base https://lea-bnp-demo.onrender.com --save
```

`--save` writes the run to `eval/results/`, which the console shows as history. Exit code 1 on any failure, so it can gate a release.

## Deploy (Render)

`render.yaml` describes the service. Set `MISTRAL_API_KEY` in the Render dashboard; never commit it. Optional: `MISTRAL_AGENT_VERSION` pins the agent version (default: latest).
