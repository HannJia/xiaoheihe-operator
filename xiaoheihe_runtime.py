from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

try:
    from .vendor_xiaoheihe.api_client import XiaoheiheApiClient
    from .vendor_xiaoheihe.models import Credentials, LoginState, QRLoginSession
    from .vendor_xiaoheihe.security import secure_unlink, validate_profile_id
except ImportError:  # pragma: no cover - direct Runner fallback
    from vendor_xiaoheihe.api_client import XiaoheiheApiClient
    from vendor_xiaoheihe.models import Credentials, LoginState, QRLoginSession
    from vendor_xiaoheihe.security import secure_unlink, validate_profile_id


class CredentialStore:
    def __init__(self, data_dir: Path) -> None:
        self.directory = data_dir / "credentials"
        self.directory.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            self.directory.chmod(0o700)

    def path(self, profile_id: str) -> Path:
        safe = validate_profile_id(profile_id)
        return (self.directory / f"{safe}.json").resolve()

    def load(self, profile_id: str) -> Credentials | None:
        path = self.path(profile_id)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("cookies"), dict):
            raise ValueError("小黑盒凭证文件格式无效")
        return Credentials(
            profile_id=validate_profile_id(str(payload.get("profile_id", profile_id))),
            uid=str(payload.get("uid", "")),
            nickname=str(payload.get("nickname", "")),
            cookies={str(k): str(v) for k, v in payload["cookies"].items()},
            access_token=str(payload.get("access_token", "")),
            refresh_token=str(payload.get("refresh_token", "")),
            device_id=str(payload.get("device_id", "")),
            signing_key=str(payload.get("signing_key", "")),
            logged_in_at=str(payload.get("logged_in_at", "")),
        )

    def save(self, credentials: Credentials) -> None:
        path = self.path(credentials.profile_id)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(credentials), ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
        if os.name == "posix":
            path.chmod(0o600)

    def remove(self, profile_id: str) -> None:
        """Remove a profile after the remote service rejects its session."""

        secure_unlink(self.path(profile_id))


class XiaoheiheRuntime:
    def __init__(self, data_dir: Path, profile_id: str) -> None:
        self.profile_id = validate_profile_id(profile_id)
        self.store = CredentialStore(data_dir)
        self.credentials = self.store.load(self.profile_id)
        self.session: QRLoginSession | None = None
        self._client: XiaoheiheApiClient | None = None
        self._lock = asyncio.Lock()
        self._qr_lock = asyncio.Lock()

    async def client(self, *, anonymous: bool = False) -> XiaoheiheApiClient:
        async with self._lock:
            if not anonymous and self._client is not None:
                return self._client
            client = XiaoheiheApiClient(
                self.profile_id,
                credentials=None if anonymous else self.credentials,
                on_auth_invalid=self._on_auth_invalid,
            )
            await client.start()
            if not anonymous:
                self._client = client
            return client

    async def request_qr(self) -> dict[str, Any]:
        async with self._qr_lock:
            client = await self.client(anonymous=True)
            session = await client.request_qr()
            self.session = session
            await client.close()
            return {
                "profile_id": self.profile_id,
                "qr_content": session.qr_content,
                "request_id": session.request_id,
                "expires_at": session.expires_at,
                "remaining_seconds": max(0, int(session.expires_at - time.time())),
            }

    async def check_qr(self) -> dict[str, Any]:
        async with self._qr_lock:
            session = self.session
            if session is None:
                return {"state": LoginState.IDLE.value, "message": "请先生成二维码"}
            client = await self.client(anonymous=True)
            state, message, credentials = await client.check_qr(session)
            await client.close()
            # A newer request may have replaced this session while the network
            # call was in flight. Never let an old response overwrite it.
            if self.session is not session:
                return {
                    "state": self.session.state.value if self.session else LoginState.IDLE.value,
                    "message": "二维码已更新，请继续使用当前二维码",
                    "profile_id": self.profile_id,
                    "nickname": self.credentials.nickname if self.credentials else "",
                }
            session.state = state
            session.message = message
            if credentials is not None:
                self.credentials = credentials
                self.store.save(credentials)
                self.session = None
                if self._client is not None:
                    await self._client.close()
                    self._client = None
            return {
                "state": state.value,
                "message": message,
                "profile_id": self.profile_id,
                "nickname": credentials.nickname if credentials else (self.credentials.nickname if self.credentials else ""),
            }

    async def status(self) -> dict[str, Any]:
        if self.credentials is None:
            return {"state": LoginState.IDLE.value, "has_credentials": False, "profile_id": self.profile_id}
        try:
            client = await self.client()
            info = await client.check_credentials()
            return {
                "state": LoginState.SUCCESS.value,
                "has_credentials": True,
                "profile_id": self.profile_id,
                "uid": self.credentials.uid,
                "nickname": self.credentials.nickname,
                "account": {"keys": sorted(info)[:40]} if isinstance(info, dict) else {},
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "state": "credential_invalid",
                "has_credentials": True,
                "profile_id": self.profile_id,
                "error": type(exc).__name__,
            }

    async def _on_auth_invalid(self, profile_id: str, status_code: int) -> None:
        if profile_id == self.profile_id:
            self.credentials = None
            self.store.remove(profile_id)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

