"""Run the existing pytest-style contract checks with stdlib unittest.

The MaiBot runtime image does not include pytest. Keeping these checks as
small functions makes them usable by pytest while this bridge keeps the same
coverage available in the supported offline unittest command.
"""

from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path


class ExistingContractChecks(unittest.TestCase):
    def test_protocol_and_loader_checks(self):
        protocols = importlib.import_module("test_protocols")
        plugin_loading = importlib.import_module("test_plugin_loading")
        protocols.test_notification_parser_returns_typed_wrapper()
        protocols.test_notification_parser_preserves_nested_reply_route()
        protocols.test_notification_parser_handles_post_mention_without_comment_target()
        protocols.test_drawpic_gateway_uses_formal_api()
        protocols.test_media_contract_uses_image_dimensions_and_cos_signature()
        plugin_loading.test_plugin_module_loads_with_runner_sdk_surface()

    def test_store_policy_checks(self):
        checks = importlib.import_module("test_store_policy")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            checks.test_store_deduplicates_incoming_and_outgoing(path / "dedupe")
            checks.test_store_retries_pre_send_failure_without_external_id(path / "retry")
            checks.test_dry_run_does_not_consume_daily_quota_or_send_cooldown(path / "dry")
            checks.test_incoming_event_stays_pending_until_reply_is_terminal(path / "pending")
            checks.test_incoming_event_uses_bounded_retries_then_stops(path / "bounded")
            migration = path / "migration"
            migration.mkdir()
            checks.test_store_migrates_recent_orphaned_claim_but_protects_outgoing_actions(migration)
            checks.test_policy_rejects_sensitive_and_technical_content()

    def test_webui_and_qr_checks(self):
        checks = importlib.import_module("test_webui_login")
        checks.test_qr_generator_emits_png_data_url_without_external_dependencies()
        checks.test_webui_declares_custom_login_page_and_static_apis()
        checks.test_invalid_credentials_can_be_removed_from_the_profile_store()

    def test_signing_known_vector(self):
        checks = importlib.import_module("test_comment_request_contract")
        checks.test_comment_endpoint_and_signing_known_vector()

