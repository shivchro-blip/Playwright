# Form Data and Page Mapping

A page step pairs a page object with the data to enter:

```json
{ "screen": "contactPage", "formData": { "email": null, "phone": "5185550100", "next": "click" } }
```

Each key is a **field name from the page object** (or a `CUSTOMFIELD_*` command). The page object supplies the locator and type:

```json
{ "fields": {
    "email": { "locator": "#email", "type": "text", "autoGen": "random_email" },
    "phone": { "locator": "#phone", "type": "text" },
    "next":  { "locator": "button:has-text('Next')", "type": "button" }
} }
```

## Values

| Value | Result |
|---|---|
| `"some text"` | Default action for the field type with this value (fill, select, upload ...). |
| `null` | Generate a value with the field's `autoGen` rule from `rand_gen_rules.json`. |
| `"click"`, `"check"`, `"dblclick"`, `"hover"`, `"setchecked"` | Run that action instead of the default. |
| `"DICT.key"` | A value saved earlier in the test (dictionary). |
| `"DATA.key"` | The value of `key` in the test case's data set (see *Data sets* below). |
| `"ENV.NAME"` | An environment variable (also works inside locators). |
| `{"type": "confirmtext"}` | Explicit action object. Any executable type works, e.g. `{"type": "keyboard", "value": "Enter"}`, `{"type": "wait", "value": "2"}`. Locator settings still come from the page object (put `contains` there for `confirmtext`). |

## Data sets: one test, many sets of values

Keep what a test types (addresses, names, numbers) in `Data/TestData/<name>.json` and name the file in the test case:

```json
{ "defaults": { "Country": "United States" },
  "sets": { "albany": { "City": "Albany", "Zip": "12206" },
            "nyc":    { "City": "New York", "Zip": "10001" } } }
```
```json
{ "testCaseID": "TC-APPLY", "dataSet": "applicants", "steps": [
  { "screen": "Address", "formData": { "City": "DATA.City", "Zip": "DATA.Zip", "Country": "DATA.Country" } } ] }
```

The test case runs once per set, as `TC-APPLY[albany]` and `TC-APPLY[nyc]`. A value that is exactly `DATA.<key>` (also as a part of a `|`-joined value, inside an explicit action such as `{"type": "fill", "value": "DATA.City"}`, or as the value of an `override`) is replaced by the set's value, with the set's own values over the file's `defaults`. Text that merely contains `DATA.x` is left alone. A key a set does not have stops the run with a message naming the test, the set and what it does have. Keys are letters, digits, `_` and `-`; set and file names the same; values are text or numbers.

A reused test case (`testCase` step) or common action does not see the set, except through an `override` on the step that calls it. The run option `runDataSet` (or the Run from a step dialog) runs only one set.

## Choosing an option: `{value}` in a locator

Put `{value}` in a field's locator and the test supplies the option, so the page object holds no test data:

```json
"TaxNotPaid": { "locator": "[data-attribute-id='TaxNotPaid'] button:text-is('{value}')", "type": "button" }
```
```json
{ "screen": "Background", "formData": { "TaxNotPaid": "No" } }
```

The value is put into the locator (quotes are escaped) and the field is clicked. `ENV.NAME` and `DICT.key` work as the value. A `{value}` field with no value fails with a message naming it; when a page runs without form data at all, such fields are skipped.

## Date pickers

A field of type `datepicker` picks a date in a Material UI calendar popup: it opens the calendar from the button next to the input (or from `"picker": "<locator>"` on the field), pages to the month and clicks the day.

```json
"BeginDate": { "locator": "[data-attribute-id='BeginDate'] input", "type": "datepicker" }
```
```json
{ "BeginDate": "+30d" }
```

Values: `MM/DD/YYYY`, `YYYY-MM-DD`, `today`, or `+30d` / `-7d` from today; `null` uses the field's `autoGen` rule (for example `getDateBetween(29, 30, '%m/%d/%Y')`). The calendar header is read from the `CalendarHeader-label` element and days are the `Day-root` buttons (MUI X markup); other date widgets need their own action.

## Repeated sections: step `index`

Mark the fields of a repeated section `"indexed": true` (with their usual `index`, normally 0) and run the same fields again with a step `index`:

```json
{ "screen": "Principal", "formData": { "Name": "Ann", "Save": "click" } },
{ "screen": "Principal", "index": 1, "formData": { "Name": "Bob", "Save": "click" } }
```

In the second step every field flagged `indexed` uses index 1 instead of its own; fields without the flag keep theirs. This replaces copying each field as `Name1`, `Save1`, ....

## Repeating a field

Keys must be unique in JSON, so add pipe suffixes to use a field more than once - everything after the first `|` is ignored when looking up the field:

```json
{ "next": "click", "next|": "click", "next||": "click" }
```

## Loops

| Key | Meaning |
|---|---|
| `"addRow|ELoop(3)": "click"` | Run the action 3 times on the same element. |
| `"deleteIcon|LLoop(3)": "click"` | Run it on matches 0, 1 and 2 of the locator (list loop). |
| `"field|ELoop(DICT.count)"` | Loop count from the dictionary. |

## Page object keys

| Key | Purpose |
|---|---|
| `path` | Navigate to `base_url + path` before the step. |
| `fullpath` | Navigate to this absolute URL before the step. |
| `titleLocator` + `titleContains` | Fail the step if the page title element does not contain the text. |
| `errorLocators` + `errorContains` | Lists; after the actions, matching visible errors are reported. |
| `fields.<name>.index` | Use the Nth match of the locator. |
| `fields.<name>.role` / `name` | Locate by ARIA role and name instead of a CSS locator. |
| `fields.<name>.waitForNewTab` | The action opens a new tab; continue there. |
| `fields.<name>.indexed` | `true`: a step `index` replaces this field's `index` (repeated sections). |
| `fields.<name>.picker` | Locator of the button that opens the calendar, for `datepicker` fields. |

See *Custom Fields* for every `CUSTOMFIELD_*` command and *Executables* for every action type.
