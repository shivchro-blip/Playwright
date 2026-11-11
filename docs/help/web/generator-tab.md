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

**Debug** (next to **Run**) runs the open test case and, if a step fails, keeps the browser open: fix the test case or a page here, save, and resume in the same browser (see *Pause on failure* in the Control tab help).

**Run** (and **From step...**) run only the open test case, on its environment with your saved browser options, and show the run right here under the toolbar: its status, each result with where it failed, **Details** (screenshot, DOM, trace, log), the last lines of the console, **Cancel** while it runs and **Report** when it is ready. You stay on the test case to fix it and run it again. **Open in Control** shows the same run on the Control tab; the Control tab's own test selection is not used.

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

## Data sets

A **data set** keeps the values a test types in one file (`Data/TestData/<name>.json`) so a test case can run against many sets and the test itself holds no addresses or numbers. The **Data sets** tab edits them:

- **+** creates a file; the list shows how many sets each has and which test cases use it.
- The grid has **one row per set and one column per key**. The **defaults** row applies to every set; an empty cell falls back to it. **+ Set**, **+ Column**, duplicate and remove work on rows and columns; **Form / JSON** switches to the raw file. Values are text.
- **Export** (download icon) saves the file as CSV, which opens in Excel. **Import** (upload icon) replaces the sets and columns with those of a `.csv` or `.xlsx` file: the first row holds the keys, the first column the set names, and a row named `#defaults` the default values; an empty cell means *not set here*. Nothing is written until you click **Save**. Excel numbers are read as written (a zip code with a leading zero should be formatted as text in Excel, or it is already lost there).
- In a test case, choose the **Data set** (below the ID row of the editor). The value menu (the **{ }** button next to a value) now offers *From the data set*, which writes `DATA.<key>`. **Check** warns if a file is missing, a `DATA.` key is not defined in every set, or the steps use `DATA.` values with no data set.
- The test case then runs once per set, as `TC-1[austin]`, `TC-1[dallas]`; each shows as its own result. A missing value stops the run with a message naming the set.

Details, including what a reused test or common action sees, are in *Form Data and Page Mapping*.

## Recorder

Record a test by clicking through the site instead of writing locators by hand. It is **off by default**: turn on the **Recorder** switch next to the section tabs (saved as `recorderEnabled` in `.env`), then click **Record**.

