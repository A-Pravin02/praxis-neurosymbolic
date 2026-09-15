"""A local open-weights model run with Hugging Face transformers.

Used by the evaluation harness to run the LLM baselines for real when no hosted provider
key is configured. It is not a dependency of the deployed service: `transformers` and
`torch` are imported lazily and are not in requirements.txt.

    LLM_PROVIDER=local  LOCAL_LLM_MODEL=Qwen/Qwen2.5-1.5B-Instruct

Decoding is greedy, so a benchmark run is reproducible on the same hardware.
"""
from __future__ import annotations

import os
import threading

from app.neuro.base import BaseProvider, Completion, CompletionRequest, Role

DEFAULT_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"


class LocalTransformersProvider(BaseProvider):
    name = "local"

    def __init__(self, model: str | None = None) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.model_name = model or os.environ.get("LOCAL_LLM_MODEL", DEFAULT_MODEL)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self._torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        self.model = self.model_name
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_name,
            torch_dtype=torch.float16 if self.device == "cuda" else torch.float32,
        ).to(self.device)
        self._model.eval()
        self._lock = threading.Lock()

    def complete(self, request: CompletionRequest) -> Completion:
        messages = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        for message in request.messages:
            role = "assistant" if message.role is Role.ASSISTANT else \
                "system" if message.role is Role.SYSTEM else "user"
            messages.append({"role": role, "content": message.content})
        prompt = self.tokenizer.apply_chat_template(messages, tokenize=False,
                                                    add_generation_prompt=True)
        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.device)
        with self._lock, self._torch.no_grad():
            output = self._model.generate(
                **inputs, max_new_tokens=request.max_tokens or 350, do_sample=False,
                pad_token_id=self.tokenizer.eos_token_id)
        generated = output[0][inputs["input_ids"].shape[1]:]
        text = self.tokenizer.decode(generated, skip_special_tokens=True)
        return Completion(text=text.strip(), model=self.model_name, provider=self.name,
                          prompt_tokens=int(inputs["input_ids"].shape[1]),
                          completion_tokens=int(generated.shape[0]))
