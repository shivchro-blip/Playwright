# Explicit steps and common action parameters

## Explicit steps ("do")

A classic screen step says what to do through rules you have to know: the order of the keys in `formData` is the order of the actions, a field used twice is written `name|` (then `name||`), and what a value does depends on its shape (`"click"`, `"check"`, `{"type": "keyboard", ...}`). An **explicit** step lists the actions instead:

```json
{"screen": "Login", "do": [
  {"field": "username", "value": "ann"},
  {"field": "password", "value": "ENV.PASSWORD"},
  {"field": "signIn",   "click": true},
  {"field": "username", "value": "again"},
  {"field": "search",   "press": "Enter"},
  {"field": "spinner",  "wait": 2},
  {"field": "agree",    "check": true},
  {"field": "dob",      "use": {"type": "muidate", "value": "1990-01-01"}}
]}
```

| Say | Means | Classic equivalent |
|---|---|---|
| `"value": x` | type it, or choose it (by the field's type, as before; `DICT.`, `ENV.`, `DATA.` work) | `"field": x` |
| `"click": true` | click | `"field": "click"` |
| `"check": true` | tick | `"field": "check"` |
| `"press": "Enter"` | press a key | `{"type": "keyboard", "value": "Enter"}` |
| `"wait": 2` | wait (seconds) | `{"type": "wait", "value": "2"}` |
| `"use": {...}` | any classic value, passed through as is | the same value |

The same field simply appears again; no `|`. Everything else in the step (`index`, `override`, `loop`...) works as before.

**Both formats run the same and can be mixed**, even in one test case: the runner turns an explicit step into the classic one just before running it, and every classic step converts to explicit and back to exactly what it was. Nothing has to be converted.

In the Generator, a test case's **Steps: classic / explicit** button rewrites its screen steps in the other format when you Save; the rows you edit are the same either way. Usage, Impact, Journeys, field renames and Tidy see the fields of explicit steps like classic ones. A malformed explicit step (no field, or two verbs in one action) is reported by the validator with its number.

## Common action parameters

A common action can take parameters. In the action, write `{{name}}` where a value should go, and optionally give defaults:

```json
{"commonActions": {"log in": {
  "params": {"user": "demo"},
  "steps": [{"screen": "Login", "do": [
    {"field": "username", "value": "{{user}}"},
    {"field": "password", "value": "ENV.PASSWORD"},
    {"field": "signIn", "click": true}]}]}}}
```

A test case passes its values with `with`:

```json
{"commonAction": "log in", "with": {"user": "ann"}}
```

- A value that is exactly `"{{name}}"` takes the parameter's value with its type (true, a number); inside other text it is written in (`"Hello {{user}}"`).
- A parameter neither passed nor defaulted stays as `{{name}}`, so the run shows the mistake instead of typing nothing.
- Each call gets its own copy of the action's steps, so one call's values (or `override`) never leak into the next call.