1. Pick the environment and start URL (the system's `base_url`), optionally starting logged in from `Data/session.json`, and click **Start recording**.
2. A Chromium window opens on this computer. Click and type through the flow, then close the window.
3. Review what was recorded: the fields (locators), the step's form data and any warnings, then pick a page (a new one, or an existing page to add fields to; fields with the same locator are reused) and a test file, and **Save page and test case**.

The whole recording becomes one page step; a field used again gets a pipe suffix (`q|`).

### Smart capture (the default)

**How to record > Smart capture** records with the full picture of every element you touch, so the test still finds it on the next run. At the moment of each click or entry the recorder notes the element's id, `name`, `data-testid` (also `data-test`, `data-cy`, `data-qa`), your site's ID attribute, `aria-label`, label text, placeholder, role and text, and every locator that could find it with how many elements that locator matches right then. It keeps the most stable one that matches **exactly one** element:

| Order | Kept as | Example |
|---|---|---|
| 1 | the site's own id attribute (found on the page) | `[data-automation-id='app_form_TaxPaid'] button:text-is('No')` |
| 2 | a test id | `[data-testid="phone-number"]` |
| 3 | a fixed id | `[id="email"]` |
| 4 | `name` (with the value for radios and checkboxes) | `input[name="contact"][value="phone"]` |
| 5 | `aria-label` | `input[aria-label="Search"]` |
| 6 | the label, matched exactly | `internal:label="Name"s` (does not also match *First name*) |
| 7 | the placeholder | `input[placeholder="Email address"]` |
| 8 | a button's or link's text, exactly | `internal:role=button[name="Continue"s]` |
| 9 | its place inside an element with a fixed id | `#billing > button` (two *Save* buttons on the page) |

- **Generated ids are never used**: ids with long numbers or hashes, framework prefixes (`mat-`, `mui-`, `ember`, `j_idt`, `react-select-`...) or React's `:r1:` change on every page load, which is the usual reason a recorded test fails the next day.
- **Field names come from what a person sees**: the label, `aria-label`, placeholder or `name` (`firstName`, `emailAddress`, `stateSelect`, `saveButton`), not from the locator.
- Clicks that only focus a text box are dropped; checkboxes and radios are recorded as *check*; a select records the option you picked; Enter is recorded as a key press; a value typed again before leaving the field keeps only the last value.
- A field it could only locate by its position on the page, or with an index, is listed in a warning to check before saving.
- What each element looked like is saved as its **locator profile** (`Data/locatorProfiles/<SYSTEM>.json`), so self-healing can find it from the first run if the page changes later.
- Password fields are never read, and passwords, PINs, one-time codes and tokens are not saved: the step gets `ENV.<FIELD>` instead (for example `"password": "ENV.PASSWORD"`) and the review says which key to add to `.env` (or the secret store). A user name is kept as typed.

**Playwright codegen** (the other choice) keeps the locators codegen writes, as before; an `exact=True` locator now stays exact.

### Multi-screen flows (wizard mode)

For a long flow with a "Save & Continue" button on every screen, open **Multi-screen flows** in the Record dialog:

- **Split into one page per screen** - a click on one of the listed buttons (default: `Save & Continue`, `Save and Continue`, `Next`, `Continue`, `Submit`) ends the screen. Each screen becomes its own page and its own `screen` step, so the report names the screen that failed and each page stays small. Only the first page navigates (it gets the start URL); the others pick up where the previous screen left off. Button names are matched exactly (case-insensitive), so a `Next month` button does not end a screen. Name each page in the review before saving; names that already exist get the new fields merged in.
### The site's own element ids (found for you)

Many applications put their own identifier on elements, for example `data-automation-id="app_form_Zip"` or `data-qa="checkout-pay"`, often with the same prefix on every value. Locators built on it are the most stable ones a site offers. **You do not need to know or type it**: smart capture and **From page** look at the page and take a `data-*` attribute to be the site's id attribute when at least three elements carry it, nearly all of its values are different, and they are not generated (long numbers, hashes). One whose name says it is an identifier (`id`, `key`, `qa`, `test`, `cy`, `e2e`, `auto`, `hook`, `ref`, `field`...) is preferred. The prefix every value shares, up to a separator (`_ - . :`), is left out of the field names (`app_form_Zip` becomes `Zip`). The review says what was found. Several choices inside one marked element (Yes / No) are named after both (`TaxNotPaidNo`).

**Advanced: override what is detected** lets you name the attribute and the prefix yourself; that is also how to give them to **Playwright codegen**, which cannot detect them.

Check the fields and locators in the review: the recorder can only turn into fields what codegen writes. Not converted: assertions (`expect`), actions inside iframes, and later navigations (they are reported as warnings). Values typed into fields that look like passwords or codes are **not saved**; use `ENV.<KEY>` values instead.

The recorder only works when the web app listens on this machine (`AUTOPLAY_HOST=127.0.0.1`), because the browser opens where the app runs.

## Pages

1. Select a page on the left, or **+** to create one (optional folder). The list shows how many tests and common actions use each page (**Used by**).
2. **Page settings**: `path` (added to the system base URL) or `fullpath` (absolute URL) to navigate on entry; title check; error locators / texts.
3. **Fields**: name, type, locator, index and autoGen rule. **{ }** edits all keys of a field (role, name, nested, tableId, waitForNewTab, picker, ...), including **indexed**. Use the filter on large pages. A field that no test uses is shown in italics.
4. **From page** scans the live page and proposes fields (see below).
5. **Components**: the shared components the page uses (a header, an address block defined once for many pages), and **Shared component** to let other pages use this one. See [Shared page components](../framework/page-components.md).

Page names must be unique within a system; the list marks duplicates (the last one loaded wins at run time).

**Used by** (under the page title) lists the tests that use the page; click one to open it.

### Maintain: rename and tidy

The **Maintain** menu works on the whole page and the tests that use it:

- **Rename page...** renames the file and updates every `screen` (and `override`) that names it, in tests and common actions.
- **Rename a field...** renames it on the page and in every step that uses it, keeping pipe suffixes.
- **Move fields into a component...** moves the chosen fields into a new shared component that the page then uses; their names stay, so no test changes.
- **Tidy up this page...** finds fields that are exactly the same (merged into one, with `name`, `name|`, `name||` in the steps that used several), fields no test uses (removed), redundant `autoGen: "click"` on buttons, links, checkboxes and radios, and fields that differ only by `index` (a hint to use `indexed` and a step index).
- **Check all locators on the live page...** opens the page once in headless Chromium and resolves every field the way a run does: *found*, *not found*, *hidden*, *several matches* (set `index`), *index too high*, or *not checked* (fields whose option comes from the test, table cells, nested chains, `DICT.`/`ENV.` values). Problems are listed first; **Problems only** can be turned off. It checks what is in the editor, saved or not. After a site change it turns "the nightly run failed somewhere" into a short list. A page you reach by clicking through earlier screens shows its later fields as not found or hidden: that describes the page as loaded, not always a broken locator.
- **Suggest fixes** (in that dialog, when fields are *not found*, *hidden* or *index too high*) scans the live page and ranks the elements that could replace each one, by what the field still says: its name, its old locator (a renamed id such as `#zip-code` → `#applicantZipCode` scores high), its label, placeholder or text, and its type. Up to three suggestions per field are listed with a score and the reason; elements a working field already uses are left out. **Use** puts the locator into the field in the editor (type, autoGen and the other keys are kept); nothing is saved until you click Save. A field with no good match says so. Treat a suggestion as a lead to check, not a fix: a redesign can move a label to a different field.

Rename and Tidy each ask you to **Preview** first: it lists the files that would change and writes nothing. **Apply** then changes all of them, or none: if a file changed on disk since the preview, or a write fails, every file is left as it was. Save or revert your edits before using it. Renaming or deleting a used field by typing in the field list does not update tests; Save warns you.

### From page: add fields by scanning

**From page** opens the page in headless Chromium on this machine and lists the buttons, inputs, links, checkboxes and menus it can see, with a proposed name, type and locator for each. Locators are checked with the same rules a run uses; one that matches several elements gets the `index` of the element it was proposed for. If the site marks its elements with an id attribute of its own, the scan finds it (see *The site's own element ids*): elements inside such a container are located through it, names leave out the prefix the values share, and several choices in one container (Yes / No) are named after both. **Advanced** overrides what is found. Tick what you want and **Add selected fields**; the page stays unsaved until you Save. Fields already on the page are marked and not added twice. Only what is visible is listed, and not elements inside iframes.

## Safety

- If a file changed on disk after you opened it (another tab, a teammate, `git pull`), saving is refused - click **Revert** to reload.
- Writes are atomic and keep the file's indentation.
- Leaving an editor with unsaved changes asks first.

## Edit history

Every save of a page, a test file or a data set keeps the version it replaces, in `.history/` in the workspace (git-ignored; the last 40 versions of each file). Click **History** in the editor (the clock icon for a data set) to pick a version, see what differs from the file now (red lines were removed, green lines added) and **Restore this version**. A restore is itself a save, so the version it replaces is kept and it can be undone the same way. It also brings back test cases you deleted (a test file's history covers all its cases). The first save of a file can show the whole file as changed when the editor re-indents it.

## Common actions

A **common action** is a list of steps that many test cases call by name (for example *login as applicant*): `{"commonAction": "login as applicant"}`. They live in `Tests/com/*.json` and the **Common actions** tab edits them:

- The list shows each action's file, number of steps and how many test steps call it. **+** creates one in a file of your choice.
- The editor shows the action's steps as JSON (`{"steps": [{"screen": "<page>", "formData": {...}}]}`), who calls it, and **Values a test can change**: every field of its steps, with the `override` text a test step uses to use another value for one call (`"override": "<page>|<field>|<value>"`; `"loop": N` repeats the call).
- **Check** reports errors that block saving (a step without a `screen`, `formData` that is not an object) and warnings (a page or field that does not exist in any system).
- **Rename** changes the name and every test step that calls it, after showing how many calls will change. The old versions of those files stay in **History**, which is also how you undo a change to an action.
- **Delete** is refused while a test calls the action.

