# Element identity

A page's field has one locator: `#q`, a label, a role and name. When a redesign renames an id or moves a button, that locator finds nothing and every test that uses the field fails, even though the element is still on the page.

A field can also carry an **identity**: what its element *is*, and other ways to find it. A run uses that identity when the locator breaks, and tells you so, instead of failing.

## What an identity holds

- **What the element is** (its fingerprint): the kind of element, its type, role, id, name, test id, label, ARIA label, placeholder and text, as they were when the field was captured.
- **Other locators**, best first: every other locator that found this element, and only this one, on the live page. For a search box that could be `input[name='q']`, then its label `Search`.
- When it was last checked on the live page.

It is stored with the field in the page file, under `identity`. Pages without it work exactly as before, and older versions of AutoPlay ignore it.

## How a field gets one

- **Recording** (the recorder) and **From page** (Add fields from the live page) give every field they add an identity.
- **Learn identities** (Generator, Pages, above the fields) scans the live page and gives each existing field the identity of the element its locator finds there. Fields it cannot find (hidden, on a later step of the page, or with a broken locator) are listed and left alone. Nothing is saved until you **Save** the page.
- Recording a page again adds an identity to fields that did not have one.

## During a run

For each field with an identity:

- **Its locator finds the element**: it is used, as always. If that element no longer looks like the field's element (the page changed under the locator), the run notes a *drift* and still uses it: an identity never swaps an element that was found.
- **Its locator finds nothing**: the other locators are tried in order. The first that finds exactly one element that looks like the field's element (at least 60% alike: same kind of element, and most of its id, name, label and other traits) is used for this run, and the run notes a *fallback*. A locator that finds a different element is skipped: a text area is never used for a text box.
- **Nothing finds it**: the run notes it as *missing*, and the step fails as it always did.

The page file is never changed by a run. Self-healing (**selfHeal**) still works as before, after the identity has been tried.

| Setting (.env or run options) | What it does | Default |
|---|---|---|
| `identityMinScore` | How alike (0–100) an element must be to the field's to be used as a fallback | `60` |
| `identityWaitMs` | How long a field's own locator gets to find its element before the fallbacks are tried | `3000` |

## Health, and promoting a fallback

Each field in the Pages editor has a shield button showing its identity:

- grey: no identity yet;
- green: healthy (90% or more over the last 30 runs);
- amber or red: recent runs needed a fallback, saw a drift, or could not find it.

Open it to see what the element is, its locators in order, and how recent runs found it. When runs keep using the same fallback, **Promote the fallback it keeps using** makes that fallback the field's locator, and the old locator becomes the first fallback. **Save** the page to keep it. **Forget identity** removes the identity from the field.
