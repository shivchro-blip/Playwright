"""Shrink a pasted page's HTML to what is needed to build a POM: controls, labels, buttons, tables, visibility.

Usage (no packages to install, Python 3.8+):
    python compact_html.py                 copy the page HTML, run this, then paste (the result is on the clipboard)
    Get-Clipboard -Raw | python compact_html.py - | Set-Clipboard     (PowerShell, no tkinter needed)
    python compact_html.py page.html       read a file instead; writes page.compact.html and also copies the result
Options:
    --max-options N   keep at most N options per dropdown (default 12; selected options are always kept)
    --keep-chrome     keep header / nav / footer (dropped by default)
"""
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
DROP = {"script", "style", "link", "meta", "svg", "noscript", "img", "iframe", "head", "title", "br", "hr", "canvas", "video", "audio"}
CHROME = {"header", "nav", "footer"}
KEEP = {"form", "input", "select", "option", "optgroup", "textarea", "button", "a", "label", "table", "thead", "tbody",
        "tfoot", "tr", "th", "td", "caption", "h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "li", "legend",
        "fieldset"}
DROP_CLASS = ("interview-helptip", "error-wrap", "interview-version-info", "sr-only-skip", "ui-datepicker-trigger",
              "skip-link", "breadcrumb")
KEEP_ATTRS = ("id", "class", "type", "name", "value", "placeholder", "for", "href", "title", "role", "aria-label",
              "aria-required", "aria-invalid", "maxlength", "data-attributeid", "data-opaid", "data-code", "data-toggle",
              "data-target", "data-dismiss")
FLAGS = ("disabled", "readonly", "checked", "selected", "multiple", "required", "hidden")
KEEP_DIV_CLASS = ("alert", "modal", "error-list", "radio", "button_container", "page-header", "container-fluid",
                  "dashboard-table", "question", "heading", "form-group-keep")
