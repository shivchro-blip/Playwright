"""Manage web app users:  python -m webapp.users {add,passwd,role,disable,enable,remove,list,token,revoke}

Users are stored in <workspace>/reports/users.json (AUTOMATENY_USERS_FILE). Login is used when
AUTOMATENY_AUTH_ENABLED=true, and always when the app listens on a network address.
"""
from __future__ import annotations

import argparse
import getpass
import sys

from webapp.services.users import ROLE_LABELS, ROLES, UserStore
from webapp.settings import get_settings


def _password(prompt: str = "Password: ") -> str:
    first = getpass.getpass(prompt)
    if getpass.getpass("Repeat: ") != first:
        raise ValueError("The passwords don't match.")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m webapp.users", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add", help="add a user (asks for the password)")
    add.add_argument("username")
    add.add_argument("--role", choices=ROLES, default="viewer",
                     help="; ".join(ROLE_LABELS[r] for r in ROLES))
    for name, text in (("passwd", "set a new password"), ("disable", "block sign-in"), ("enable", "allow again"),
                       ("remove", "delete the user")):
        sub.add_parser(name, help=text).add_argument("username")
    role = sub.add_parser("role", help="change a role")
    role.add_argument("username")
    role.add_argument("role", choices=ROLES)
    sub.add_parser("list", help="list users and their tokens")
    token = sub.add_parser("token", help="create an API token (printed once)")
    token.add_argument("username")
    token.add_argument("--name", default="token", help="what the token is for, e.g. ci")
    revoke = sub.add_parser("revoke", help="revoke an API token by id")
    revoke.add_argument("username")
    revoke.add_argument("token_id")
    args = parser.parse_args(argv)

    store = UserStore(get_settings().resolved_users_file)
    try:
        if args.cmd == "add":
            store.add(args.username, _password(), args.role)
            print(f"Added {args.username} ({args.role}) to {store.path}")
        elif args.cmd == "passwd":
            store.update(args.username, password=_password("New password: "))
            print("Password changed.")
        elif args.cmd == "role":
            store.update(args.username, role=args.role)
            print(f"{args.username} is now {args.role}.")
        elif args.cmd in ("disable", "enable"):
            store.update(args.username, disabled=args.cmd == "disable")
            print(f"{args.username} {args.cmd}d.")
        elif args.cmd == "remove":
            store.remove(args.username)
            print(f"Removed {args.username}.")
        elif args.cmd == "list":
            for u in store.list():
                tokens = ", ".join(f"{t['id']} ({t['name']})" for t in u.tokens) or "no tokens"
                print(f"{u.username:24} {u.role:7} {'disabled ' if u.disabled else ''}{tokens}")
        elif args.cmd == "token":
            print(store.new_token(args.username, args.name))
            print("Use it as 'Authorization: Bearer <token>'. It is shown only now.", file=sys.stderr)
        elif args.cmd == "revoke":
            print("Revoked." if store.revoke_token(args.username, args.token_id) else "No such token.")
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
