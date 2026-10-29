# Framework Overview

Tests are data, not code. One parametrized pytest test (`tests/dynamic_test_suite.py::test_dynamic_execution`) runs every selected test case JSON, once per browser.

## How a test runs

1. **Selection** - test cases under `Tests/<SYSTEM>/MasterRegressionSuite/**` whose tags match `runTags`.
2. **Login** - for the test's system and environment, `systemData.json` gives `base_url` and `logged_in_check`. The browser opens `base_url`; if the `logged_in_check` selector is already visible, login is skipped. Otherwise the `login_pre` steps run (or HTTP credentials for `loginType: urlEmbed`).
3. **Steps** run in order:
   - `{"screen": "<page>", "formData": {...}}` - load `Pages/<SYSTEM>/**/<page>.json`, navigate if it has `path`/`fullpath`, check its title, then turn each formData entry into an action.
   - `{"commonAction": "<name>"}` - run shared steps from `Tests/com/**` (`commonActions` object).
   - `{"testCase": "<id>"}` - run another test case in a fresh browser context (it may use its own system/env).
   - `{"API_REQUEST": "<endpoint>"}` - call an endpoint from `Data/mappings/apiEndpoints.json`.
4. **Reporting** - every step is an Allure step; failures attach screenshots. Results go to the run's `allure-results/`.

## Building blocks

| Piece | File | Purpose |
|---|---|---|
| Page object | `Pages/<SYSTEM>/**.json` | Field name -> locator, type, optional `autoGen` rule |
| Test case | `Tests/<SYSTEM>/MasterRegressionSuite/**.json` | Ordered steps and the data to enter |
| Common action | `Tests/com/**.json` | Reusable step sequences |
| System data | `Data/systemData.json` | Base URLs, login detection, login steps, credential env var names |
| Random data | `Data/rand_gen_rules.json` | Named generators used by `autoGen` and `null` values |
| Dictionary | (runtime) | Values saved during a test (`CUSTOMFIELD_SAVEFIELD`) and read back with `DICT.<key>` |

## Field type -> default action

| Type | Action |
|---|---|
| `button`, `link`, `checkbox`, `radio`, `table` | click |
| `text`, `input`, `textarea` | fill |
| `select` | select option |
| `number` | type number |
| `document`, `sladocument`, `correctionDocument`, `fileChooserDocument` | upload file |

A formData value can override the action - see *Form Data and Page Mapping*.

## Running without the web app

```
set systemConfig=GOOGLE
set envConfig=dev
set runTags=smoke
set browsers=chrome
python -m pytest tests/dynamic_test_suite.py -v -s --alluredir=workspace/reports/allure-results
```

Values not set in the shell come from `.env`.
