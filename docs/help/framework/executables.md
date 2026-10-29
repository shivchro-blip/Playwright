# Executables Reference (Action `"type"`)

This page documents the action objects executed by your runner functions (e.g., `fillAction`, `clickAction`, etc.).  
Where your framework uses additional dispatching code, the names below assume the `"type"` values shown in your action dictionaries and helper calls.

---

## Value expansion rules (`ENV.*`, `DICT.*`, and pipe-join)

### Single token expansion
In `fillAction` and `selectAction`, if `value` is a string:

- `ENV.MY_VAR` resolves to `os.getenv("MY_VAR", "")`
- `DICT.MyKey` resolves to `dictionaryContext.get("MyKey")`

If the resolved value is blank, the code attaches Allure failure details.

### Pipe-join expansion
If `value` contains `|`, it is treated as multiple parts. Each part may be:
- `ENV.*` token
- `DICT.*` token
- literal text

The executor expands each part and joins them with spaces.

Example:
- `value = "ENV.FIRST|ENV.LAST"` becomes `"Chris Devito"` (if those env vars exist).

---

## `fill` (executed by `fillAction`)

**Purpose:** Fill an input with a string.

### Common keys
- `value` (string; supports ENV/DICT and pipe-join)

### Example
```json
{ "type": "fill", "value": "ENV.USERNAME" }
```

---

## `fillFromDictionary` (executed by `fillFromDictionaryAction`)

**Purpose:** Fill a field using a dictionary key name.

### Common keys
- `field` (POM/logical field name)
- `value` (dictionary key name)

### Example
```json
{ "type": "fillFromDictionary", "field": "ApplicationIdInput", "value": "APP_ID" }
```

---

## `click` (executed by `clickAction`)

**Purpose:** Click an element (optionally focus before clicking; optionally capture a response and store a JSON value into the dictionary).

### Common keys
- `focus` (boolean; when true, does `element.focus()` and waits)
- `request` (string formatted: `<responseURL>|<key>`)

### Example: focus and click
```json
{ "type": "click", "focus": true }
```

### Example: click + save response JSON field
```json
{
  "type": "click",
  "request": "https://api.example.com/submit|agenda_no"
}
```
Effect: stores `response.json()["agenda_no"]` into `dictionaryContext` under key `agenda_no`.

---

## `check` (executed by `checkAction`)

**Purpose:** Check a checkbox/radio control.

### Example
```json
{ "type": "check" }
```

---

## `hover` (executed by `hoverAction`)

**Purpose:** Hover an element.

### Example
```json
{ "type": "hover" }
```

---

## `dblclick` (executed by `dblclickAction`)

**Purpose:** Double-click an element.

### Example
```json
{ "type": "dblclick" }
```

---

## `select` (executed by `selectAction`)

**Purpose:** Select an option in a `<select>` element.

### `value` formats
- `"someOptionValue"`
- `"index=3"`
- `"ENV.SOME_VAR"` / `"DICT.SomeKey"`
- pipe-joined (expanded then joined; uncommon but supported)

### Example: by value
```json
{ "type": "select", "value": "NY" }
```

### Example: by index
```json
{ "type": "select", "value": "index=2" }
```

---

## `document` (executed by `documentAction`)

**Purpose:** Upload a file using Playwright `set_input_files`.

### Common keys
- `value` (file path; backslashes are normalized to `/`)

### Example
```json
{ "type": "document", "value": "C:\\files\\mydoc.pdf" }
```

---

## `sladocument` (executed by `sladocumentAction`)

**Purpose:** SLA-specific upload workflow using file chooser per table row.

### Common keys
- `value` (file path)

### Notes
Looks for:
- `tr button[alt='Click to upload file']` (all rows)

---

## `correctionDocument` (executed by `correctionDocumentAction`)

**Purpose:** Correction workflow: iterates options and uploads a file each time.

### Notes
Hard-coded locators used by the executor:
- `select[id='docErrorSelectId']`
- `select[id='extraDocumentSelectId']`
- upload locator comes from `action["locator"]`

---

## `save` (executed by `saveAction`)

**Purpose:** Extract a value from an element and store it in the dictionary.

### Common keys
- `value` (dictionary key to store into)
- `regex` (optional; if present and matches, stores `match.group(1)`)

### Example (with regex capture group)
```json
{
  "type": "save",
  "value": "APP_ID",
  "regex": "Application ID:\\s*(\\w+)"
}
```

