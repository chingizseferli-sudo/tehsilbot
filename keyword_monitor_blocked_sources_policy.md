# Keyword Monitor Blocked Source Policy

Release 1 - Stage 2 - Accuracy Pack #31

Scope: active sources with `http_403` / blocked diagnostics.

## Principles

- Do not bypass origin protections.
- Do not add aggressive retry, scraping, proxy, or browser-bypass behavior for blocked sites.
- Do not deactivate high-value official, government, education, or major media domains automatically.
- Prefer gentle alternatives only when obvious: existing RSS, obvious `/rss`, `/feed`, `/sitemap.xml`, or normal homepage/latest URL with standard browser headers.
- Apply changes only when the alternative returns bot-accepted article URLs.
- If a source is blocked but recently produced articles, keep it active and mark for review rather than deactivating.

## Classification Rules

### high_value_keep_review
Use when the source is official, education/government, major media, or strategically useful.
Action: keep active, review manually, do not deactivate.

### safe_fallback_candidate
Use when a gentle RSS/sitemap/latest alternative returns bot-accepted article URLs.
Action: test targeted verification with `DISABLE_TELEGRAM_SEND=true`; apply only low-risk config if needed.

### accept_blocked_monitor
Use when the site blocks direct bot access but the domain has value or recent article history.
Action: keep active as known limitation; avoid pressure on origin.

### low_value_deactivate_later
Use when source is low-value, repeatedly blocked, no recent article history, and no safe fallback.
Action: do not deactivate in this pack; only later with explicit release/operator approval.

## Pack #31 Triage Summary

Total active blocked/http_403 sources inspected: 21.

### Fixed by verification, no config change needed

- `azertag.az` - RSS works, 10 candidates found, health updated from `http_403` to `old_news`.
- `milletinsesi.info` - RSS path works, 10 candidates found, health updated from `http_403` to `old_news`.

### high_value_keep_review

- `corp.ady.az` - official railway domain; Google News RSS empty; direct site blocks. Keep active/review.
- `azmiu.edu.az` - education domain; homepage OK, RSS/sitemap not useful. Keep active/review.
- `missiya.edu.az` - education domain; feed exists but no accepted article URLs. Keep active/review.

### safe_fallback_candidate rejected for now

- `hesab.az` - sitemap returned one article-like URL, but it is a direct-pay education path, not news. Do not apply.

### low_value_deactivate_later candidates

- `sam.az`
- `dersevi.az`
- `libraff.az`
- `wikimed.az`

These are not deactivated in Pack #31. They require explicit later approval.

### accept_blocked_monitor / review as known blocked

- `2gis.az`
- `ann.az`
- `realtv.az`
- `102xeber.info`
- `aztimes.az`
- `Demokrat.az`
- `idemitsu.az`
- `nar.az`
- `tehsil-press.az`
- `unikal.az`
- `versus.az`

## Release Acceptance Note

Blocked sources are not automatically Release 1 blockers if:

- they are classified,
- high-value sources are preserved,
- Telegram failures remain zero,
- source health clearly shows `http_403`, and
- no aggressive crawling/bypass is introduced.
