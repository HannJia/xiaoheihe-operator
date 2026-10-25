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
    assert config.browse.feed_active_start_time == "08:00"
    assert config.browse.feed_active_end_time == "23:00"
    assert config.automation.post_active_start_time == "10:00"
    assert config.automation.post_active_end_time == "22:00"
    assert config.content.generate_post_images is False
    assert config.automation.posts_per_day == 1
    assert config.post_topic_ids == ["Steam", "PC 游戏"]
    assert module._time_window_active("08:00", "23:00", 12 * 3600)
    assert not module._time_window_active("08:00", "23:00", 23 * 3600)
    local_01 = module.time.mktime((2026, 1, 1, 1, 0, 0, 0, 0, -1))
    local_03 = module.time.mktime((2026, 1, 1, 3, 0, 0, 0, 0, -1))
    assert module._time_window_active("22:00", "02:00", local_01)
    assert module._time_window_active("09:00", "09:00", local_03)
