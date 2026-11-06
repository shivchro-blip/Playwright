"""Smart capture: record a test with the full picture of every element touched, so the recorded locator still
finds it on the next run.

Playwright codegen writes one locator per action and nothing else, and the recorder could only turn that text into
a field: it never knew the element's id, name or label, kept auto-generated ids such as #input-8231 that change on
every page load, and lost codegen's exact matching. Smart capture runs the recording browser itself with a script
in the page that, at the moment of each click or entry, describes the element (tag, type, id, name, test ids, the
site's id attribute, aria-label, label text, placeholder, role, text) and lists the locators that could find it,
each with how many elements it matches right then. The most stable one that matches exactly one element is kept:

    site id attribute > data-testid (and data-test, data-cy, data-qa) > a fixed id > name > aria-label
    > label text (exact) > placeholder > button or link text (exact) > link address > a short CSS path

Ids that look generated (digits, hashes, framework prefixes, React's :r1:) are never used. Field names come from
what a person sees (label, aria-label, placeholder, name). The description is saved as the field's locator profile,
so self-healing can find the element from the first run if the page changes later.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from webapp.services.recorder import (
    TYPE_SUFFIX, Recording, RecordOptions, Screen, _looks_secret, _pascal, secret_ref, secret_warning,
)

DYNAMIC_ID = re.compile(
    r"\d{3,}|[0-9a-f]{8,}|^:|:r\w*:|^(mat|mui|ember|ext-gen|ext-comp|react-select|j_id|jdt|ui-id|yui|gwt|ng-|cdk-|"
    r"headlessui|radix|downshift|rc_|tabs-|__)|[-_]\d+$|^\d", re.IGNORECASE)
TEST_ATTRS = ("data-testid", "data-test", "data-test-id", "data-cy", "data-qa")

# Runs in every page of the recording browser. Describes the element an action targets and the locators that could
# find it, each with its match count now. `window.__autoplayRecord(event)` is the binding to the recorder.
CAPTURE_JS = r"""
(() => {
  if (window.__autoplayCapture) return;
  window.__autoplayCapture = true;
  const ID_ATTR = %ID_ATTR%;
  const TEST_ATTRS = %TEST_ATTRS%;
  const DYNAMIC = new RegExp(%DYNAMIC%, 'i');
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const q = s => JSON.stringify(s);
  const count = sel => { try { return document.querySelectorAll(sel).length; } catch (e) { return -1; } };
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const TEXTLIKE = new Set(['', 'text', 'email', 'tel', 'url', 'search', 'password', 'number', 'date',
                            'datetime-local', 'month', 'time', 'week', 'color', 'range']);
  const tagOf = el => el.tagName.toLowerCase();
  const typeOf = el => (el.getAttribute('type') || '').toLowerCase();
  const isText = el => (tagOf(el) === 'input' && TEXTLIKE.has(typeOf(el))) || tagOf(el) === 'textarea'
                        || el.isContentEditable;
  const CLICKABLE = 'button, a[href], [role=button], [role=link], [role=menuitem], [role=tab], [role=option], ' +
                    '[role=checkbox], [role=radio], [role=switch], input[type=submit], input[type=button], ' +
                    'input[type=reset], input[type=image], summary';
  function labelOf(el) {
    if (el.labels && el.labels.length) return norm(el.labels[0].innerText);
    const by = el.getAttribute('aria-labelledby');
    if (by) return norm(by.split(/\s+/).map(i => (document.getElementById(i) || {}).innerText || '').join(' '));
    return '';
  }
  function roleOf(el) {
    const r = el.getAttribute('role'); if (r) return r;
    const t = tagOf(el), ty = typeOf(el);
    if (t === 'button' || (t === 'input' && ['submit', 'button', 'reset', 'image'].includes(ty))) return 'button';
    if (t === 'a' && el.hasAttribute('href')) return 'link';
    if (t === 'input' && ty === 'checkbox') return 'checkbox';
    if (t === 'input' && ty === 'radio') return 'radio';
    if (t === 'select') return 'combobox';
    if (isText(el)) return 'textbox';
    return '';
  }
  const textOf = el => norm(el.innerText || el.value || el.getAttribute('aria-label') || el.title || '').slice(0, 80);
  function sameText(el, text) {   // elements of the same kind showing exactly this text
    const role = roleOf(el);
    const pool = role === 'button'
               ? document.querySelectorAll('button, [role=button], input[type=submit], input[type=button]')
               : role === 'link' ? document.querySelectorAll('a[href], [role=link]')
               : document.getElementsByTagName(tagOf(el));
    let n = 0; for (const o of pool) if (textOf(o) === text && visible(o)) n++;
    return n;
  }
  function cssPath(el) {   // [path, anchored on an element with a fixed id or test id]
    const parts = [];
    let node = el, anchored = false;
    while (node && node.nodeType === 1 && parts.length < 5) {
      const t = tagOf(node);
      if (node.id && !DYNAMIC.test(node.id) && node !== el) {
        parts.unshift('#' + CSS.escape(node.id)); anchored = true; break;
      }
      const tid = TEST_ATTRS.map(a => [a, node.getAttribute(a)]).find(([, v]) => v);
      if (tid && node !== el) { parts.unshift(`[${tid[0]}=${q(tid[1])}]`); anchored = true; break; }
      const parent = node.parentElement;
      if (!parent) { parts.unshift(t); break; }
      const same = [...parent.children].filter(c => c.tagName === node.tagName);
      parts.unshift(same.length > 1 ? `${t}:nth-of-type(${same.indexOf(node) + 1})` : t);
      node = parent;
    }
    return [parts.join(' > '), anchored];
  }
  function describe(el) {
    const t = tagOf(el), ty = typeOf(el), role = roleOf(el), text = textOf(el), label = labelOf(el);
    const d = {tag: t, type: ty, role, id: el.id || '', name: el.getAttribute('name') || '',
               aria: el.getAttribute('aria-label') || '', label, placeholder: el.getAttribute('placeholder') || '',
               title: el.getAttribute('title') || '', text: isText(el) ? '' : text,
               href: t === 'a' ? (el.getAttribute('href') || '') : '', testid: '', site_id: '', candidates: []};
    for (const a of TEST_ATTRS) if (el.getAttribute(a)) { d.testid = el.getAttribute(a); d.testid_attr = a; break; }
    const add = (kind, sel, n, stable = true) => {
      if (sel && n > 0) d.candidates.push({kind, sel, count: n, stable});
    };
    if (ID_ATTR) {
      const own = el.getAttribute(ID_ATTR);
      if (own) {
        d.site_id = own; d.site_id_own = true;
        add('site_id', `[${ID_ATTR}='${own.replace(/'/g, "\\'")}']`, count(`[${ID_ATTR}=${q(own)}]`));
      }
      else {
        const box = el.parentElement && el.parentElement.closest(`[${ID_ATTR}]`);
        if (box) {
          const v = box.getAttribute(ID_ATTR); d.site_id = v;
          const base = `[${ID_ATTR}='${v.replace(/'/g, "\\'")}']`;
          const inner = [...box.querySelectorAll(t)].filter(visible);
          if (role === 'button' || role === 'link') {
            const same = inner.filter(o => textOf(o) === text).length;
            if (text) add('site_id', `${base} ${t}:text-is('${text.replace(/'/g, "\\'")}')`, same);
          }
          add('site_id', `${base} ${t}`, inner.length);
        }
      }
    }
    if (d.testid) add('testid', `[${d.testid_attr}=${q(d.testid)}]`, count(`[${d.testid_attr}=${q(d.testid)}]`));
    if (d.id) add('id', `[id=${q(d.id)}]`, count(`[id=${q(d.id)}]`), !DYNAMIC.test(d.id));
    if (d.name) {
      const sel = ty === 'radio' || ty === 'checkbox' ? `${t}[name=${q(d.name)}][value=${q(el.value || '')}]`
                                                     : `${t}[name=${q(d.name)}]`;
      add('name', sel, count(sel), !DYNAMIC.test(d.name));
    }
    if (d.aria) add('aria', `${t}[aria-label=${q(d.aria)}]`, count(`${t}[aria-label=${q(d.aria)}]`));
    if (label) {
      let n = 0; for (const l of document.querySelectorAll('label')) if (norm(l.innerText) === label) n++;
      add('label', `internal:label=${q(label)}s`, n || 1, !/\d{2,}/.test(label));
    }
    if (d.placeholder) {
      const sel = `${t}[placeholder=${q(d.placeholder)}]`;
      add('placeholder', sel, count(sel));
    }
    if ((role === 'button' || role === 'link' || role === 'tab' || role === 'menuitem' || role === 'option') && text) {
      const sel = role === 'button' || role === 'link' ? `internal:role=${role}[name=${q(text)}s]`
                                                       : `${t}:text-is(${q(text)})`;
      add('text', sel, sameText(el, text), !/\d{2,}/.test(text) && text.length <= 50);
    }
    if (d.href && !/[?#]|\d{3,}/.test(d.href)) add('href', `a[href=${q(d.href)}]`, count(`a[href=${q(d.href)}]`));
    const [path, anchored] = cssPath(el);
    if (path) {
      const all = [...document.querySelectorAll(path)];
      d.css = {sel: path, count: all.length, index: Math.max(0, all.indexOf(el)), anchored};
    }
    return d;
  }
  const send = ev => { try { window.__autoplayRecord(ev); } catch (e) { } };
  const pending = new Map();   // text field -> last value, sent on change, Enter or before a click elsewhere
  function flush(except) {
    for (const [el, info] of pending) if (el !== except) { send(info); pending.delete(el); }
  }
  document.addEventListener('input', e => {
    const el = e.target;
    if (!(el instanceof Element) || !isText(el)) return;
    const secret = typeOf(el) === 'password';
    const value = secret ? '' : (el.isContentEditable ? el.innerText : el.value);
    pending.set(el, {action: 'fill', el: describe(el), value, secret, url: location.href});
  }, true);
  document.addEventListener('change', e => {
    const el = e.target;
    if (!(el instanceof Element)) return;
    if (pending.has(el)) { send(pending.get(el)); pending.delete(el); return; }
    const t = tagOf(el), ty = typeOf(el);
    if (t === 'select') {
      const opt = el.options[el.selectedIndex];
      send({action: 'select', el: describe(el), value: opt ? norm(opt.text) : el.value, url: location.href});
    } else if (ty === 'checkbox' || ty === 'radio') {
      send({action: el.checked ? 'check' : 'uncheck', el: describe(el), url: location.href});
    } else if (ty === 'file') {
      send({action: 'upload', el: describe(el), value: [...el.files].map(f => f.name).join(', '), url: location.href});
    }
  }, true);
  document.addEventListener('keydown', e => {
    const el = e.target;
    if (!(el instanceof Element) || !['Enter', 'Tab', 'Escape'].includes(e.key) || !isText(el)) return;
    if (pending.has(el)) { send(pending.get(el)); pending.delete(el); }
    if (e.key === 'Enter') send({action: 'press', el: describe(el), value: 'Enter', url: location.href});
  }, true);
  document.addEventListener('click', e => {
    if (!e.isTrusted || !(e.target instanceof Element)) return;
    const raw = e.target;
    if (isText(raw) || tagOf(raw) === 'select' || tagOf(raw) === 'option') return;   // focusing, not acting
    const box = raw.closest('label');
    if (box && (box.control || box.querySelector('input'))) return;                  // its input reports the change
    if (tagOf(raw) === 'input' && ['checkbox', 'radio', 'file'].includes(typeOf(raw))) return;
    const el = raw.closest(CLICKABLE) || raw;
    flush(el);
    send({action: 'click', el: describe(el), url: location.href});
  }, true);
  window.addEventListener('beforeunload', () => flush(null));
})();
"""


def capture_script(id_attribute: str = "") -> str:
    return (CAPTURE_JS.replace("%ID_ATTR%", json.dumps(id_attribute or ""))
            .replace("%TEST_ATTRS%", json.dumps(list(TEST_ATTRS)))
            .replace("%DYNAMIC%", json.dumps(DYNAMIC_ID.pattern)))


# ───────────── events -> fields and steps ─────────────

PRIORITY = ("site_id", "testid", "id", "name", "aria", "label", "placeholder", "text", "href")
KIND_FROM_ROLE = {"button": "button", "link": "link", "checkbox": "checkbox", "radio": "radio", "combobox": "select",
                  "tab": "button", "menuitem": "button", "option": "button", "switch": "checkbox"}


@dataclass
class Choice:
    config: dict  # the field's locator keys (locator, index)
    kind: str  # site_id | testid | id | ... | css
    stable: bool
    note: str = ""


def choose_locator(el: dict) -> Choice:
    """The most stable candidate that matches exactly one element; otherwise the best one with an index."""
    candidates = [c for c in el.get("candidates") or [] if isinstance(c, dict) and c.get("sel")]
    ranked = sorted(candidates, key=lambda c: (not c.get("stable", True), PRIORITY.index(c["kind"])
                                               if c.get("kind") in PRIORITY else 99))
    for c in ranked:
        if c.get("count") == 1 and c.get("stable", True):
            return Choice({"locator": c["sel"]}, c["kind"], True)
    css = el.get("css") or {}
    if css.get("sel") and css.get("count") == 1:  # position on the page outlives an id that is regenerated
        if css.get("anchored"):  # inside an element with a fixed id: "#billing > button"
            return Choice({"locator": css["sel"]}, "css", True)
        return Choice({"locator": css["sel"]}, "css", False, "located by its position on the page (no fixed id, "
                      "name or label)")
    for c in ranked:
        if c.get("count") == 1:
            return Choice({"locator": c["sel"]}, c["kind"], False,
                          f"the only locator matching just this element is its {c['kind']}, which looks generated")
    if ranked:  # several matches everywhere: the stable one, plus which match it is
        best = ranked[0]
        index = css.get("index", 0) if best.get("count", 0) > 1 else 0
        return Choice({"locator": best["sel"], "index": int(index)}, best["kind"], False,
                      f"{best['count']} elements match its {best['kind']}; the recording used match {index + 1}")
    if css.get("sel"):
        return Choice({"locator": css["sel"], "index": int(css.get("index", 0))}, "css", False,
                      "located by its position on the page (no id, name or label)")
    return Choice({}, "none", False, "no way to find this element again")


def field_kind(el: dict, action: str) -> str:
    if action == "select":
        return "select"
    if action == "upload":
        return "document"
    if action in ("check", "uncheck"):
        return "radio" if el.get("type") == "radio" else "checkbox"
    if action in ("fill", "press"):
        return "text"
    return KIND_FROM_ROLE.get(el.get("role") or "", "button")


def name_for(el: dict, kind: str, taken: set[str], options: RecordOptions) -> str:
    """From what a person sees: the site id (prefix removed), label, aria-label, placeholder, name, fixed id, test
    id, text; camelCase with the type's suffix (firstName, saveButton), like the rest of the workspace."""
    site = el.get("site_id") or ""
    if site and options.id_attribute:
        raw = site[len(options.id_prefix):] if options.id_prefix and site.startswith(options.id_prefix) else site
        base = _pascal(raw)[:40]
        if el.get("role") in ("button", "link") and el.get("text") and not el.get("site_id_own"):
            base += _pascal(el["text"])[:20]
        base = base or kind
        if base[:1].isdigit():
            base = "F" + base
    else:
        ident = el.get("id") if el.get("id") and not DYNAMIC_ID.search(el["id"]) else ""
        name_attr = el.get("name") if el.get("name") and not DYNAMIC_ID.search(el["name"]) else ""
        source = (el.get("label") or el.get("aria") or el.get("placeholder") or name_attr or ident
                  or el.get("testid") or el.get("text") or el.get("title") or "")
        words = re.findall(r"[A-Za-z][a-z]*|[A-Z]+(?![a-z])|\d+", source)[:5]
        base = (words[0].lower() + "".join(w[:1].upper() + w[1:].lower() for w in words[1:])) if words else kind
        if base[:1].isdigit():
            base = "f" + base
        suffix = TYPE_SUFFIX.get(kind, "")
        if suffix and not base.lower().endswith(suffix.lower()):
            base += suffix
        base = base[:40] or "field"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}{n}", n + 1
    return name


def profile_of(el: dict) -> dict:
    """The element as self-healing remembers it (Data/locatorProfiles)."""
    return {"tag": el.get("tag", ""), "id": el.get("id", ""), "name": el.get("name", ""),
            "testid": el.get("testid", ""), "type": el.get("type", ""), "role": el.get("role", ""),
            "placeholder": el.get("placeholder", ""), "aria": el.get("aria", ""), "label": el.get("label", ""),
            "text": el.get("text", "")}


@dataclass
class _Page:
    fields: dict[str, dict] = field(default_factory=dict)
    step: dict = field(default_factory=dict)
    by_locator: dict[str, str] = field(default_factory=dict)
    uses: dict[str, int] = field(default_factory=dict)
    profiles: dict[str, dict] = field(default_factory=dict)


def events_to_recording(events: list[dict], start_url: str, options: RecordOptions | None = None) -> Recording:
    """Captured events (in order) -> the same Recording the codegen path builds: fields, one formData step per
    page (a screen per wizard page), warnings; plus `profiles` (field -> element description)."""
    options = options or RecordOptions()
    rec = Recording(start_url=start_url)
    breaks = {b.strip().casefold() for b in options.break_on if b and b.strip()}
    pages: list[_Page] = []
    current = _Page()
    fragile: list[str] = []

    def add(name: str, value) -> None:
        current.uses[name] = current.uses.get(name, 0) + 1
        current.step[name + "|" * (current.uses[name] - 1)] = value  # next, next|, next||: the field again

    last_fill: tuple[str, str] | None = None
    for ev in events:
        el = ev.get("el") if isinstance(ev, dict) else None
        action = ev.get("action") if isinstance(ev, dict) else None
        if not isinstance(el, dict) or action not in ("fill", "select", "check", "uncheck", "upload", "click", "press"):
            continue
        choice = choose_locator(el)
        if not choice.config:
            rec.warnings.append(f"Could not find a way to locate the element of a {action}; skipped.")
            continue
        kind = field_kind(el, action)
        key = json.dumps(choice.config, sort_keys=True)
        name = current.by_locator.get(key)
        if name is None:
            name = name_for(el, kind, set(current.fields), options)
            current.fields[name] = {**choice.config, "type": kind}
            current.by_locator[key] = name
            current.profiles[name] = profile_of(el)
            if not choice.stable:
                fragile.append(f"{name}: {choice.note}")
        if action == "fill":
            value = ev.get("value") or ""
            if ev.get("secret") or (value and _looks_secret(name, {**current.fields[name], **profile_of(el)})):
                rec.warnings.append(secret_warning(name))
                value = secret_ref(name)
            if last_fill and last_fill[0] == name and list(current.step)[-1:] and \
                    current.step[list(current.step)[-1]] == last_fill[1]:
                current.step[list(current.step)[-1]] = value  # typed again before leaving it: keep the last value
            else:
                add(name, value)
                rec.actions += 1
            last_fill = (name, value)
            continue
        last_fill = None
        if action == "press":
            add(name, {"type": "keyboard", "value": str(ev.get("value") or "Enter")})
        elif action == "select":
            add(name, str(ev.get("value") or ""))
        elif action == "check":
            add(name, "check")
        elif action == "uncheck":
            add(name, "click")
            rec.warnings.append(f"'{name}' was unchecked; saved as a click, so check its starting state.")
        elif action == "upload":
            add(name, str(ev.get("value") or ""))
            rec.warnings.append(f"'{name}' uploads {ev.get('value')}; put that file where the tests run and use its "
                                "full path.")
        else:
            add(name, "click")
        rec.actions += 1
        label = (el.get("text") or el.get("aria") or "").strip().casefold()
        if breaks and action == "click" and label in breaks:
            pages.append(current)
            current = _Page()
            last_fill = None
    if breaks:
        if current.step:
            pages.append(current)
        rec.screens = [Screen(f"Screen{i + 1}", p.fields, [p.step]) for i, p in enumerate(pages)]
    elif current.step:
        rec.fields, rec.steps = current.fields, [current.step]
        pages = [current]
    rec.profiles = [p.profiles for p in pages]  # type: ignore[attr-defined]
    rec.warnings = list(dict.fromkeys(rec.warnings))  # one line per field, however often it was typed into
    if fragile:
        rec.warnings.append("Check these locators, which may not find the element next time: " + "; ".join(fragile))
    if not rec.actions:
        rec.warnings.append("No actions were recorded.")
    rec.source = json.dumps(_masked(events), indent=1, ensure_ascii=False)
    return rec


def _masked(events: list[dict]) -> list[dict]:
    out = []
    for ev in events:
        ev = dict(ev)
        el = ev.get("el") or {}
        if ev.get("secret") or (ev.get("action") == "fill" and _looks_secret(str(el.get("name", "")), el)):
            ev["value"] = "••••••" if ev.get("value") or ev.get("secret") else ""
        ev["el"] = {k: v for k, v in el.items() if k != "candidates"}
        out.append(ev)
    return out
