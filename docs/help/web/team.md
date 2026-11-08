# Accounts, roles and the audit trail

On your own machine the app needs no login: it listens on `127.0.0.1` only and every action is done by `local` (an admin). To share it with a team, turn on sign-in.

## Turn on sign-in

1. Create the first admin (asks for a password, at least 10 characters):

   ```
   python -m webapp.users add ann --role admin
   ```

2. Start the app with `AUTOPLAY_AUTH_ENABLED=true`. Sign-in is also **required automatically** when `AUTOPLAY_HOST` is not a loopback address (for example `0.0.0.0`): the app refuses to start without an admin. Behind a reverse proxy that does its own login you can opt out with `AUTOPLAY_INSECURE_NO_AUTH=true`.

People sign in at `/login`. Sessions last 12 hours (signed, HttpOnly, SameSite=Lax cookie). Five wrong passwords for a name, or from one address, pause sign-in for 5 minutes. The header shows who is signed in, their role and **Sign out**.

## Roles

Each role can do everything the one before it can.

| Role | Can |
|---|---|
| viewer | See everything: runs, reports, failure details, test cases, pages, schedules, help |
| runner | Start, queue, re-run and cancel runs; run a schedule now; check locators; AI triage and summaries |
| editor | Change test cases and pages, record tests, change schedules, delete runs, apply AI suggestions and drafts |
| admin | Settings (`.env` defaults, AI assist and recorder switches) and the **Activity** log |

Roles are checked on the server for every UI action and API call; a refused action says which role is needed and is recorded.

## Roles per system and environment

Someone can have a different role in one system, in one environment of every system, or in one environment of one
system: "editor, but only a viewer in production", "a viewer who may run the smoke tests on PORTAL's uat".

```
python -m webapp.users grant bob viewer --env prod                 # a viewer in every system's prod
python -m webapp.users grant bob editor --system PORTAL            # an editor on PORTAL
python -m webapp.users grant bob runner --system PORTAL --env uat  # a runner on PORTAL's uat
python -m webapp.users ungrant bob --env prod
```

The most specific grant wins (system and environment, then system, then environment); everywhere else the user's
own role applies. Grants are checked wherever an action belongs to a system or environment: starting, re-running
and cancelling runs, running journeys, monitor checks, schedules and impacted tests, release checks, and editing
test cases and pages (the API does the same). Shared things (common actions, data sets, settings, users) use the
user's own role. The header shows the role and, on hover, the grants; `GET /api/v1/me` returns them.

## Single sign-on (OpenID Connect)

People can sign in with your identity provider (Entra ID, Okta, Google Workspace, Keycloak, Auth0, ...). Register
AutoPlay there as a web application with the redirect URI `<public url>/login/sso/callback`, then set:

| Setting | Meaning |
|---|---|
| `AUTOPLAY_OIDC_ISSUER` | The provider's issuer URL (https), e.g. `https://login.microsoftonline.com/<tenant>/v2.0` |
| `AUTOPLAY_OIDC_CLIENT_ID` / `AUTOPLAY_OIDC_CLIENT_SECRET` | The application's id and secret |
| `AUTOPLAY_PUBLIC_URL` | How people reach AutoPlay (used for the redirect URI) |
| `AUTOPLAY_OIDC_USERNAME_CLAIM` | The claim that names the person (default `email`, which must not be unverified). Use a claim people cannot change themselves. |
| `AUTOPLAY_OIDC_GROUPS_CLAIM` | The claim with the person's groups (default `groups`) |
| `AUTOPLAY_OIDC_ROLE_MAP` | Groups to roles: `qa-leads=admin, qa=editor, portal-team=PORTAL/*:editor, ops=*/prod:runner` |
| `AUTOPLAY_OIDC_DEFAULT_ROLE` | The role of a person in no mapped group who has no account yet (empty: refused) |
| `AUTOPLAY_OIDC_LABEL` | The button says "Sign in with <label>" |
| `AUTOPLAY_OIDC_ONLY` | `true`: no password sign-in for people (API tokens still work) |

The sign-in page then shows **Sign in with ...**. The sign-in uses the authorization code flow with PKCE; the
token is fetched directly from the provider and its issuer, audience, expiry and nonce are checked.

`group=role` sets the role everywhere (the highest of the person's mapped groups); `group=SYSTEM/env:role` grants a
role in one system (`PORTAL/*`), one environment (`*/prod`) or one environment of one system. When the provider sends
mapped groups it is the source of truth: the role and grants are updated at every sign-in. A person in no mapped
group keeps the role an admin gave them here (`python -m webapp.users add sam@contoso.com --sso --role runner`), or
gets the default role, or is refused. With a group mapped to `admin`, no local admin is needed to start the app.
Every SSO sign-in and refusal is in the Activity log.

## Managing users

```
python -m webapp.users list
python -m webapp.users add bob --role runner
python -m webapp.users role bob editor
python -m webapp.users passwd bob
python -m webapp.users disable bob        # or enable / remove
python -m webapp.users add sam@contoso.com --sso --role runner   # signs in with single sign-on only
```

Users are stored in `reports/users.json` in the workspace (git-ignored; `AUTOPLAY_USERS_FILE` to move it). Passwords are scrypt hashes; the file never holds a usable secret. The app always keeps at least one active admin.

## API tokens (scripts and CI)

```
python -m webapp.users token ci-bot --name github-actions     # printed once
curl -H "Authorization: Bearer any_..." http://host:8080/api/runs
```

A token acts with its user's role. Only a hash is stored; revoke with `python -m webapp.users revoke <user> <token id>` (ids are in `list`).

## Activity (audit trail)

Admins open **Activity** in the top bar. It lists, newest first: sign-ins (and failed or throttled ones), refused actions, runs started, queued, re-run, cancelled and deleted, test case and page saves, recordings, schedule changes, setting changes and every API call that changes something (with its result). The log is `reports/audit.jsonl` in the workspace, one JSON object per line. AI calls have their own log, `reports/ai-audit.jsonl`.

## Run queue

Runs started while another is in progress are **queued** (up to 20) instead of refused: the Run button says **Queue**, the run shows as *queued* with its place in line, and it starts as soon as a slot is free. A queued run can be cancelled. Schedules use the same queue. `AUTOPLAY_MAX_CONCURRENT_RUNS` (default 1) sets how many run at once on this machine; each run drives real browsers, so raise it with care.

Remote runners (other machines taking runs from the queue) are not part of the app yet.

## Custom autoGen code

`Data/rand_gen_rules.json` may hold Python snippets (`custom_functions`, `dynamic_objects`). The web app cannot edit that file, and the snippets run under a policy: imports only from `random`, `datetime`, `string`, `math`, `re`, `decimal`, `uuid`, `time`, `calendar`, `itertools`, `json`, `faker` and `os.path`; no `open`, `exec`, `eval`, `getattr` and similar; no attribute names starting with `_`. A refused snippet is reported as broken with the reason, and only rules that use it fail. This is a guard rail, not a sandbox: review changes to that file like code. `RANDGEN_ALLOW_UNSAFE_CODE=true` in `.env` lifts the policy.
