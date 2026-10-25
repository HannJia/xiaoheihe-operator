from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from vendor_xiaoheihe.api_client import API_BASE_URL, SendUncertainError, XiaoheiheApiClient
from vendor_xiaoheihe.endpoints import EndpointName, endpoint
from vendor_xiaoheihe.http_compat import NetworkError
from vendor_xiaoheihe.models import Credentials, RoutingTarget
from vendor_xiaoheihe.request_signing import generate_hkey, generate_workshop_rnd


class _Cookies:
    def __init__(self):
        self.jar = []

    def set(self, name: str, value: str) -> None:
        self.jar = [cookie for cookie in self.jar if cookie.name != name]
        self.jar.append(SimpleNamespace(name=name, value=value))


class _Response:
    status_code = 200
    headers = {}

    def __init__(self, payload):
        self.payload = payload
        self.cookies = _Cookies()

    def json(self):
        return self.payload


class _RecordingClient:
    def __init__(self, *, fail=False):
        self.cookies = _Cookies()
        self.calls = []
        self.fail = fail

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.fail:
            raise NetworkError("offline test failure")
        return _Response({"status": "ok", "commentid": "fixture-comment-id"})

    async def aclose(self):
        pass


def _make_client(http_client: _RecordingClient) -> XiaoheiheApiClient:
    return XiaoheiheApiClient(
        "default",
        credentials=Credentials(
            profile_id="default",
            uid="fixture-user-id",
            nickname="fixture-user",
            cookies={"user_pkey": "fixture-cookie"},
        ),
        client=http_client,
        min_request_interval_seconds=0,
    )


def test_comment_endpoint_and_signing_known_vector():
    contract = endpoint(EndpointName.CREATE_COMMENT)
    assert contract.base_url == API_BASE_URL
    assert contract.path == "/bbs/app/comment/create"
    assert contract.retry_safe is False
    assert generate_hkey(
        contract.path, 1_780_000_000, "0123456789ABCDEF0123456789ABCDEF"
    ) == "S1Z1D23"
    assert generate_workshop_rnd(
        1_780_000_000, "0123456789ABCDEF0123456789ABCDEF"
    ) == "15:356487f6474322af6c2125db6fe55d328f90f27c71ba2e2a35523243eca4fc85"


class CommentRequestContractTests(unittest.TestCase):
    def test_comment_write_constructs_web_request_without_network(self):
        for parent_id, root_id in (("-1", "-1"), ("1002", "1001")):
            with self.subTest(parent_id=parent_id, root_id=root_id):
                asyncio.run(self._assert_comment_write(parent_id, root_id))

    async def _assert_comment_write(self, parent_id, root_id):
        transport = _RecordingClient()
        client = _make_client(transport)
        route = RoutingTarget(
            profile_id="default",
            post_id="1000",
            root_comment_id="" if root_id == "-1" else root_id,
            parent_comment_id="" if parent_id == "-1" else parent_id,
        )
        content = "fixture reply & + %" + chr(10) + "第二行"
        result = await client.send_comment(route, content)

        assert result.confirmed
        assert result.external_comment_id == "fixture-comment-id"
        assert len(transport.calls) == 1
        method, path, request = transport.calls[0]
        assert method == "POST"
        assert path == "/bbs/app/comment/create"
        assert request["headers"]["Content-Type"] == (
            "application/x-www-form-urlencoded;charset=utf-8"
        )
        assert request["headers"]["Origin"] == "https://www.xiaoheihe.cn"
        assert request["data"] == {
            "is_cy": "0",
            "link_id": "1000",
            "reply_id": parent_id,
            "root_id": root_id,
            "text": content,
        }

        params = request["params"]
        expected_base = {
            "app": "heybox",
            "os_type": "web",
            "x_app": "heybox_website",
            "x_client_type": "web",
            "x_os_type": "Windows",
            "x_client_version": "",
            "client_type": "web",
            "web_version": "3.0",
            "version": "999.0.4",
        }
        assert {key: params[key] for key in expected_base} == expected_base
        assert "heybox_id" not in params
        assert "device_id" not in params
        assert params["hkey"] == generate_hkey(
            "/bbs/app/comment/create", int(params["_time"]), params["nonce"]
        )
        assert params["_rnd"] == generate_workshop_rnd(
            int(params["_time"]), params["nonce"]
        )
        await client.close()

    def test_comment_write_timeout_is_uncertain_and_never_retried(self):
        asyncio.run(self._assert_timeout_is_uncertain())

    async def _assert_timeout_is_uncertain(self):
        transport = _RecordingClient(fail=True)
        client = _make_client(transport)
        route = RoutingTarget(profile_id="default", post_id="1000")

        with self.assertRaises(SendUncertainError):
            await client.send_comment(route, "offline test")

        self.assertEqual(len(transport.calls), 1)
        await client.close()
