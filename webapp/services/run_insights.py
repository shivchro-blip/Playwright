"""Run history analysis: per-test trends and flaky tests, run-to-run comparison, and the built-in HTML report.

Works on the RunManager's runs only (run.json of every run), so it needs no database and no Allure CLI.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from webapp.services.run_artifacts import TestDetails
    from webapp.services.run_manager import Run

WINDOW = 10  # recent results per test that trends and flakiness look at
FAILING = {"failed", "error"}
PASSING = {"passed", "xfailed"}
FINAL = FAILING | PASSING | {"skipped", "xpassed"}  # running/cancelled results say nothing about the test
FINISHED_RUNS = {"passed", "failed", "error"}
SLOWER_RATIO, SLOWER_SECONDS = 1.5, 1.0

Key = tuple[str, str]  # (test_id, browser)


@dataclass
class TestTrend:
    test_id: str
    browser: str
    outcomes: list[str] = field(default_factory=list)  # oldest -> newest, at most WINDOW
    run_ids: list[str] = field(default_factory=list)
    durations: list[float] = field(default_factory=list)

    @property
    def verdicts(self) -> list[bool]:
        """True = pass, False = fail, for results that are either."""
        return [o in PASSING for o in self.outcomes if o in PASSING | FAILING]

    @property
    def flips(self) -> int:
        v = self.verdicts
        return sum(1 for a, b in zip(v, v[1:]) if a != b)

    @property
    def failures(self) -> int:
        return sum(1 for o in self.outcomes if o in FAILING)

    @property
    def flaky(self) -> bool:
        """Passed and failed in the window and changed its mind at least twice (P F P, F P F, ...).

        A single change is a regression or a fix, not flakiness."""
        v = self.verdicts
        return self.flips >= 2 and any(v) and not all(v)

    @property
    def state(self) -> str:
        """flaky | failing | fixed | broke | passing | none"""
        v = self.verdicts
        if not v:
            return "none"
        if self.flaky:
            return "flaky"
        if len(v) > 1 and v[-1] != v[-2]:
            return "fixed" if v[-1] else "broke"
        return "passing" if v[-1] else "failing"

    def to_dict(self) -> dict:
        return {"test_id": self.test_id, "browser": self.browser, "outcomes": self.outcomes,
                "run_ids": self.run_ids, "flips": self.flips, "failures": self.failures,
                "flaky": self.flaky, "state": self.state}


def finished(runs: Iterable[Run], system: str | None = None, env: str | None = None,
             until: str | None = None) -> list[Run]:
    """Finished runs, oldest first, optionally for one system/env and created no later than `until`."""
    out = [r for r in runs if r.status in FINISHED_RUNS
           and (system is None or r.config.system == system) and (env is None or r.config.env == env)
           and (until is None or r.created_at <= until)]
    return sorted(out, key=lambda r: r.created_at)


def trends(runs: Iterable[Run], system: str | None = None, env: str | None = None, until: str | None = None,
           window: int = WINDOW) -> dict[Key, TestTrend]:
    """Each test's last `window` final results across finished runs (one result per run and browser)."""
    out: dict[Key, TestTrend] = {}
    for run in finished(runs, system, env, until):
        for r in list(run.results.values()):
            if r.outcome not in FINAL:
                continue
            t = out.setdefault((r.test_id, r.browser), TestTrend(r.test_id, r.browser))
            t.outcomes.append(r.outcome)
            t.run_ids.append(run.id)
            t.durations.append(r.duration)
    for t in out.values():
        del t.outcomes[:-window], t.run_ids[:-window], t.durations[:-window]
    return out


def flaky_tests(runs: Iterable[Run], system: str | None = None, env: str | None = None) -> list[TestTrend]:
    found = [t for t in trends(runs, system, env).values() if t.flaky]
    return sorted(found, key=lambda t: (-t.flips, -t.failures, t.test_id))


# ───────────── comparison ─────────────

CHANGES = ["new failure", "fixed", "still failing", "slower", "faster", "status changed", "added", "removed",
           "unchanged"]


@dataclass
class CompareRow:
    test_id: str
    browser: str
    before: str | None
    after: str | None
    before_s: float | None
    after_s: float | None
    change: str

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _change(before, after, before_s, after_s) -> str:
    if before is None:
        return "added"
    if after is None:
        return "removed"
    if before not in FAILING and after in FAILING:
        return "new failure"
    if before in FAILING and after in FAILING:
        return "still failing"
    if before in FAILING:
        return "fixed"
    if before != after:
        return "status changed"
    if before in PASSING and before_s and after_s is not None:
        if after_s >= before_s * SLOWER_RATIO and after_s - before_s >= SLOWER_SECONDS:
            return "slower"
        if before_s >= after_s * SLOWER_RATIO and before_s - after_s >= SLOWER_SECONDS:
            return "faster"
    return "unchanged"


