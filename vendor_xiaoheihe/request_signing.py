from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Any

from .models import Credentials

# The hkey algorithm is independently ported to Python from the MIT-licensed
# heybox-core implementation by XiaHouSheng. See THIRD_PARTY_NOTICES.md.
_HKEY_ALPHABET = "AB45STUVWZEFGJ6CH01D237IXYPQRKLMN89"  # gitleaks:allow
_WORKSHOP_RND_VERSION = "15"
_WORKSHOP_RND_KEY = "Z7mFG4tQp9Ws2LxB8H"  # Public web-client compatibility value.
# MD5 is required by the upstream compatibility contract. It is not used for
# password storage, integrity protection, or any local security decision.


@dataclass(frozen=True, slots=True)
class SignedRequest:
    params: dict[str, str]
    headers: dict[str, str]


class RequestSigner:
    """Build Xiaoheihe's per-request hkey, timestamp and nonce query fields."""

    def sign(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
        signing_key: str,
        device_id: str,
        include_device_id: bool = True,
        now: int | None = None,
        nonce: str | None = None,
    ) -> SignedRequest:
        timestamp = now if now is not None else int(time.time())
        request_nonce = (
            nonce
            or hashlib.md5(
                f"{timestamp}{secrets.token_hex(16)}".encode(),
                usedforsecurity=False,
            )
            .hexdigest()
            .upper()
        )
        normalized = {
            str(key): str(value)
            for key, value in sorted((params or {}).items())
            if value is not None
        }
        normalized.setdefault("_time", str(timestamp))
        normalized.setdefault("nonce", request_nonce)
        normalized.setdefault("hkey", generate_hkey(path, timestamp, request_nonce))
        if device_id and include_device_id:
            normalized.setdefault("device_id", device_id)
        # These arguments remain in the boundary for future, independently
        # verified Workshop signing. They are not part of the GET hkey.
        del method, json_body, signing_key, include_device_id
        return SignedRequest(params=normalized, headers={})


def generate_device_id() -> str:
    """Return the stable 32-character web client identifier expected upstream."""

    return secrets.token_hex(16)


def generate_xhh_token_id(
    *,
    now: int | None = None,
    random_parts: tuple[str, str, str] | None = None,
) -> str:
    """Build the non-secret web client cookie used by Xiaoheihe requests.

    The shape is independently implemented from the MIT-licensed heybox-bot
    client. Random input is used instead of copying constants from unlicensed
    reference projects.
    """

    timestamp = str(now if now is not None else int(time.time()))
    parts = random_parts or tuple(secrets.token_hex(16) for _ in range(3))
    raw = b"".join(
        hashlib.md5(part.encode(), usedforsecurity=False).digest() for part in (timestamp, *parts)
    )
    return base64.b64encode(raw + b"\x00").decode("ascii")


def ensure_client_identity(
    credentials: Credentials,
    *,
    device_id: str = "",
) -> bool:
    """Fill missing web-client metadata and report whether credentials changed."""

    changed = False
    if not credentials.device_id:
        credentials.device_id = device_id or generate_device_id()
        changed = True
    if not credentials.cookies.get("x_xhh_tokenid"):
        credentials.cookies["x_xhh_tokenid"] = generate_xhh_token_id()
        changed = True
    return changed


def generate_hkey(path: str, timestamp: int, nonce: str) -> str:
    normalized_path = f"/{'/'.join(part for part in str(path).split('/') if part)}/"
    parts = (
        _map_to_alphabet(str(timestamp + 1), _HKEY_ALPHABET[:-2]),
        _map_to_alphabet(normalized_path, _HKEY_ALPHABET),
        _map_to_alphabet(str(nonce), _HKEY_ALPHABET),
    )
    interleaved = "".join(
        part[index]
        for index in range(max(len(part) for part in parts))
        for part in parts
        if index < len(part)
    )[:20]
    digest = hashlib.md5(interleaved.encode(), usedforsecurity=False).hexdigest()
    mixed = _mix_tail([ord(char) for char in digest[-6:]])
    suffix = str(sum(mixed) % 100).zfill(2)
    prefix = _map_to_alphabet(digest[:5], _HKEY_ALPHABET[:-4])
    return f"{prefix}{suffix}"


def _map_to_alphabet(value: str, alphabet: str) -> str:
    return "".join(alphabet[ord(char) % len(alphabet)] for char in value)


def _xtime(value: int) -> int:
    return (((value << 1) ^ 27) & 255) if value & 128 else value << 1


def _q(value: int) -> int:
    return _xtime(value) ^ value


def _r(value: int) -> int:
    return _q(_xtime(value))


def _y(value: int) -> int:
    return _r(_q(_xtime(value)))


def _g(value: int) -> int:
    return _y(value) ^ _r(value) ^ _q(value)


def _mix_tail(values: list[int]) -> list[int]:
    first, second, third, fourth = values[:4]
    return [
        _g(first) ^ _y(second) ^ _r(third) ^ _q(fourth),
        _q(first) ^ _g(second) ^ _y(third) ^ _r(fourth),
        _r(first) ^ _q(second) ^ _g(third) ^ _y(fourth),
        _y(first) ^ _r(second) ^ _q(third) ^ _g(fourth),
        *values[4:],
    ]


def generate_workshop_rnd(timestamp: int, nonce: str) -> str:
    """Build the v15 HMAC-SHA256 query signature used by web comment writes."""

    message = f"{_WORKSHOP_RND_KEY}{nonce}{timestamp}:{nonce}".encode("utf-8")
    signature = hmac.new(
        _WORKSHOP_RND_KEY.encode("utf-8"), message, hashlib.sha256
    ).hexdigest()
    return f"{_WORKSHOP_RND_VERSION}:{signature}"
