# Remote runners

By default a run happens on the machine that hosts the app. A **remote runner** is another machine (a lab PC, a VM, a server with a display) that runs tests for the app, so several people can queue runs without sharing one browser host.

The runner calls the app; the app never connects to the runner. So the runner needs no open port and works behind a firewall or proxy. It only needs to reach the app's address.

## Set up a runner

1. **In the app**, click **Runners** in the top bar (admins), type a name and optional labels, and click **Add runner**. The token is shown **once**, together with the command to start the agent; only a hash is kept (`reports/runners.json`). The same dialog shows each runner's status, machine, code version and current run, and edits labels or removes a runner (its token stops working at once).

   On the command line this is `python -m webapp.runners add lab1 --label windows --label chrome`, `list` and `remove NAME`.

2. **On the runner machine**, set the project up (`setup.ps1`), then start the agent:

   ```
   python -m webapp.runner_agent --server https://autoplay.example.com --token apr_...
   ```

   The token can also come from `AUTOPLAY_RUNNER_TOKEN`, and the address from `AUTOPLAY_SERVER`. Leave the agent running; it checks for work every few seconds and does one run at a time.

3. In the app, **Run on** appears in Run setup once a runner exists. Pick the runner and run as usual. The list shows each runner as *online* or *offline*. A run sent to an offline runner waits until it connects.

## Labels

Give runners labels (`windows`, `chrome`, `lab2`) and pick **Any runner labelled windows** under *Run on*: the run goes to the first free runner with that label, so several machines share the queue. A schedule has the same *Run on* choice, so a nightly suite can run on a runner instead of the app's machine.

The REST API accepts `"runner": "lab1"` or `"runner": "label:windows"` when starting a run, and `GET /api/runners` lists the runners.

## What happens in a run

- The agent takes the run, downloads the test definitions (`Data`, `Pages`, `Tests`) from the app, runs pytest on its own machine and sends the console back live. Progress, cancel and the log in Control and History work as for a local run.
- When it finishes, the agent uploads the Allure results. The app writes the built-in report and, if the Allure CLI is installed there, the Allure report, exactly as for a local run. Notifications fire as usual.
- **Cancel** reaches the runner within a second or two and stops the browser.
- If a runner stops calling in for 90 seconds during a run, the run ends as an error ("Runner lab1 stopped responding").

## Logins and secrets

- Values in the runner's own `.env` (or its environment) are used for logins, so passwords can stay on the runner. Use `--env-file` to point at a different file. The app's `.env` is **never** sent to a runner.
- Login values typed into the app's *Login credentials* for a run are sent to the runner for that run only, held in memory and removed from the log. That is why the agent **refuses plain `http` to a remote server** (use `https`; `--allow-insecure` overrides it on a network you trust).

## Limits and good practice

- Run the **same version of the code** on the app and the runner (`git pull` on both). The agent and app compare a protocol number and refuse to work together if it differs; the framework code itself comes from the runner's checkout, the test definitions from the app.
- Saved browser states (*Save the state after every step*, *Run from a step*) are local only and are refused for a remote run: they hold session cookies.
- If the app is reached through a DNS name or proxy, add it to `AUTOPLAY_ALLOWED_HOSTS` on the app, or the runner's calls are refused.
- Treat a runner token like a password: whoever has it can take queued runs, including any login values typed for them. Remove and re-add a runner to rotate its token.
