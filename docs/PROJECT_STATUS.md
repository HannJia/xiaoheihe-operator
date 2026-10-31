# Project status

Snapshot date: 2026-11-01 (Asia/Shanghai)

The full handoff is in `docs/handoff/2026-10-26-小黑盒插件发布交接.md` in the workspace. The current release is `0.5.0`.

## Release scope

Version 0.5.0 adds:

- Persona follows the host MaiBot (`bot.nickname` + `personality.personality`), with an optional `[content] persona_override`; the previous hard-coded studio persona is gone. Requires the `config.get` capability.
- Randomised recommendation-feed browsing (2–4 posts per session, comments spread over the day), post likes with `likes_per_day`, and no browsing or posting while the life-frequency plugin reports sleep or a nap.
- Separate prompts for proactive comments and replies, and larger output limits.
- The 2026-10-29 image-fallback fix: a failed or timed-out drawing task no longer blocks the text post.

Version 0.4.1 added a hard dry-run boundary for proactive posts: draft generation is recorded, while image generation, image upload, and `create_post()` are skipped.

Direct-message automation is outside this release. It is disabled by default and is not promised by the marketplace listing. See Direct-message options for future designs.

## Release and marketplace state

- GitHub repository: `https://github.com/HannJia/xiaoheihe-operator`
- GitHub release: `v0.5.0`. The marketplace index syncs GitHub Releases daily at 02:00 and 15:00 (Beijing time) and opens its own review issue.
- v0.4.1 was approved through MaiBot plugin-repo Issue #1000 (closed); the plugin is listed in the MaiBot plugin center.
- The remaining suggestions are non-blocking: safer credential temp-file replacement and a cooldown for QR login Tools.
- The published icon is the user-provided Xiaoheihe-style image in `assets/icon.png`.

## Defaults and server notes

- New installs browse from 08:00 to 23:00 in server local time.
- New installs post from 10:00 to 22:00 in server local time.
- The recommendation-feed interval default is 600 seconds; the actual gap is randomised between 0.5× and 1.5×.
- New installs like at most 3 posts per day.
- The published default is `enabled=true`, `mode=auto`, with low daily quotas. Direct-message sending remains disabled.

## Verification

- 58 tests pass on MaiBot 1.3.12 (SDK 2.12.0) and 1.3.19 (SDK 2.20.0), including the new persona-source tests.
- An existing 0.4.x `config.toml` validates against the real SDK; `persona_override` defaults to empty and renders as a textarea.
- The like endpoint was verified once against a live account: `is_award_link` changed from 0 to 1.
- The proactive-post dry-run regression test passes with image generation enabled and asserts zero image-upload and `create_post()` calls.
- Real comment and threaded-reply checks must remain low quota and be verified by reading the resulting post tree.
- The repository must not contain credentials, cookies, tokens, SSH keys, server configuration, or runtime databases.

## Known limits

- The plugin does not provide a stable direct-message send contract.
- The like endpoint comes from a third-party capture, not public documentation.
- Android devices, emulators, cloud phones, and a separate chat micro-frontend are future options only.
- Image generation, life context, and identity references are optional; without them, text-only posting and clock/persona fallback remain available.
