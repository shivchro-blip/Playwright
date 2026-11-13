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

## Add to a test case, and continue from a paused test

**Save** has two modes. *New test case* is the one above. *Add to a test case* writes the page the same way and **inserts the steps into a test case you already have**, after the step number you give (0 puts them at the start). The Studio stays open and the position moves past what was added, so the next page can be captured and added straight after.

To build the later pages of a long test without running it from step 1 each time, run the test with **Pause on failure** and **Pause before steps** set to the step you want to start at (or press **Pause** in Control), then press **Continue in Studio** in the paused panel. The Studio works on that test's own browser (Chromium browsers: chrome, msedge, chromium), already on the right page with everything the test did before it, and the test case and the position are filled in for you. Steps you run in the Studio happen in that browser, so when you finish press **End the test** instead of resuming; run the test from the start (or from the step) later to check the whole flow.