def compare(base: Run, target: Run) -> list[CompareRow]:
    """Test by test, what changed from `base` (the earlier run) to `target`. Most important changes first."""
    def index(run: Run) -> dict[Key, tuple[str, float]]:
        return {(r.test_id, r.browser): (r.outcome, r.duration) for r in list(run.results.values())}

    a, b = index(base), index(target)
    rows = []
    for key in sorted(a.keys() | b.keys()):
        (before, before_s), (after, after_s) = a.get(key, (None, None)), b.get(key, (None, None))
        rows.append(CompareRow(key[0], key[1], before, after, before_s, after_s,
                               _change(before, after, before_s, after_s)))
    return sorted(rows, key=lambda r: (CHANGES.index(r.change), r.test_id, r.browser))


def summarize(rows: list[CompareRow]) -> dict[str, int]:
    counts = {c: 0 for c in CHANGES}
    for r in rows:
        counts[r.change] += 1
    return {c: n for c, n in counts.items() if n}


def previous_run(runs: Iterable[Run], target: Run) -> Run | None:
    """The latest finished run before `target` on the same system and environment."""
    earlier = [r for r in finished(runs, target.config.system, target.config.env)
               if r.created_at < target.created_at and r.id != target.id]
    return earlier[-1] if earlier else None


# ───────────── built-in report ─────────────

TREND_RUNS = 20
REPORT_CSS_FILE = Path(__file__).resolve().parents[1] / "static" / "report.css"


@cache
def _report_css() -> str:
    return REPORT_CSS_FILE.read_text(encoding="utf-8")


