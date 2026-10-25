# Changelog

## 0.3.8 - 2026-10-26

- Changed the run-mode field from free text to a `dry_run` / `auto` dropdown.
- Set the published default to low-quota automatic operation: enabled, `auto`, one post per day, Steam + PC 游戏.
- Kept direct-message automation disabled.

## 0.3.7 - 2026-10-26

- Displayed the installed plugin marketplace names「麦麦绘图」「生活频率」「自我身份档案」in linkage settings.
- Kept plugin IDs and API names beside the Chinese names for troubleshooting and replacement.

## 0.3.6 - 2026-10-26

- Documented the three linked plugins and how to derive their MaiBot API names.
- Clarified that duplicate post targets are accepted in the UI and deduplicated before sending.
- Added the linkage details to the Chinese WebUI hints and README.

## 0.2.9 - 2026-10-25

- Applied MaiBot marketplace review fixes for integration classification and host version bounds.
- Removed unused `api.list` and `config.get` capability declarations.
- Restricted the LLM-facing QR login tools to status and PNG Data URL output; raw login URLs remain internal.
- Added a regression test for QR login output safety.

## 0.2.8 - 2026-10-25

- Consolidated the safe publish boundary for the MaiBot marketplace.
- Added project status and future direct-message option documents.
- Kept direct-message automation out of scope and disabled by default.
- Documented the 60-minute feed interval and the low-quota verification requirement.
- Preserved the existing QR login, feed browsing, post, comment, threaded-reply, image, quota, cooldown, idempotency, and readback safeguards.
