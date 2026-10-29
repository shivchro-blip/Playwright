# Custom Fields Reference (`CUSTOMFIELD_***`)

This page documents every `CUSTOMFIELD_***` handled by `customFieldHandler(...)`, including the exact `field_value` format it expects and the action object it returns.

---

## General behaviors

### Field name pipe stripping
If any `field_name` contains a pipe (`|`), only the portion before the pipe is used.

Example:

- `CUSTOMFIELD_SAVEFIELD|any-data` is treated as `CUSTOMFIELD_SAVEFIELD`.

### POM-backed fields (`pomFields`)
Most custom fields accept a POM field name (e.g., `SearchBox`, `SubmitButton`) and resolve the locator/config via:

- `actionHelper.getFieldConfig(pomFields, <pomFieldName>)`


### Dictionary tokens (`DICT.*`)
Some custom fields (and most executables) support dictionary lookups. Convention:
- `DICT.MyKey` resolves to `dictionaryContext.get("MyKey")`
- AKA: Loads the data from dictionary saved under "MyKey"

Note: some custom fields resolve `DICT.*` during action creation (e.g., `CUSTOMFIELD_CLICKINELEMENTFROMLIST`), while others defer it to execution time.

---

## `CUSTOMFIELD_TYPEDATE`

**Purpose:** Create a `filldate` action that types a computed date (`today +/- N days`) formatted as `MMDDYYYY`.

### `field_value` format
```
<operator>|<days>
```
- `<operator>`: `+` or `-` (any other value results in “today”)
- `<days>`: integer

### Returns
```json
{ "type": "filldate", "value": "MMDDYYYY", "field": "", "locator": "main" }
```

### Example
```
+|7
```

---

## `CUSTOMFIELD_SAVEFIELD`

**Purpose:** Extract a value from a POM field and save it into the dictionary (supports regex extraction).

### `field_value` format
```
<variableName>|<pomFieldName>|<regex>
```

**Important:** In `saveAction(...)`, when a regex match exists, the code saves **`match.group(1)`**.  
Your regex should include a capturing group.

### Returns
```json
{
  "type": "save",
  "field": "<pomFieldName>",
  "value": "<variableName>",
  "locator": "<from POM>",
  "role": "<from POM>",
  "contains": "<from POM>",
  "regex": "<regex>"
}
```

### Example (capture group)
Save an Application ID from label text like `Application ID: 12345`:
```
APP_ID|ApplicationIdLabel|Application ID:\s*(\w+)
```

---

## `CUSTOMFIELD_SAVELOCATOR` (currently stubbed)

**Purpose:** Intended to save from a raw locator, but the current function only logs and does **not** return an action.

### `field_value` format (as coded)
```
<variableName>|<screenLocator>
```

**Status:** No action returned → using this in a testcase currently does nothing.

---

## `CUSTOMFIELD_FILLFIELD`

**Purpose:** Fill a POM field with a value pulled from the dictionary.

### `field_value` format
```
<variableName>|<pomFieldName>
```

### Returns
```json
{
  "type": "fillFromDictionary",
  "field": "<pomFieldName>",
  "value": "<variableName>",
  "locator": "<from POM>"
}
```

### Example
```
APP_ID|ApplicationIdInput
```

---

## `CUSTOMFIELD_FILLLOCATOR` (currently stubbed)

**Purpose:** Intended to fill using a raw locator, but the current function only logs and does **not** return an action.

**Status:** No action returned → using this in a testcase currently does nothing.

---

## `CUSTOMFIELD_WAITFORCONTAINS`

**Purpose:** Wait until a target field contains *any* of the provided values.

### `field_value` format
```
<pomFieldName>|<contains1>|<contains2>|...
```

### Returns
```json
{
  "type": "waitforcontains",
  "field": "<pomFieldName>",
  "locator": "<from POM>",
  "role": "<from POM>",
  "contains": ["<contains1>", "<contains2>", "..."]
}
```

### Example
Wait for either `Approved` or `Completed`:
```
StatusBanner|Approved|Completed
```

### DICT support
At execution time, each `contains` value can be a `DICT.*` token (resolved by the executor).

---

## `CUSTOMFIELD_WAITWHILELOADING`

**Purpose:** Wait until a locator count becomes `0` (spinner/overlay gone).

### `field_value` format
```
<pomFieldName>
```

### Returns
```json
{
  "type": "waitwhileloading",
  "field": "<pomFieldName>",
  "locator": "<from POM>",
  "role": "<from POM>"
}
```

---

## `CUSTOMFIELD_CONFIRMTEXT`

**Purpose:** Assert that an element contains the expected text (supports `DICT.*`).

### `field_value` format
```
<pomFieldName>|<containsText>
```

### Returns
```json
{
  "type": "confirmtext",
  "field": "<pomFieldName>",
  "locator": "<from POM>",
  "role": "<from POM>",
  "contains": "<containsText>"
}
```

---

## `CUSTOMFIELD_WAIT`

**Purpose:** Sleep for N seconds.

### `field_value` format
```
<seconds>
```

### Returns
```json
{ "type": "wait", "value": "<seconds>", "field": "", "locator": "main" }
```

---

## `CUSTOMFIELD_KEYBOARD`

**Purpose:** Press a keyboard key or chord via Playwright (`page.keyboard.press(...)`).

### `field_value` format
```
<field>|<key>
```

### Returns
```json
{ "type": "keyboard", "field": "<field>", "value": "<key>", "locator": "" }
```

### Examples
```
main|Enter
main|Control+A
main|Backspace
```

