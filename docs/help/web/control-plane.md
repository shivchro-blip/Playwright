# Control plane: many teams, one view

When several teams, products or clients use AutoPlay, give each its own **tenant** and watch all of them from the **control plane**.

A tenant is a complete AutoPlay of its own: its own workspace (pages, test cases, journeys, monitors), runs and reports, users and roles, logins (`.env`), secret store settings and remote runners, in its own process on its own port. Nothing is shared between tenants, so one team cannot see, change or break another's tests, results or credentials.

The control plane creates tenants, starts them and restarts them if they stop, registers AutoPlay instances that run elsewhere, and shows the whole **portfolio**:

| Column | |
|---|---|
| **Health** | *Needs attention* (red): a critical or high journey is failing, or a monitor is down. *Warning* (amber): another journey is failing, or the latest release check is NO-GO. *Healthy* (green). *Unknown*: not reachable, or its token was refused, with the reason. |
| **Journeys** | Passing and failing journey statuses (journey × environment). |
| **Latest release** | The newest release check and its GO / NO-GO decision. |
| **Monitors** | Journeys up and down across its monitors. |
| **Runs** | Running now, and passed / failed in the last 24 hours. |
| **Runners** | Remote runners online. |

**Needs attention** lists everything red or unknown across all tenants first, then the warnings, so the first line is the first thing to fix. The page refreshes every 15 seconds; **Open** goes to the tenant (people sign in there with their tenant account).

## Start it

```
python -m webapp.control serve
```

It listens on `http://127.0.0.1:8090` and starts every tenant it manages. Settings are `AUTOPLAY_CP_*` variables:

| Variable | Default | |
|---|---|---|
| `AUTOPLAY_CP_DIR` | `controlplane/` in the repo | tenants.json, secrets.env (the service tokens, readable by its owner only), the tenants' workspaces (`tenants/<id>`), logs, the control plane's users and audit log |
| `AUTOPLAY_CP_PORT` / `AUTOPLAY_CP_HOST` | 8090 / 127.0.0.1 | Sign-in is required as soon as it listens on a network address (or with `AUTOPLAY_CP_AUTH_ENABLED=true`). |
| `AUTOPLAY_CP_FIRST_PORT` | 8101 | New tenants get the next free port from here. |
| `AUTOPLAY_CP_SUPERVISE` | true | Start the managed tenants and restart them when they stop (after 2, 4, 8 ... up to 60 seconds). |

The control plane has its own users: `python -m webapp.control user add NAME --role admin` (or `viewer`). Viewers see the portfolio; admins add, start, stop and remove tenants.

## Add a tenant

**New tenant > Create one here** (or `python -m webapp.control tenant create "Licensing" --admin lead`):

1. A workspace in `controlplane/tenants/<id>`: empty, or a copy of the pages, test cases, journeys and data of another workspace (**Copy pages and tests from**). Runs, users, secrets (`.env`), heal records, monitors and schedules are never copied. It gets its own empty `.env`, so it never uses anybody else's logins.
2. Its first **admin**, with a password shown once (change it after signing in), in the tenant's own users file.
3. A read-only **service account** (`controlplane`, viewer) whose API token the control plane keeps in `secrets.env` to read the tenant's summary. That token opens only this tenant.
4. Its own port; it starts at once, with sign-in required.

**New tenant > Register one that runs elsewhere** (or `tenant register NAME --url ... --token ...`) adds an AutoPlay running on another server. Create a viewer account there (`python -m webapp.users add controlplane --role viewer`, then `python -m webapp.users token controlplane`) and paste its token. The control plane only reads it.

**Remove** forgets a tenant; its workspace and runs stay on disk.

## API

| Call | |
|---|---|
| `GET /cp/api/portfolio` | Every tenant's health, reasons and summary, and the totals and attention list (`?fresh=true` skips the 10-second cache). |
| `GET /cp/api/tenants`, `POST /cp/api/tenants`, `DELETE /cp/api/tenants/{id}` | List, create (`name`, `admin`, optional `template`, `port`) or register (`name`, `url`, `token`), remove. |
| `POST /cp/api/tenants/{id}/start`, `/stop`, `/restart` | Managed tenants. |

Each tenant answers `GET /api/summary` (any signed-in account): its journeys per system and environment, the failing critical and high journeys, the latest releases, monitors, runs and runners.
