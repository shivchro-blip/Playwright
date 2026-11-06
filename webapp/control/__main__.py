"""The control plane, from the command line.

    python -m webapp.control serve                                    # the web app (port 8090)
    python -m webapp.control user add NAME --role admin               # its own users (viewer or admin)
    python -m webapp.control tenant create "Licensing" --admin lead   # a new tenant, run by the control plane
    python -m webapp.control tenant register "Team B" --url https://... --token any_...
    python -m webapp.control tenant list
    python -m webapp.control tenant remove ID                         # forget it (its files stay)
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m webapp.control", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="Run the control plane web app (and the tenants it manages)")
    user = sub.add_parser("user", help="The control plane's own users").add_subparsers(dest="action", required=True)
    add = user.add_parser("add")
    add.add_argument("name")
    add.add_argument("--role", choices=("viewer", "admin"), default="viewer")
    add.add_argument("--password", help="(default: ask)")
    token = user.add_parser("token")
    token.add_argument("name")
    tenant = sub.add_parser("tenant", help="Tenants").add_subparsers(dest="action", required=True)
    create = tenant.add_parser("create", help="A new tenant with its own workspace, run by the control plane")
    create.add_argument("name")
    create.add_argument("--admin", required=True, help="User name of the tenant's first admin")
    create.add_argument("--password", default="", help="Their password (default: generated and printed once)")
    create.add_argument("--owner", default="")
    create.add_argument("--template", default="", help="Copy pages and tests from this workspace")
    create.add_argument("--workspace", default="", help="Where to put it (default: <dir>/tenants/<id>)")
    create.add_argument("--port", type=int, default=0)
    register = tenant.add_parser("register", help="An AutoPlay that runs elsewhere (read only)")
    register.add_argument("name")
    register.add_argument("--url", required=True)
    register.add_argument("--token", default="", help="API token of a viewer account there (default: ask)")
    register.add_argument("--owner", default="")
    tenant.add_parser("list")
    remove = tenant.add_parser("remove")
    remove.add_argument("id")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.cmd == "serve":
        from webapp.control.app import serve
        serve()
        return 0
    from webapp.control.service import control
    from webapp.control.tenants import TenantError
    cp = control()
    try:
        if args.cmd == "user":
            if args.action == "add":
                password = args.password or getpass.getpass(f"Password for {args.name}: ")
                cp.users.add(args.name, password, args.role)
                print(f"Added {args.name} ({args.role}) to {cp.settings.dir / 'users.json'}")
            else:
                print(cp.users.new_token(args.name, "cli"))
        elif args.action == "create":
            tenant, password = cp.create(args.name, args.admin, owner=args.owner, password=args.password,
                                         template=args.template, workspace=args.workspace, port=args.port)
            print(f"Created {tenant.id}: {tenant.workspace}, {tenant.url} (starts with the control plane)")
            if not args.password:
                print(f"Admin {args.admin}, password (shown once): {password}")
        elif args.action == "register":
            token = args.token or getpass.getpass("API token: ")
            tenant = cp.register(args.name, args.url, token.strip(), owner=args.owner)
            print(f"Registered {tenant.id}: {tenant.url}")
        elif args.action == "list":
            print(json.dumps([{**t.to_dict(), "has_token": bool(cp.tenants.token(t))} for t in cp.tenants.list()],
                             indent=1))
        elif args.action == "remove":
            gone = cp.remove(args.id)
            if gone is None:
                print(f"No tenant {args.id!r}", file=sys.stderr)
                return 1
            print(f"Removed {gone.id}" + (f"; its workspace stays in {gone.workspace}" if gone.workspace else ""))
    except (TenantError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
