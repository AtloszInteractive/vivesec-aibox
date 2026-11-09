"""Administration of the authentication service from the box's shell (E27 S3).

  python manage.py init                       # data directory, key files, database
  python manage.py create-user NAME [--display-name X] [--role R]... [--group G]...
                               [--drive /storage/drives/room/]... [--temporary] [--password-stdin]
  python manage.py update-user NAME [--display-name X] [--role R]... [--no-roles]
                               [--group G]... [--no-groups] [--drive D]... [--no-drives]
  python manage.py reset-totp NAME            # the next login enrols a new authenticator
  python manage.py unlock NAME
  python manage.py reset-password NAME [--temporary] [--password-stdin]
  python manage.py suspend NAME | activate NAME | delete-user NAME
  python manage.py list-users
  python manage.py events [--limit N] [--user NAME]

Uses the same AUTH_* environment as the service. Passwords are read from the
terminal (twice), or as one line from standard input with --password-stdin;
they are never taken from the command line. Every change is in the event log
with the actor `cli`.
"""
import argparse
import getpass
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import auth  # noqa: E402
import config  # noqa: E402
import service  # noqa: E402
import store as store_module  # noqa: E402
import tokens  # noqa: E402

ACTOR = "cli"


def read_password(from_stdin, stdin=None):
    if from_stdin:
        line = (stdin or sys.stdin).readline()
        return line[:-1] if line.endswith("\n") else line
    first = getpass.getpass("Password: ")
    if getpass.getpass("Repeat: ") != first:
        raise auth.AuthError("passwords_differ", 400)
    return first


def init(settings, out):
    os.makedirs(settings.data_dir, mode=0o700, exist_ok=True)
    try:
        os.chmod(settings.data_dir, 0o700)
    except OSError:
        pass
    for path, what in ((settings.service_key_file, "service key"),
                       (settings.jwt_secret_file, "token signing key")):
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, mode=0o700, exist_ok=True)
        state = "created" if tokens.create_key(path) else "kept"
        tokens.read_key(path, what)
        out.write("%s %s: %s\n" % (what, state, path))
    store_module.Store(settings.db_path).close()
    out.write("database: %s\n" % settings.db_path)


def build_parser():
    parser = argparse.ArgumentParser(description="ViVeSec AI Box authentication service administration")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    create = commands.add_parser("create-user")
    create.add_argument("username")
    create.add_argument("--display-name", default="")
    create.add_argument("--role", action="append", default=[])
    create.add_argument("--group", action="append", default=[])
    create.add_argument("--drive", action="append", default=[])
    create.add_argument("--temporary", action="store_true",
                        help="the user must change the password at the first login")
    create.add_argument("--password-stdin", action="store_true")
    update = commands.add_parser("update-user")
    update.add_argument("username")
    update.add_argument("--display-name")
    for name in ("role", "group", "drive"):
        update.add_argument("--" + name, action="append")
        update.add_argument("--no-%ss" % name, action="store_true", help="clear the list")
    for name in ("reset-totp", "unlock", "suspend", "activate", "delete-user"):
        commands.add_parser(name).add_argument("username")
    reset = commands.add_parser("reset-password")
    reset.add_argument("username")
    reset.add_argument("--temporary", action="store_true")
    reset.add_argument("--password-stdin", action="store_true")
    commands.add_parser("list-users")
    events = commands.add_parser("events")
    events.add_argument("--limit", type=int, default=50)
    events.add_argument("--user")
    return parser


def _list_option(args, name):
    if getattr(args, "no_%ss" % name):
        return []
    return getattr(args, name)


def run(argv, env=None, out=sys.stdout, stdin=None):
    args = build_parser().parse_args(argv)
    settings = config.Settings.from_env(env)
    if args.command == "init":
        init(settings, out)
        return 0
    authenticator = service.open_service(settings)
    try:
        command = args.command
        if command == "create-user":
            password = read_password(args.password_stdin, stdin)
            result = authenticator.create_user(ACTOR, args.username, password, args.display_name,
                                               args.role, args.group, args.drive, args.temporary)
        elif command == "update-user":
            result = authenticator.update_user(ACTOR, args.username, args.display_name,
                                               _list_option(args, "role"), _list_option(args, "group"),
                                               _list_option(args, "drive"))
        elif command == "reset-totp":
            result = authenticator.reset_totp(ACTOR, args.username)
        elif command == "unlock":
            result = authenticator.unlock(ACTOR, args.username)
        elif command in ("suspend", "activate"):
            result = authenticator.update_user(ACTOR, args.username,
                                               status="suspended" if command == "suspend" else "active")
        elif command == "delete-user":
            result = authenticator.delete_user(ACTOR, args.username)
        elif command == "reset-password":
            password = read_password(args.password_stdin, stdin)
            result = authenticator.reset_password(ACTOR, args.username, password, args.temporary)
        elif command == "list-users":
            result = authenticator.list_users()
        else:
            result = authenticator.list_events(args.limit, args.user)
    finally:
        authenticator.store.close()
    out.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return 0


def main(argv=None):
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except auth.AuthError as e:
        sys.stderr.write("error: %s\n" % e.code)
        return 1
    except (config.ConfigError, tokens.KeyFileError) as e:
        sys.stderr.write("error: %s (run `manage.py init` first?)\n" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
