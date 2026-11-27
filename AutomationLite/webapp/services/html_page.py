"""Build a page object and a first test step from a page's HTML, with no browser and no login.

Paste the page's HTML (tools/compact_html.py shrinks it) and get what a person would write by hand: a page's fields
(name, type, locator, autoGen) and the step that fills it in. How the site marks and clicks its elements comes from a
conventions profile (webapp/services/page_conventions.py), so the same builder serves any site:

- a Yes/No question becomes two fields, `Question_Yes` and `Question_No`, clicked through their labels; the step
  picks one of them;
- a checkbox, a date picker (typed key by key), a split SSN, a date in three selects, an upload and a list of radio
  options get the field their pages need;
- names and locators come from the site's own id attribute, autoGen rules from the label text (checked against the
  field's placeholder);
- what the page hid when it was captured (data-hidden) stays out of the main step; the question that probably
  reveals it is named, and a second step answers every question Yes and fills what that reveals.

Nothing is guessed silently: what cannot be decided (a required field no rule fits, a list with Add New links, a
page without buttons) is in `notes`. Standard library only; unit-tested on real pages in tests/unit/fixtures.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from webapp.services import page_conventions

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
GENERIC_VALUES = {"", "true", "false", "on", "yes", "no"}  # a radio value that tells the options apart is none of these
SKIP_INPUT_TYPES = {"hidden", "submit", "button", "image", "reset"}
PLACEHOLDER_OPTION = re.compile(r"(?i)^(?:y{2,4}|m{2,3}|d{2}|-+\s*select.*|select.*|choose.*)$")
GENERATED = re.compile(r"[0-9a-f]{16,}|\d{5,}")  # an id or name that changes on every page load
NO_PLACEHOLDER_NAME = "<PageName>"


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict[str, str], parent: "Node | None"):
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children: list[Node | str] = []

    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def text(self) -> str:
        parts: list[str] = []

        def walk(node: "Node") -> None:
            for child in node.children:
                if isinstance(child, str):
                    parts.append(child)
                elif child.tag not in ("script", "style"):
                    walk(child)
        walk(self)
        return " ".join("".join(parts).split())

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.stack = [self.root]

    def _node(self, tag, attrs) -> Node:
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        return node

    def handle_starttag(self, tag, attrs):
        node = self._node(tag, attrs)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._node(tag, attrs)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse(html: str) -> Node:
    tree = _Tree()
    tree.feed(html or "")
    tree.close()
    return tree.root


def pascal(text: str, limit: int | None = None) -> str:
    out = "".join(w[:1].upper() + w[1:] for w in re.findall(r"[A-Za-z0-9]+", text or ""))
    return out[:limit] if limit else out


def _q(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


@dataclass
class FieldDraft:
    name: str
    cfg: dict                       # what goes in the page file: locator, type, autoGen, typeKeys, index
    label: str = ""                 # what a person sees (the question, the option, the placeholder)
    kind: str = "text"              # text | select | choice | option | row | checkbox | upload | split
    required: bool = False
    hidden: bool = False            # the page captured it hidden: another answer reveals it
    group: str = ""                 # the Yes/No pair or option group it belongs to
    option: str = ""                # Yes / No, or the option's text
    options: list[tuple[str, str]] = field(default_factory=list)  # a select's (value, label)
    preselected: bool = False       # a select that already shows an option
    hidden_root: int = 0            # the outermost hidden wrapper it sits in: fields of one wrapper appear together


@dataclass
class PageDraft:
    profile: str
    page_name: str = ""
    fields: list[FieldDraft] = field(default_factory=list)
    common_action: str = ""
    notes: list[str] = field(default_factory=list)
    reveals: dict[str, list[str]] = field(default_factory=dict)  # a question -> the hidden fields after it

    def page_json(self, only: set[str] | None = None) -> dict:
        return {"fields": {f.name: dict(f.cfg) for f in self.fields if only is None or f.name in only}}

    def default_names(self, *, prefer: str = "No", include_optional: bool = False,
                      include_hidden: bool = False) -> set[str]:
        """The fields a step fills by default: what the page requires and shows, one answer per Yes/No question.
        With include_hidden (the "everything answered Yes" step) also what the page hid, whatever it requires."""
        names: set[str] = set()
        answered: set[str] = set()
        for f in self.fields:
            if f.hidden and not include_hidden:
                continue
            if not (f.required or include_optional or (f.hidden and f.kind != "checkbox")):
                continue
            if f.kind in ("choice", "option"):
                if f.group in answered:
                    continue
                pick = f.option == prefer or not any(
                    g.group == f.group and g.option == prefer for g in self.fields)
                if not pick:
                    continue
                answered.add(f.group)
            if f.kind == "row":
                continue
            names.add(f.name)
        return names

    def form_data(self, names: set[str] | None = None, *, prefer: str = "No", **options) -> dict:
        """The formData for the chosen fields (default: default_names). Choices come in page order, so a question is
        answered before the fields it reveals. A field the draft cannot give a value is left out (see notes)."""
        chosen = self.default_names(prefer=prefer, **options) if names is None else names
        data: dict = {}
        answered: set[str] = set()
        for f in self.fields:
            if f.name not in chosen:
                continue
            if f.kind in ("choice", "option"):
                if f.group in answered:
                    continue
                answered.add(f.group)
                data[f.name] = "click"
            elif f.kind == "checkbox":
                data[f.name] = "click"
            elif f.kind == "select":
                if f.cfg.get("autoGen"):
                    data[f.name] = None
                elif not f.preselected and f.options:
                    data[f.name] = f.options[0][0]
            elif f.kind in ("text", "upload", "split"):
                if f.cfg.get("autoGen"):
                    data[f.name] = None
        return data

    def steps(self, screen: str = "", names: set[str] | None = None, **options) -> list[dict]:
        out: list[dict] = [{"screen": screen or self.page_name or NO_PLACEHOLDER_NAME,
                            "formData": self.form_data(names, **options)}]
        if self.common_action:
            out.append({"commonAction": self.common_action})
        return out


def _find_page_name(root: Node) -> str:
    for in_header in (True, False):
        for node in root.walk():
            if node.tag in ("h1", "h2") and (not in_header or any("page-header" in a.classes()
                                                                   for a in node.ancestors())):
                text = node.text()
                if text:
                    return pascal(text)
    return ""


def _hidden_by_ancestor(node: Node, hidden_attr: str) -> bool:
    for n in node.ancestors():
        if hidden_attr and n.attrs.get(hidden_attr) == "1":
            return True
        if "display:none" in (n.attrs.get("style") or "").replace(" ", "").lower():
            return True
    return False


def _container(node: Node, attr: str) -> Node | None:
    if attr:
        for n in (node, *node.ancestors()):
            if n.attrs.get(attr):
                return n
    return None


def _controls(root: Node):
    for n in root.walk():
        if n.tag == "input" and (n.attrs.get("type") or "text").lower() not in SKIP_INPUT_TYPES:
            yield n
        elif n.tag in ("select", "textarea"):
            yield n


def _autogen(profile: dict, label: str, placeholder: str) -> str:
    for pattern, rule in profile["autogen_by_label"]:
        if re.search(pattern, label or "", re.I):
            if rule == "@id9":  # an EIN-like id: the hyphenated rule only where the placeholder shows a hyphen
                return "fein" if "-" in (placeholder or "") else "getIntBetween(100000000, 1000000000)"
            return rule
    return ""


class _Builder:
    def __init__(self, html: str, profile: dict):
        self.profile = profile
        self.root = parse(html)
        self.draft = PageDraft(profile=profile["name"], page_name=_find_page_name(self.root))
        self.attr = profile["id_attribute"]
        self.strip = re.compile(profile["name_strip"]) if profile["name_strip"] else None
        self.taken: set[str] = set()
        self.skipped = 0
        self.labels_for: dict[str, Node] = {}
        for n in self.root.walk():
            if n.tag == "label" and n.attrs.get("for"):
                self.labels_for.setdefault(n.attrs["for"], n)

    # ── names and locators

    def unique(self, name: str) -> str:
        base = name or "Field"
        final, n = base, 2
        while final in self.taken:
            final, n = f"{base}{n}", n + 1
        self.taken.add(final)
        return final

    def base_name(self, container: Node | None, label: str, ctl: Node) -> str:
        if container is not None:
            value = container.attrs[self.attr]
            return pascal(self.strip.sub("", value) if self.strip else value) or pascal(label, 40) or "Field"
        return pascal(label, 40) or pascal(ctl.attrs.get("name", ""), 40) or pascal(ctl.attrs.get("id", ""), 40) \
            or "Field"

    def where(self, container: Node | None, ctl: Node) -> str | None:
        """The locator prefix for a control; None when the page gives it nothing stable to hold on to."""
        if container is not None:
            return f"[{self.attr}='{_q(container.attrs[self.attr])}']"
        name, cid = ctl.attrs.get("name", ""), ctl.attrs.get("id", "")
        if name and not GENERATED.search(name):
            return f"{ctl.tag}[name='{_q(name)}']"
        if cid and not GENERATED.search(cid):
            return f"#{cid}"
        return None

    def question(self, container: Node, own_ids: set[str]) -> str:
        """The container's own label: the first one that does not belong to an option, a caption or an error."""
        for n in container.walk():
            if n.tag == "label" and n.attrs.get("for", "") not in own_ids and not (
                    n.classes() & {self.profile["radio_label_class"], self.profile["checkbox_label_class"],
                                   self.profile["upload"]["button_class"] or "-", "error"}):
                if n.text():
                    return n.text()
        return ""

    def label_of(self, ctl: Node, question: str) -> str:
        lab = self.labels_for.get(ctl.attrs.get("id", ""))
        return (lab.text() if lab else "") or question or ctl.attrs.get("placeholder", "") \
            or ctl.attrs.get("aria-label", "")

    def add(self, f: FieldDraft) -> None:
        f.hidden_root = getattr(self, "hidden_root", 0) if f.hidden else 0
        self.draft.fields.append(f)

    # ── one container's controls

    def run(self) -> PageDraft:
        groups: dict[int, tuple[Node | None, list[Node]]] = {}
        for ctl in _controls(self.root):
            c = _container(ctl, self.attr)
            groups.setdefault(id(c) if c is not None else id(ctl), (c, []))[1].append(ctl)
        for container, ctls in groups.values():
            self.container(container, ctls)
        self.finish()
        return self.draft

    def container(self, container: Node | None, ctls: list[Node]) -> None:
        p = self.profile
        kinds = {"radio": [], "checkbox": [], "file": [], "select": [], "text": []}
        for c in ctls:
            t = (c.attrs.get("type") or "").lower()
            kinds["select" if c.tag == "select" else t if t in ("radio", "checkbox", "file") else "text"].append(c)
        own_ids = {c.attrs.get("id", "") for c in ctls}
        question = self.question(container, own_ids) if container is not None else ""
        required = bool(container is not None and p["required_class"] in container.classes()) or \
            any(c.attrs.get("aria-required") == "true" for c in ctls)
        hidden = _hidden_by_ancestor(ctls[0], p["hidden_attr"]) or \
            (container is not None and p["hidden_attr"] and container.attrs.get(p["hidden_attr"]) == "1") or \
            all((c.attrs.get("name") or "").startswith("hidden-") for c in ctls)
        self.hidden_root = next((id(a) for a in reversed(list(ctls[0].ancestors()))
                                 if p["hidden_attr"] and a.attrs.get(p["hidden_attr"]) == "1"), 0)
        ctx = (container, question, required, hidden)
        if kinds["radio"]:
            self.radios(kinds["radio"], *ctx)
            return
        self.uploads(kinds["file"], kinds["text"], *ctx)
        self.checkboxes(kinds["checkbox"], *ctx)
        self.selects(kinds["select"], *ctx)
        self.texts(kinds["text"], *ctx)

    def radios(self, radios, container, question, required, hidden) -> None:
        p = self.profile
        radio_cls, (yes_for, no_for) = p["radio_label_class"], (p["yes_no"]["yes_for"], p["yes_no"]["no_for"])
        base = self.base_name(container, question, radios[0])
        loc = self.where(container, radios[0])
        if loc is None:
            self.skipped += len(radios)
            return
        labs = [self.labels_for.get(r.attrs.get("id", "")) for r in radios]
        shown = [(lab.text() if lab else "") for lab in labs]
        yes = [i for i, (lab, t) in enumerate(zip(labs, shown))
               if (yes_for and lab is not None and lab.attrs.get("for", "").startswith(yes_for))
               or (not yes_for and t.lower() == "yes")]
        no = [i for i, (lab, t) in enumerate(zip(labs, shown))
              if (no_for and lab is not None and lab.attrs.get("for", "").startswith(no_for))
              or (not no_for and t.lower() == "no")]
        if len(radios) == 2 and len(yes) == 1 and len(no) == 1:
            for option, prefix in (("Yes", yes_for), ("No", no_for)):
                sel = f"label[for^='{prefix}']" if prefix else f"label:text-is('{option}')"
                self.add(FieldDraft(self.unique(f"{base}_{option}"), {"locator": f"{loc} {sel}", "type": "click"},
                                    question or option, "choice", required, hidden, base, option))
            return
        if any(a.tag == "table" for a in radios[0].ancestors()):  # a list of records: pick the row by its text
            sel = f"label.{radio_cls}" if any(lab is not None and radio_cls in lab.classes() for lab in labs) \
                else "input[type='radio']"
            self.add(FieldDraft(self.unique(base),
                                {"locator": f"{loc} tr:has(label:has-text('{{value}}')) {sel}", "type": "click"},
                                question or "pick a row", "row", required, hidden, base))
            return
        values = [r.attrs.get("value", "") for r in radios]
        distinct = len(set(values)) == len(values) and not {v.lower() for v in values} & GENERIC_VALUES
        for r, lab, text in zip(radios, labs, shown):
            option = pascal(text, 30) or pascal(r.attrs.get("value", ""), 30) or "Option"
            styled = lab is not None and radio_cls in lab.classes()
            if distinct and styled:
                sel = f"input[value='{_q(r.attrs['value'])}'] + label.{radio_cls}"
            elif styled:
                sel = f"label.{radio_cls}:text-is('{_q(text)}')"
            else:
                sel = f"label:text-is('{_q(text)}')"
            self.add(FieldDraft(self.unique(f"{base}_{option}"), {"locator": f"{loc} {sel}", "type": "click"},
                                text or option, "option", required, hidden, base, text))

    def uploads(self, files, texts, container, question, required, hidden) -> None:
        up = self.profile["upload"]
        for f_ in files:
            base = self.base_name(container, question or self.label_of(f_, question), f_)
            loc = self.where(container, f_)
            if loc is None:
                self.skipped += 1
                continue
            button = next((n for n in container.walk() if n.tag == "label" and up["button_class"] in n.classes()),
                          None) if container is not None and up["button_class"] else None
            if button is not None:  # the visible "Choose a file" label; the input and a text area behind it are hidden
                cfg = {"locator": f"{loc} label.{up['button_class']}", "type": up["type"], "autoGen": up["autoGen"]}
            else:
                cfg = {"locator": f"{loc} input[type='file']", "type": "document", "autoGen": up["autoGen"]}
            self.add(FieldDraft(self.unique(base), cfg, question or "file", "upload", required, False))
        if files:  # the text area that holds an uploaded file's content is not a field
            texts[:] = [t for t in texts if not (t.tag == "textarea" and t.attrs.get("data-hidden") == "1")]

    def checkboxes(self, checks, container, question, required, hidden) -> None:
        cls = self.profile["checkbox_label_class"]
        for c in checks:
            base = self.base_name(container, question or self.label_of(c, question), c)
            loc = self.where(container, c)
            if loc is None:
                self.skipped += 1
                continue
            lab = self.labels_for.get(c.attrs.get("id", ""))
            cfg = ({"locator": f"{loc} label.{cls}", "type": "click"} if lab is not None and cls in lab.classes()
                   else {"locator": f"{loc} input[type='checkbox']", "type": "checkbox"})
            self.add(FieldDraft(self.unique(base), cfg, self.label_of(c, question), "checkbox", required, hidden))

    def parts(self, ctls: list[Node], tag: str) -> dict[int, dict]:
        """Controls of one tag that share a container: told apart by a class only that control has (a date's
        dmyyear / dmymonth / dmyday), else by their position."""
        same = [c for c in ctls if c.tag == tag]
        if len(same) < 2:
            return {}
        common = set.intersection(*(c.classes() for c in same)) | {"form-control", "optional", "required"}
        out: dict[int, dict] = {}
        for i, c in enumerate(same):
            own = [k for k in sorted(c.classes() - common)
                   if all(k not in o.classes() for o in same if o is not c)]
            out[id(c)] = {"class": own[0] if own else "", "index": i}
        return out

    def selects(self, selects, container, question, required, hidden) -> None:
        parts_cfg, tell = self.profile["parts"], self.parts(selects, "select")
        for s in selects:
            base = self.base_name(container, question or self.label_of(s, question), s)
            loc = self.where(container, s)
            if loc is None:
                self.skipped += 1
                continue
            part = tell.get(id(s), {})
            cfg: dict = {"locator": f"{loc} select", "type": "select"}
            name = base
            if part:
                known = parts_cfg.get(part["class"], {})
                if part["class"]:
                    cfg["locator"] = f"{loc} select.{part['class']}"
                else:
                    cfg["index"] = part["index"]
                name = f"{base}{known.get('name') or pascal(part['class']) or part['index'] + 1}"
                if known.get("autoGen"):
                    cfg["autoGen"] = known["autoGen"]
            opts = [(o.attrs.get("value", o.text()), o.text()) for o in s.walk()
                    if o.tag == "option" and o.attrs.get("value", o.text()) != ""
                    and not PLACEHOLDER_OPTION.match(o.text().strip())]
            pre = any("selected" in o.attrs and o.attrs.get("value", "x") != "" for o in s.walk()
                      if o.tag == "option")
            label = self.label_of(s, question)
            if "autoGen" not in cfg and _autogen(self.profile, label, "") in ("county", "state", "city"):
                cfg["autoGen"] = _autogen(self.profile, label, "")
            self.add(FieldDraft(self.unique(name), cfg, label, "select", required, hidden,
                                options=opts, preselected=pre))

    def texts(self, texts, container, question, required, hidden) -> None:
        p = self.profile
        split = next((sp for sp in p["split_inputs"] if all(
            any((t.attrs.get("name") or "").endswith(s) for t in texts) for s in sp["suffixes"])), None)
        if split is not None:
            base = self.base_name(container, question, texts[0])
            loc = self.where(container, texts[0])
            for i, suffix in enumerate(split["suffixes"]):
                cfg = {"locator": f"{loc + ' ' if container is not None else ''}input[name$='{_q(suffix)}']",
                       "type": "text"}
                if split.get("type_keys"):
                    cfg["typeKeys"] = True
                if split.get("autogen"):
                    cfg["autoGen"] = split["autogen"][i]
                self.add(FieldDraft(self.unique(f"{base}{split['names'][i]}"), cfg,
                                    f"{question or base} ({split['names'][i].lower()})", "split", required, hidden))
            texts = [t for t in texts if not any((t.attrs.get("name") or "").endswith(s) for s in split["suffixes"])]
        for index, t in enumerate(texts):
            label = self.label_of(t, question)
            base = self.base_name(container, question or label, t)
            loc = self.where(container, t)
            if loc is None:
                self.skipped += 1
                continue
            cfg = {"locator": f"{loc} {'textarea' if t.tag == 'textarea' else 'input'}" if container is not None
                   else loc, "type": "text"}
            if container is not None and len(texts) > 1:
                cfg["index"] = index
            placeholder = t.attrs.get("placeholder", "")
            if (p["datepicker_class"] and p["datepicker_class"] in t.classes()) or \
                    re.fullmatch(r"(?i)\s*(mm/dd/yyyy|dd/mm/yyyy)\s*", placeholder):
                cfg["typeKeys"], cfg["autoGen"] = True, p["date_autogen"]
            else:
                rule = _autogen(p, label, placeholder)
                if rule:
                    cfg["autoGen"] = rule
            self.add(FieldDraft(self.unique(base if len(texts) == 1 else f"{base}{index + 1}"), cfg,
                                label or placeholder, "text", required, hidden))

    # ── what was left to decide

    def finish(self) -> None:
        d, p = self.draft, self.profile
        question, source = "", {}  # the last question the page showed; the one that came before each hidden wrapper
        for f in d.fields:
            if f.hidden and f.hidden_root:
                if f.hidden_root not in source and question:
                    source[f.hidden_root] = question
                if f.hidden_root in source:  # one entry per hidden question or option group, not per answer
                    items = d.reveals.setdefault(source[f.hidden_root], [])
                    if (f.group or f.name) not in items:
                        items.append(f.group or f.name)
            elif not f.hidden and (f.kind == "choice" and f.option == "Yes" or f.kind == "option"):
                question = f.group
        buttons: list[tuple[str, bool]] = []
        for n in self.root.walk():
            text = ""
            if n.tag == "input" and (n.attrs.get("type") or "").lower() in ("button", "submit"):
                text = n.attrs.get("value", "")
            elif n.tag == "button" or (n.tag == "a" and "btn" in n.classes()):
                text = n.text()
            if text.strip():
                buttons.append((text.strip().lower(), "btn-primary" in n.classes()))
        forward = [(p["buttons"][t], primary) for t, primary in buttons
                   if t in p["buttons"] and p["buttons"][t] not in ("Back", "Exit application")]
        d.common_action = next((a for a, primary in forward if primary), forward[0][0] if forward else "")
        if not d.fields:
            d.notes.append("No fields were found in this HTML.")
        if not buttons:
            d.notes.append("The HTML has no buttons, so the step ends with the default common action "
                           f"({p['default_common_action']}); check it is the one this page needs.")
            d.common_action = p["default_common_action"]
        elif not d.common_action:
            d.notes.append("None of the page's buttons is a known common action; add the step that moves on "
                           "yourself.")
        if any(n.tag == "a" and "add-new-instance" in n.classes() for n in self.root.walk()):
            d.notes.append("The page has Add New links (a list of records): add those as click fields by hand; "
                           "the builder does not read lists.")
        needs = [f.name for f in d.fields if f.required and not f.hidden and f.kind in ("text", "split")
                 and not f.cfg.get("autoGen")]
        if needs:
            d.notes.append("Required, but no autoGen rule fits the label, so the step leaves them out: "
                           + ", ".join(needs) + ". Give them a value in the step or add a rule.")
        no_rule = [f.name for f in d.fields if f.required and not f.hidden and f.kind == "select"
                   and not f.cfg.get("autoGen") and not f.preselected and f.options]
        if no_rule:
            d.notes.append("Required selects with no rule use their first option: " + ", ".join(no_rule) + ".")
        rows = [f.name for f in d.fields if f.kind == "row"]
        if rows:
            d.notes.append("A list of records to pick from: " + ", ".join(rows)
                           + " - the step must say which row, by its text.")
        for question_name, shown in d.reveals.items():
            d.notes.append(f"{', '.join(shown[:6])}{' ...' if len(shown) > 6 else ''} "
                           f"{'was' if len(shown) == 1 else 'were'} hidden when the page was captured; "
                           f"probably revealed by {question_name} (assumed from the page order).")
        if self.skipped:
            d.notes.append(f"{self.skipped} control(s) had no stable id or name to build a locator from and were "
                           "left out.")


class HtmlError(ValueError):
    """The HTML cannot be read as a page (the message says why, in words for the person who pasted it)."""


def build(html: str, profile: dict | None = None) -> PageDraft:
    """The page draft for a page's HTML (profile: see page_conventions; detected from the HTML when omitted)."""
    try:
        return _Builder(html, profile or page_conventions.detect(html)).run()
    except RecursionError:  # elements nested thousands deep: not a page a person built
        raise HtmlError("This HTML is nested too deeply to read; compact it first (tools/compact_html.py).") from None