---

## `CUSTOMFIELD_CLICKELEMENTFROMLIST`

**Purpose:** Click an element selected from a list using helper logic.

### `field_value` format
```
<pomFieldName>|<element>
```

### Returns
```json
{
  "type": "clickElementFromList",
  "field": "<pomFieldName>",
  "locator": "<from POM>",
  "element": "<element>"
}
```

---

## `CUSTOMFIELD_CLICKINELEMENTFROMLIST`

**Purpose:** Find an element in a list by matching `text` and `value`, then perform `actionToClick` on that element.

If `value` contains `DICT.`, it is resolved **during action creation**.

### `field_value` format
```
<pomFieldName>|<text>|<value>|<actionToClick>
```

### Returns
```json
{
  "type": "clickInElementFromList",
  "field": "<pomFieldName>",
  "locator": "<from POM>",
  "text": "<text>",
  "value": "<value>",
  "actionToClick": "<actionToClick>"
}
```

### Example
```
ResultsTableRows|Application ID|DICT.APP_ID|click
```

---

## `CUSTOMFIELD_SAVELISTFIELD`

**Purpose:** Extract a field value, split on spaces, and save each token to indexed dictionary keys.

### `field_value` format
```
<variableName>|<pomFieldName>|<regex>
```

### Returns
```json
{
  "type": "saveList",
  "field": "<pomFieldName>",
  "value": "<variableName>",
  "locator": "<from POM>",
  "role": "<from POM>",
  "contains": "<from POM>",
  "regex": "<regex>"
}
```

### Behavior note
The executor currently splits the extracted value on spaces and saves:
- `<variableName>[1]`, `<variableName>[2]`, ...

---

## `CUSTOMFIELD_PAUSE`

**Purpose:** Invoke `page.pause()` for debugging with Playwright Inspector.

### `field_value` format
Any string (stored as `value`), but not used by the executor.

### Returns
```json
{ "type": "pause", "value": "<field_value>", "field": "", "locator": "main" }
```

---

## `CUSTOMFIELD_CONFIRMICON`

**Purpose:** Assert element has a CSS class (or matches class expectation) to confirm an icon state.

Supports `DICT.*` for the `contains` value at execution time.

### `field_value` format
```
<pomIconName>|<contains>
```

### Returns
```json
{
  "type": "confirmicon",
  "field": "<pomIconName>",
  "locator": "<from POM>",
  "role": "<from POM>",
  "title": "<from POM>",
  "contains": "<contains>"
}
```

---

## `CUSTOMFIELD_SEARCHREFRESH`

**Purpose:** Repeatedly fill a search field until a confirm field contains the searched value, optionally clicking a Search button each attempt.

### `field_value` formats

**Without search button** (3 pipes / 4 parts):
```
<searchBoxFieldName>|<searchValue>|<confirmFieldName>|<retryCount>
```

**With search button** (4 pipes / 5 parts):
```
<searchBoxFieldName>|<searchValue>|<confirmFieldName>|<searchButtonFieldName>|<retryCount>
```

### Returns
```json
{
  "type": "searchRefresh",
  "value": "<searchValue>",
  "searchFieldLocator": "<from POM searchBox>",
  "confirmFieldLocator": "<from POM confirmField>",
  "searchButtonFieldLocator": "<from POM searchButton or empty>",

  "searchFieldIndex": 0,
  "confirmFieldIndex": 0,
  "searchButtonFieldIndex": 0,

  "hasSearchButton": true/false,
  "retryCount": "<retryCount>",

  "field": "<searchBoxFieldName>",
  "locator": "<from POM searchBox>"
}
```

### DICT support
- If `searchValue` contains `DICT.`, it is resolved at execution time.
- `confirmFieldLocator` supports embedded tokens like `DICT.SomeKey` and the executor replaces them before locating.

---

## `CUSTOMFIELD_SCANFILE`

**Purpose:** Start a Windows TWAIN scan workflow using `pywinauto`.

### `field_value` format
```
<pomFieldName>
```

### Returns
```json
{
  "type": "scanFile",
  "locator": "<from POM>",
  "field": "<pomFieldName>",
  "value": ""
}
```

### Runtime expectations
The executor connects to Windows dialogs by title:
- `Select Source`
- `TWAIN Sample DS`

---

## `CUSTOMFIELD_CLICKFIELD`

**Purpose:** Click a POM field with `focus=True` (executor focuses element before clicking).

### `field_value` format
```
<pomFieldName>
```

### Returns
```json
{
  "type": "click",
  "locator": "<from POM>",
  "field": "<pomFieldName>",
  "value": "",
  "focus": true
}
```

---

## `CUSTOMFIELD_DOWNLOAD`

**Purpose:** Click an element and save the browser download to `./reports/downloads/<filename>`.

### `field_value` format
```
<pomFieldName>
```

### Returns
```json
{
  "type": "download",
  "locator": "<from POM>",
  "field": "<pomFieldName>",
  "value": ""
}
```

---

## `CUSTOMFIELD_GATHER`

**Purpose:** Gather and print an ARIA snapshot for a locator.

### `field_value` format
```
<raw locator string>
```

### Returns
```json
{
  "type": "gather",
  "locator": "<raw locator>",
  "field": "",
  "value": ""
}
```

### Example
```
#mainForm
```

---

## Functions present but not dispatched by `customFieldHandler`
The following action builders exist in code but are not reachable unless you add a handler branch:
- `customField_WaitForContainsLoop_CreateAction(...)` (no corresponding `CUSTOMFIELD_*` mapping in the handler)
