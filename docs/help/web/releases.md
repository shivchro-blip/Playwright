# Release checks

A **release check** answers the question a release manager, a CIO or an auditor asks before going live: *is this release safe, and what is the proof?* It decides **GO** or **NO-GO** from the journeys (see *Journeys*), keeps the evidence behind the decision, and records who approved or rejected it.

## Check a release

1. Run the journeys' test cases on the release environment (**Journeys > Run**, a schedule, or CI).
2. Open **Releases**, pick the system and click **New release check**.
3. Name the release (*Release 4.2*, *Sprint 31*), pick the environment, and adjust the policy if you need to:
   - **Journeys:** all of the system's, or the ones this release touches.
   - **Journeys that block the release:** critical and high by default.
   - **Evidence at most N days old:** 7 by default.
   - **Only results since:** set it to when the release was deployed, so results from before it do not count.
4. Click **Check**.

## The decision

- **NO-GO** when a blocking journey is failing, has test cases that never ran (in the period), or was last proven longer ago than allowed. Each reason is written out, for example *Contact details (high) is failing: TC-104 at Applicant > Zip (FILL)*.
- **GO** when every blocking journey is proven working.
- **Warnings** list what does not block but must not be hidden: failing journeys of lower criticality, **quarantined** test cases (their failures do not count), and tests that passed only after a **retry**.

## Evidence and signatures

Each check is saved in `reports/releases/<id>/` as a pack: the decision and reasons, every journey with the latest result of each of its test cases (run, time, where it failed), and the runs used. Two things make it tamper-evident:

- a **SHA-256 fingerprint** of its content;
- an **HMAC signature** made with this server's key (`reports/releases/.release-key`).

The app checks both every time the pack is shown. A changed pack is flagged as *evidence changed* and can no longer be signed.

**Report** opens a self-contained page to print to PDF or attach to a change ticket. **Evidence (JSON)** downloads the pack.

## Sign-off

People with the *editor* or *admin* role **approve** or **reject** a check. Each sign-off records who, when, why, and the fingerprint of the evidence they signed.

- Rejecting needs a reason.
- Approving a NO-GO needs a written reason. The check then shows *approved with accepted risk*, so an override is never silent.
- The check's status is *awaiting sign-off*, *approved*, *approved with accepted risk* or *rejected*.

## As a release gate in CI

```
curl -s -X POST https://autoplay.example.com/api/releases \
  -H "Authorization: Bearer $AUTOPLAY_TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "Release 4.2", "system": "PORTAL", "env": "uat", "since": "2026-11-06T09:00"}' \
  | python -c "import json,sys; r=json.load(sys.stdin); print(r['decision'], r['reasons']); sys.exit(r['decision'] != 'go')"
```

The call needs the *runner* role.

- `GET /api/releases?system=` lists checks.
- `GET /api/releases/<id>` returns the pack with its sign-offs and verification.
- `POST /api/releases/<id>/signoff` signs off (*editor* role).

## After each deploy (deploy-aware releases)

Announce each deploy from your CI/CD pipeline and every deploy carries its own release check:

```
curl -X POST <app>/api/v1/deploys -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
     -d '{"system": "PORTAL", "env": "uat", "build": "482",
          "repo": "acme/portal", "sha": "<commit>", "callback_url": "https://ci.example/hook"}'
```

AutoPlay then, on its own:

1. runs the system's journeys on that environment (or only those listed in `journeys`);
2. when the run finishes, makes a release check from that run's results (only results since the deploy count), named
   after the system and build, created by "deploy";
3. reports **GO** or **NO-GO**:
   - to `callback_url`: a POST with the decision, its reasons, the run, the release check and a link to its report;
   - with `repo` and `sha`: a GitHub commit status "AutoPlay release check", *pending* at once, then *success* or
     *failure* with the first reason and a link to the evidence (needs `RELEASE_GITHUB_TOKEN` in `.env`, or the
     `ISSUE_GITHUB_TOKEN` already set for filing bugs; `RELEASE_GITHUB_API_URL` for GitHub Enterprise);
4. on GO, starts a check of each monitor watching that system and environment.

The call answers at once with the deploy (status *running*); `GET /api/v1/deploys/{id}` follows it to *decided* (or
*failed*, with the reason, also sent to the callback and as an *error* status). Automation rules waiting for a deploy
fire on the same announcement. The **After each deploy** card on this tab lists recent deploys with their decision,
release check and what was reported. Announcing needs a run permission for that system and environment.

