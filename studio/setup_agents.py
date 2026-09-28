"""
setup_agents.py: creates or updates Léa's four Studio agents from the files in this folder.

    agents.json          per agent: id, name, model, temperature, tools, who it hands off to
    agents/persona.md    Léa's personality and the rules every agent follows
    agents/<role>.md     the job of that agent
    tools.json           the tool definitions (function tools and the document library)

Each agent's instructions are persona.md followed by its own file. Handoffs are set after all
four exist, because they refer to each other's IDs. New agent IDs are written back to agents.json.

    MISTRAL_API_KEY=... python studio/setup_agents.py            # create or update in Studio
    python studio/setup_agents.py --spec spec.json                # only write what would be sent

Agent names must be plain ASCII without brackets: Mistral turns each name into the name of a
handoff function (handoff_to_...), and "é" or "(" makes every conversation fail.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "agents.json"


def build() -> dict[str, dict[str, Any]]:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    tools = {t.get("function", {}).get("name") or t["type"]: t
             for t in json.loads((HERE / "tools.json").read_text(encoding="utf-8"))}
    persona = (HERE / "agents" / "persona.md").read_text(encoding="utf-8").strip()
    spec = {}
    for role, agent in config.items():
        if not re.fullmatch(r"[A-Za-z0-9 _-]+", agent["name"]):
            raise ValueError(f"{role}: agent name {agent['name']!r} must be plain ASCII without brackets")
        job = (HERE / "agents" / f"{role}.md").read_text(encoding="utf-8").strip()
        spec[role] = {
            "id": agent.get("id"), "name": agent["name"], "model": agent["model"],
            "description": agent["description"], "instructions": f"{persona}\n\n{job}\n",
            "tools": [tools[name] for name in agent["tools"]],
            "completion_args": {"temperature": agent["temperature"], "max_tokens": 2048, "top_p": 1},
            "hands_off_to": agent["hands_off_to"],
        }
    return spec


def apply(spec: dict[str, dict[str, Any]]) -> dict[str, str]:
    from mistralai.client import Mistral

    client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
    ids = {}
    for role, agent in spec.items():
        fields = {k: agent[k] for k in ("name", "model", "description", "instructions", "tools", "completion_args")}
        if agent["id"]:
            ids[role] = client.beta.agents.update(agent_id=agent["id"], **fields).id
        else:
            ids[role] = client.beta.agents.create(**fields).id
        print(f"{role:<8} {ids[role]}")
    for role, agent in spec.items():
        client.beta.agents.update(agent_id=ids[role], handoffs=[ids[r] for r in agent["hands_off_to"]])
    return ids


def main() -> None:
    ap = argparse.ArgumentParser(description="Create or update Léa's Studio agents.")
    ap.add_argument("--spec", help="write the agents as JSON to this file instead of calling Mistral")
    args = ap.parse_args()
    spec = build()
    if args.spec:
        Path(args.spec).write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
        print("written", args.spec)
        return
    ids = apply(spec)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    for role, agent_id in ids.items():
        config[role]["id"] = agent_id
    CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("handoffs set; IDs saved in", CONFIG.name)


if __name__ == "__main__":
    main()
