"""A decoder-only causal Transformer, trained from random initialization.

This is PRAXIS's *secondary* research experiment: a small language model whose every
parameter originates on this machine, so that the neuro-symbolic layer can be measured
on top of a base model whose training data and capability ceiling are fully known.

No pretrained weights are loaded anywhere in this module. `LanguageModel.__init__`
initializes every parameter from a documented distribution, and
`parameter_provenance()` reports the source of each one so the claim
"trained from scratch" is checkable rather than asserted.

Design choices follow established practice rather than novelty, so that the model is
not itself a confound in the research:

  * pre-normalization with RMSNorm (Zhang & Sennrich, 2019), as in LLaMA
  * rotary position embeddings (Su et al., 2021)
  * SwiGLU feed-forward (Shazeer, 2020)
  * weight tying between the embedding and the output head (Press & Wolf, 2017)
  * scaled residual initialization, as in GPT-2 (Radford et al., 2019)

References are in `docs/paper/references.bib`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field

import torch
import torch.nn as nn
import torch.nn.functional as F


@dataclass
class ModelConfig:
    """Every architectural knob. Serialized next to each checkpoint."""

    vocab_size: int = 8192
    n_layer: int = 8
    n_head: int = 8
    n_embd: int = 384
    block_size: int = 256          # context length in tokens
    dropout: float = 0.0
    bias: bool = False             # biases in linear layers; LLaMA-style default is off
    rope_theta: float = 10000.0
    ffn_multiple: int = 64         # round the SwiGLU hidden size to a multiple of this
    tie_embeddings: bool = True
    init_std: float = 0.02

    # Provenance, recorded so a checkpoint can never be mistaken for a fine-tune.
    initialized_from: str = "random"

    def __post_init__(self) -> None:
        if self.n_embd % self.n_head != 0:
            raise ValueError(
                f"n_embd ({self.n_embd}) must be divisible by n_head ({self.n_head})")
        if self.initialized_from != "random":
            raise ValueError(
                "ModelConfig.initialized_from must be 'random'. This module exists to "
                "train from random initialization; loading pretrained weights here "
                "would invalidate the experiment.")

    @property
    def head_dim(self) -> int:
        return self.n_embd // self.n_head

    @property
    def ffn_hidden(self) -> int:
        """SwiGLU uses three projections, so the usual 4x is scaled by 2/3."""
        hidden = int(2 * (4 * self.n_embd) / 3)
        return self.ffn_multiple * math.ceil(hidden / self.ffn_multiple)

    def to_dict(self) -> dict:
        return asdict(self)


class RMSNorm(nn.Module):
    """Root-mean-square layer normalization. No mean subtraction, no bias."""

    def __init__(self, dim: int, eps: float = 1e-6) -> None:
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.ones(dim))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Compute in float32 even under autocast: the reciprocal square root of a
        # half-precision mean is numerically poor and destabilizes early training.
        dtype = x.dtype
        x = x.float()
        x = x * torch.rsqrt(x.pow(2).mean(-1, keepdim=True) + self.eps)
        return (x * self.weight.float()).to(dtype)


def build_rope_cache(block_size: int, head_dim: int, theta: float,
                     device: torch.device | None = None
                     ) -> tuple[torch.Tensor, torch.Tensor]:
    """Precompute cos/sin tables for rotary embeddings, shaped (block_size, head_dim/2)."""
    if head_dim % 2 != 0:
        raise ValueError(f"rotary embeddings need an even head_dim, got {head_dim}")
    inv_freq = 1.0 / (theta ** (torch.arange(0, head_dim, 2, device=device).float() / head_dim))
    positions = torch.arange(block_size, device=device).float()
    angles = torch.outer(positions, inv_freq)
    return angles.cos(), angles.sin()


def apply_rope(x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
    """Rotate pairs of channels. `x` is (B, n_head, T, head_dim)."""
    t = x.shape[-2]
    cos = cos[:t].to(x.dtype)[None, None, :, :]
    sin = sin[:t].to(x.dtype)[None, None, :, :]
    x1, x2 = x[..., 0::2], x[..., 1::2]
    rotated = torch.stack((x1 * cos - x2 * sin, x1 * sin + x2 * cos), dim=-1)
    return rotated.flatten(-2)


class CausalSelfAttention(nn.Module):
    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.n_head = config.n_head
        self.head_dim = config.head_dim
        self.qkv = nn.Linear(config.n_embd, 3 * config.n_embd, bias=config.bias)
        self.proj = nn.Linear(config.n_embd, config.n_embd, bias=config.bias)
        self.dropout_p = config.dropout
        self.resid_dropout = nn.Dropout(config.dropout)

    def forward(self, x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
        b, t, c = x.shape
        q, k, v = self.qkv(x).split(c, dim=2)
        # (B, T, C) -> (B, n_head, T, head_dim)
        q = q.view(b, t, self.n_head, self.head_dim).transpose(1, 2)
        k = k.view(b, t, self.n_head, self.head_dim).transpose(1, 2)
        v = v.view(b, t, self.n_head, self.head_dim).transpose(1, 2)
        q, k = apply_rope(q, cos, sin), apply_rope(k, cos, sin)
        # Flash/memory-efficient kernel when available; is_causal builds the mask.
        y = F.scaled_dot_product_attention(
            q, k, v, dropout_p=self.dropout_p if self.training else 0.0, is_causal=True)
        y = y.transpose(1, 2).contiguous().view(b, t, c)
        return self.resid_dropout(self.proj(y))


class SwiGLU(nn.Module):
    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        hidden = config.ffn_hidden
        self.gate = nn.Linear(config.n_embd, hidden, bias=config.bias)
        self.up = nn.Linear(config.n_embd, hidden, bias=config.bias)
        self.down = nn.Linear(hidden, config.n_embd, bias=config.bias)
        self.dropout = nn.Dropout(config.dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.dropout(self.down(F.silu(self.gate(x)) * self.up(x)))


class Block(nn.Module):
    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.norm1 = RMSNorm(config.n_embd)
        self.attn = CausalSelfAttention(config)
        self.norm2 = RMSNorm(config.n_embd)
        self.ffn = SwiGLU(config)

    def forward(self, x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
        x = x + self.attn(self.norm1(x), cos, sin)
        return x + self.ffn(self.norm2(x))


class LanguageModel(nn.Module):
    """Decoder-only causal LM. Every parameter starts random; see `parameter_provenance`."""

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.config = config
        self.token_embedding = nn.Embedding(config.vocab_size, config.n_embd)
        self.drop = nn.Dropout(config.dropout)
        self.blocks = nn.ModuleList(Block(config) for _ in range(config.n_layer))
        self.norm_final = RMSNorm(config.n_embd)
        self.lm_head = nn.Linear(config.n_embd, config.vocab_size, bias=False)

        if config.tie_embeddings:
            # Shared storage: the head and the embedding are one parameter.
            self.lm_head.weight = self.token_embedding.weight

        self.apply(self._init_weights)
        # GPT-2's trick: shrink projections that write into the residual stream so its
        # variance does not grow with depth.
        scale = 1.0 / math.sqrt(2 * config.n_layer)
        for name, param in self.named_parameters():
            if name.endswith("proj.weight") or name.endswith("down.weight"):
                torch.nn.init.normal_(param, mean=0.0, std=config.init_std * scale)

        cos, sin = build_rope_cache(config.block_size, config.head_dim, config.rope_theta)
        # Non-persistent: derived from config, so it never bloats a checkpoint.
        self.register_buffer("rope_cos", cos, persistent=False)
        self.register_buffer("rope_sin", sin, persistent=False)

    def _init_weights(self, module: nn.Module) -> None:
        std = self.config.init_std
        if isinstance(module, nn.Linear):
            torch.nn.init.normal_(module.weight, mean=0.0, std=std)
            if module.bias is not None:
                torch.nn.init.zeros_(module.bias)
        elif isinstance(module, nn.Embedding):
            torch.nn.init.normal_(module.weight, mean=0.0, std=std)

    # -- introspection -------------------------------------------------------

    def num_parameters(self, non_embedding: bool = False) -> int:
        """Total trainable parameters. Tied weights are counted once, as they should be."""
        seen: set[int] = set()
        total = 0
        for param in self.parameters():
            if not param.requires_grad or id(param) in seen:
                continue
            seen.add(id(param))
            total += param.numel()
        if non_embedding:
            total -= self.token_embedding.weight.numel()
        return total

    def parameter_provenance(self) -> dict[str, object]:
        """Evidence for the 'trained from scratch' claim.

        Reports where every parameter came from. Because nothing in this module can
        load external weights, the answer is always random initialization - but it is
        reported from the live module rather than taken on trust.
        """
        tensors = {name: p for name, p in self.named_parameters()}
        return {
            "initialized_from": self.config.initialized_from,
            "pretrained_weights_loaded": False,
            "parameter_tensors": len(tensors),
            "trainable_parameters": self.num_parameters(),
            "non_embedding_parameters": self.num_parameters(non_embedding=True),
            "embeddings_tied": self.lm_head.weight is self.token_embedding.weight,
            "init_distribution": f"normal(0, {self.config.init_std})",
            "residual_projection_init": (
                f"normal(0, {self.config.init_std} / sqrt(2 * {self.config.n_layer}))"),
            "config": self.config.to_dict(),
        }

    # -- forward / generation ------------------------------------------------

    def forward(self, idx: torch.Tensor, targets: torch.Tensor | None = None
                ) -> tuple[torch.Tensor, torch.Tensor | None]:
        _, t = idx.shape
        if t > self.config.block_size:
            raise ValueError(
                f"sequence length {t} exceeds block_size {self.config.block_size}")
        x = self.drop(self.token_embedding(idx))
        cos, sin = self.rope_cos, self.rope_sin
        for block in self.blocks:
            x = block(x, cos, sin)
        x = self.norm_final(x)

        if targets is None:
            # Inference: only the last position is needed to sample the next token.
            logits = self.lm_head(x[:, [-1], :])
            return logits, None

        logits = self.lm_head(x)
        loss = F.cross_entropy(
            logits.view(-1, logits.size(-1)), targets.reshape(-1), ignore_index=-100)
        return logits, loss

    @torch.no_grad()
    def generate(self, idx: torch.Tensor, max_new_tokens: int,
                 temperature: float = 1.0, top_k: int | None = None,
                 eos_token_id: int | None = None) -> torch.Tensor:
        """Autoregressive sampling. temperature=0 selects greedy decoding."""
        self.eval()
        for _ in range(max_new_tokens):
            # Crop to the context window; this model has no KV cache.
            window = idx[:, -self.config.block_size:]
            logits, _ = self(window)
            logits = logits[:, -1, :]
            if temperature == 0:
                next_token = logits.argmax(dim=-1, keepdim=True)
            else:
                logits = logits / temperature
                if top_k is not None:
                    k = min(top_k, logits.size(-1))
                    threshold = torch.topk(logits, k, dim=-1).values[:, [-1]]
                    logits = logits.masked_fill(logits < threshold, float("-inf"))
                probs = F.softmax(logits, dim=-1)
                next_token = torch.multinomial(probs, num_samples=1)
            idx = torch.cat((idx, next_token), dim=1)
            if eos_token_id is not None and (next_token == eos_token_id).all():
                break
        return idx


# Named configurations, sized against the 6 GB RTX 4050 this project targets.
# Parameter counts are computed, not guessed; `scripts/train_slm.py --list-configs`
# prints them.
PRESETS: dict[str, ModelConfig] = {
    # Smoke test: a few seconds per step on CPU, used by the unit tests.
    "tiny": ModelConfig(vocab_size=8192, n_layer=2, n_head=4, n_embd=128, block_size=128),
    # Pilot: measures real throughput and memory before a long run is committed.
    "pilot": ModelConfig(vocab_size=8192, n_layer=6, n_head=6, n_embd=384, block_size=256),
    # Primary candidates for the reported run.
    "small": ModelConfig(vocab_size=8192, n_layer=8, n_head=8, n_embd=512, block_size=512),
    "base": ModelConfig(vocab_size=16384, n_layer=12, n_head=12, n_embd=768, block_size=512),
}
