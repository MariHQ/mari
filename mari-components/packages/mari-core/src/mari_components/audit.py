"""Storage-neutral tamper-evident audit values and hash-chain rules."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import typing as t
import uuid
from dataclasses import asdict, dataclass, field


# Key names whose values never belong in the audit store. Matched case-insensitively
# as substrings so camelCase and dashed variants (apiKey, x-api-key) are caught too;
# short stems that collide with ordinary words (pin, otp, cred, dsn, pat) are bounded.
_SENSITIVE = re.compile(
    r"passw(?:or)?d|passphrase|pwd"
    r"|token|jwt|bearer|secret|authorization|auth(?!or)|cookie|session"
    r"|credential|\bcreds?\b"
    r"|api[_-]?key|(?:private|ssh|signing|encryption|access|master|client|service)[_-]?key"
    r"|csrf|xsrf|\botp\b|totp|mfa|\bpin\b|nonce|salt|oauth"
    r"|\bssn\b|social[_-]?security|credit[_-]?card|card[_-]?number|\bcv[cv]\b|\biban\b"
    r"|account[_-]?number|routing[_-]?number"
    r"|connection[_-]?string|(?:database|db)[_-]?url|\bdsn\b|webhook|[_-]pat\b",
    re.I,
)


# String values that look like secrets are scrubbed whatever their key is called.
# Each pattern is a well-known credential shape; nothing here is an entropy guess,
# so content hashes, commit ids, and UUIDs pass through untouched.
_SECRET_VALUES = (
    # PEM private keys, whole block
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    # JWT: three base64url segments, header always starts with eyJ
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    # HTTP auth headers; the credential must contain a digit so prose like
    # "bearer token_expired_message" is left alone
    re.compile(r"\b(?:bearer|basic)\s+(?=[A-Za-z0-9._~+/=-]{16,}\b)(?=[^\s]*\d)[A-Za-z0-9._~+/=-]+", re.I),
    # vendor token prefixes
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),                                   # AWS
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b"),  # GitHub
    re.compile(r"\bxox[abpors]-[A-Za-z0-9-]{10,}\b"),                                # Slack
    re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+"),                # Slack webhook
    re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b"),                               # Anthropic, OpenAI
    re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}\b"),                         # Stripe
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),                                        # Google
)
# user:password@ inside any URL; only the password is replaced
_URL_CREDENTIALS = re.compile(r"(?<=://)([^/\s:@]+):([^@\s/]+)(?=@)")


def scrub(text: str) -> str:
    """Replace credential-shaped spans inside a string with [REDACTED]."""
    for pattern in _SECRET_VALUES:
        text = pattern.sub("[REDACTED]", text)
    return _URL_CREDENTIALS.sub(r"\1:[REDACTED]", text)


def redact(value: t.Any) -> t.Any:
    if isinstance(value, dict):
        return {str(key): ("[REDACTED]" if _SENSITIVE.search(str(key)) else redact(item))
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return scrub(value)
    return value


@dataclass(frozen=True, slots=True)
class AuditEvent:
    project_id: int
    actor_type: str
    actor_id: str
    actor_name: str
    action: str
    resource_type: str
    resource_id: str
    outcome: str = "success"
    reason: str = ""
    request_id: str = ""
    correlation_id: str = ""
    detail: dict[str, t.Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    def __post_init__(self) -> None:
        if self.project_id < 0:
            raise ValueError("project_id cannot be negative")
        if self.outcome not in {"success", "failure", "denied", "manual"}:
            raise ValueError("invalid audit outcome")
        if not self.action or not self.resource_type:
            raise ValueError("audit action and resource type are required")


def _canonical(row: dict[str, t.Any]) -> bytes:
    serializable = dict(row)
    when = serializable.get("occurred_at")
    if isinstance(when, dt.datetime):
        serializable["occurred_at"] = when.astimezone(dt.timezone.utc).isoformat()
    return json.dumps(serializable, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def chained_row(event: AuditEvent, previous_hash: str) -> dict[str, t.Any]:
    row = asdict(event)
    row["detail_json"] = json.dumps(
        redact(row.pop("detail")), sort_keys=True, separators=(",", ":"), default=str,
    )
    row["previous_hash"] = previous_hash
    row["event_hash"] = hashlib.sha256(previous_hash.encode() + _canonical(row)).hexdigest()
    return row


def verify_chain(rows: list[dict[str, t.Any]]) -> bool:
    previous_by_project: dict[int, str] = {}
    for original in sorted(rows, key=lambda row: (row["occurred_at"], row["event_id"])):
        row = dict(original)
        actual = row.pop("event_hash")
        project_id = int(row["project_id"])
        previous = previous_by_project.get(project_id, "")
        if row.get("previous_hash") != previous:
            return False
        if hashlib.sha256(previous.encode() + _canonical(row)).hexdigest() != actual:
            return False
        previous_by_project[project_id] = actual
    return True
