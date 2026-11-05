"""Basic accessibility scan of the page a test is on (opt-in with a11yScan=true).

This is a small set of checks that need no extra package, not a replacement for a full audit tool. It looks for
problems a tester can fix or raise: images without alt text, form fields, buttons and links without a name, a page
without a title or language, duplicate ids, positive tabindex, empty or skipped headings, and focusable elements
inside aria-hidden. Colour contrast is NOT checked.

After each screen step of a test the page is scanned; new findings are logged as `[a11y] <screen>: ...` and attached
to the test in the Allure results as "Accessibility: <screen>" (JSON). The scan never fails a test.
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

MAX_FINDINGS = 200
IMPACT_ORDER = {"serious": 0, "moderate": 1, "minor": 2}

SCAN_SCRIPT = r"""() => {
  const out = [];
  const visible = (el) => {
    if (!el.getClientRects().length) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none';
  };
  const hiddenByAria = (el) => !!el.closest('[aria-hidden="true"]');
  const text = (el) => (el.textContent || '').replace(/\s+/g, ' ').trim();
  const labelledBy = (el) => (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
      .map((id) => document.getElementById(id)).filter(Boolean).map(text).join(' ').trim();
  const name = (el) => {
    const aria = (el.getAttribute('aria-label') || '').trim();
    if (aria) return aria;
    const by = labelledBy(el);
    if (by) return by;
    if (el.id) {
      for (const l of document.querySelectorAll('label[for]')) {
        if (l.getAttribute('for') === el.id && text(l)) return text(l);
      }
    }
    const wrap = el.closest('label');
    if (wrap && text(wrap)) return text(wrap);
    return (el.getAttribute('title') || '').trim();
  };
  const pick = (el) => {
    if (el.id && document.querySelectorAll('[id="' + CSS.escape(el.id) + '"]').length === 1) return '#' + CSS.escape(el.id);
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && parts.length < 4; n = n.parentElement) {
      let part = n.tagName.toLowerCase();
      if (n.getAttribute('name')) part += '[name="' + n.getAttribute('name') + '"]';
      else if (n.parentElement) {
        const same = [...n.parentElement.children].filter((c) => c.tagName === n.tagName);
        if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(n) + 1) + ')';
      }
      parts.unshift(part);
      if (n.id || n.parentElement === document.body) break;
    }
    return parts.join(' > ');
  };
  const add = (rule, impact, el, help, selector) => out.push({
    rule, impact, selector: selector || pick(el), snippet: el.outerHTML.replace(/\s+/g, ' ').slice(0, 140), help});

  if (!(document.title || '').trim()) add('document-title', 'serious', document.documentElement, 'The page has no title.');
  if (!(document.documentElement.getAttribute('lang') || '').trim())
    add('html-lang', 'serious', document.documentElement, 'The page does not say what language it is in (html lang).');

  for (const el of document.querySelectorAll('img, [role="img"]')) {
    if (!visible(el) || hiddenByAria(el) || el.getAttribute('role') === 'presentation' || el.getAttribute('role') === 'none') continue;
    if (el.tagName === 'IMG' && el.hasAttribute('alt')) continue;
    if (name(el)) continue;
    add('image-alt', 'serious', el, 'The image has no alt text (use alt="" if it is only decoration).');
  }
  for (const el of document.querySelectorAll('input, select, textarea')) {
    const type = (el.getAttribute('type') || 'text').toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image'].includes(type) || !visible(el) || hiddenByAria(el)) continue;
    if (name(el)) continue;
    if ((el.getAttribute('placeholder') || '').trim())
      add('label-placeholder-only', 'moderate', el, 'The field is named only by its placeholder, which disappears when typing.');
    else add('form-label', 'serious', el, 'The form field has no label.');
  }
  for (const el of document.querySelectorAll('button, [role="button"], input[type="submit"], input[type="button"]')) {
    if (!visible(el) || hiddenByAria(el)) continue;
    if (text(el) || name(el) || (el.tagName === 'INPUT' && (el.value || '').trim())) continue;
    if ([...el.querySelectorAll('img')].some((i) => (i.getAttribute('alt') || '').trim())) continue;
    add('button-name', 'serious', el, 'The button has no text or accessible name.');
  }
  for (const el of document.querySelectorAll('a[href], [role="link"]')) {
    if (!visible(el) || hiddenByAria(el)) continue;
    if (text(el) || name(el) || [...el.querySelectorAll('img')].some((i) => (i.getAttribute('alt') || '').trim())) continue;
    add('link-name', 'serious', el, 'The link has no text or accessible name.');
  }
  const seen = new Map();
  for (const el of document.querySelectorAll('[id]')) seen.set(el.id, (seen.get(el.id) || 0) + 1);
  for (const [id, count] of seen) {
    if (id && count > 1) add('duplicate-id', 'moderate', document.getElementById(id), 'The id "' + id + '" is used ' + count + ' times.',
        '[id="' + id + '"]');
  }
  for (const el of document.querySelectorAll('[tabindex]')) {
    if (parseInt(el.getAttribute('tabindex'), 10) > 0)
      add('tabindex-positive', 'moderate', el, 'A positive tabindex changes the natural keyboard order.');
  }
  let last = 0;
  for (const el of document.querySelectorAll('h1, h2, h3, h4, h5, h6, [role="heading"]')) {
    if (!visible(el) || hiddenByAria(el)) continue;
    const level = el.tagName.startsWith('H') && el.tagName.length === 2 ? parseInt(el.tagName[1], 10)
        : parseInt(el.getAttribute('aria-level') || '2', 10);
    if (!text(el) && !name(el)) add('empty-heading', 'moderate', el, 'The heading is empty.');
    else if (last && level > last + 1)
      add('heading-order', 'minor', el, 'The heading jumps from level ' + last + ' to ' + level + '.');
    last = level;
  }
  for (const el of document.querySelectorAll('[aria-hidden="true"]')) {
    const focusable = el.matches('a[href], button, input, select, textarea, [tabindex]') ? [el] : [];
    for (const f of [...focusable, ...el.querySelectorAll('a[href], button, input, select, textarea, [tabindex]')]) {
      if (f.getAttribute('tabindex') === '-1' || f.disabled || f.getAttribute('type') === 'hidden') continue;
      add('aria-hidden-focusable', 'serious', f, 'A focusable element is inside aria-hidden content.');
    }
  }
  return out;
}"""


def enabled() -> bool:
    return str(os.getenv("a11yScan") or "").lower() in ("1", "true", "yes", "on")


async def scan(page) -> list[dict]:
    """The findings on the page's top document, most serious first."""
    findings = await page.evaluate(SCAN_SCRIPT)
    findings.sort(key=lambda f: (IMPACT_ORDER.get(f["impact"], 9), f["rule"], f["selector"]))
    return findings[:MAX_FINDINGS]


