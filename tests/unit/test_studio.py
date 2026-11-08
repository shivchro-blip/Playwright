"""Studio drafts: steps become the test's form data the way a recording is saved."""
import pytest

from webapp.services.studio import Draft, Step, StudioError, _check_url


def test_steps_become_form_data_in_order():
    draft = Draft("http://x/apply", {"searchBox": {"locator": "#q", "type": "text"},
                                     "goButton": {"role": "button", "name": "Go", "type": "button"}},
                  [Step("searchBox", "fill", "first"), Step("goButton", "click"), Step("searchBox", "fill", "again"),
                   Step("searchBox", "press", "Enter"), Step("agree", "check"), Step("state", "select", "Texas")])
    rec = draft.recording()
    assert rec.start_url == "http://x/apply" and set(rec.fields) == {"searchBox", "goButton"}
    assert rec.steps == [{"searchBox": "first", "goButton": "click", "searchBox|": "again",
                          "searchBox||": {"type": "keyboard", "value": "Enter"}, "agree": "check",
                          "state": "Texas"}]
    assert Draft().recording().steps == []


def test_only_web_addresses_open():
    assert _check_url("https://example.org/a") == "https://example.org/a"
    for bad in ("", "file:///etc/passwd", "javascript:alert(1)", "example.org"):
        with pytest.raises(StudioError):
            _check_url(bad)
