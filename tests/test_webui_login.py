from __future__ import annotations

import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from qr_generator import qr_data_url, qr_png_bytes
from vendor_xiaoheihe.models import Credentials
from xiaoheihe_runtime import CredentialStore


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def test_qr_generator_emits_png_data_url_without_external_dependencies():
    value = "https://api.xiaoheihe.cn/account/qr_login/?qr=test-token&app=web"
    payload = qr_png_bytes(value)
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(payload) > 500
    data_url = qr_data_url(value)
    assert data_url.startswith("data:image/png;base64,")
    assert base64.b64decode(data_url.split(",", 1)[1]).startswith(b"\x89PNG")


def test_webui_declares_custom_login_page_and_static_apis():
    declaration = json.loads((PLUGIN_ROOT / "webui.json").read_text(encoding="utf-8"))
    page = declaration["pages"][0]
    html = (PLUGIN_ROOT / page["entry"]).read_text(encoding="utf-8")
    assert declaration["schema_version"] == 2
    assert "custom_pages" in declaration["required_capabilities"]
    assert page["mode"] == "custom"
    assert page["queries"]["status"]["api"] == "webui_login_status"
    assert page["actions"]["request_qr"]["api"] == "webui_request_login"
    assert "maibot.query('status')" in html
    assert "maibot.action('request_qr'" in html
    assert "refreshInFlight" in html
    assert "qr_content" not in html
    assert "https://" not in html


def test_invalid_credentials_can_be_removed_from_the_profile_store():
    with TemporaryDirectory() as directory:
        store = CredentialStore(Path(directory))
        credentials = Credentials(
            profile_id="default",
            uid="u-1",
            nickname="test",
            cookies={"session": "secret"},
        )
        store.save(credentials)
        path = store.path("default")
        assert path.exists()
        store.remove("default")
        assert not path.exists()
