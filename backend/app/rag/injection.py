"""Prompt-injection screening for retrieved content.

Retrieved text is *data*. The structural defences (escaping, envelope fencing,
never placing document text in the system role) are what actually stop indirect
prompt injection; this detector is an additional signal used to quarantine and
warn, not the primary control. See docs/SECURITY.md.

A quarantined span is still shown to the user as evidence — hiding it would be
worse — but it is wrapped so the model treats it as inert, it cannot be the sole
support for a claim, and it is recorded in the audit trail.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Each pattern carries a weight. The score is the sum of matched weights,
# normalised by a saturation constant, so one strong pattern is enough.
_PATTERNS: list[tuple[str, re.Pattern[str], float]] = [
    ("ignore_previous", re.compile(
        r"\bignore\s+(all\s+|any\s+)?(previous|prior|earlier|above)\s+"
        r"(instructions?|prompts?|rules?|directions?)", re.I), 1.0),
    ("disregard", re.compile(
        r"\b(disregard|forget|override|discard)\s+(all\s+|any\s+|the\s+)?"
        r"(previous|prior|above|system|earlier)\b", re.I), 1.0),
    ("new_instructions", re.compile(
        r"\b(new|updated|revised)\s+(system\s+)?(instructions?|prompt|directive)s?\b:",
        re.I), 0.8),
    ("role_assumption", re.compile(
        r"\byou\s+are\s+now\s+(a|an|the)\b|\bact\s+as\s+(a|an|the)\s+(admin|root|system)",
        re.I), 0.7),
    ("system_prompt_leak", re.compile(
        r"\b(reveal|print|show|output|repeat|disclose)\s+(your\s+|the\s+)?"
        r"(system\s+prompt|instructions?|hidden\s+rules?|initial\s+prompt)", re.I), 0.9),
    ("data_exfiltration", re.compile(
        r"\b(reveal|dump|list|export|send|leak|disclose)\s+(all\s+)?"
        r"(tenant|customer|user|employee|other)?\s*"
        r"(data|records?|documents?|secrets?|passwords?|tokens?|api\s*keys?)", re.I), 1.0),
    ("policy_override", re.compile(
        r"\b(bypass|circumvent|disable|turn\s+off)\s+(the\s+)?"
        r"(policy|policies|safety|guardrails?|verification|access\s+control)", re.I), 1.0),
    ("tool_command", re.compile(
        r"\b(execute|run|eval)\s*\(|<\s*script\b|\bDROP\s+TABLE\b|\bDELETE\s+FROM\b", re.I),
     0.8),
    ("fake_system_turn", re.compile(
        r"(^|\n)\s*(system|assistant)\s*:\s*", re.I), 0.5),
    ("delimiter_forgery", re.compile(
        r"(</?\s*(system|system_instructions|untrusted_document|instructions)\s*>)"
        r"|(\[/?INST\])|(<\|im_(start|end)\|>)", re.I), 0.9),
    ("urgency_authority", re.compile(
        r"\b(this\s+is\s+(an\s+)?(admin|administrator|developer|anthropic|openai)\s+"
        r"(override|instruction|command))", re.I), 0.9),
]

_SATURATION = 1.0
QUARANTINE_THRESHOLD = 0.6


@dataclass
class InjectionVerdict:
    score: float = 0.0
    flagged: bool = False
    patterns: list[str] = field(default_factory=list)
    excerpts: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"score": round(self.score, 3), "flagged": self.flagged,
                "patterns": self.patterns}


def scan(text: str) -> InjectionVerdict:
    if not text:
        return InjectionVerdict()
    total = 0.0
    names: list[str] = []
    excerpts: list[str] = []
    for name, pattern, weight in _PATTERNS:
        match = pattern.search(text)
        if match:
            total += weight
            names.append(name)
            start = max(0, match.start() - 30)
            excerpts.append(text[start:match.end() + 30].replace("\n", " ").strip())
    score = min(1.0, total / _SATURATION) if total else 0.0
    return InjectionVerdict(score=score, flagged=score >= QUARANTINE_THRESHOLD,
                            patterns=names, excerpts=excerpts[:3])


# ---------------------------------------------------------------------------
# Neutralisation for prompt assembly
# ---------------------------------------------------------------------------

_FENCE_BREAKERS = re.compile(
    r"</?\s*(system_instructions|untrusted_document|evidence|instructions|system)\s*>",
    re.I)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def neutralise(text: str, max_chars: int = 4000) -> str:
    """Make a span safe to place inside an evidence envelope.

    Removes control characters, defuses forged envelope delimiters and any
    chat-template markers, and truncates. This is applied to EVERY retrieved
    span, flagged or not.
    """
    if not text:
        return ""
    cleaned = _CONTROL.sub(" ", text)
    cleaned = _FENCE_BREAKERS.sub(lambda m: m.group(0).replace("<", "(").replace(">", ")"),
                                  cleaned)
    cleaned = cleaned.replace("<|im_start|>", "(im_start)").replace("<|im_end|>", "(im_end)")
    cleaned = cleaned.replace("[INST]", "(INST)").replace("[/INST]", "(/INST)")
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + " ..."
    return cleaned
