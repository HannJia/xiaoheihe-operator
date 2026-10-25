from __future__ import annotations

import asyncio
import base64

from drawpic_gateway import DrawingGateway
from vendor_xiaoheihe.api_client import _cos_authorization, _image_info
from vendor_xiaoheihe.models import DirectMessage, Notification, NotificationType
from vendor_xiaoheihe.parsers import (
    parse_direct_message_page,
    parse_notifications,
    parse_thread_context,
)


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


def test_notification_parser_preserves_nested_reply_route():
    page = parse_notifications(
        "default",
        {
            "result": {
                "items": [
                    {
                        "message_type": 2,
                        "message_id": "m-nested",
                        "comment": {
                            "comment_id": "comment-child",
                            "root_comment_id": "comment-root",
                            "text": "楼中楼问题",
                        },
                        "post": {"link_id": "p-2"},
                    }
                ]
            }
        },
        NotificationType.REPLY,
    )
    notification = page.items[0]["notification"]
    assert notification.route.post_id == "p-2"
    assert notification.route.root_comment_id == "comment-root"
    assert notification.route.parent_comment_id == "comment-child"


def test_notification_parser_handles_post_mention_without_comment_target():
    page = parse_notifications(
        "default",
        {
            "result": {
                "items": [
                    {
                        "message_type": 16,
                        "message_id": "m-post",
                        "post": {"link_id": "p-3", "title": "有人提到我"},
                    }
                ]
            }
        },
        NotificationType.MENTION,
    )
    notification = page.items[0]["notification"]
    assert notification.post_id == "p-3"
    assert notification.root_comment_id == ""
    assert notification.parent_comment_id == ""
    assert notification.external_comment_id == "post_message_m-post"


def test_thread_parser_reads_root_specific_current_comment():
    context = parse_thread_context(
        {
            "result": {
                "link": {"linkid": "p-4", "title": "自己的帖子", "userid": "owner"},
                "comments": [],
                "current_comment": {
                    "comment": [
                        {
                            "commentid": "root-4",
                            "userid": "someone",
                            "text": "已有评论",
                        }
                    ]
                },
            }
        },
        "p-4",
    )
    assert [item["commentid"] for item in context.comments] == ["root-4"]


def test_direct_message_parser_normalizes_incoming_and_outgoing_messages():
    page = parse_direct_message_page(
        "default",
        {
            "result": {
                "messages": [
                    {
                        "msg_id": "dm-in-1",
                        "sender_uid": "big-account",
                        "sender_nickname": "大号",
                        "content": "你好呀",
                        "timestamp": 1700000000,
                        "conversation_id": "room-1",
                    },
                    {
                        "msg_id": "dm-out-1",
                        "sender_uid": "bot-1",
                        "content": "你好",
                        "timestamp": 1700000001,
                        "conversation_id": "room-1",
                    },
                ],
                "has_more": False,
            }
        },
        owner_uid="bot-1",
        source="direct_message_inbox",
    )
    assert len(page.items) == 2
    assert isinstance(page.items[0], DirectMessage)
    assert page.items[0].incoming is True
    assert page.items[0].peer_uid == "big-account"
    assert page.items[1].incoming is False
    assert page.items[1].conversation_id == "room-1"


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
