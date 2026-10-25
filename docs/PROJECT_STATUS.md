# Project status

Snapshot date: 2026-10-26 (Asia/Shanghai)

## Release scope

Version 0.4.0 covers QR login, scheduled game-related feed browsing, independent browsing/posting time windows, post publishing, comments, threaded replies, optional image/life/identity integrations, SQLite idempotency, quotas, cooldowns, send-result verification, and the MaiBot marketplace review fixes.

Direct-message automation is outside this release. It is disabled by default and is not promised by the marketplace listing. See Direct-message options for future designs.

## Verification

- Python compilation and repository tests must pass before release.
- Dry-run and readback paths are separate from real writes.
- Real comment and threaded-reply checks must remain low quota and be verified by reading the resulting post tree.
- The repository must not contain credentials, cookies, tokens, SSH keys, server configuration, or runtime databases.

## Known limits

- The plugin does not provide a stable direct-message send contract.
- Android devices, emulators, cloud phones, and a separate chat micro-frontend are future options only.
- Image generation, life context, and identity references are optional; without them, text-only posting and clock/persona fallback remain available.
- New installs browse by default from 08:00 to 23:00 and post from 10:00 to 22:00 in server local time; each window can be changed independently.
