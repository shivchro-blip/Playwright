# Common Problems

## "No test cases match the selected tags" / run ends with "No tests were collected"

- Tags are required; only tests with a matching tag run.
- Check the test has steps (tests without steps are skipped).
- The **Tests** panel on the Control tab shows exactly what will run.

## "Missing login data for system 'X' in env 'Y'"

The test case's system/env has no entry in `Data/systemData.json`. Either add the entry or fix the test case's `system` / `env` (a test's own `"env"` wins over the Control tab selection).

## Login fails or "Login pre-test completed but verification failed"

- **Login credentials** on the Control tab must show both variables as *set*. Add them to `.env`.
- `logged_in_check` must match something visible only after login.
- Try with Headless off and Slow-mo 500 to watch the login steps.

## "Page Config: X not found"

The step's page does not exist in `Pages/<SYSTEM>`. **Generator → Check** lists these. Page lookup uses the test case's `system`.

## "Failed to generate Actions - No Field Config for: X"

The formData key is not a field of that page. Add the field in **Generator → Pages**, or fix the key (a `|` suffix is fine: `next|`).

## "Unable to confirm text on screen"

`confirmtext` reads the expected text from the page object field's `contains` key. Check it in the field's **{ }** editor.

## Setup fails: "unable to get local issuer certificate"

`setup.ps1` stops at *Installing Playwright Chromium* with `UNABLE_TO_GET_ISSUER_CERT_LOCALLY`. Your network (usually a company proxy) re-signs HTTPS traffic with its own root certificate. Windows trusts it, so your browser works, but the Node program that downloads Chromium does not.

`setup.ps1` now retries automatically using the Windows certificate store. If it still fails:

1. Ask IT for the company root certificate (a `.pem` or `.crt` file) and run, in the same PowerShell window:

   ```
   $env:NODE_EXTRA_CA_CERTS = "C:\path\to\company-root.pem"
   .\setup.ps1
   ```

2. If the error is a timeout or "connection refused" instead, the host `cdn.playwright.dev` is blocked: ask IT to allow it, or set `$env:HTTPS_PROXY = "http://proxy-host:port"` and re-run.

Do not set `NODE_TLS_REJECT_UNAUTHORIZED=0`: it switches certificate checking off for everything that process downloads. `setup.ps1` is safe to re-run; it skips what is already installed.

The same fix applies if `pip install` fails with a certificate error: set `$env:PIP_CERT = "C:\path\to\company-root.pem"` (or `pip config set global.cert ...`).

## Google (or another public site) shows a captcha

Search engines answer automated searches with a captcha page (`/sorry/`). Test forms and pages without submitting searches.

## Allure report button is disabled

The Allure CLI is not installed or not found. See *History and Reports*. Raw results are still saved for each run.

## "File was changed on disk since you opened it"

Someone (another tab, a teammate, `git pull`) changed the file. Click **Revert** to load the latest version, then re-apply your edit.

## Parallel runs

With more than one worker, live framework prints are hidden (pytest-xdist captures them) but per-test results still update. Each test opens its own browser, so watch memory with many workers.
