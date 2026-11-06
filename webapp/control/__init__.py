"""The control plane: many AutoPlay tenants (teams, products, clients), one view of all of them.

Each tenant is its own AutoPlay instance: its own workspace (pages, tests, journeys), runs, users, logins (.env)
and remote runners, in its own process. Nothing is shared between tenants, so one team cannot see or break
another's. The control plane creates tenants (a workspace, an admin, a read-only service account), starts and
restarts the ones it manages, registers ones that run elsewhere, and reads every tenant's /api/summary with that
service account's token to show the whole portfolio: which journeys are failing, which releases are NO-GO, which
monitors are down, what is running.
"""
