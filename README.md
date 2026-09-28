# Léa BNP mock demo

A demo retail-banking website in the BNP Paribas look with Léa, a customer support assistant built on four Mistral Studio agents (a triage agent that hands off to an FAQ, an account and a card agent), and an evals console for the admin. **Not affiliated with BNP Paribas.** All data is synthetic.

- Demo site: https://lea-bnp-demo.onrender.com
- Evals console: https://lea-bnp-demo.onrender.com/admin (Overview, Golden set, Test cases, Traces, Routing, Guardrails, Cost, Agents)

## What runs where

| Piece | Where it runs | What it does |
| --- | --- | --- |
| `mock-website/static/index.html` | Visitor's browser | The demo bank page and chat widget. Sends every message to `/api/chat`; draws the cards from what comes back and shows which agents handled it. Sign-in and the in-app approval are simulated. |
| `mock-website/static/admin/` | Admin's browser | Evals console, a separate site: golden-set runs with five check layers, the test cases, traces of every step, routing per agent, guardrails, cost, the four live agents and health. |
| `mock-website/server.py` | Render (Frankfurt), FastAPI | The pages and the API: `/api/chat`, `/api/confirm`, `/api/admin/*`, `/health`. |
| `mock-website/lea.py` | Render | The conversation with the agents (Conversations API): input check, handoffs read into a route, tool allow-list per agent, tool loop, card-lock hold, output check, metrics. |
| `mock-website/guardrails.py` | Render | Input checks (patterns + Mistral moderation) and output checks. |
| `mock-website/demo_bank.py` + `demo_bank.json` | Render | Stand-in for BNP's API gateway and core banking: checks each tool request and answers with standard demo data. No real bank is called. |
| `mock-website/metrics.py`, `evals.py` | Render | Turn records (no text), traces (last 50, memory only), golden-set runs, prices from mistral.ai/pricing/api. |
| Studio agents (`studio/agents.json`) | Mistral (EU) | Triage on `mistral-small-latest` with `transfer_to_advisor`; FAQ (document library), account (`get_account_balance`) and card (`lock_credit_card`) on `mistral-medium-latest`. Triage hands off to the three; each hands back to triage. Instructions: `studio/agents/persona.md` + one file per agent; tools: `studio/tools.json`. |
| Library "BNP Retail Support KB (Demo)" | Mistral (EU) | `kb-retail-support.md`: branch hours, fees, FAQ. The FAQ agent searches it itself. |
| `mistral-moderation-2603` | Mistral (EU) | Scores every typed message (jailbreaking, personal data) before the agent sees it. |

## The rule: the model proposes, code decides

1. **Input check** (`guardrails.check_input`): card numbers (Luhn), PINs, passwords, typed fake session notes; moderation blocks jailbreaking (≥ 0.3); the personal-data score (≥ 0.5) is flagged, not blocked.
2. **Tool allow-list per agent** (`lea._not_allowed`): each agent may only use its own tools, whatever it asks for.
3. **Guest block** (`demo_bank.check`): banking tools only for a signed-in session.
4. **Customer check**: the customer ID in a tool call must be the signed-in customer.
5. **Confirmation hold** (`lea._tool_loop`): a card lock is not run when the model asks; it waits as "pending" until the customer taps "Lock card" and approves in the app (`/api/confirm`).
6. **Output check** (`guardrails.check_output`): the balance in the answer must equal the tool result; "locked" only after a successful lock.

## Run locally

```bash
pip install -r requirements.txt
export MISTRAL_API_KEY=...
cd mock-website && python server.py      # http://127.0.0.1:8766 and /admin
```

## Evaluation

The golden set is `eval/golden.json` (17 cases, checked on five layers: routing, tools, confirmation, guardrails, answer). Run it from the evals console, or from the command line against any deployment (no API key needed):

```bash
python mock-website/evals.py --base https://lea-bnp-demo.onrender.com --save
```

`--save` writes the run to `eval/results/`, which the console shows as history. Exit code 1 on any failure, so it can gate a release.

## The Studio agents

`studio/setup_agents.py` creates or updates the four agents from `studio/agents.json`, the instruction files and `studio/tools.json`, then sets the handoffs:

```bash
MISTRAL_API_KEY=... python studio/setup_agents.py
```

Agent names must be plain ASCII without brackets: Mistral derives the handoff function name from the agent name, and "é" or "(" makes every conversation fail.

## Setup notes

- The Mistral Library must be shared with the workspace (Studio → Libraries → Share → Viewers: Entire workspace). A private library fails with "Error calling tool 'library_search'" when the agent runs through an API key.
- The personal-data moderation score is logged, not blocked: in a bank, "What is my balance?" already scores 0.50 (golden set, 28 Sep).

## Deploy (Render)

`render.yaml` describes the service. This service does not deploy by itself: after a push use Manual Deploy → Deploy latest commit. Set `MISTRAL_API_KEY` in the Render dashboard; never commit it. The agent IDs come from `studio/agents.json`.