MD5 = re.compile(r"(?<=[_-])[0-9a-fA-F]{32}\b")
GUID = re.compile(r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\b")


def shorten(text, limit):
    text = MD5.sub("…", GUID.sub("<guid>", text or ""))
    return text if len(text) <= limit else text[:limit] + "…"


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.kids = tag, dict(attrs), parent, []


class Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", [], None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, [(k, v if v is not None else "") for k, v in attrs], self.cur)
        self.cur.kids.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.kids.append(Node(tag, [(k, v if v is not None else "") for k, v in attrs], self.cur))

    def handle_endtag(self, tag):
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.cur = node.parent

    def handle_data(self, data):
        self.cur.kids.append(data)


class Compactor:
    def __init__(self, max_options=12, keep_chrome=False):
        self.max_options, self.keep_chrome = max_options, keep_chrome
        self.lines = []

    def hidden(self, node):
        style = (node.attrs.get("style") or "").replace(" ", "").lower()
        return "display:none" in style or "hidden" in node.attrs or node.attrs.get("type") == "hidden"

    def dropped(self, node):
        tag, cls = node.tag, (node.attrs.get("class") or "").lower()
        if tag in DROP or (tag in CHROME and not self.keep_chrome):
            return True
        if tag == "input" and node.attrs.get("type") == "hidden":
            return True
        return any(c in cls for c in DROP_CLASS)

    def meaningful(self, node):
        a = node.attrs
        if node.tag in KEEP:
            return True
        if a.get("data-attributeid") or a.get("data-opaid"):
            return True
        if node.tag in ("div", "span", "section", "main", "ul"):
            cls = (a.get("class") or "").lower()
            if any(c in cls for c in KEEP_DIV_CLASS):
                return True
            ident = a.get("id") or ""
            if ident and not ident.startswith(("rn_", "container_")):
                return True
        return False

    def attrs_text(self, node):
        out = []
        for key in KEEP_ATTRS:
            val = node.attrs.get(key)
            if val is None or (val == "" and key not in ("value",)):
                continue
            if key == "class":
                val = " ".join(val.split()[:6])
            limit = 90 if key in ("title", "aria-label", "href") else 60
            out.append(f'{key}="{shorten(val, limit)}"')
        out += [flag for flag in FLAGS if flag in node.attrs]
        if self.hidden(node):
            out.append('data-hidden="1"')
        return (" " + " ".join(out)) if out else ""

    def text_of(self, node):
        parts = []
        for kid in node.kids:
            if isinstance(kid, str):
                parts.append(kid)
            elif kid.tag not in DROP and not self.dropped(kid):
                parts.append(self.text_of(kid))
        return " ".join("".join(parts).split())

    def has_element_kids(self, node):
        return any(isinstance(k, Node) and not self.dropped(k) and
                   (k.tag in KEEP or self.meaningful(k) or self.has_element_kids(k)) for k in node.kids)

    def walk(self, node, depth):
        pad = " " * depth
        if isinstance(node, str):
            text = " ".join(node.split())
            if text:
                self.lines.append(pad + shorten(text, 160))
            return
        if self.dropped(node):
            return
        if node.tag == "select":
            return self.select(node, depth)
        if not self.meaningful(node):
            for kid in node.kids:  # unwrap: keep the children, lose the wrapper
                self.walk(kid, depth)
            return
        attrs = self.attrs_text(node)
        if node.tag in VOID:
            self.lines.append(f"{pad}<{node.tag}{attrs}>")
            return
        if not self.has_element_kids(node):
            text = shorten(self.text_of(node), 160)
            if text or attrs:
                self.lines.append(f"{pad}<{node.tag}{attrs}>{text}</{node.tag}>")
            return
        self.lines.append(f"{pad}<{node.tag}{attrs}>")
        for kid in node.kids:
            self.walk(kid, depth + 1)
        self.lines.append(f"{pad}</{node.tag}>")

    def select(self, node, depth):
        pad = " " * depth
        options = []
        stack = list(node.kids)
        while stack:
            kid = stack.pop(0)
            if isinstance(kid, Node):
                if kid.tag == "option":
                    options.append(kid)
                else:
                    stack = list(kid.kids) + stack
        keep, seen = [], 0
        for opt in options:
            if "selected" in opt.attrs or seen < self.max_options:
                keep.append(opt)
            seen += 1
        self.lines.append(f"{pad}<select{self.attrs_text(node)}>")
        for opt in keep:
            sel = " selected" if "selected" in opt.attrs else ""
            self.lines.append(f'{pad} <option value="{shorten(opt.attrs.get("value", ""), 40)}"{sel}>'
                              f"{shorten(self.text_of(opt), 80)}</option>")
        if len(options) > len(keep):
            self.lines.append(f"{pad} <!-- {len(options) - len(keep)} more options -->")
        self.lines.append(f"{pad}</select>")


def compact(html, max_options=12, keep_chrome=False):
    builder = Builder()
    builder.feed(html)
    builder.close()
    comp = Compactor(max_options, keep_chrome)
    for kid in builder.root.kids:
        comp.walk(kid, 0)
    return "\n".join(comp.lines)


def read_clipboard():
    import tkinter
    root = tkinter.Tk()
    root.withdraw()
    try:
        return root.clipboard_get()
    finally:
        root.destroy()


def write_clipboard(text):
    import tkinter
    root = tkinter.Tk()
    root.withdraw()
    root.clipboard_clear()
    root.clipboard_append(text)
    root.update()
    root.destroy()


def main(argv):
    max_options, keep_chrome, files, i = 12, False, [], 0
    while i < len(argv):
        if argv[i] == "--max-options":
            max_options, i = int(argv[i + 1]), i + 1
        elif argv[i] == "--keep-chrome":
            keep_chrome = True
        else:
            files.append(argv[i])
        i += 1
    if files and files[0] == "-":  # PowerShell: Get-Clipboard -Raw | python compact_html.py - | Set-Clipboard
        sys.stdout.reconfigure(encoding="utf-8")
        print(compact(sys.stdin.read(), max_options, keep_chrome))
        return
    if files:
        src = Path(files[0])
        html = src.read_text(encoding="utf-8", errors="replace")
    else:
        src = None
        html = read_clipboard()
    result = compact(html, max_options, keep_chrome)
    if src:
        out = src.with_suffix(".compact.html")
        out.write_text(result, encoding="utf-8")
        print(f"Saved {out}")
    try:
        write_clipboard(result)
        print("Copied to the clipboard: paste it into the chat.")
    except Exception as e:  # no display / tkinter missing
        print(f"(Could not use the clipboard: {e})")
        if not src:
            print(result)
    print(f"{len(html):,} -> {len(result):,} characters ({100 - len(result) * 100 // max(len(html), 1)}% smaller)")


if __name__ == "__main__":
    main(sys.argv[1:])
