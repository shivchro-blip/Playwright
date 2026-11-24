"""tools/compact_html.py (shrink pasted page HTML) and tools/fix_legal_names.py (clean legal-name generators)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SAMPLE = """<html><head><script>var x=1;</script><style>.a{}</style></head><body><nav>menu</nav>
<form id="interview-form"><input type="hidden" id="requestId" value="75579B77-D71C-7D75-CD8C-8EAE7D65D1D5">
<div id="rn_OpaControl_x_531f3cbc23d6fc6c7e3a11a934f66b97" class="rn_OpaControl required"
 data-attributeid="LSI_A" data-opaid="global_a_LSI_A" style="display: none;">
<div class="form-group"><label for="global_a_LSI_A_531f3cbc23d6fc6c7e3a11a934f66b97">Reason</label>
<select id="global_a_LSI_A_531f3cbc23d6fc6c7e3a11a934f66b97" aria-required="true">%s</select></div>
<div class="interview-helpTip-wrap"><p>Long tip</p></div></div>
<input type="button" id="btnNext" value="Save &amp; Continue"></form></body></html>"""


def test_compact_keeps_controls_and_drops_noise():
    mod = _load("compact_html")
    options = "".join(f'<option value="{i}">Item {i}</option>' for i in range(40)) + \
        '<option value="x" selected>Chosen</option>'
    out = mod.compact(SAMPLE % options, max_options=5)
    assert 'data-attributeid="LSI_A"' in out and 'data-hidden="1"' in out
    assert "531f3cbc" not in out and "…" in out            # the long id suffix is shortened
    assert "<script" not in out and "menu" not in out      # scripts and the site menu are gone
    assert "requestId" not in out and "Long tip" not in out
    assert 'value="x" selected' in out and "more options" in out  # the selected option always survives
    assert 'value="Save & Continue"' in out


def test_fix_legal_names_edits_only_those_lines(tmp_path):
    mod = _load("fix_legal_names")
    old = ('{\n  "rand_gen_rules": {\n    "legalNameInc": "generate_legalNameInc()",\n    "name": "get_name()"\n  },\n'
           '  "custom_functions": {\n    "generate_legalName": "old",\n    "generate_legalNameInc": "old",\n'
           '    "get_name": "keep"\n  },\n  "address_list": [\n   { "use": true, "street": "1 A St" }\n ]\n}')
    new, done, notes = mod.patch_text(old)
    data = json.loads(new)
    assert data["custom_functions"]["get_name"] == "keep" and "address_list" in new
    assert "faker.first_name()" in data["custom_functions"]["generate_legalNameLLP"]
    assert data["rand_gen_rules"]["legalNameLLP"] == "generate_legalNameLLP()"
    assert "generate_legalNameLLC" in notes[0] or notes  # functions missing from this tiny file are reported
    assert mod.patch_text(new)[0] == new                    # running it again changes nothing
