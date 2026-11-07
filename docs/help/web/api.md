# The API (v1)

Everything the web app does is also available over HTTP, for scripts, CI jobs and dashboards. The full list of endpoints, with their parameters and a **Try it out** button, is on the app's **API** page (`/docs`, top right of the app).

## Versions

- **`/api/v1/...`** is the versioned API: `GET /api/v1/runs`, `POST /api/v1/runs`, `GET /api/v1/events`, ... Use it in new scripts.
- **`/api/...`** serves exactly the same endpoints under their first name, so scripts written before v1 keep working. It is not on the docs page.
- Every API answer carries the header `X-API-Version: 1`. `GET /api/v1/meta` describes the API: its version, where it lives and its features.
- v1 only grows: endpoints and optional parameters can be added, but nothing a script relies on is removed or made required. A change that would break scripts gets a new version, `/api/v2`, next to v1.

## Signing in

When the app requires sign-in, send a token with each call (see **Team and sign-in**):

```
curl -H "Authorization: Bearer any_..." http://host:8080/api/v1/runs
```

## Common calls

| What | Call |
|---|---|
| Start a run | `POST /api/v1/runs` with `{"system", "env", "tags", "browsers"}` |
| Follow a run | `GET /api/v1/runs/<id>` and `GET /api/v1/runs/<id>/log?since=N` |
| Runs of a scope | `GET /api/v1/runs?system=&env=&since=&finished=true&limit=` |
| A test's history | `GET /api/v1/tests/<test id>/history` |
| What changed | `GET /api/v1/events?since=N&wait=25` (see **History and reports**) |
| Journeys of a system | `GET /api/v1/journeys?system=` |
| Release check | `POST /api/v1/releases` with `{"system", "env", ...}` |
| Find anything | `GET /api/v1/model/search?q=` |

## For developers: the contract

The paths, methods and parameters of v1 are recorded in `docs/api/v1-contract.json`. A unit test compares the code with it: removing or renaming anything, or making a parameter required, fails with what broke. Adding an endpoint or an optional parameter fails until the snapshot is updated with `python -m webapp.api_contract --write`, so every change to the API is visible in review.
