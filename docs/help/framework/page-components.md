# Shared page components

Many pages have the same fields: a header with a menu, an address block, a pair of Save and Back buttons.
Define them once in a **component** and use it on every page that has them. A locator fixed in the component is
fixed on every page.

## A component

A component is a page file marked `"component": true`. Put it in `Pages/<SYSTEM>/components/` (any folder of the
system works):

```json
{
  "component": true,
  "fields": {
    "street": { "locator": "input[name='street']", "type": "text" },
    "city":   { "locator": "input[name='city']",   "type": "text" }
  }
}
```

A component can use other components too.

## Using it on a page

List the components a page uses in `"use"`, before its own fields:

```json
{
  "path": "/apply",
  "use": [
    "Header",
    { "component": "Address", "prefix": "mailing", "within": "#mailing-address" },
    { "component": "Address", "prefix": "home",    "within": "#home-address" }
  ],
  "fields": {
    "submit": { "role": "button", "name": "Submit", "type": "button" }
  }
}
```

The page now has every field of `Header` under its own name, plus `mailing.street`, `mailing.city`,
`home.street` and `home.city`. Tests use them like any field:

```json
{ "screen": "Apply", "formData": { "mailing.street": "1 Main St", "home.city": "Troy", "submit": "click" } }
```

| Key | Meaning |
|---|---|
| `component` | The component's name (its file name without `.json`). A plain string is the same as `{"component": "..."}`. |
| `prefix` | Optional. The fields are named `prefix.field` on this page. Needed when a page uses one component twice. |
| `within` | Optional. A CSS or XPath locator of the container: the component's fields are looked for only inside it, so the same component can sit on one page twice. |

A field of the page's own with the same name as a component's field replaces it on that page (for a page where
one locator differs).

Pages without `"use"` work exactly as before; nothing needs to change in existing files.

## In the web app

**Generator → Pages** shows a page's components above its fields:

- **Use a component** adds one, with an optional prefix and container.
- **Shared component** marks the open page as a component, so other pages can use it. A component lists the pages
  that use it.
- Click a component's name to open it. Its fields are shown on the page as tags; a struck-out tag is replaced by the
  page's own field.
- **Maintain → Move fields into a component** moves fields of the page into a new component that the page then
  uses. The fields keep their names, so no test changes.

Renaming a component's field (**Maintain → Rename a field** on the component) renames it in every step of every page
that uses it, prefix included. Renaming a component updates every page's `"use"`. Removing a component's field is
refused while any page's step still uses it. Tidy counts a component's field as used when a page that uses the
component uses it.

A self-heal accepted for a field that comes from a component is written to the component, so every page that uses it
gets the fix.

## When something is wrong

The step fails and says why: a component that is not found, a page used as a component without
`"component": true`, a component that uses itself, or two components giving the same field name (give one a
prefix). The page editor refuses to save such a page with the same message.
