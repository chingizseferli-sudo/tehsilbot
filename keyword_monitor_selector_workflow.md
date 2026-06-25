# Keyword Monitor Selector Workflow

Release 1 - Stage 2 - Accuracy Pack #25

Purpose: this checklist replaces blind auto-repair for the remaining `manual_review_missing_selector` / `fallback_empty` sources.

Rules:
- Do not bulk-change these sources.
- Do not delete or deactivate during selector review.
- Do not send Telegram while testing.
- Save a selector only when the bot produces accepted article candidates.
- If a source looks static, low-value, or not clearly news-like, leave it as manual review / accept-monitor.

## Manual Workflow

1. Open the source URL in a browser.
2. Find the real latest/news/listing section, not header, menu, sidebar, footer, social links, or search widgets.
3. Use selector picker to select the repeating article card container or direct title links.
4. Prefer selectors that return multiple article cards.
5. Avoid one-off XPath-like selectors and layout wrappers.
6. Test selector against bot acceptance:
   - candidate links must be article-like;
   - links must stay on the source domain;
   - titles must be real article/news titles;
   - section/category/static pages must not dominate.
7. Save only if bot-accepted candidates appear.
8. Verify with:

```powershell
$env:DISABLE_TELEGRAM_SEND="true"
$env:RUN_ONCE="1"
python site_monitor.py
python keyword_monitor_health_report.py
```

## Summary

- Total remaining missing-selector fallback sources: 41
- Priority 1: 22
- Priority 2: 13
- Priority 3: 6
- Recommended next action: manually repair Priority 1 first, then only continue to Priority 2 if Release 1 health still needs it.

## Priority 1 - Official / Education / University / High-Value Institutional

| Source | Current issue | Current URL / method | What to open | Selector should target | Expected article URL pattern | Suggested final action |
|---|---|---|---|---|---|---|
| asoiu.edu.az | fallback_empty, missing selector | `https://asoiu.edu.az` / selector | Open homepage, find news/events area | Repeating news cards or title links | `/news/`, `/az/news/`, dated article URLs | repair selector; change latest_url only if a clear news page exists |
| atu.edu.az | fallback_empty, missing selector | `https://www.atu.edu.az` / selector | Open homepage, locate news/announcements | News card grid/list, not overview wrapper | `/az/news/`, `/news/`, article detail URL | repair selector |
| bdu-qazax.edu.az | fallback_empty, missing selector | `https://bdu-qazax.edu.az` / selector | Open homepage/news section | Latest news cards or title anchors | `/news/`, `/xeber/`, `/az/news/` | repair selector or change latest_url to news page if found |
| bhm.edu.az | fallback_empty, missing selector | `https://bhm.edu.az` / selector | Open homepage and news page if present | Article cards in news/listing area | `/news/`, `/xeberler/`, detail slug | repair selector |
| bmtk.edu.az | fallback_empty, missing selector | `https://bmtk.edu.az` / selector | Open homepage, find actual news list | Repeating article item container | `/news/`, `/xeber/`, `/az/news/` | repair selector |
| cenub.edu.az | fallback_empty, missing selector | `https://cenub.edu.az` / selector | Open homepage/news area | Repeating news cards | `/news/`, `/xeber/`, detail page | repair selector |
| goyezen.edu.az | fallback_empty, missing selector | `https://goyezen.edu.az` / selector | Open homepage, find updates/news | News card list | `/news/`, `/xeber/`, detail slug | repair selector |
| gsaz.az | fallback_empty, missing selector | `https://gsaz.az/articles` / selector | Open `/articles` directly | Article list rows/cards | `/articles/`, article detail path | repair selector |
| jpis.az | fallback_empty, missing selector | `https://jpis.az` / selector | Open homepage, find publications/news | Article/publication card list | `/news/`, `/article/`, `/publication/` | repair selector or accept-monitor if journal-only/static |
| jpit.az | fallback_empty, missing selector | `https://jpit.az` / selector | Open homepage, find publications/news | Article/publication list | `/news/`, `/article/`, `/publication/` | repair selector or accept-monitor if journal-only/static |
| kaspi.edu.az | fallback_empty, missing selector | `https://www.kaspi.edu.az` / selector | Open homepage, find news/blog area | News/blog cards | `/news/`, `/blog/`, article slug | repair selector |
| kepez.edu.az | fallback_empty, missing selector | `https://kepez.edu.az/news` / selector | Open `/news` directly | News listing cards | `/news/`, detail slug | repair selector |
| lider.edu.az | fallback_empty, missing selector | `https://lider.edu.az` / selector | Open homepage/news area | Latest news cards | `/news/`, `/xeber/` | repair selector |
| lsu.edu.az | fallback_empty, missing selector | `https://lsu.edu.az` / selector | Open homepage, find news/events | News cards, not layout container | `/news/`, `/az/news/`, detail slug | repair selector |
| nmi.edu.az | fallback_empty, missing selector | `https://www.nmi.edu.az` / selector | Open homepage/news area | News post list in content area | `/news/`, `/az/news/`, post slug | repair selector |
| ppe-journal.edu.az | fallback_empty, missing selector | `https://ppe-journal.edu.az` / selector | Open homepage/issues/news area | Publication/article list | `/article/`, `/news/`, issue detail | accept-monitor or repair selector if real news exists |
| qax.edu.az | fallback_empty, missing selector | `https://qax.edu.az` / selector | Open homepage/news area | Local education updates/cards | `/news/`, `/xeber/`, detail page | repair selector |
| sheki.edu.az | fallback_empty, missing selector | `https://sheki.edu.az` / selector | Open homepage/news area | Local education updates/cards | `/news/`, `/xeber/`, detail page | repair selector |
| stimul.edu.az | fallback_empty, missing selector | `https://stimul.edu.az` / selector | Open homepage/news/blog area | News/blog item cards | `/news/`, `/blog/`, detail slug | repair selector |
| telebetehsil.az | fallback_empty, missing selector | `https://telebetehsil.az` / selector | Open homepage/news area | News/list item cards | `/news/`, `/xeber/`, article slug | repair selector |
| tkta.edu.az | fallback_empty, missing selector | `https://www.tkta.edu.az` / selector | Open homepage/media/news section | News cards in media/news area | `/az/media/news`, `/news/`, detail slug | repair selector; change latest_url if media/news page exists |
| ufaz.az | fallback_empty, missing selector | `https://www.ufaz.az` / selector | Open homepage/news area | News/event cards | `/news/`, `/events/`, article slug | repair selector |

