# Project status

Version: **0.8.0** (2026-11-13).

## Behavior

- Daily AI news is opt-in, with `dry_run` as the installation default. The default start time is 08:00 server time; generation can run independently of simulated sleep.
- News `auto` requires both global and news modes to be `auto`. It privately delivers a verified draft and image, persists their exact bytes and destination, then waits for the configured owner's QQ private message `可以发送`. `取消发送` cancels. Each approval applies only to that day's delivered draft; previews from `/xhh_news_preview` and historical dry-run records cannot be sent by approval.
- Source discovery is followed by direct public HTTPS article retrieval, original-publication checks, independent semantic review, literal evidence checks and event deduplication. Missing evidence or an invalid review cannot produce a publishable draft. Semantic review is model-assisted and may still miss errors.
- News graphics typeset the verified Chinese facts and comments with source photos or labelled excerpts. Optional life-post drawing remains separate and has an independent `daily_posts_enabled` switch.
- Website comments use the v16 signing and parameter contract. Gateway 403 responses do not delete otherwise valid credentials. This is a compatibility implementation of the website client, not an official public API guarantee.
- Other existing capabilities include QR login, persona following the host, randomized game-feed browsing, post likes, comments and replies, low quotas, cooldowns and readback reconciliation. Xiaoheihe direct-message automation remains disabled and unsupported.

## Verification

- The regression suite covers source/evidence/date handling, review batches, duplicate events, persistence, owner-only private confirmation, cancelled/expired/undelivered drafts, repeat confirmations, upload failures and posting uncertainty.
- Real SDK 2.20.0 component registration, old configuration validation and declared Python dependencies have been checked on MaiBot 1.3.19.
- No unapproved post is used as a live test. Public posting of a daily draft requires its owner's explicit private confirmation.
- Runtime configuration, account identifiers, credentials and databases are excluded from the repository. Source fetching does not send account cookies and rejects private-network targets and redirects.

## Marketplace

Repository: [HannJia/xiaoheihe-operator](https://github.com/HannJia/xiaoheihe-operator). The plugin is already registered in MaiBot's release-based marketplace. New stable GitHub Releases are discovered and reviewed by the marketplace's scheduled index sync; publishing a Release does not itself prove marketplace approval.

Git tag, GitHub Release and Manifest version must agree. Never rewrite an indexed tag. Keep `/config.toml`, runtime data and credentials outside the release tree.

## Requirements and limits

The Manifest declares httpx, cryptography and Pillow. Chinese image rendering also needs a Chinese font, such as WenQuanYi Zen Hei or Noto Sans CJK on Linux, or Microsoft YaHei on Windows. A missing font is reported and the preview is text-only. If an approved image cannot be uploaded, publication pauses instead of silently dropping it.

Generation can skip a day if fewer than three independent current stories survive verification. An inaccessible original page is skipped. After an actual post request begins, uncertain results are reconciled by reading the account; they are never blindly retried. A prepare-stage failure requires a new confirmation.
