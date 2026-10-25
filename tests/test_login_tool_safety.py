from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
import types
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_plugin_module():
    sdk = types.ModuleType("maibot_sdk")

    def field(*, default=None, default_factory=None, **kwargs):
        return default_factory() if default_factory is not None else default

    def decorator(*args, **kwargs):
        def apply(function):
            return function

        return apply

    class PluginConfigBase:
        pass

    class MaiBotPlugin:
        def __init__(self):
            self.ctx = None

    sdk.API = decorator
    sdk.Command = decorator
    sdk.Field = field
    sdk.MaiBotPlugin = MaiBotPlugin
    sdk.PluginConfigBase = PluginConfigBase
    sdk.Tool = decorator
    sys.modules["maibot_sdk"] = sdk

    httpx = types.ModuleType("httpx")
    httpx.Timeout = lambda *args, **kwargs: object()
    httpx.Limits = lambda *args, **kwargs: object()
    httpx.AsyncClient = type("AsyncClient", (), {})
    httpx.TimeoutException = type("TimeoutException", (Exception,), {})
    httpx.NetworkError = type("NetworkError", (Exception,), {})
    httpx.HTTPStatusError = type("HTTPStatusError", (Exception,), {})
    sys.modules["httpx"] = httpx

    module_name = "xiaoheihe_operator_login_safety_test"
    spec = importlib.util.spec_from_file_location(
        module_name,
        PLUGIN_ROOT / "plugin.py",
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_login_tool_returns_image_and_status_without_raw_qr_url():
    module = _load_plugin_module()
    plugin = module.XiaoheiheOperatorPlugin()
    plugin.config = module.XiaoheiheConfig()
    raw_url = "https://api.xiaoheihe.cn/account/qr_login/?qr=secret-token"

    class FakeSession:
        qr_content = raw_url
        expires_at = time.time() + 180

    class FakeRuntime:
        session = None
        credentials = None

        async def request_qr(self):
            self.session = FakeSession()
            return {"qr_content": raw_url}

    plugin._runtime = FakeRuntime()
    result = asyncio.run(plugin.handle_request_login())

    assert result["success"] is True
    assert result["state"] == "waiting_scan"
    assert result["qr_image"].startswith("data:image/png;base64,")
    assert "qr_content" not in result
    assert raw_url not in repr(result)