def _e(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _seconds(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f} s" if value < 60 else f"{int(value) // 60}m {int(value) % 60:02d}s"


def _run_seconds(run: Run) -> float | None:
    try:
        return (datetime.fromisoformat(run.finished_at) - datetime.fromisoformat(run.started_at)).total_seconds()
    except (TypeError, ValueError):
        return None


def _dots(outcomes: list[str]) -> str:
    def cls(o: str) -> str:
        return "p" if o in PASSING else "f" if o in FAILING else ""
    label = ", ".join(outcomes)
    return (f'<span class="dots" role="img" aria-label="Last {len(outcomes)} results, oldest first: {_e(label)}">'
            + "".join(f'<i class="{cls(o)}" title="{_e(o)}"></i>' for o in outcomes) + "</span>")


def _trend_chart(runs: list[Run], current_id: str) -> str:
    """Stacked bars, one per run: passed / failed / other. Native <title> tooltips; the table below is the data."""
    if len(runs) < 2:
        return '<p class="eyebrow">Trend appears after two finished runs of this system and environment.</p>'
    w, h, pad_l, pad_b, top = 1000, 180, 34, 24, 10
    totals = [max(len(r.results), 1) for r in runs]
    peak = max(totals)
    slot = (w - pad_l) / len(runs)
    bar = max(min(slot - 6, 36), 4)
    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Test results of the last {len(runs)} runs">',
             f'<line class="axis" x1="{pad_l}" y1="{h - pad_b}" x2="{w}" y2="{h - pad_b}"/>',
             f'<text x="{pad_l - 6}" y="{top + 4}" text-anchor="end">{peak}</text>',
             f'<text x="{pad_l - 6}" y="{h - pad_b}" text-anchor="end">0</text>']
    scale = (h - pad_b - top) / peak
    for i, run in enumerate(runs):
        c = run.counts()
        segments = [("p", sum(c.get(k, 0) for k in PASSING)), ("f", sum(c.get(k, 0) for k in FAILING))]
        segments.append(("o", sum(c.values()) - segments[0][1] - segments[1][1]))
        x, y = pad_l + i * slot + (slot - bar) / 2, h - pad_b
        tip = f"{run.id}: {segments[0][1]} passed, {segments[1][1]} failed, {segments[2][1]} other"
        parts.append(f"<g><title>{_e(tip)}</title>")
        drawn = [(k, n) for k, n in segments if n]
        for j, (kind, n) in enumerate(drawn):
            seg_h = n * scale
            gap = 2 if j < len(drawn) - 1 else 0  # 2px surface gap between stacked segments
            y -= seg_h
            radius = 4 if j == len(drawn) - 1 else 0
            parts.append(f'<rect class="bar-{kind}" x="{x:.1f}" y="{y:.1f}" width="{bar:.1f}" '
                         f'height="{max(seg_h - gap, 1):.1f}" rx="{radius}"/>')
        if run.id == current_id:
            parts.append(f'<rect class="current" x="{x - 3:.1f}" y="{top - 4}" width="{bar + 6:.1f}" '
                         f'height="{h - pad_b - top + 8}" rx="5"/>')
        parts.append("</g>")
    first, last = runs[0].created_at[:10], runs[-1].created_at[:10]
    parts.append(f'<text x="{pad_l}" y="{h - 6}">{_e(first)}</text>')
    parts.append(f'<text x="{w}" y="{h - 6}" text-anchor="end">{_e(last)}</text></svg>')
    legend = ('<div class="legend"><span><i style="background: var(--pass)"></i>Passed</span>'
              '<span><i style="background: var(--fail)"></i>Failed or error</span>'
              '<span><i style="background: var(--other)"></i>Skipped or other</span>'
              '<span>Dashed outline: this run</span></div>')
    return legend + '<div class="chart">' + "".join(parts) + "</div>"


def build_report(run: Run, runs: Iterable[Run], details: dict[Key, TestDetails] | None = None) -> str:
    """Self-contained HTML summary of one run, with each test's recent results and the system's trend.

    Every value is escaped; links to attachments are relative to the run folder."""
    runs = list(runs)
    details = details or {}
    cfg = run.config
    counts = run.counts()
    history = trends(runs, cfg.system, cfg.env, until=run.created_at)
    base = previous_run(runs, run)
    changes = summarize(compare(base, run)) if base else {}
    recent = finished(runs, cfg.system, cfg.env, until=run.created_at)[-TREND_RUNS:]

    out = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           f"<title>Run {_e(run.id)}</title><style>{_report_css()}</style></head><body><main>"]
    out.append(f'<section><div class="eyebrow">AutomateNY run report</div><h1><span class="mono">{_e(run.id)}</span>'
               f'<span class="pill {_e(run.status)}">{_e(run.status)}</span></h1><div class="meta">'
               f"<span>System <b>{_e(cfg.system)}/{_e(cfg.env)}</b></span>"
               f"<span>Tags <b>{_e(', '.join(cfg.tags))}</b></span>"
               f"<span>Browsers <b>{_e(', '.join(cfg.browsers))}</b></span>"
               f"<span>Started <b>{_e((run.started_at or run.created_at).replace('T', ' '))}</b></span>"
               f"<span>Duration <b>{_e(_seconds(_run_seconds(run)))}</b></span></div>")
    if run.error:
        out.append(f'<p class="msg">{_e(run.error)}</p>')
    out.append("</section>")

    tiles = [("Tests", sum(counts.values()))] + sorted(counts.items(), key=lambda kv: -kv[1])
    out.append('<section><h2>Summary</h2><div class="tiles">' + "".join(
        f'<div class="tile"><div class="eyebrow">{_e(k)}</div><div class="n">{n}</div></div>' for k, n in tiles))
    out.append("</div>")
    if base:
        text = ", ".join(f"{n} {c}" for c, n in changes.items()) or "no tests"
        out.append(f'<p class="note">Compared with the previous run <b class="mono">{_e(base.id)}</b>: {_e(text)}.</p>')
    out.append("</section>")

    out.append(f"<section><h2>Trend: last {len(recent)} runs of {_e(cfg.system)}/{_e(cfg.env)}</h2>"
               + _trend_chart(recent, run.id))
    if len(recent) >= 2:
        out.append('<div class="table-wrap"><table><thead><tr><th>Run</th><th>Status</th>'
                   '<th class="num">Passed</th><th class="num">Failed</th><th class="num">Other</th>'
                   '<th class="num">Duration</th></tr></thead><tbody>')
        for r in reversed(recent):
            c = r.counts()
            p, f = sum(c.get(k, 0) for k in PASSING), sum(c.get(k, 0) for k in FAILING)
            out.append(f'<tr><td class="mono">{_e(r.id)}</td><td>{_e(r.status)}</td><td class="num">{p}</td>'
                       f'<td class="num">{f}</td><td class="num">{sum(c.values()) - p - f}</td>'
                       f'<td class="num">{_e(_seconds(_run_seconds(r)))}</td></tr>')
        out.append("</tbody></table></div>")
    out.append("</section>")

    by_screen = failures_by_screen(run, details)
    if by_screen:
        out.append("<section><h2>Failures by screen</h2><p class=\"note\">Several failures on one screen usually "
                   "have one cause.</p><div class=\"table-wrap\"><table><thead><tr><th>Screen</th>"
                   "<th class=\"num\">Failures</th><th>Tests</th></tr></thead><tbody>")
        for screen, tests in by_screen:
            out.append(f"<tr><td><b>{_e(screen)}</b></td><td class=\"num\">{len(tests)}</td>"
                       f"<td>{_e(', '.join(tests))}</td></tr>")
        out.append("</tbody></table></div></section>")

    out.append('<section><h2>Tests</h2><div class="table-wrap"><table><thead><tr><th>Status</th><th>Test case</th>'
               '<th>Browser</th><th class="num">Duration</th><th>Recent results</th></tr></thead><tbody>')
    order = {"failed": 0, "error": 1}
    for r in sorted(run.results.values(), key=lambda r: (order.get(r.outcome, 2), r.test_id)):
        trend = history.get((r.test_id, r.browser))
        flag = '<span class="tag flaky">flaky</span>' if trend and trend.flaky else ""
        item = details.get((r.test_id, r.browser))
        body = ""
        if r.outcome in FAILING:
            point = item.failure if item else None
            if point:
                trail = " &rsaquo; ".join(f"<b>{_e(c)}</b>" for c in point.crumbs)
                body += f'<p class="where">Failed at {trail}</p>'
            message = (item.message if item and item.message else r.message) or ""
            if message:
                body += f'<pre class="msg">{_e(message[:4000])}</pre>'
            first = [point.screenshot] if point and point.screenshot else []
            shots = first or (item.by_kind("image") if item else [])
            if shots:
                body += '<div class="shots">' + "".join(
                    f'<a href="{_e(_relative(s.url, run.id))}"><img src="{_e(_relative(s.url, run.id))}" '
                    f'alt="{_e(s.name)}" loading="lazy"></a>' for s in shots) + "</div>"
        out.append(f'<tr><td><span class="pill {_e(r.outcome)}">{_e(r.outcome)}</span></td>'
                   f"<td><code>{_e(r.test_id)}</code>{flag}{body}</td><td>{_e(r.browser)}</td>"
                   f'<td class="num">{_e(_seconds(r.duration))}</td>'
                   f"<td>{_dots(trend.outcomes) if trend else ''}</td></tr>")
    if not run.results:
        out.append('<tr><td colspan="5">No test results were recorded.</td></tr>')
    out.append("</tbody></table></div></section></main></body></html>")
    return "".join(out)


