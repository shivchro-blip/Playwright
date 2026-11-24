# Helper scripts (the `tools` folder)

Small scripts for the people who write pages and tests. They use only Python, so there is nothing to install.
Run every command in PowerShell from the folder that contains `tools` and `workspace` (for example
`C:\NYBE\playwrightweb`).

## `compact_html.py`: shrink a page's HTML before you share it

Pasting a whole page is slow and mostly noise. This keeps only what a page object (POM) needs: the controls with their
`data-attributeid` / `data-opaid`, labels, buttons, tables, and which parts are hidden. It drops scripts, styles, the
site menu, help-tip boxes and the framework's hidden inputs, shortens the long hash at the end of ids, and trims long
dropdowns (the selected option is always kept).

Copy the page HTML, then run:

```powershell
Get-Clipboard -Raw | python tools\compact_html.py - | Set-Clipboard
```

The short version replaces what was on the clipboard: paste it where you need it. Other ways to run it:

```powershell
python tools\compact_html.py                       # reads and writes the clipboard itself (needs Python's tkinter)
python tools\compact_html.py page.html             # reads a file, writes page.compact.html and copies the result
python tools\compact_html.py page.html --max-options 40   # keep up to 40 options per dropdown (default 12)
python tools\compact_html.py page.html --keep-chrome      # keep the header, nav and footer
```

Copy the form (or the popup's `modal-content`), not the whole page, to get the smallest result.

## `fix_legal_names.py`: clean legal-name test data

The default `legalName*` rules built names from `faker.name()`, which can add titles, suffixes, periods and
apostrophes and made long names that the portal's name check refused. This rewrites only those generators in your
`rand_gen_rules.json`: one first name and one last name (letters only) plus the legal indicator, for example
`CARLA SMITH LLP`. It covers `legalName`, `legalNameLLC`, `legalNameLLP`, `legalNameCorporation`, `legalNameLimited`,
`legalNameLtd`, `legalNameIncorp` and `legalNameInc`.

```powershell
python tools\fix_legal_names.py "C:\NYBE\playwrightweb\workspace\Data\rand_gen_rules.json"
```

Your other rules, the address list and your spacing are left as they are. A backup is saved next to the file as
`rand_gen_rules.json.bak`, the file is not written if the result would be invalid JSON, and running it again changes
nothing. A line it cannot find is reported as `NOTE: ... add it by hand`. Restart any open run afterwards, because a
run reads the rules when it starts.

## `build_lite.py`: rebuild the AutoPlay Lite folder (for developers)

AutoPlay Lite is the smaller edition for people who only run tests. It is the same code, rebuilt into the
`AutomationLite` folder. Rebuild it after changing code, before you commit:

```powershell
python tools\build_lite.py             # rebuild AutomationLite
python tools\build_lite.py --check     # change nothing; exit code 1 when the folder is out of date
python tools\build_lite.py --reset-workspace   # also replace AutomationLite's workspace (it is kept by default)
```
