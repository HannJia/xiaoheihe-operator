from __future__ import annotations

import asyncio
import base64
import time
from typing import Any


class DrawingGateway:
    """只通过 maimai-drawpic-plugin 的正式 API 调用生图。"""

    def __init__(self, api: Any) -> None:
        self.api = api

    async def submit(
        self,
        prompt: str,
        *,
        request_id: str,
        model: str = "",
        source_images: list[bytes] | None = None,
    ) -> dict[str, Any]:
        encoded = [base64.b64encode(item).decode("ascii") for item in (source_images or [])]
        result = await self.api.call(
            "maimai-drawpic-plugin.drawpic_submit_v2",
            version="2",
            prompt=prompt,
            request_id=request_id,
            model=model,
            task_type="edit_image" if encoded else "draw",
            source_images=encoded,
            stream_id="",
        )
        if isinstance(result, dict):
            payload = result.get("result") if isinstance(result.get("result"), dict) else result
            # Depending on the host RPC adapter, a successful call is either
            # wrapped with success=true or returned as the v2 receipt itself.
            if result.get("success") is True or result.get("accepted") is True:
                if _find_task_id(payload):
                    return payload
        if not isinstance(result, dict) or result.get("success") is not True:
            # v2 submissions are idempotent by request_id. A worker restart
            # or a retry can therefore receive a rejection even though the
            # original task was accepted. Recover the persisted task instead
            # of submitting a second image generation request.
            recovered = await self.api.call(
                "maimai-drawpic-plugin.drawpic_request_v2",
                version="2",
                request_id=request_id,
            )
            if isinstance(recovered, dict):
                payload = recovered.get("result") if isinstance(recovered.get("result"), dict) else recovered
                if _find_task_id(payload):
                    return payload
            raise RuntimeError("绘图任务提交失败且找回任务失败")
        return result.get("result") if isinstance(result.get("result"), dict) else result

    async def wait(self, task_id: str, *, request_id: str, timeout_seconds: float = 120) -> dict[str, Any]:
        deadline = time.monotonic() + max(1.0, timeout_seconds)
        while time.monotonic() < deadline:
            response = await self.api.call(
                "maimai-drawpic-plugin.drawpic_task_v2",
                version="2",
                task_id=task_id,
            )
            payload = _unwrap_payload(response)
            status = str(payload.get("status", "")).casefold()
            # The drawpic worker can publish the terminal state before its
            # result index becomes readable. Keep polling in that narrow
            # window instead of attempting an image read that is guaranteed
            # to fail and marking the external post as uncertain.
            if status == "completed" and not payload.get("images"):
                await asyncio.sleep(2)
                continue
            if status in {"completed", "failed", "rejected", "cancelled", "unconfirmed"}:
                return payload
            await asyncio.sleep(2)
        return {"status": "pending", "task_id": task_id, "request_id": request_id}

    async def read_image(self, task_id: str, *, index: int = 0) -> bytes:
        chunks: list[bytes] = []
        offset = 0
        deadline = time.monotonic() + 120
        while True:
            if time.monotonic() >= deadline or len(chunks) >= 256:
                raise RuntimeError("绘图图片分块读取超时")
            response = await self.api.call(
                "maimai-drawpic-plugin.drawpic_image_v2",
                version="2",
                task_id=task_id,
                index=index,
                kind="original",
                offset=offset,
            )
            payload = _unwrap_payload(response)
            if not isinstance(payload, dict):
                raise RuntimeError("绘图图片读取响应无效")
            error = payload.get("error")
            if error:
                code = error.get("code") if isinstance(error, dict) else str(error)
                raise RuntimeError(f"绘图图片读取失败:{code}")
            encoded = str(payload.get("data", ""))
            if not encoded:
                raise RuntimeError("绘图图片读取返回空分块")
            data = base64.b64decode(encoded, validate=True)
            chunks.append(data)
            if bool(payload.get("done")):
                return b"".join(chunks)
            next_offset = int(payload.get("next_offset", offset + len(data)))
            if next_offset <= offset:
                raise RuntimeError("绘图图片读取偏移未前进")
            offset = next_offset


def _find_task_id(value: object) -> str:
    if isinstance(value, dict):
        for key in ("task_id", "taskId", "id"):
            item = value.get(key)
            if item not in (None, ""):
                return str(item)
        for item in value.values():
            found = _find_task_id(item)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_task_id(item)
            if found:
                return found
    return ""


def _unwrap_payload(value: object) -> object:
    """Accept direct, result-wrapped, or nested RPC response payloads."""

    current = value
    for _ in range(4):
        if not isinstance(current, dict):
            return current
        if any(key in current for key in ("status", "task_id", "images", "data", "error")):
            return current
        next_value = None
        for key in ("result", "data", "payload"):
            candidate = current.get(key)
            if isinstance(candidate, dict):
                next_value = candidate
                break
        if next_value is None:
            return current
        current = next_value
    return current

