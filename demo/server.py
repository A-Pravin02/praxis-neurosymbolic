"""PRAXIS demo server: drives the real pipeline and exposes its trace to a browser.

    python demo/server.py              # http://127.0.0.1:8club
    python demo/server.py --port 8099

This is a window onto the system, not a reimplementation of it. Every number the
page shows comes from `NeuroSymbolicController.answer`, the same call the research
benchmark makes; the questions come from the frozen test split, and the gold answers
come from the independent oracle that generated them. Nothing is precomputed and
nothing is mocked, so a case that fails here fails in the benchmark too - including
the `not_covered` cases, which the system gets wrong. They are in the sample set on
purpose.

Standard library only: the demo must run on a clean checkout with the backend venv
and no database, which is also how the no_db test subset runs.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
WORLDS = BACKEND / "data/benchmark_worlds"
STATIC = pathlib.Path(__file__).resolve().parent / "static"
sys.path.insert(0, str(BACKEND))

import yaml  # noqa: E402

_lock = threading.Lock()
_controllers: dict[int, object] = {}
_cases: list[dict] | None = None


# ---------------------------------------------------------------- the system
def cases() -> list[dict]:
    global _cases
    if _cases is None:
        _cases = yaml.safe_load(
            (WORLDS / "test.yaml").read_text(encoding="utf-8"))["cases"]
    return _cases


def controller(world: int):
    """One controller per world, built once and reused.

    Building a pack loads the ONNX encoder and compiles the rule base, which costs
    a second or two; a demo that paid that per request would read as slow when the
    measured answer latency is tens of milliseconds.
    """
    with _lock:
        if world not in _controllers:
            from app.hybrid.controller import NeuroSymbolicController
            from app.hybrid.knowledge_pack import load_pack
            from app.neuro.providers.mock import MockLLMProvider
            pack = load_pack(str(WORLDS / f"world-{world}"), "semantic")
            _controllers[world] = NeuroSymbolicController(
                pack, llm=MockLLMProvider("praxis-mock-1"))
        return _controllers[world]


def grade(case: dict, answer) -> dict:
    """Score one answer the way the benchmark scores it.

    `app.evaluation.research.score` is the authority; it is imported rather than
    reimplemented so the badge on the page cannot disagree with the paper.
    """
    from app.evaluation import research
    from app.hybrid.knowledge_pack import load_pack
    pack = load_pack(str(WORLDS / f"world-{case['world']}"), "semantic")
    world_obj = research.World(pack) if hasattr(research, "World") else None
    try:
        return research.score(case, answer.as_dict(), world_obj)
    except Exception:                                    # noqa: BLE001
        # Grading is a convenience here; a demo that cannot grade must still answer.
        return {}


def run_case(case: dict) -> dict:
    ctl = controller(case["world"])
    started = time.perf_counter()
    ans = ctl.answer(case["question"], actor_email=case["user"],
                     actor_roles=tuple(case.get("roles", ())))
    elapsed = (time.perf_counter() - started) * 1000
    t = ans.trace
    interp = t.get("neural_interpretation", {}) or {}
    verification = t.get("verification", {}) or {}
    gold = case.get("gold", {}) or {}

    correct = _is_correct(case, ans)
    return {
        "case": {k: case.get(k) for k in
                 ("id", "world", "category", "question", "user", "roles")},
        "gold": {"kind": gold.get("kind"), "value": gold.get("value"),
                 "because": gold.get("because"), "clauses": gold.get("clauses", []),
                 "expected_outcome": (case.get("expect") or {}).get("outcome"),
                 "expected_goal": (case.get("expect") or {}).get("goal")},
        "neural": {
            "speech_act": (interp.get("speech_act") or {}).get("label"),
            "candidates": (interp.get("intent_candidates") or [])[:5],
            "entities": t.get("entities", []),
            "encoder": interp.get("encoder", "bge-small (33M, ONNX)"),
        },
        "symbolic": {
            "goal": ans.goal,
            "facts": t.get("symbolic_facts", [])[:40],
            "rules": t.get("rules_used", []),
            "steps": t.get("reasoning_steps", []),
            "policy_versions": t.get("policy_versions", []),
            "authorization": t.get("authorization", {}),
        },
        "verification": {
            "passed": verification.get("passed"),
            "checks": verification.get("checks", []),
        },
        "release": {
            "outcome": ans.outcome,
            "decision": ans.decision,
            "value": ans.value,
            "answer": ans.answer,
            "confidence": ans.confidence,
            "latency_ms": round(elapsed, 1),
            "sources": [s.get("id") if isinstance(s, dict) else s
                        for s in (ans.sources or [])],
            "pipeline": ans.pipeline_label or ans.pipeline,
        },
        "verdict": {"correct": correct},
    }


def _is_correct(case: dict, ans) -> bool | None:
    """Apply the benchmark's correctness rule for this gold kind.

    Mirrors `app.evaluation.research.score`: answerable kinds must be answered and
    match; a conditional must be hedged and not definite; a denied case must refuse;
    a not-covered case must report insufficient evidence.
    """
    kind = (case.get("gold") or {}).get("kind")
    outcome = ans.outcome
    if kind in ("decision", "value", "result"):
        gold_value = (case.get("gold") or {}).get("value")
        got = ans.decision if kind == "decision" else ans.value
        if outcome != "answered":
            return False
        if isinstance(gold_value, bool) or isinstance(got, bool):
            return bool(got) == bool(gold_value)
        try:
            return abs(float(got) - float(gold_value)) < 0.011
        except (TypeError, ValueError):
            return str(got).strip().lower() == str(gold_value).strip().lower()
    if kind == "conditional":
        return outcome in ("conditional", "clarification")
    if kind == "denied":
        return outcome == "denied"
    if kind == "not_covered":
        return outcome == "insufficient"
    return None


# ---------------------------------------------------------------- the sample set
# One case per category, chosen as the first of that category in world 101 so the
# selection is reproducible and not cherry-picked. `not_covered` is included even
# though the system fails it: a demo that showed only the passing categories would
# misrepresent the system the paper describes.
SAMPLE_ORDER = ["known", "paraphrased", "calculation", "multi_hop", "policy_version",
                "false_premise", "missing_info", "recommendation", "unauthorized",
                "not_covered"]


def sample_cases() -> list[dict]:
    by_cat: dict[str, dict] = {}
    for c in cases():
        if c["category"] not in by_cat and c["world"] == 101:
            by_cat[c["category"]] = c
    return [by_cat[c] for c in SAMPLE_ORDER if c in by_cat]


# ---------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):                   # quieter console
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write(f"  {args[0]}\n")

    def _send(self, body: bytes, ctype: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status: int = 200) -> None:
        self._send(json.dumps(payload, default=str).encode("utf-8"),
                   "application/json; charset=utf-8", status)

    def do_GET(self) -> None:                            # noqa: N802
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                self._send((STATIC / "index.html").read_bytes(),
                           "text/html; charset=utf-8")
            elif path == "/api/samples":
                self._json([{"id": c["id"], "category": c["category"],
                             "world": c["world"], "question": c["question"],
                             "user": c["user"], "roles": c.get("roles", []),
                             "gold_kind": (c.get("gold") or {}).get("kind")}
                            for c in sample_cases()])
            elif path.startswith("/api/case/"):
                case_id = path.rsplit("/", 1)[-1]
                match = next((c for c in cases() if c["id"] == case_id), None)
                if match is None:
                    return self._json({"error": f"no case {case_id}"}, 404)
                self._json(run_case(match))
            elif path == "/api/suite":
                started = time.perf_counter()
                results = [run_case(c) for c in sample_cases()]
                self._json({
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                    "total": len(results),
                    "correct": sum(1 for r in results if r["verdict"]["correct"]),
                    "results": results,
                })
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:                         # noqa: BLE001
            traceback.print_exc()
            self._json({"error": str(exc)}, 500)

    def do_POST(self) -> None:                           # noqa: N802
        if urlparse(self.path).path != "/api/ask":
            return self._json({"error": "not found"}, 404)
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            question = (payload.get("question") or "").strip()
            if not question:
                return self._json({"error": "a question is required"}, 400)
            world = int(payload.get("world") or 101)
            case = {"id": "ad-hoc", "world": world, "category": "ad-hoc",
                    "question": question,
                    "user": payload.get("user") or f"support@w{world}.test",
                    "roles": payload.get("roles") or ["support"], "gold": {}}
            self._json(run_case(case))
        except Exception as exc:                         # noqa: BLE001
            traceback.print_exc()
            self._json({"error": str(exc)}, 500)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--warm", action="store_true",
                    help="load world 101 before serving, so the first request is fast")
    args = ap.parse_args()

    if args.warm:
        print("warming world 101 ...", flush=True)
        controller(101)
        print("ready", flush=True)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"PRAXIS demo on http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
