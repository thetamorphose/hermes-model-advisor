"""Deterministic redaction for exported audit data."""
from __future__ import annotations

import re
from typing import Any

PATTERNS = [
    ("PRIVATE_KEY", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.S)),
    ("OPENAI_KEY", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("GITHUB_TOKEN", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("GITHUB_PAT", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("AWS_ACCESS_KEY", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("BEARER", re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/-]{12,}")),
    ("ASSIGNMENT", re.compile(r"(?i)\b(password|token|api[_-]?key|secret)\s*([:=])\s*([^\s'\"&]{6,})")),
    ("URL_CREDENTIAL", re.compile(r"(https?://)([^\s/@:]+):([^\s/@]+)@")),
]


def redact_text(value: str) -> str:
    for label, pattern in PATTERNS:
        if label == "BEARER":
            value = pattern.sub(lambda m: m.group(1) + "<REDACTED:BEARER>", value)
        elif label == "ASSIGNMENT":
            value = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}<REDACTED:{label}>", value)
        elif label == "URL_CREDENTIAL":
            value = pattern.sub(lambda m: m.group(1) + "<REDACTED:URL_CREDENTIAL>@", value)
        else:
            value = pattern.sub(f"<REDACTED:{label}>", value)
    return value


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    return value
