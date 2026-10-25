from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def test_plugin_module_loads_with_runner_sdk_surface():
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

    sdk.Command = decorator
    sdk.API = decorator
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

    plugin_root = Path(__file__).resolve().parents[1]
    module_name = "xiaoheihe_operator_test_plugin"
    spec = importlib.util.spec_from_file_location(
        module_name,
        plugin_root / "plugin.py",
        submodule_search_locations=[str(plugin_root)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    assert hasattr(module, "XiaoheiheOperatorPlugin")
    assert module.create_plugin().__class__.__name__ == "XiaoheiheOperatorPlugin"
    config = module.XiaoheiheConfig()
    assert config.plugin.config_version == "0.1.0"
    assert config.enabled is True
    assert config.mode == "auto"
    assert config.browse.feed_interval_seconds == 600
    assert config.automation.posts_per_day == 1
    assert config.post_topic_ids == ["Steam", "PC 游戏"]
