from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from vendor_xiaoheihe.api_client import API_BASE_URL, SendUncertainError, XiaoheiheApiClient
from vendor_xiaoheihe.endpoints import EndpointName, endpoint
from vendor_xiaoheihe.http_compat import NetworkError
from vendor_xiaoheihe.models import Credentials, RoutingTarget
from vendor_xiaoheihe.request_signing import generate_hkey, generate_workshop_rnd, generate_web_rnd16
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import base64
import json


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
            "x_client_type": "weboutapp",
            "x_os_type": "Windows",
            "web_version": "3.0",
            "version": "999.0.4",
        }
        assert {key: params[key] for key in expected_base} == expected_base
        assert params["heybox_id"] == "fixture-user-id"
        assert params["device_id"]
        assert params["hkey"] == generate_hkey(
            "/bbs/app/comment/create", int(params["_time"]), params["nonce"]
        )
        assert params["_rnd"].startswith("16:")
        raw = base64.urlsafe_b64decode(params["_rnd"][3:] + "===")
        decoded = AESGCM(base64.b64decode("HFFSTsqmvG3uGxPNXDQW+1VARfqgG4nT6LS1PX7F8NQ=")).decrypt(
            raw[:12], raw[12:], b"heybox-rnd:v16"
        )
        assert json.loads(decoded) == [params["_time"], params["nonce"], "/bbs/app/comment/create/"]
        await client.close()

    def test_rnd16_matches_official_webcrypto_vector(self):
        self.assertEqual(
            generate_web_rnd16(1780000000, "0123456789ABCDEF0123456789ABCDEF", "/bbs/app/comment/create", iv=bytes(12)),
            "16:AAAAAAAAAAAAAAAAVlN0UbTYlVieEahJ7DF4_g8e6NO6QBueggbGwoFKj5NGnBSFyTyhSdYy2isCQhgl-0646MvumLxa64BYzD4LnBJBuLHZd50R8xchY1LenxUH-mUhkh9kqB19PJ8",
        )

    def test_gateway_403_preserves_credentials_but_401_invalidates(self):
        async def scenario(status):
            transport = _RecordingClient()
            async def request(*args, **kwargs):
                res = _Response({"status":"failed"})
                res.status_code = status
                return res
            transport.request = request
            invalid = []
            async def callback(profile, code): invalid.append(code)
            client = _make_client(transport)
            client._on_auth_invalid = callback
            with self.assertRaises(Exception):
                await client.check_credentials()
            await client.close()
            return invalid
        self.assertEqual(asyncio.run(scenario(403)), [])
        self.assertEqual(asyncio.run(scenario(401)), [401])

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

    def test_like_post_uses_web_form_write_contract(self):
        asyncio.run(self._assert_like_request())

    async def _assert_like_request(self):
        transport = _RecordingClient()
        client = _make_client(transport)

        await client.like_post("1000")

        assert len(transport.calls) == 1
        method, path, request = transport.calls[0]
        assert method == "POST"
        assert path == "/bbs/app/profile/award/link"
        assert request["data"] == {"link_id": "1000", "award_type": "1"}
        params = request["params"]
        assert params["web_version"] == "3.0"
        assert "heybox_id" not in params
        assert "device_id" not in params
        assert params["hkey"] == generate_hkey(
            "/bbs/app/profile/award/link", int(params["_time"]), params["nonce"]
        )
        assert params["_rnd"] == generate_workshop_rnd(int(params["_time"]), params["nonce"])
        await client.close()
