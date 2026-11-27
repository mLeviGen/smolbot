import re

PATTERNS = [
    re.compile(r"\b\d{5,16}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"(?i)\b(?:authorization:\s*(?:bearer|basic)|bearer)\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?i)\b(?:password|passwd|secret|api[_-]?key|[a-z0-9_]*token)\s*[:=]\s*"
        r"""(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;]+)"""
    ),
    re.compile(r"(?i)https?://[^/\s@]+@"),
    re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?"
        r"(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|$)"
    ),
]


def redact(text, secrets):
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            text = text.replace(secret, "[REDACTED]")
    for pattern in PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text