def build_markdown(run: Run, details: dict[Key, TestDetails] | None = None, max_failures: int = 25) -> str:
    """The run as Markdown (a CI job summary): status, counts, and each failure with where it failed."""
    details = details or {}
    cfg, counts = run.config, run.counts()

    def code(text: str, limit: int = 160) -> str:
        return "`" + " ".join(str(text).replace("`", "'").split())[:limit] + "`"

    out = [f"## AutomateNY run {run.id}: {run.status}", "",
           f"System **{cfg.system}/{cfg.env}** | tags **{', '.join(cfg.tags)}** | "
           f"browsers **{', '.join(cfg.browsers)}**", ""]
    if run.error:
        out += [f"> {' '.join(run.error.split())}", ""]
    if counts:
        out += ["| " + " | ".join(["Tests", *counts]) + " |", "|" + "---|" * (len(counts) + 1),
                "| " + " | ".join([str(sum(counts.values())), *map(str, counts.values())]) + " |", ""]
    failed = [r for r in run.results.values() if r.outcome in FAILING]
    if failed:
        out.append("### Failures")
        for r in sorted(failed, key=lambda r: r.test_id)[:max_failures]:
            item = details.get((r.test_id, r.browser))
            where = item.failure.short if item and item.failure else ""
            message = ((item.message if item and item.message else r.message) or "").strip().splitlines()
            line = f"- {code(r.test_id)} ({r.browser})"
            if where:
                line += f" failed at {code(where)}"
            if message:
                line += f": {code(message[0])}"
            out.append(line)
        if len(failed) > max_failures:
            out.append(f"- ... and {len(failed) - max_failures} more")
        screens = failures_by_screen(run, details)
        if len(screens) > 1 or any(len(t) > 1 for _, t in screens):
            out += ["", "By screen: " + "; ".join(f"{code(s)} x{len(t)}" for s, t in screens)]
        out.append("")
    return "\n".join(out)


def failures_by_screen(run: Run, details: dict[Key, TestDetails]) -> list[tuple[str, list[str]]]:
    """Failed tests grouped by the screen they failed on, biggest group first; tests with no known screen are
    left out."""
    groups: dict[str, list[str]] = {}
    for r in run.results.values():
        item = details.get((r.test_id, r.browser))
        if r.outcome in FAILING and item and item.failure and item.failure.screen:
            groups.setdefault(item.failure.screen, []).append(r.test_id)
    return sorted(((s, sorted(t)) for s, t in groups.items()), key=lambda kv: (-len(kv[1]), kv[0]))


def _relative(url: str, run_id: str) -> str:
    """/runs/<id>/allure-results/x -> allure-results/x (the report sits in the run folder)."""
    prefix = f"/runs/{run_id}/"
    return url[len(prefix):] if url.startswith(prefix) else url
