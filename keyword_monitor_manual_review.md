# Keyword Monitor Manual Review Checklist

Release 1 — Stage 2 — Accuracy Pack #20  
Scope: manual selector/source review only.  
Generated: 2026-06-25

## Safety Rules

- Do not auto-apply changes from this checklist.
- Do not delete sources.
- Do not deactivate sources from this checklist alone.
- Do not send Telegram during review.
- Verify each source manually in Admin Source Detail / selector picker before changing method or selector.
- Prefer fixing the existing source configuration over broad method changes.

## Priority Legend

- High: current source is a hard reading failure and likely needs selector/source URL repair.
- Medium: current source is readable enough to diagnose, but needs manual method/URL review.
- Low: current behavior is acceptable or freshness/date protection is working.

---

## 1. Selector Empty Sources — Review First

### adalet.az

- Current issue: `selector_empty`
- Current method: `selector`
- Current URL: `https://adalet.az`
- Current config signal: selector missing, article_pattern exists, RSS fallback URL exists
- What to check manually:
  - Open the site and identify the actual latest news/listing block.
  - Verify whether the current XPath/article_pattern still points to visible news cards.
  - Check whether Google News/RSS fallback is safer than selector for this domain.
- Likely fix type: selector repair or method fallback review
- Priority: high
- Do not auto-apply: yes

### ayafe.az

- Current issue: `selector_empty`
- Current method: `selector`
- Current URL: `https://ayafe.az/blog/page/3`
- Current config signal: selector missing, article_pattern exists, RSS exists
- What to check manually:
  - Confirm whether `/blog/page/3` is the correct latest page; it may be a paginated old page.
  - Find the current main blog/news listing URL.
  - Select the visible article card/list container.
- Likely fix type: latest_url repair plus selector repair
- Priority: high
- Do not auto-apply: yes

### baki-baku.az

- Current issue: `selector_empty`
- Current method: `selector`
- Current URL: `https://baki-baku.az/xeber`
- Current config signal: selector exists but appears stale or too broad (`.menu-item.menu-item-type-post_type`)
- What to check manually:
  - Replace menu-item selector with the actual news list/card selector.
  - Check whether `/xeber` still renders news without JavaScript-only content.
  - Validate candidate URLs are article links, not navigation links.
- Likely fix type: selector repair
- Priority: high
- Do not auto-apply: yes

### baku.news

- Current issue: `selector_empty`
- Current method: `selector`
- Current URL: `https://baku.news`
- Current config signal: selector missing, article_pattern exists, RSS exists
- What to check manually:
  - Identify whether homepage or a dedicated news page is the correct latest source.
  - Select the latest-news list, not sidebar/menu blocks.
  - Prefer RSS only if it contains current articles and dates.
- Likely fix type: selector repair or latest_url repair
- Priority: high
- Do not auto-apply: yes

### banker.az

- Current issue: `selector_empty`
- Current method: `selector`
- Current URL: `https://banker.az/`
- Current config signal: selector missing, old XPath/article_pattern exists, RSS exists
- What to check manually:
  - Check if the homepage/news grid structure changed.
  - Select current article card container.
  - Validate date extraction because banker-style pages can contain many categories.
- Likely fix type: selector repair
- Priority: high
- Do not auto-apply: yes

### ganjlik.az

- Current issue: `selector_empty`
- Current method: `selector`
- Current URL: `https://ganjlik.az`
- Current config signal: selector missing, generic article_pattern exists, RSS exists
- What to check manually:
  - Confirm if the domain still has a public news/list page.
  - Select current article card/list block if present.
  - If no current content exists, mark accept/monitor rather than forcing repair.
- Likely fix type: selector repair or accept-monitor after manual check
- Priority: high
- Do not auto-apply: yes

### ica.az

- Current issue: `selector_empty`
- Current method: `selector`
- Current URL: `https://ica.az/`
- Current config signal: selector missing, article_pattern exists, RSS exists
- What to check manually:
  - Find the real news/publications block.
  - Existing XPath likely points to an old WordPress layout.
  - Confirm article URLs and publication dates are extractable.
- Likely fix type: selector repair
- Priority: high
- Do not auto-apply: yes

### showmedia.az

- Current issue: `selector_empty`
- Current method: `selector`
- Current URL: `https://showmedia.az/news/rss`
- Current config signal: URL still points to RSS path but method is selector
- What to check manually:
  - Confirm correct human-readable latest/news page.
  - If RSS is valid, method should be RSS; if invalid XML remains, choose visible latest page and selector.
  - Do not keep selector method on `/news/rss` unless it renders HTML article links.
- Likely fix type: latest_url repair plus method/selector review
- Priority: high
- Do not auto-apply: yes

---

## 2. Fallback Empty Manual Review Sources

### 7news.az

- Current issue: `fallback_empty`
- Current method: `selector`
- Current URL: `https://7news.az/sitemap.xml`
- Current config signal: selector missing; URL is sitemap-like
- What to check manually:
  - Replace latest_url with visible latest/news page if available.
  - Select article list block manually.
  - Avoid monitoring sitemap XML with selector method.
- Likely fix type: latest_url repair plus selector repair
- Priority: medium
- Do not auto-apply: yes

### azinsaat.az

- Current issue: `fallback_empty`
- Current method: `selector`
- Current URL: `https://azinsaat.az/sitemap.xml`
- Current config signal: selector missing; URL is sitemap-like
- What to check manually:
  - Locate public news/articles page.
  - Select real article card/list container.
  - If site has no news page, accept-monitor or remove from active review later.
