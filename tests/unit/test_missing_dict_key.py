"""A locator that uses a DICT key nobody saved fails the step with that key's name, not a NoneType crash."""
import asyncio

from utils import dynamic_form_handler as dfh


class _Page:
    def locator(self, selector):  # never reached when a key is missing
        raise AssertionError("no element should be built")


class _Dict:
    def get(self, key):
        return None

    def load(self, key):
        return None


def test_missing_key_is_recorded_and_no_element_is_built(monkeypatch):
    async def none(_ctx, _key):
        return None
    monkeypatch.setattr(dfh, "loadFromDictionary", none)
    dfh._set_locator_problem(None)
    element = asyncio.run(dfh.get_element(_Dict(), "directorCategoryAll", _Page(),
                                          "div:has(label:text-is('DICT.directorFirst'))"))
    assert element is None
    assert "directorFirst" in dfh._locator_problem
    assert "CUSTOMFIELD_SAVEFIELD" in dfh._locator_problem
