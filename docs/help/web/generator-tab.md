# Generator Tab

Edit the workspace JSON directly from the browser. Pick a **System** at the top; it applies to both editors.

Every editor has a **Form / JSON** toggle. The form covers the common keys; the JSON view shows and edits everything. Keys the form does not know are always kept.

## Test cases

1. Select a test on the left (search filters by ID, tags and file), or click **+** to create one in an existing or new file.
2. Edit ID, system, env, module, tags and description.
3. Add steps:
   - **+ Page** - runs a page object. Add form fields underneath.
   - **+ Common action** - runs shared steps from `Tests/com`. Optional **Override** `screen|field|value`.
   - **+ Reusable test** - runs another test case in its own browser context.
4. Reorder with the arrows, duplicate or remove steps.
5. **Check** lists problems, **Save** writes the file, **Run** runs just this test.

### Form fields on a page step

| Value type | Saved as | Meaning |
|---|---|---|
| Text | `"value"` | Fill / select this value, or a shorthand action such as `click`. |
| Auto | `null` | Generate a value from the field's `autoGen` rule. |
| Number | `5` | Numeric value. |
| JSON | `{...}` | Explicit action, e.g. `{"type": "confirmtext"}` or `{"type": "keyboard", "value": "Enter"}`. |

The field picker lists the page's fields plus every `CUSTOMFIELD_*` command. To use the same field twice in one step, add a pipe suffix: `next`, `next|`, `next||`. **+ All page fields** adds every field of the page as Auto.

### Check (validation)

Warnings do not block saving:

- page not found in `Pages/<SYSTEM>`
- field not on the selected page
- common action or reusable test not found
- no tags (the Control tab cannot run it) or no steps

Errors (missing ID, malformed steps, the same field twice) block saving.

## Pages

1. Select a page on the left, or **+** to create one (optional folder).
2. **Page settings**: `path` (added to the system base URL) or `fullpath` (absolute URL) to navigate on entry; title check; error locators / texts.
3. **Fields**: name, type, locator, index and autoGen rule. **{ }** edits all keys of a field (role, name, nested, tableId, waitForNewTab, ...). Use the filter on large pages.

Page names must be unique within a system; the list marks duplicates (the last one loaded wins at run time).

## Safety

- If a file changed on disk after you opened it (another tab, a teammate, `git pull`), saving is refused - click **Revert** to reload.
- Writes are atomic and keep the file's indentation.
- Leaving an editor with unsaved changes asks first.
