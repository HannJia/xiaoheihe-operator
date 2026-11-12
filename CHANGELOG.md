# Changelog

## 0.8.0 - 2026-11-13

- Daily news now defaults to 08:00 server time. Generation does not follow simulated sleep unless `respect_sleep` is enabled. The deadline limits generation retries, while delivered drafts can be approved until the end of the same day.
- In news `auto` mode, persist the exact verified text, image, destination and hashtags, send the complete preview privately, and wait for the configured owner's QQ private message `可以发送`. `取消发送` cancels the draft. Other users, groups, older confirmations, undelivered previews and next-day drafts cannot authorize a post.
- Approval survives restarts. Repeated confirmations cannot duplicate posts; image upload failures keep the draft pending instead of silently changing an approved image post into text. A fresh confirmation is required after preparation fails. The publish boundary checks approval and the exact saved payload before uploading and again before sending.
- Includes the 0.6–0.7 news improvements: original-source and first-publication checks, independent semantic review with verbatim evidence, metric/condition/availability checks, short-comment review, event deduplication, bounded review batches, and a sealed final body. Missing evidence or review results cannot enter publication.
- News images are deterministic Chinese infographics with verified text, source photos or labelled excerpts. Optional normal daily posts have an independent `daily_posts_enabled` switch; life-image prompts no longer hard-code an account label or request a social-feed screenshot.
- Updated website comment signing to the current v16 AES-GCM contract and request parameters; a gateway 403 no longer deletes otherwise valid credentials.
- Declared httpx, cryptography and Pillow package dependencies. News remains disabled and `dry_run` on new installs; the owner's ID and runtime configuration are never shipped.

## 0.6.0 - 2026-11-06

- Added an opt-in daily AI news post (`[news]`, off by default). From `post_time` (09:00) it searches with the host's unified web service, filters candidates (no date, older than 36 hours, blocked keywords, used in the last 3 days, more than 2 per site), lets the model pick 3–5 items, reads the source pages and writes a digest with a short opinion. Numbers and English words in each fact must appear in its source; lines that fail the check, claim first-hand use, reveal an AI identity or contain links are removed. The post lists source names only; links go to the owner in a private message.
- The news illustration uses the drawing plugin without the identity reference image; a failed drawing is retried once with a new request id, then the post goes out as text only.
- `dry_run` sends the draft and image to the owner without uploading or posting. `auto` requires both `[basic] mode` and `[news] mode` to be `auto`; the post request is sent at most once and is never repeated after it starts. Pre-send failures retry up to 3 times at least 10 minutes apart; at `deadline_time` (11:00) the day is given up and the owner is told why.
- Added the operator command `/xhh_news_preview`, which builds a draft immediately and sends it to the owner without writing a record or posting.
- Added `[basic] owner_qq` for private notifications (lost login, news drafts and results). Declared `web.search`, `web.fetch`, `chat.get_stream_by_user_id`, `send.text` and `send.image`.
- Replies to comments now answer the comment first; the post body is only background, so a "thanks" or "I like you" is no longer answered with the post topic.
- Text-only posts now use `link_tag` 27. The previous value 21 was rejected as a wrong post section, so the text fallback after a failed image never went out.
- Readback of unconfirmed sends is limited to the last 48 hours and runs every 10 minutes instead of every poll.
- The news self-disclosure check now also catches "我是 AI" with a space and no longer flags "作为程序员".

## 0.5.0 - 2026-11-01

- Persona now follows the host MaiBot by default: comments, replies and posts use `bot.nickname` and `personality.personality` (the same identity the host replyer uses), re-read at most once a minute so WebUI persona edits and profile switches take effect without a restart. The previous hard-coded studio persona has been removed.
- Added the optional `[content] persona_override` field to give Xiaoheihe a different identity or tone; when the host persona cannot be read and no override is set, a neutral gamer persona is used. Declared the `config.get` capability for this.
- Reworked recommendation-feed browsing: the interval is randomised between 0.5× and 1.5× `feed_interval_seconds`; each session opens 2–4 posts in feed order; comments and likes only go to posts that pass the game filter; the daily comment quota is spread over the remaining sessions; the first session after a restart is delayed by 2–15 minutes.
- Added post likes (`POST /bbs/app/profile/award/link`) with the new `likes_per_day` quota (default 3). Each post is liked at most once and likes do not consume the comment cooldown.
- Browsing and proactive posting pause while the linked life-frequency plugin reports the persona as asleep or napping.
- Rewrote the comment prompts: proactive comments and replies to others use separate instructions, avoid restating the post and avoid stock phrases; output limits raised to 600 tokens for comments and 900 for posts to stop truncation.
- Fixed optional image generation failures so a timed-out drawing task is recorded and the independent Xiaoheihe text-post path can continue.
- Added nested task-payload handling and audit markers for image fallback; no new drawing request is created during fallback.
- Raised the SDK upper bound to 2.99.99; tested with MaiBot 1.3.12 (SDK 2.12.0) and 1.3.19 (SDK 2.20.0).

## 0.4.1 - 2026-10-26

- Fixed the proactive-post send boundary so `dry_run` records the generated draft without image generation, image upload, or a Xiaoheihe `create_post` request.
- Added a regression test covering `dry_run` with image generation enabled.

## 0.4.0 - 2026-10-26

- Added independent server-local time windows for recommendation browsing/active comments and proactive posts.
- Made the three MaiBot integrations optional so text-only Xiaoheihe posting works without them.
- Changed new-install image generation to opt-in instead of requiring the drawing plugin.
- Added the user-provided Xiaoheihe-style local avatar to the manifest.
- Corrected the release documentation boundary to match the published `auto` default.

## 0.3.9 - 2026-10-26

- Changed the new-install recommendation flow interval default to 600 seconds (10 minutes).
- Clarified that 600 seconds is the plugin's recommended default, not an official Xiaoheihe polling interval.
- Set the SDK compatibility upper bound to the tested 2.12.x line.

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
