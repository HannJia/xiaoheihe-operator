# Changelog

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
