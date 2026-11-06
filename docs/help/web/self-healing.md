# Self-healing locators

Most test maintenance after a site redesign is locators that no longer find their element: `#zip-code` became `#applicantZipCode`, a button's text changed. **Self-heal locators** keeps those runs going and turns each break into a one-click fix.

Turn it on per run with the **Self-heal locators** switch under *Browser & artifacts* in Control (or `selfHeal=true` in Settings or a schedule's options). It is off by default.

## What happens in a run

- While a run goes well, each field's element is **remembered by what it looks like**: its id, name, data-testid, label, placeholder, text and type. These profiles are kept in `Data/locatorProfiles/<SYSTEM>.json` and get better with every run.
- When a field's locator finds **no element at all** (waiting up to 5 seconds), the page is scanned. Each element on it is scored against what the field still says (its name, old locator, label, placeholder or text) and its remembered profile, the same way *Suggest fixes* does.
- The best element is used **only if** all of these hold:
  - it is the same kind of element: a text box never replaces a button;
  - it scores at least 60% (`selfHealMinScore`);
  - its locator matches exactly one element;
  - nobody has rejected it before.
  Otherwise the step fails as usual.
- Elements that exist but are hidden are not healed: that is a page state, not a broken locator.
- Every heal is logged as `[heal] <field> on <page>: ...`, attached to the test (*Healed locator*), and the result shows a **Healed** chip. A run never changes your page files.

## Review

History shows **Healed locators** for every heal that is waiting. Each row has the field, the old and new locator, the score with its reason, and how often and in which tests it happened.

- **Accept** writes the new locator into the page. Type, autoGen and the other keys are kept, and the old version stays in the page's edit history. The next run no longer needs healing.
- **Reject** keeps the page as it is and stops the runner from using that replacement again (`Data/heals.json`).

Accepting and rejecting need the *editor* role and are recorded in Activity.

## What it does not do

- It does not heal table cells, nested locator chains, locators that use `{value}`, `DICT.` or `ENV.`, or loops.
- A healed pass is not a clean pass. Release checks list every test that passed with healed locators as a **warning**, so nobody signs a release on healed results without seeing it.
- It cannot know your intent. If a redesign moved a label to a different field, the best match can be wrong: review heals before accepting them.
