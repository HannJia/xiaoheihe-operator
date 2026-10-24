from __future__ import annotations

import asyncio
import base64

from drawpic_gateway import DrawingGateway
from vendor_xiaoheihe.api_client import _cos_authorization, _image_info
from vendor_xiaoheihe.models import Notification, NotificationType
from vendor_xiaoheihe.parsers import parse_notifications


def test_notification_parser_returns_typed_wrapper():
    page = parse_notifications(
        "default",
        {
            "result": {
                "items": [
                    {
                        "message_type": 2,
                        "message_id": "m-1",
                        "comment": {"comment_id": "c-1", "text": "你好"},
                        "post": {"link_id": "p-1", "title": "游戏"},
                        "sender": {"uid": "u-1", "nickname": "玩家"},
                    }
                ]
            }
        },
        NotificationType.REPLY,
    )
    assert len(page.items) == 1
    notification = page.items[0]["notification"]
    assert isinstance(notification, Notification)
    assert notification.message_id.startswith("xhh_reply_m-1")
    assert notification.post_id == "p-1"


class _FakeApi:
    async def call(self, name: str, **kwargs):
        if name.endswith("submit_v2"):
            return {"success": True, "result": {"task_id": "task-1"}}
        if name.endswith("image_v2"):
            return {
                "success": True,
                "result": {"data": base64.b64encode(b"image").decode(), "done": True},
            }
        return {"success": True, "result": {"status": "completed", "task_id": "task-1"}}


def test_drawpic_gateway_uses_formal_api():
    async def run():
        gateway = DrawingGateway(_FakeApi())
        submitted = await gateway.submit("游戏日常", request_id="req-1", source_images=[b"ref"])
        assert submitted["task_id"] == "task-1"
        image = await gateway.read_image("task-1")
        assert image == b"image"

    asyncio.run(run())


def test_media_contract_uses_image_dimensions_and_cos_signature():
    png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (640).to_bytes(4, "big") + (480).to_bytes(4, "big")
    mime, width, height = _image_info(png_header, "image/png")
    assert (mime, width, height) == ("image/png", 640, 480)
    signature = _cos_authorization(
        {"tmpSecretId": "id", "tmpSecretKey": "key", "sessionToken": "session"},
        "PUT",
        "/a.png",
        {"host": "bucket.cos.ap-shanghai.myqcloud.com", "content-type": "image/png"},
        100,
        200,
    )
    assert "q-sign-algorithm=sha1" in signature
    assert "q-ak=id" in signature
