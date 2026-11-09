# Application map

The **Map** tab shows the screens of a system and how the tests move between them, colored by how well each screen is
tested. It answers "what parts of the application do our tests never reach?" and "which screens are failing right
now?" at a glance.

## What it shows

- **Screens** are the system's pages (shared components are parts of pages, not screens of their own).
- **Arrows** are moves: two screens one after the other in a test case's steps, with common actions and reused test
  cases followed into. The number on an arrow is how many test cases make that move.
- **Colors**:
  - **failing** (red): a test case failed on this screen in its environment's latest run;
  - **untested** (amber): no test case reaches it;
  - **new** (blue, dashed): its page file changed in the last 14 days and at most one test case uses it;
  - **tested** (green).

A failed test case counts against the screen it failed on when the run knows it (the failed step was a page step),
and against every screen it goes through otherwise. Screens it got through before failing count as passed.

On large systems the drawing keeps the failing and untested screens and the busiest others (80 at most); the table
below always lists every screen.

## The screens table

Each screen with its state, how many test cases go through it (and how many start there), the journeys that do
(**critical** is marked), and per environment how its test cases did in the latest run. **Needs attention** shows only
untested, failing and new screens. Click a screen to open its page in the Generator.

From scripts: `GET /api/v1/map/{system}`.
