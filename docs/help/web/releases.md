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
