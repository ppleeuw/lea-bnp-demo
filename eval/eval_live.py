"""
eval_live.py: the golden set, run against the live demo (or a local server).

Each case in golden.json is a short conversation with checks:
    expect_tools       tools that must have run successfully (document_library counts when the library was searched)
    forbid_tools       tools that must NOT have run successfully (a refused call does not count as run)
    expect_guardrail   the input check that must have blocked the message
    must_contain       every phrase must be in the last answer (case-insensitive)
    must_contain_any   at least one phrase must be in the last answer
    must_not_contain   none of these phrases may be in the last answer

Run (no API key needed, it calls the website like a browser does):
    python eval/eval_live.py                                  live site
    python eval/eval_live.py --base http://127.0.0.1:8766     local server
Exit code 1 if any case fails, so it can gate a release.
"""
import argparse
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def post(base, body):
    req = urllib.request.Request(base.rstrip("/") + "/api/chat", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def ran(trace, name):
    """True if the tool ran successfully in this turn (a refused call has an error)."""
    return any(t.get("name") == name and "error" not in (t.get("result") or {}) for t in trace)


def run_case(base, case):
    conversation_id, data, trace, guardrail = None, {}, [], None
    for turn in case["turns"]:
        body = turn if isinstance(turn, dict) else {"text": turn}
        body = dict(body, authenticated=case.get("auth", False), conversation_id=conversation_id)
        data = post(base, body)
        conversation_id = data.get("conversation_id") or conversation_id
        trace += data.get("tool_trace") or []
        guardrail = (data.get("guardrail") or {}).get("reason") or guardrail
    answer = (data.get("assistant_text") or "").lower()
    problems = []
    for t in case.get("expect_tools", []):
        if not ran(trace, t):
            problems.append(f"expected tool did not run: {t}")
    for t in case.get("forbid_tools", []):
        if ran(trace, t):
            problems.append(f"forbidden tool ran: {t}")
    if case.get("expect_guardrail") and guardrail != case["expect_guardrail"]:
        problems.append(f"expected guardrail {case['expect_guardrail']}, got {guardrail}")
    for p in case.get("must_contain", []):
        if p.lower() not in answer:
            problems.append(f"answer lacks: {p}")
    anyof = case.get("must_contain_any", [])
    if anyof and not any(p.lower() in answer for p in anyof):
        problems.append("answer lacks any of: " + " | ".join(anyof))
    for p in case.get("must_not_contain", []):
        if p.lower() in answer:
            problems.append(f"answer contains: {p}")
    return problems, data, trace


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("LEA_BASE_URL", "https://lea-bnp-demo.onrender.com"))
    ap.add_argument("--cases", default=os.path.join(HERE, "golden.json"))
    args = ap.parse_args()
    cases = json.load(open(args.cases, encoding="utf-8"))
    failed = 0
    print(f"{'case':<48} {'result':<6} {'secs':>5}  detail")
    print("-" * 110)
    for case in cases:
        t0 = time.time()
        try:
            problems, data, trace = run_case(args.base, case)
        except Exception as e:  # noqa: BLE001
            problems, data, trace = [f"request failed: {e}"], {}, []
        secs = time.time() - t0
        ok = not problems
        failed += 0 if ok else 1
        tools = ",".join(t.get("name", "?") + ("" if "error" not in (t.get("result") or {}) else "(refused)") for t in trace)
        detail = f"tools={tools or '-'} guardrail={(data.get('guardrail') or {}).get('reason')}" if ok else "; ".join(problems)
        print(f"{case['name'][:48]:<48} {'PASS' if ok else 'FAIL':<6} {secs:5.1f}  {detail[:90]}")
        if not ok:
            print(f"{'':<62}answer: {(data.get('assistant_text') or '')[:150]!r}")
    print("-" * 110)
    print(f"{len(cases) - failed}/{len(cases)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
