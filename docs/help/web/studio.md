# Studio

**Studio** (Generator → **Studio**) builds a test on the live page, one step at a time, so you see each step work before you move on. No recorder to install, no page to build first.

1. **Open the page.** Pick the environment; the address starts as the open page's (or the system's base URL). **Open** loads it in a browser on the AutoPlay machine (with the saved login, `Data/session.json`, when there is one) and shows its picture.
2. **Click what the test should use** on the picture: a box to type in, a button, a choice. The Studio picks the element under the click and shows how it will be found: the most reliable locator that finds that element and only it (the site's id attribute, a test id, an id, a name, its role and name, its label...). Every other locator that also finds only it is kept as the field's **identity**, so runs still find it when the main locator breaks (see **Element identity**).
3. **Name it and say what to do**: type (with the text), click, choose (an option's label or value), tick, or press a key. The name is suggested from what the element says; picking the same element again reuses its field.
4. **Run step.** The step runs on the live page right away and the picture updates. It is added to the test only when it worked; when it did not, you see why (for example, nothing found in time) and can pick again.
5. **Save** writes the fields to a page (a new one, or an existing one: fields it already has are reused, new ones added) and the steps as a test case in the test file you choose, then opens the test case in the editor. From there it runs like any other test.

Notes:

- The picture is a still image: after scrolling or anything the page does on its own, use **Refresh the picture**.
- A step that types a password is saved like any other value; replace it with `ENV.<NAME>` in the test case (or use the recorder, which does that for you).
- Each person's Studio has its own browser, closed when they leave the page.
