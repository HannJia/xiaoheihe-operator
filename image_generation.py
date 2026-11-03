"""直接调用 OpenAI 兼容生图接口（/images/generations）生成帖子配图。

不依赖「麦麦绘图」插件。用户既可以用 MaiBot 已加载的「画图」provider，
也可以在插件配置里单独填一个中转站（base_url / api_key / model）。

接口约定（已实测飞鱼中转站）：
  POST {base_url}/images/generations
  body: {"model": ..., "prompt": ..., "n": 1, "size": "1024x1024"}
  返回: {"data": [{"b64_json": "..."}]} 或 {"data": [{"url": "..."}]}

用 urllib 实现，与插件 vendor_xiaoheihe 保持一致，避免引入额外依赖。
"""

from __future__ import annotations

import asyncio
import base64
import json
import urllib.error
import urllib.request
from typing import Any


class ImageGenerationError(RuntimeError):
    """生图失败，调用方应降级为纯文字发帖，而不是抛给上层。"""


class DirectImageGenerator:
    """OpenAI 兼容生图，封装成异步接口，方便在插件里 await。"""

    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.model = model

    async def generate(self, prompt: str) -> bytes:
        """生成一张图，返回 PNG/JPEG 字节。失败抛 ImageGenerationError。"""

        if not self.base_url or not self.api_key or not self.model:
            raise ImageGenerationError("生图配置不完整：需要 base_url / api_key / model")

        url = self.base_url + "/images/generations"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "n": 1,
            "size": "1024x1024",
        }

        def _sync_call() -> bytes:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                method="POST",
                headers={
                    "Authorization": "Bearer " + self.api_key,
                    "Content-Type": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    raw = resp.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass
                raise ImageGenerationError(f"生图接口返回 {exc.code}: {detail}") from exc
            except (urllib.error.URLError, OSError) as exc:
                raise ImageGenerationError(f"生图请求失败: {type(exc).__name__}") from exc

            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ImageGenerationError("生图接口返回非 JSON") from exc

            items = data.get("data") or []
            if not items:
                raise ImageGenerationError(
                    f"生图接口没有返回图片数据: {raw[:300]}"
                )

            item = items[0]
            if item.get("b64_json"):
                try:
                    return base64.b64decode(item["b64_json"])
                except Exception as exc:
                    raise ImageGenerationError("图片 base64 解码失败") from exc
            if item.get("url"):
                try:
                    with urllib.request.urlopen(item["url"], timeout=180) as img_resp:
                        return img_resp.read()
                except (urllib.error.URLError, OSError) as exc:
                    raise ImageGenerationError(f"下载生成图片失败: {type(exc).__name__}") from exc
            raise ImageGenerationError("生图返回里既没有 b64_json 也没有 url")

        return await asyncio.get_running_loop().run_in_executor(None, _sync_call)


def build_generator(base_url: str, api_key: str, model: str) -> DirectImageGenerator | None:
    """三个参数都填了才返回生成器，否则返回 None。"""

    if base_url and api_key and model:
        return DirectImageGenerator(base_url=base_url, api_key=api_key, model=model)
    return None
