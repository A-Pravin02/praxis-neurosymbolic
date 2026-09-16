"""A local, OpenAI-compatible chat-completions server for an open-weights model.

    python scripts/local_llm_server.py --model C:/Users/aprav/models/Qwen2.5-1.5B-Instruct
    python scripts/local_llm_server.py --model C:/Users/aprav/models/Qwen2.5-7B-Instruct --4bit

Then point Praxis (or the benchmark) at it:

    LLM_PROVIDER=openai LLM_BASE_URL=http://127.0.0.1:8765/v1 LLM_MODEL=<name>

From Docker the host is reachable as http://host.docker.internal:8765/v1.

Why a server rather than an in-process provider. The model needs CUDA PyTorch, which
the application's environment does not carry; a server keeps that dependency out of the
product. It also makes the evaluation honest about what it measures: every system -
baseline or Praxis - reaches the model through the same OpenAI-style API a hosted model
would be reached through.

Reproducibility. Decoding is greedy. Requests that arrive together are generated as one
padded batch, which is much faster on a GPU and can, rarely, change a greedy token by
floating-point noise; for that reason every completion is also written to an SQLite
cache keyed by (model, messages, max_tokens), and an identical request is served from the
cache. A benchmark re-run therefore reproduces its answers exactly, and the cache is the
record of every raw model output the reported results were scored on.

Standard library only for the HTTP side; torch and transformers for the model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import queue
import sqlite3
import threading
import time
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _hide_vision_packages() -> None:
    """Keep transformers from importing torchvision/torchaudio, which a text model never uses.

    A machine whose torchvision was built for a different torch fails inside transformers'
    import of *any* model class ("operator torchvision::nms does not exist"). Hiding the two
    packages from this process avoids changing the machine's installed packages.
    """
    import importlib.util

    real = importlib.util.find_spec

    def find_spec(name, *args, **kwargs):
        if name.split(".")[0] in ("torchvision", "torchaudio"):
            return None
        return real(name, *args, **kwargs)

    importlib.util.find_spec = find_spec


class Generator:
    def __init__(self, path: str, *, four_bit: bool, batch: int, window_ms: int,
                 cache: Path) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.torch = torch
        self.name = Path(path).name
        self.tokenizer = AutoTokenizer.from_pretrained(path)
        self.tokenizer.padding_side = "left"
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        kwargs: dict = {"torch_dtype": torch.float16}
        if four_bit:
            from transformers import BitsAndBytesConfig
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_quant_type="nf4")
            kwargs["device_map"] = "cuda"
        self.model = AutoModelForCausalLM.from_pretrained(path, **kwargs)
        if not four_bit:
            self.model = self.model.to("cuda")
        self.model.eval()
        self.batch = batch
        self.window = window_ms / 1000
        self.queue: queue.Queue = queue.Queue()
        cache.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(cache), check_same_thread=False)
        self.db.execute("create table if not exists completions (key text primary key, "
                        "model text, request text, completion text, created real)")
        self.db_lock = threading.Lock()
        self.served = {"generated": 0, "cached": 0}
        threading.Thread(target=self._loop, daemon=True).start()

    # -- cache ---------------------------------------------------------------------------
    def _key(self, messages: list[dict], max_tokens: int) -> str:
        blob = json.dumps({"model": self.name, "messages": messages, "max": max_tokens},
                          sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def cached(self, key: str) -> str | None:
        with self.db_lock:
            row = self.db.execute("select completion from completions where key=?",
                                  (key,)).fetchone()
        return row[0] if row else None

    def store(self, key: str, messages: list[dict], completion: str) -> None:
        with self.db_lock:
            self.db.execute("insert or replace into completions values (?,?,?,?,?)",
                            (key, self.name, json.dumps(messages, ensure_ascii=False),
                             completion, time.time()))
            self.db.commit()

    # -- generation ------------------------------------------------------------------------
    def complete(self, messages: list[dict], max_tokens: int) -> tuple[str, bool]:
        key = self._key(messages, max_tokens)
        hit = self.cached(key)
        if hit is not None:
            self.served["cached"] += 1
            return hit, True
        future: Future = Future()
        self.queue.put((messages, max_tokens, future))
        text = future.result()
        self.store(key, messages, text)
        self.served["generated"] += 1
        return text, False

    def _loop(self) -> None:
        while True:
            items = [self.queue.get()]
            deadline = time.perf_counter() + self.window
            while len(items) < self.batch:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    break
                try:
                    items.append(self.queue.get(timeout=remaining))
                except queue.Empty:
                    break
            try:
                texts = self._generate([m for m, _, _ in items], max(t for _, t, _ in items))
                for (_, _, future), text in zip(items, texts):
                    future.set_result(text)
            except Exception as exc:  # noqa: BLE001 - fail the waiting requests, keep serving
                for _, _, future in items:
                    if not future.done():
                        future.set_exception(exc)

    def _generate(self, conversations: list[list[dict]], max_tokens: int) -> list[str]:
        prompts = [self.tokenizer.apply_chat_template(c, tokenize=False,
                                                      add_generation_prompt=True)
                   for c in conversations]
        inputs = self.tokenizer(prompts, return_tensors="pt", padding=True).to("cuda")
        with self.torch.no_grad():
            output = self.model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False,
                                         temperature=None, top_p=None, top_k=None,
                                         pad_token_id=self.tokenizer.pad_token_id)
        width = inputs["input_ids"].shape[1]
        return [self.tokenizer.decode(row[width:], skip_special_tokens=True).strip()
                for row in output]


def serve(generator: Generator, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # quiet
            pass

        def _send(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path.rstrip("/").endswith("/models"):
                self._send(200, {"data": [{"id": generator.name, "object": "model"}]})
            else:
                self._send(200, {"status": "ok", "model": generator.name, **generator.served})

        def do_POST(self) -> None:
            if not self.path.rstrip("/").endswith("/chat/completions"):
                self._send(404, {"error": "not found"})
                return
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            if request.get("stream"):
                self._send(400, {"error": "streaming is not supported by this server"})
                return
            messages = [{"role": m["role"], "content": m["content"]}
                        for m in request.get("messages", [])]
            max_tokens = int(request.get("max_tokens") or 512)
            try:
                text, cached = generator.complete(messages, max_tokens)
            except Exception as exc:  # noqa: BLE001
                self._send(500, {"error": f"{type(exc).__name__}: {exc}"})
                return
            self._send(200, {
                "id": "local", "object": "chat.completion", "model": generator.name,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": text}}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "cached": cached},
            })

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"serving {generator.name} on http://0.0.0.0:{port}/v1", flush=True)
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--4bit", dest="four_bit", action="store_true")
    parser.add_argument("--batch", type=int, default=12)
    parser.add_argument("--window-ms", type=int, default=40)
    parser.add_argument("--cache")
    args = parser.parse_args()
    _hide_vision_packages()
    cache =Path(args.cache) if args.cache else \
        ROOT / "eval_reports" / "llm_cache" / f"{Path(args.model).name}.sqlite"
    generator = Generator(args.model, four_bit=args.four_bit, batch=args.batch,
                          window_ms=args.window_ms, cache=cache)
    serve(generator, args.port)


if __name__ == "__main__":
    main()
