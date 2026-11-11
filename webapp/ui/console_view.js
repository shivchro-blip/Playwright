// Live console. Lines arrive as plain strings and are rendered only through Vue text interpolation (never as HTML).
// Pinned to the bottom while the reader is at the bottom; scrolling up holds the place and offers "Jump to latest".
// Tools: show everything / warnings and errors / errors only, find with next and previous, wrap, copy.
// The log does not announce each line to a screen reader (aria-live off); the run's status line does that.
const LOG_RE = /^(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(\S+:\d+)\s+(.*)$/;
const HANDLER_RE = /^(\d{4}-\d\d-\d\d [\d:,.]+) - (DEBUG|INFO|WARNING|ERROR|CRITICAL) - (.*)$/;
const RESULT_RE = /^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)$/;
const QUOTE_RE = /'[^']*'/g;
const SEVERITY = { DEBUG: 0, INFO: 0, WARNING: 1, ERROR: 2, CRITICAL: 2 };

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
    return { kind: "log", level, src, parts: quoteParts(msg), sev: SEVERITY[level] || 0, plain: `${level} ${src} ${msg}` };
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
  const sev = cls === "cl-fail" || /^E\s{2,}\S/.test(text) ? 2 : /^\s*(\S*Warning:|warnings\.warn)/.test(text) ? 1 : 0;
  return { kind: "text", cls, text, sev, plain: text };
}

export default {
  template: `
    <div class="any-console-wrap">
      <div class="any-console-tools" role="toolbar" aria-label="Console tools">
        <label class="any-ct-field"><span>Show</span>
          <select v-model="level" aria-label="Which lines to show">
            <option value="all">Everything</option>
            <option value="warn">Warnings and errors</option>
            <option value="error">Errors only</option>
          </select>
        </label>
        <label class="any-ct-field any-ct-find"><span class="any-ct-sr">Find in the console</span>
          <input type="search" v-model="query" placeholder="Find" autocomplete="off" aria-label="Find in the console"
                 @keydown.enter.prevent="step($event.shiftKey ? -1 : 1)" />
        </label>
        <span class="any-ct-count" role="status">{{ countLabel }}</span>
        <button type="button" class="any-ct-btn" aria-label="Previous match" :disabled="!matches.length" @click="step(-1)">&#8593;</button>
        <button type="button" class="any-ct-btn" aria-label="Next match" :disabled="!matches.length" @click="step(1)">&#8595;</button>
        <button type="button" class="any-ct-btn" :aria-pressed="wrap ? 'true' : 'false'" @click="wrap = !wrap">Wrap lines</button>
        <button type="button" class="any-ct-btn" @click="copy">{{ copied || 'Copy' }}</button>
        <span v-if="hiddenCount" class="any-ct-hidden">{{ hiddenCount }} lines hidden by the filter</span>
      </div>
      <div ref="body" :class="['any-console-body', wrap ? 'is-wrap' : '']" role="log" aria-label="Console output" aria-live="off" tabindex="0" @scroll="onScroll">
        <div v-for="item in visible" :key="item.id" :data-id="item.id" :class="['any-cl', item.cls, matchSet.has(item.id) ? 'is-hit' : '', item.id === activeId ? 'is-hit-active' : '']"><template v-if="item.kind === 'log'"><span :class="'lvl lvl-' + item.level">{{ item.level }}</span><span class="src">{{ item.src }}</span><span class="msg"><span v-for="(p, j) in item.parts" :key="j" :class="p.q ? 'q' : null">{{ p.t }}</span></span></template><template v-else>{{ item.text }}</template></div>
        <div v-if="isActive" class="any-cl"><span class="any-cursor" aria-hidden="true"></span></div>
      </div>
      <button v-if="!pinned && unseen" type="button" class="any-ct-jump" @click="jump">&#8595; {{ unseen }} new {{ unseen === 1 ? 'line' : 'lines' }}: jump to latest</button>
    </div>`,
  props: {
    lines: { type: Array, default: () => [] },
    active: Boolean,
    maxLines: { type: Number, default: 1500 },
  },
  data() {
    return { items: [], nextId: 0, pinned: true, isActive: this.active, level: "all", query: "", wrap: false, hit: -1, unseen: 0, copied: "" };
  },
  computed: {
    visible() {
      const min = this.level === "error" ? 2 : this.level === "warn" ? 1 : 0;
      return min ? this.items.filter((i) => i.sev >= min) : this.items;
    },
    matches() {
      const q = this.query.trim().toLowerCase();
      return q ? this.visible.filter((i) => i.plain.toLowerCase().includes(q)).map((i) => i.id) : [];
    },
    matchSet() { return new Set(this.matches); },
    activeId() { return this.hit >= 0 && this.hit < this.matches.length ? this.matches[this.hit] : -1; },
    hiddenCount() { return this.items.length - this.visible.length; },
    countLabel() {
      if (!this.query.trim()) return "";
      return this.matches.length ? `${this.hit + 1} of ${this.matches.length}` : "No matches";
    },
  },
  watch: {
    // Full re-sync from the server (initial render or an element update)
    lines: { handler(value) { this.items = []; this.push(value || []); }, immediate: true },
    active(value) { this.isActive = value; },
    query() { this.hit = this.matches.length ? 0 : -1; this.$nextTick(this.showHit); },
    level() { this.hit = this.matches.length ? 0 : -1; },
  },
  methods: {
    push(lines) {
      const atBottom = this.pinned;
      for (const text of lines) this.items.push({ id: this.nextId++, ...parse(String(text)) });
      if (this.items.length > this.maxLines) this.items.splice(0, this.items.length - this.maxLines);
      if (atBottom) this.$nextTick(this.scrollToBottom);
      else this.unseen += lines.length;
    },
    clear() {
      this.items = [];
      this.pinned = true;
      this.unseen = 0;
    },
    setActive(value) {
      this.isActive = value;
      if (this.pinned) this.$nextTick(this.scrollToBottom);
    },
    // Find this text (a line the page wants to show: the one a failure came from)
    find(text) {
      this.level = "all";
      this.query = String(text || "");
    },
    scrollToBottom() {
      const el = this.$refs.body;
      if (el) el.scrollTop = el.scrollHeight;
    },
    jump() {
      this.pinned = true;
      this.unseen = 0;
      this.scrollToBottom();
    },
    onScroll() {
      const el = this.$refs.body;
      this.pinned = el.scrollTop + el.clientHeight >= el.scrollHeight - 4;
      if (this.pinned) this.unseen = 0;
    },
    step(direction) {
      const n = this.matches.length;
      if (!n) return;
      this.hit = this.hit < 0 ? (direction > 0 ? 0 : n - 1) : (this.hit + direction + n) % n;
      this.$nextTick(this.showHit);
    },
    showHit() {
      if (this.activeId < 0) return;
      const el = this.$refs.body && this.$refs.body.querySelector(`[data-id="${this.activeId}"]`);
      if (el) el.scrollIntoView({ block: "center" });
    },
    async copy() {
      const text = this.visible.map((i) => i.plain).join("\n");
      let ok = false;
      try {  // the clipboard API can wait forever when the page is not focused: give it a second
        await Promise.race([navigator.clipboard.writeText(text),
                            new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), 1000))]);
        ok = true;
      } catch (e) {
        const area = document.createElement("textarea");
        area.value = text;
        document.body.appendChild(area);
        area.select();
        try { ok = document.execCommand("copy"); } catch (e2) { ok = false; }
        area.remove();
      }
      this.copied = ok ? "Copied" : "Could not copy";
      setTimeout(() => { this.copied = ""; }, 1500);
    },
  },
};
