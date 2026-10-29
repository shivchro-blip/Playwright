// Live console for the Control page. Lines arrive as plain strings and are rendered only through Vue text
// interpolation (never as HTML). Pinned to the bottom while the user is at the bottom, like ui.log.
const LOG_RE = /^(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(\S+:\d+)\s+(.*)$/;
const HANDLER_RE = /^(\d{4}-\d\d-\d\d [\d:,.]+) - (DEBUG|INFO|WARNING|ERROR|CRITICAL) - (.*)$/;
const RESULT_RE = /^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)$/;
const QUOTE_RE = /'[^']*'/g;

function quoteParts(text) {
  const parts = [];
  let last = 0;
  for (const m of text.matchAll(QUOTE_RE)) {
    if (m.index > last) parts.push({ t: text.slice(last, m.index), q: false });
    parts.push({ t: m[0], q: true });
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push({ t: text.slice(last), q: false });
  return parts;
}

function parse(text) {
  let m = LOG_RE.exec(text) || HANDLER_RE.exec(text);
  if (m) {
    const [level, src, msg] = LOG_RE.test(text) ? [m[1], m[2], m[3]] : [m[2], m[1], m[3]];
    return { kind: "log", level, src, parts: quoteParts(msg) };
  }
  let cls = "";
  const trimmed = text.trim();
  if (RESULT_RE.test(trimmed)) cls = trimmed === "PASSED" || trimmed === "XPASS" ? "cl-pass" : trimmed === "SKIPPED" || trimmed === "XFAIL" ? "cl-skip" : "cl-fail";
  else if (/^(FAILED|ERROR) \S+::/.test(trimmed)) cls = "cl-fail";
  else if (/^PASSED \S+::/.test(trimmed)) cls = "cl-pass";
  else if (/^tests\/\S*::/.test(trimmed)) cls = "cl-node";
  else if (/^[-=_]{5,}.*[-=_]{5,}$/.test(trimmed)) cls = "cl-rule";
  else if (/^\[webapp\]/.test(trimmed)) cls = "cl-muted";
  else if (/^[{[(]/.test(trimmed)) cls = "cl-data";
  return { kind: "text", cls, text };
}

export default {
  template: `
    <div ref="body" class="any-console-body" role="log" aria-label="Console output" tabindex="0" @scroll="onScroll">
      <div v-for="item in items" :key="item.id" :class="['any-cl', item.cls]"><template v-if="item.kind === 'log'"><span :class="'lvl lvl-' + item.level">{{ item.level }}</span><span class="src">{{ item.src }}</span><span class="msg"><span v-for="(p, j) in item.parts" :key="j" :class="p.q ? 'q' : null">{{ p.t }}</span></span></template><template v-else>{{ item.text }}</template></div>
      <div v-if="isActive" class="any-cl"><span class="any-cursor" aria-hidden="true"></span></div>
    </div>`,
  props: {
    lines: { type: Array, default: () => [] },
    active: Boolean,
    maxLines: { type: Number, default: 1500 },
  },
  data() {
    return { items: [], nextId: 0, pinned: true, isActive: this.active };
  },
  watch: {
    // Full re-sync from the server (initial render or an element update)
    lines: { handler(value) { this.items = []; this.push(value || []); }, immediate: true },
    active(value) { this.isActive = value; },
  },
  methods: {
    push(lines) {
      const atBottom = this.pinned;
      for (const text of lines) this.items.push({ id: this.nextId++, ...parse(String(text)) });
      if (this.items.length > this.maxLines) this.items.splice(0, this.items.length - this.maxLines);
      if (atBottom) this.$nextTick(this.scrollToBottom);
    },
    clear() {
      this.items = [];
      this.pinned = true;
    },
    setActive(value) {
      this.isActive = value;
      if (this.pinned) this.$nextTick(this.scrollToBottom);
    },
    scrollToBottom() {
      const el = this.$refs.body;
      if (el) el.scrollTop = el.scrollHeight;
    },
    onScroll() {
      const el = this.$refs.body;
      this.pinned = el.scrollTop + el.clientHeight >= el.scrollHeight - 4;
    },
  },
};
