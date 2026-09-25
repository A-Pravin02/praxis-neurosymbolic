"""Run the README's LIVE DEMO CHECKLIST against the running stack and report what happens.

    docker compose up -d && python scripts/verify_demo.py


Reports what each question actually returns: pipeline, outcome, confidence and the
first line of the answer. It asserts nothing -- the point is to see the real behaviour,
including where it differs from what the README claims.
"""
import json
import sys
import urllib.request

API = "http://127.0.0.1:8000/api"
# The seeded demo password, published in the README: this stack ships with fixed demo
# accounts and no real credential. Nothing here reads a secret.
PW = "Praxis-Demo-2026!"

QUESTIONS = [
    (1, "priya@meridian.test", "am I eligible for free delivery?"),
    (2, "priya@meridian.test", "the Copper Kettle arrived damaged, can I return it?"),
    (3, "priya@meridian.test", "how long do I have to cancel an order"),
    (4, "priya@meridian.test", "who do I contact if my laptop breaks"),
    (5, "priya@meridian.test", "what does the refund policy say"),
    (6, "priya@meridian.test", "can I return order ORD-22003?"),
    (7, "priya@meridian.test", "can I give this back?"),
    (8, "priya@meridian.test", "who won the 2018 world cup"),
    (9, "priya@meridian.test", "what refund do I get for a late delivery?"),
    (10, "support@meridian.test",
     "a customer's laptop arrived damaged 12 days ago and they want a replacement, "
     "what can I offer?"),
]


def post(path, payload, token=None):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(API + path, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


def login(email):
    data = post("/auth/login", {"email": email, "password": PW})
    return data["tokens"]["access_token"]


def main():
    tokens = {}
    for n, email, question in QUESTIONS:
        if email not in tokens:
            tokens[email] = login(email)
        try:
            answer = post("/chat", {"message": question, "pipeline_mode": "neurosymbolic"}, tokens[email])
        except Exception as exc:  # report, do not stop: a later question may still work
            print(f"{n}. [{email.split('@')[0]}] {question}\n   ERROR: {exc}\n")
            continue
        pipeline = answer.get("route") or "?"
        outcome = answer.get("gate_decision", "?")
        conf = answer.get("confidence")
        text = (answer.get("answer") or "").replace("\n", " ")[:150]
        srcs = len(answer.get("sources") or [])
        print(f"{n}. [{email.split('@')[0]}] {question}")
        print(f"   route={pipeline!r} gate={outcome!r} "
              f"confidence={conf} sources={srcs}")
        print(f"   {text}\n")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