## Priority 2 - Regular Media / News Sources

| Source | Current issue | Current URL / method | What to open | Selector should target | Expected article URL pattern | Suggested final action |
|---|---|---|---|---|---|---|
| 7times.az | fallback_empty, missing selector | `https://7times.az` / selector | Open homepage/latest news area | Main news list cards, not scrolling/sidebar only | `/news/`, `/xeber/`, dated slug | repair selector only if clean candidates appear |
| afn.az | fallback_empty, missing selector | `https://afn.az` / selector | Open homepage/latest area | Main article cards | `/news/`, `/xeber/`, article slug | repair selector |
| ayna.az | fallback_empty, missing selector | `https://ayna.az` / selector | Open homepage/news area | Article cards in main content | `/news/`, `/article/`, slug | repair selector |
| busaat.az | fallback_empty, missing selector | `https://busaat.az` / selector | Open homepage/latest news | Main news cards/titles | `/news/`, `/xeber/`, article slug | repair selector |
| editor.az | fallback_empty, missing selector | `https://editor.az` / selector | Open homepage/latest news | Main article list | `/news/`, `/xeber/`, article slug | repair selector |
| marja.az | fallback_empty, missing selector | `https://marja.az` / selector | Open homepage/news feed | Main feed items | `/news/`, `/article/`, numeric/detail URL | repair selector |
| mia.az | fallback_empty, missing selector | `https://mia.az` / selector | Open homepage/latest news | Latest news column/list | `/news/`, `/xeber/`, detail slug | repair selector |
| milliyol.az | fallback_empty, missing selector | `https://milliyol.az` / selector | Open homepage/latest area | News cards, not entire side content | `/news/`, `/xeber/`, article slug | repair selector |
| naxcivanxeberleri.com | fallback_empty, missing selector | `https://naxcivanxeberleri.com` / selector | Open homepage/latest news | Article cards/list, avoid popup/close button | `/news/`, `/xeber/`, article slug | repair selector |
| qaynarinfo.az | fallback_empty, missing selector | `https://qaynarinfo.az` / selector | Open homepage/latest news | Main news feed cards | `/news/`, `/article/`, detail slug | repair selector |
| seherxeber.org | fallback_empty, missing selector | `https://seherxeber.org` / selector | Open homepage/latest news | Latest/review widget article links if real | `/news/`, `/xeber/`, article slug | repair selector or accept-monitor if stale |
| xalqxeber.az | fallback_empty, missing selector | `https://xalqxeber.az` / selector | Open homepage/latest news | Section news list/cards | `/news/`, `/xeber/`, article slug | repair selector |
| yenixeber.org | fallback_empty, missing selector | `https://yenixeber.org` / selector | Open homepage/right-box/latest area | Real article links in right-box/latest list | `/news/`, `/xeber/`, article slug | repair selector |

## Priority 3 - Low-Value / Static / Unclear

| Source | Current issue | Current URL / method | What to open | Selector should target | Expected article URL pattern | Suggested final action |
|---|---|---|---|---|---|---|
| aiki.az | fallback_empty, missing selector | `http://aiki.az` / selector | Open homepage; check if news is current | Only current news/articles, not old event archive | `/news/`, `/xeber/`, current post URL | accept-monitor unless fresh news exists |
| flame.az | fallback_empty, missing selector | `https://flame.az` / selector | Open homepage; confirm news exists | News cards only if real source is active | `/news/`, `/xeber/`, detail slug | accept-monitor or repair selector if active |
| fmg.az | fallback_empty, missing selector | `https://fmg.az` / selector | Open homepage; identify if news source exists | Article cards if present | `/news/`, `/article/`, detail slug | manual review |
| mqm.az | fallback_empty, missing selector | `https://mqm.az` / selector | Open homepage; check for actual updates | News/update cards | `/news/`, `/xeber/`, detail slug | manual review |
| pac.az | fallback_empty, missing selector | `https://pac.az` / selector | Open homepage; check if news exists | News/publication cards | `/news/`, `/article/`, detail slug | accept-monitor or repair selector |
| physiology.az | fallback_empty, missing selector | `https://physiology.az` / selector | Open homepage/news area | Article/news cards, not static institute text | `/news/`, `/article/`, detail slug | manual review |

## Verification Template

After saving any selector manually:

```powershell
$env:DISABLE_TELEGRAM_SEND="true"
$env:RUN_ONCE="1"
python site_monitor.py
python keyword_monitor_health_report.py
```

Expected result:
- the source should move from `fallback_empty` to `old_news`, `no_article`, `duplicate_url`, `no_monitor_match`, or `sent`;
- `selector_empty` / `fallback_empty` should not remain for a valid selector;
- Telegram delivery failures must remain zero.

## Release Notes

- This file is a manual workflow list only.
- No DB changes are implied.
- No selectors should be applied without source-by-source verification.
- The remaining missing-selector group is not a Release 1 blocker if documented as manual review and high-value sources are prioritized.
