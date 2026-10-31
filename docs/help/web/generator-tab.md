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

Each row is a field, an **action** and (for some actions) a value. The action menu offers only what fits the field:

| Action | Saved as | Meaning |
|---|---|---|
| Fill / Select option | `"value"` | Types or selects this value. |
| Choose option | `"No"` | For a field whose locator has `{value}` (see below): the test says which option. |
| Click, Double-click, Hover, Check | `"click"`, `"dblclick"`, `"hover"`, `"check"` | Does that to the field. |
| Generate a value | `null` | Uses the field's `autoGen` rule on the page (buttons and checkboxes just click). |
| Verify text | `{"type": "confirmtext"}` | Checks the field's text against `contains` on the page. |
| Press key | `{"type": "keyboard", "value": "Enter"}` | Sends a key. |
| Wait (seconds) | `{"type": "wait", "value": "2"}` | Pauses. |
| Custom (JSON) | `{...}` | Any other explicit action. |

Picking a field starts it with the natural action (a button as **Click**, a text box as **Fill**, a `{value}` field as **Choose option**). The **{ }** button next to a value inserts `ENV.NAME` (a `.env` variable, name only) or `DICT.name` (a value saved earlier in the test). The small box at the end repeats the action N times (`name|ELoop(N)`); to use the same field twice in a step, repeat it as `next`, `next|`, `next||`. **+ All page fields** adds every field of the page: clicks as **Click**, text fields as **Generate**.

The files keep the same JSON as before; the menu is only a clearer way to write it.

**Open page** (the arrow icon on a page step) jumps to that page in the Pages tab.

**Occurrence (index)** appears on page steps whose page has fields marked `indexed`. It lets one set of fields serve a repeated section: the second row, the second principal. See *Form Data and Page Mapping* for how to set it up.

### Run from a step

For a long flow, debugging step 90 should not mean repeating steps 1 to 89. **From step...** (next to **Run**) runs part of the test case, starting the browser in a saved state:

