"""Find out how a site marks its elements, so nobody has to type it in.

Many applications put their own identifier on elements (data-attribute-id, data-automation-id, data-qa,
data-hook...), often with a common prefix on every value (app_form_TaxNotPaid, app-checkout-PayButton). Locators built
on it are the most stable ones a site offers. Instead of asking each user for the attribute and the prefix,
the page is examined: a data-* attribute is taken to be the site's id attribute when at least three elements carry
it, nearly all its values are different, they are not generated (long numbers, hashes) and are not tiny; one whose
name says it is an identifier (id, key, qa, test, cy, e2e, auto, hook, ref, field...) wins over the others. The
prefix is the part every value starts with, up to a separator (_ - . :), when there is one.
"""
from __future__ import annotations

import json
import os
import re

from webapp.services.capture import DYNAMIC_ID, TEST_ATTRS

# Ranked id-like data-* attributes of the page, as a JS function body usable on its own or inside another script.
DETECT_JS_FN = r"""
function autoplayIdAttrs() {
  const HINT = /(^|-)(id|ids|key|qa|qe|test|tid|cy|e2e|auto|automation|hook|ref|field|element|component|name)($|-)/i;
  const SKIP = new Set(%SKIP%);
  const DYN = new RegExp(%DYNAMIC%, 'i');
  const stats = new Map();
  let n = 0;
  for (const el of document.querySelectorAll('*')) {
    if (++n > 8000) break;
    for (const a of el.attributes) {
      const k = a.name;
      if (!k.startsWith('data-') || SKIP.has(k) || k.startsWith('data-v-') || k.startsWith('data-bs-')) continue;
      const v = a.value;
      if (!v || v.length > 120 || /\s/.test(v.trim())) continue;
      let s = stats.get(k);
      if (!s) { s = {name: k, count: 0, values: new Set(), dyn: 0, len: 0}; stats.set(k, s); }
      s.count++; s.values.add(v); s.len += v.length; if (DYN.test(v)) s.dyn++;
    }
  }
  const out = [];
  for (const s of stats.values()) {
    if (s.count < 3 || s.values.size / s.count < 0.9 || s.len / s.count < 4 || s.dyn / s.count > 0.3) continue;
    const hint = HINT.test(s.name.slice(5));
    if (hint || s.count >= 5) out.push({name: s.name, count: s.count, hint});
  }
  out.sort((a, b) => (b.hint - a.hint) || (b.count - a.count));
  return out.map(o => o.name);
}
"""
SKIP_ATTRS = list(TEST_ATTRS) + [
    "data-reactid", "data-reactroot", "data-state", "data-value", "data-index", "data-toggle", "data-target",
    "data-placement", "data-original-title", "data-dismiss", "data-ved", "data-hveid", "data-href", "data-src",
    "data-url", "data-type", "data-role", "data-theme", "data-size", "data-variant", "data-color",
    "data-orientation", "data-side", "data-align", "data-disabled", "data-selected", "data-checked",
    "data-highlighted", "data-focus", "data-open", "data-placeholder", "data-any-scan", "data-key", "data-next",
    "data-for", "data-label", "data-title", "data-text", "data-content", "data-tooltip"]


def detect_js_fn() -> str:
    return DETECT_JS_FN.replace("%SKIP%", json.dumps(SKIP_ATTRS)).replace("%DYNAMIC%", json.dumps(DYNAMIC_ID.pattern))


def detect_expression() -> str:
    """For page.evaluate: the ranked attribute names."""
    return f"() => {{ {detect_js_fn()} return autoplayIdAttrs(); }}"


SEPARATORS = "_-.:"


def common_prefix(values) -> str:
    """The part every value starts with, cut after its last separator: app_form_Zip, app_form_Name -> app_form_.
    Nothing when fewer than three values or nothing is left of a value after it."""
    values = [str(v) for v in values if v]
    if len(set(values)) < 3:
        return ""
    prefix = os.path.commonprefix(values)
    cut = max(prefix.rfind(s) for s in SEPARATORS)
    prefix = prefix[:cut + 1] if cut >= 1 else ""
    if not prefix or any(len(v) <= len(prefix) for v in values):
        return ""
    return prefix if re.search(r"[A-Za-z]", prefix) else ""
