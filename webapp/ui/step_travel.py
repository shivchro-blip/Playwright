"""Test details > Step by step: scrub through the page after each step (webapp/services/step_frames.py), beside the
same step of the test's last passing run, with the places where the two runs part listed first."""
from __future__ import annotations

from nicegui import ui

from webapp.services.step_frames import timeline
from webapp.state import services
from webapp.ui.components import text
from webapp.ui.theme import icon


def step_travel(run, nodeid: str) -> None:
    if not nodeid:
        return
    try:
        data = timeline(services(), run, nodeid)
    except Exception:  # noqa: BLE001 - the pictures are a help; the rest of the details still show
        return
    frames = data["frames"]
    if not frames:
        return
    then = {f["n"]: f for f in data["then"]}
    diffs = {d["n"]: d for d in data["differences"]}
    start = next((i for i, f in enumerate(frames) if f["n"] in diffs), len(frames) - 1)
    state = {"i": start}

    with ui.element("section").classes("any-travel").props('aria-label="Step by step" data-testid="step-travel"'):
        with ui.element("div").classes("any-head-left"):
            text("div", "Step by step", "any-eyebrow")
            text("span", (f"compared with the last pass, run {data['last_pass']}" if data["last_pass"]
                          else "no earlier passing run with step pictures to compare with"), "any-muted text-xs")
        if data["differences"]:
            with ui.element("ul").classes("any-travel-diffs").props('data-testid="travel-diffs"'):
                for d in data["differences"]:
                    with ui.element("li"):
                        text("span", f"Step {d['n']}: {d['label']}", "any-auto-name")
                        text("span", "; ".join(d["notes"]), "any-muted text-sm")
        elif data["last_pass"]:
            text("div", "Every step matches the last pass.", "any-muted text-sm")

        @ui.refreshable
        def view() -> None:
            f = frames[state["i"]]
            other = then.get(f["n"])
            with ui.element("div").classes("any-travel-head"):
                text("span", f"Step {f['n']}: {f['label']}", "any-auto-name").props('data-testid="travel-step"')
                text("span", f"{f['status']} · {f['seconds']:.1f} s", "any-tag" + (" is-failed"
                                                                                   if f["status"] == "failed" else ""))
                if f["url"]:
                    text("span", f["url"], "any-muted text-xs mono any-travel-url")
            with ui.element("div").classes("any-travel-pair" + (" is-single" if not other else "")):
                _picture(f, "This run")
                if other:
                    _picture(other, f"Last pass ({data['last_pass']})")
            if f["n"] in diffs:
                text("div", "; ".join(diffs[f["n"]]["notes"]), "any-warn-line text-sm")

        def go(delta: int) -> None:
            state["i"] = max(0, min(len(frames) - 1, state["i"] + delta))
            slider.value = state["i"]
            view.refresh()

        with ui.element("div").classes("any-travel-bar"):
            ui.button(icon=icon("chevron-left"), color=None, on_click=lambda: go(-1)) \
                .props('flat dense aria-label="Previous step" data-testid="travel-prev"').classes("any-btn is-32")
            slider = ui.slider(min=0, max=max(len(frames) - 1, 1), step=1, value=start,
                               on_change=lambda e: (state.__setitem__("i", int(e.value)), view.refresh())) \
                .props('label-always :label-value="\'Step \' + (' + str([f["n"] for f in frames])
                       + '[modelValue] || \'\')" aria-label="Step"').classes("flex-1")
            slider.set_enabled(len(frames) > 1)
            ui.button(icon=icon("chevron-right"), color=None, on_click=lambda: go(1)) \
                .props('flat dense aria-label="Next step" data-testid="travel-next"').classes("any-btn is-32")
        view()


def _picture(frame: dict, caption: str) -> None:
    with ui.element("figure").classes("any-travel-shot"):
        if frame.get("image"):
            with ui.element("a").props(f'href="{frame["image"]}" target="_blank" rel="noopener" '
                                       f'aria-label="Open the picture of step {frame["n"]}"'):
                ui.element("img").props(f'src="{frame["image"]}" alt="{caption}: step {frame["n"]}" loading="lazy"')
        else:
            text("div", "No picture of this step", "any-muted text-sm any-travel-none")
        with ui.element("figcaption"):
            text("span", caption)
            if frame.get("html"):
                with ui.element("a").props(f'href="{frame["html"]}" target="_blank" rel="noopener"'):
                    ui.label("Page HTML")