- **From step / To step** pick the range. Steps are the top-level steps of the test case (a common action is one step).
- **Saved state after step N-1** starts the browser as it was after the step before: cookies, local and session storage and the page address. It signs in only if that session has expired, opens the saved address, and runs from step N. The option appears once an earlier run saved that state.
- **Sign in, then open this URL** (prefilled from the step's page) and **Sign in only** start fresh when no state is saved.
- **Save the state after each step I run** makes the run write those states, so the next run can resume from any step it passed. Run the whole test once with this ticked (From step 1) to create them.

The state is what the portal keeps in the browser or on its own server (a session, a saved draft). It cannot restore text typed into a form that the portal has not saved yet, so a flow whose progress lives only in the page cannot be resumed this way: start the step from a URL instead, or split the flow so each step ends where the portal saves.

Saved states contain session cookies. They stay in the run's folder, are never served by the web app, and are deleted with the run (History > Delete). The newest run's state is used for each step. Under the hood these are the run options `runFromStep`, `runToStep`, `resumeCheckpoint`, `startUrl` and `saveCheckpoints` (one test case only), which the run API accepts as well.

### Check (validation)

Warnings do not block saving:

- page not found in `Pages/<SYSTEM>`
- field not on the selected page
- a `{value}` field without the option to choose, or a step `index` on a page with no `indexed` fields
- common action or reusable test not found
- no tags (the Control tab cannot run it) or no steps

Errors (missing ID, malformed steps, the same field twice) block saving.

## Recorder

Record a test by clicking through the site instead of writing locators by hand. It is **off by default**: turn on the **Recorder** switch next to the section tabs (saved as `recorderEnabled` in `.env`), then click **Record**.

1. Pick the environment and start URL (the system's `base_url`), optionally starting logged in from `Data/session.json`, and click **Start recording**.
2. A Chromium window opens on this computer (Playwright codegen). Click and type through the flow, then close the window.
3. Review what was recorded: the fields (locators), the step's form data and any warnings, then pick a page (a new one, or an existing page to add fields to; fields with the same locator are reused) and a test file, and **Save page and test case**.

The whole recording becomes one page step; a field used again gets a pipe suffix (`q|`).

### Multi-screen flows (wizard mode)

For a long flow with a "Save & Continue" button on every screen, open **Multi-screen flows and site ids** in the Record dialog:

- **Split into one page per screen** - a click on one of the listed buttons (default: `Save & Continue`, `Save and Continue`, `Next`, `Continue`, `Submit`) ends the screen. Each screen becomes its own page and its own `screen` step, so the report names the screen that failed and each page stays small. Only the first page navigates (it gets the start URL); the others pick up where the previous screen left off. Button names are matched exactly (case-insensitive), so a `Next month` button does not end a screen. Name each page in the review before saving; names that already exist get the new fields merged in.
- **ID attribute** - if your site marks elements with an attribute such as `data-attribute-id`, enter it. The recorder passes it to Playwright codegen, writes locators like `[data-attribute-id='LSI_APP_TaxNotPaid'] button:text-is('No')`, and names the field `TaxNotPaid` (set **Remove this prefix** to `LSI_APP_`). It works without wizard mode too.

Check the fields and locators in the review: the recorder can only turn into fields what codegen writes. Not converted: assertions (`expect`), actions inside iframes, and later navigations (they are reported as warnings). Values typed into fields that look like passwords or codes are **not saved**; use `ENV.<KEY>` values instead.

The recorder only works when the web app listens on this machine (`AUTOMATENY_HOST=127.0.0.1`), because the browser opens where the app runs.

## Pages

1. Select a page on the left, or **+** to create one (optional folder). The list shows how many tests and common actions use each page (**Used by**).
2. **Page settings**: `path` (added to the system base URL) or `fullpath` (absolute URL) to navigate on entry; title check; error locators / texts.
3. **Fields**: name, type, locator, index and autoGen rule. **{ }** edits all keys of a field (role, name, nested, tableId, waitForNewTab, picker, ...), including **indexed**. Use the filter on large pages. A field that no test uses is shown in italics.
4. **From page** scans the live page and proposes fields (see below).

Page names must be unique within a system; the list marks duplicates (the last one loaded wins at run time).

**Used by** (under the page title) lists the tests that use the page; click one to open it.

### Maintain: rename and tidy

The **Maintain** menu changes a page and every test that uses it, together:

- **Rename page...** renames the file and updates every `screen` (and `override`) that names it, in tests and common actions.
- **Rename a field...** renames it on the page and in every step that uses it, keeping pipe suffixes.
- **Tidy up this page...** finds fields that are exactly the same (merged into one, with `name`, `name|`, `name||` in the steps that used several), fields no test uses (removed), redundant `autoGen: "click"` on buttons, links, checkboxes and radios, and fields that differ only by `index` (a hint to use `indexed` and a step index).

Each one asks you to **Preview** first: it lists the files that would change and writes nothing. **Apply** then changes all of them, or none: if a file changed on disk since the preview, or a write fails, every file is left as it was. Save or revert your edits before using it. Renaming or deleting a used field by typing in the field list does not update tests; Save warns you.

### From page: add fields by scanning

**From page** opens the page in headless Chromium on this machine and lists the buttons, inputs, links, checkboxes and menus it can see, with a proposed name, type and locator for each. Locators are checked with the same rules a run uses; one that matches several elements gets the `index` of the element it was proposed for. If your site marks elements with an attribute such as `data-attribute-id`, enter it (and the prefix to drop from names): elements inside such a container are located through it, and several choices in one container (Yes / No) are named after both. Tick what you want and **Add selected fields**; the page stays unsaved until you Save. Fields already on the page are marked and not added twice. Only what is visible is listed, and not elements inside iframes.

## Safety

- If a file changed on disk after you opened it (another tab, a teammate, `git pull`), saving is refused - click **Revert** to reload.
- Writes are atomic and keep the file's indentation.
- Leaving an editor with unsaved changes asks first.