- Likely fix type: latest_url repair plus selector repair
- Priority: medium
- Do not auto-apply: yes

### busy.az

- Current issue: `fallback_empty`
- Current method: `selector`
- Current URL: `https://busy.az/sitemap.xml`
- Current config signal: selector missing; URL is sitemap-like
- What to check manually:
  - Busy.az may be job/vacancy-heavy, not classic news.
  - Decide if this source belongs in Keyword Monitor Release 1.
  - If kept, choose a visible listing page and selector.
- Likely fix type: latest_url repair or accept-monitor/review classification
- Priority: medium
- Do not auto-apply: yes

### cssc.az

- Current issue: `fallback_empty`
- Current method: `selector`
- Current URL: `https://cssc.az/sitemap.xml`
- Current config signal: selector missing; URL is sitemap-like
- What to check manually:
  - Find visible news/announcements page.
  - Select listing/card container.
  - Confirm links are current and not static pages only.
- Likely fix type: latest_url repair plus selector repair
- Priority: medium
- Do not auto-apply: yes

### flame.az

- Current issue: `fallback_empty`
- Current method: `selector`
- Current URL: `https://flame.az`
- Current config signal: selector missing, generic article_pattern exists
- What to check manually:
  - Confirm whether homepage has article/news cards.
  - Select current card/list block.
  - If homepage is static, find latest/news URL or accept-monitor.
- Likely fix type: selector repair or latest_url repair
- Priority: medium
- Do not auto-apply: yes

### jpis.az

- Current issue: `fallback_empty`
- Current method: `selector`
- Current URL: `https://jpis.az`
- Current config signal: selector missing, generic article_pattern exists
- What to check manually:
  - Check whether site has a news/publications section.
  - Select visible article links only.
  - Avoid static menu/footer blocks.
- Likely fix type: selector repair
- Priority: medium
- Do not auto-apply: yes

### jpit.az

- Current issue: `fallback_empty`
- Current method: `selector`
- Current URL: `https://jpit.az`
- Current config signal: selector missing, generic article_pattern exists
- What to check manually:
  - Check current page structure and whether article links exist.
  - Select real news/list block.
  - If no update area exists, accept-monitor instead of forcing selector.
- Likely fix type: selector repair or accept-monitor
- Priority: medium
- Do not auto-apply: yes

### manuscript.az

- Current issue: `fallback_empty`
- Current method: `selector`
- Current URL: `https://www.manuscript.az/sitemap.xml`
- Current config signal: selector missing; URL is sitemap-like; article_pattern exists
- What to check manually:
  - Replace sitemap URL with visible news/blog page.
  - Select listing block.
  - Confirm article date availability.
- Likely fix type: latest_url repair plus selector repair
- Priority: medium
- Do not auto-apply: yes

### mqm.az

- Current issue: `fallback_empty`
- Current method: `selector`
- Current URL: `https://mqm.az`
- Current config signal: selector missing, generic article_pattern exists
- What to check manually:
  - Check whether public news/updates exist.
  - Select article list if present.
  - If source is static, accept-monitor or later deactivate only after policy review.
- Likely fix type: selector repair or accept-monitor
- Priority: medium
- Do not auto-apply: yes

### pac.az

- Current issue: `fallback_empty`
- Current method: `selector`
- Current URL: `https://pac.az`
- Current config signal: selector missing, generic article_pattern exists
- What to check manually:
  - Confirm whether there is a current news/announcements page.
  - Select visible article links.
  - Avoid static pages and navigation links.
- Likely fix type: selector repair or latest_url repair
- Priority: medium
- Do not auto-apply: yes

### risale.az

- Current issue: `fallback_empty`
- Current method: `selector`
- Current URL: `https://risale.az/sitemap.xml`
- Current config signal: selector missing; URL is sitemap-like
- What to check manually:
  - Replace sitemap URL with visible articles/news page.
  - Select article card/list block.
  - Confirm content freshness.
- Likely fix type: latest_url repair plus selector repair
- Priority: medium
- Do not auto-apply: yes

---

## 3. Other Manual Review Sources

### bagcam.az

- Current issue: `latest_page_empty`
- Current method: `latest_page`
- Current URL: `http://bagcam.az`
- Current config signal: RSS exists, latest page produced no candidates
- What to check manually:
  - Test whether `http://bagcam.az/feed/` is valid and current.
  - Check if HTTPS or a dedicated news/blog path exists.
  - If RSS is valid, prefer RSS; otherwise select visible listing block.
- Likely fix type: method change to RSS or latest_url repair after manual validation
- Priority: medium
- Do not auto-apply: yes

### ting.az

- Current issue: `no_date`
- Current method: `selector`
- Current URL: `https://ting.az/xeber`
- Current config signal: items found via XPath/fallback, but date missing
- What to check manually:
  - Confirm whether article pages expose dates in parseable format.
  - Check if listing page contains dates near links.
  - If content is fresh but date missing, date parser/source rule review is needed before enabling sends.
- Likely fix type: date parser review or selector/date extraction review
- Priority: medium
- Do not auto-apply: yes

---

## Final Summary

- Total manual review sources: 21
- High priority: 8
- Medium priority: 13
- Low priority: 0

Recommended next action:

1. Start with the 8 `selector_empty` sources because they are hard reading failures.
2. For each source, manually open the page, select the real news/listing block, and verify candidate URLs before saving.
3. Then review sitemap-like fallback sources where `latest_url` still points to `sitemap.xml`.
4. Keep `old_news` sources out of this checklist because freshness protection is working.
5. Do not run bulk apply from this checklist.
