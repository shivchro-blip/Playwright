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
| `"ENV.NAME"` | An environment variable (also works inside locators). |
| `{"type": "confirmtext"}` | Explicit action object. Any executable type works, e.g. `{"type": "keyboard", "value": "Enter"}`, `{"type": "wait", "value": "2"}`. Locator settings still come from the page object (put `contains` there for `confirmtext`). |

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

See *Custom Fields* for every `CUSTOMFIELD_*` command and *Executables* for every action type.