### Built-in cleanup (no-regex branch)
When no regex is used, the executor applies some system-specific cleanup:
- removes `"Application ID:"`
- strips `"EXQ-"`
- handles `"PIN:"` by splitting on `:`

---

## `saveList` (executed by `saveListAction`)

**Purpose:** Extract a value, split on spaces, and save tokens to indexed dictionary keys.

### Common keys
- `value` (base dictionary key, e.g., `TOKENS`)

### Output keys
- `TOKENS[1]`, `TOKENS[2]`, ...

---

## `waitforcontains` (executed by `waitForContainsAction`)

**Purpose:** Repeatedly reload and check until element contains one of the provided values.

### Common keys
- `contains` (list of strings; entries may be `DICT.*` tokens)

### Behavior
- Up to 10 attempts
- reloads page when not found
- does not hard-fail at the end (logs + attaches Allure details)

---

## `waitwhileloading` (executed by `waitWhileLoadingAction`)

**Purpose:** Poll element count until it becomes `0`.

### Behavior
- 30 second timeout
- checks `await element.count()` every 0.25s
- raises `TimeoutError` if still present after timeout

---

## `confirmtext` (executed by `confirmTextAction`)

**Purpose:** Assert element contains a text value (supports `DICT.*` token).

### Behavior
- 10 attempts
- warns on failures; fails hard at end

---

## `confirmicon` (executed by `confirmIconAction`)

**Purpose:** Assert element has the expected class (supports `DICT.*` token).

### Behavior
- 10 attempts
- uses `expect(element).to_have_class(contains)`

---

## `filldate` (executed by `fillDate`)

**Purpose:** Type the provided date string into the active field.

### Common keys
- `value` (already formatted, e.g., `MMDDYYYY`)

---

## `wait` (executed by `waitAction`)

**Purpose:** Sleep `N` seconds.

### Common keys
- `value` (integer parse)

**Note:** On failure, the executor tries to attach Allure details referencing `page`, but `page` is not in scope in `waitAction`. If you see errors here, that is likely why.

---

## `keyboard` (executed by `keyboardAction`)

**Purpose:** Press a key/chord via `page.keyboard.press`.

### Common keys
- `value` (e.g., `Enter`, `Control+A`)

---

## `clickElementFromList` (executed by `clickElementFromList`)

**Purpose:** Retrieve an element from a list via `ActionHelper.getElementFromListByElement` and click it.

### Common keys
- `locator`
- `element`

---

## `clickInElementFromList` (executed by `clickInElementFromList`)

**Purpose:** Retrieve an element via `ActionHelper.getElementFromListByText` then perform an action via `ActionHelper.performElementAction`.

### Common keys
- `locator`
- `text`
- `value`
- `actionToClick`

---

## `pause` (executed by `pauseAction`)

**Purpose:** Invoke `page.pause()` for interactive debugging.

---

## `searchRefresh` (executed by `searchRefresh`)

**Purpose:** Retry a search until a confirm element contains the searched value.

### Common keys
- `value` (searchValue; supports `DICT.*`)
- `searchFieldLocator`, `confirmFieldLocator`
- `searchFieldIndex`, `confirmFieldIndex`
- `hasSearchButton`
- `searchButtonFieldLocator`, `searchButtonFieldIndex` (if used)
- `retryCount`

### Special behavior: DICT tokens inside `confirmFieldLocator`
The executor replaces substrings like `DICT.SomeKey` inside the locator string before locating.

---

## `scanFile` (executed by `scanFile`)

**Purpose:** Windows-only scanning workflow via `pywinauto`.

### Notes
Connects to windows by title:
- `Select Source`
- `TWAIN Sample DS`

---

## `gather` (executed by `gathersnap`)

**Purpose:** Print an ARIA snapshot of a locator.

### Common keys
- `locator` (string)

---

## `download` (executed by `downloadFile`)

**Purpose:** Use `page.expect_download()` and save to `./reports/downloads/<suggested_filename>`.

### Common keys
- `locator`

---

## Additional executor utilities (present in code)

These functions exist and are usable **if** your dispatcher maps action types to them:

- `kendodateAction(page, element, locator)` — uses `page.evaluate(...)` to set a date input by `id`
- `setCheckedAction(page, element, locator)` — sets `.checked = true` via `page.evaluate(...)`
- `numberAction(page, element, value)` — calls `element.press(value)`

If you want them formally supported, document the intended `"type"` strings in your central dispatcher and ensure actions contain the necessary keys (e.g., `locator`, `value`).
