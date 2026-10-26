# Project status

Snapshot date: 2026-10-26 (Asia/Shanghai)

The full handoff is in `docs/handoff/2026-10-26-小黑盒插件发布交接.md` in the workspace. The current release is `0.4.1`.

## Release scope

Version 0.4.1 contains the 0.4.0 release scope plus a hard dry-run boundary for proactive posts: draft generation is recorded, while image generation, image upload, and `create_post()` are skipped.

Direct-message automation is outside this release. It is disabled by default and is not promised by the marketplace listing. See Direct-message options for future designs.

## Release and marketplace state

- GitHub repository: `https://github.com/HannJia/xiaoheihe-operator`
- GitHub release: `v0.4.1`, published after the dry-run fix passed local validation.
- MaiBot plugin-repo Issue #1000 has returned `Validation Passed` after the Issue Form body was corrected.
- The latest v0.4.1 automatic validation passed. The issue shows `6/8` review attempts and is waiting for maintainer approval. Do not trigger another `/recheck`.
- The published icon is the user-provided Xiaoheihe-style image in `assets/icon.png`.

## Defaults and server notes

- New installs browse from 08:00 to 23:00 in server local time.
- New installs post from 10:00 to 22:00 in server local time.
- The current server feed interval is 3600 seconds (60 minutes), which is an administrator setting rather than an official Xiaoheihe requirement.
- The published default is `enabled=true`, `mode=auto`, with low daily quotas. Direct-message sending remains disabled.

## Verification

- Python compilation passed for the source and English workspace mirror.
- Plugin loading, default configuration, time windows, manifest fields, and icon parity passed targeted validation.
- Recorded validation markers: `SOURCE_TEST_OK`, `MIRROR_TEST_OK`, `VALIDATION_OK`, and `XHH_COMPILEALL_OK`.
- The proactive-post dry-run regression test passes with image generation enabled and asserts zero image-upload and `create_post()` calls.
- Dry-run and readback paths are separate from real writes.
- Real comment and threaded-reply checks must remain low quota and be verified by reading the resulting post tree.
- The repository must not contain credentials, cookies, tokens, SSH keys, server configuration, or runtime databases.

## Known limits

- The plugin does not provide a stable direct-message send contract.
- Android devices, emulators, cloud phones, and a separate chat micro-frontend are future options only.
- Image generation, life context, and identity references are optional; without them, text-only posting and clock/persona fallback remain available.