def summarize(findings: list[dict]) -> str:
    if not findings:
        return "no problems found"
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["impact"]] = counts.get(f["impact"], 0) + 1
    detail = ", ".join(f"{n} {k}" for k, n in sorted(counts.items(), key=lambda kv: IMPACT_ORDER.get(kv[0], 9)))
    return f"{len(findings)} problem{'s' if len(findings) != 1 else ''} ({detail}): " + \
        ", ".join(sorted({f["rule"] for f in findings}))


class Scanner:
    """Scans each screen of one test; reports a finding once however many steps stay on that page."""

    def __init__(self):
        self.reported: set[tuple[str, str, str]] = set()

    async def after_screen(self, page, screen: str) -> list[dict]:
        try:
            found = await scan(page)
        except Exception as e:  # a page that is navigating or closed must not break the test
            logger.warning("[a11y] %s: could not scan: %s", screen, e)
            return []
        fresh = []
        for f in found:
            key = (f["rule"], f["selector"], page.url.split("#")[0])
            if key not in self.reported:
                self.reported.add(key)
                fresh.append(f)
        if fresh:
            logger.warning("[a11y] %s: %s", screen, summarize(fresh))
            attach(screen, page.url, fresh)
        else:
            logger.info("[a11y] %s: no new problems", screen)
        return fresh


def attach(screen: str, url: str, findings: list[dict]) -> None:
    import allure
    payload = {"screen": screen, "url": url, "summary": summarize(findings), "findings": findings}
    allure.attach(json.dumps(payload, indent=2), name=f"Accessibility: {screen}",
                  attachment_type=allure.attachment_type.JSON)
