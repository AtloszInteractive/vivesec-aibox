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

Directory (AD) login, E27 S6:

  python manage.py ldap-cert ldaps://dc01.example.local   # fingerprint of the server certificate, to pin
  python manage.py ldap-password [--password-stdin]       # the service account password -> its file
  python manage.py ldap-map import FILE|-                 # validate and install the group map
  python manage.py ldap-map show
  python manage.py ldap-check [NAME]                      # bind, mapped groups, what NAME would get

Uses the same AUTH_* environment as the service. Passwords are read from the
terminal (twice), or as one line from standard input with --password-stdin;
they are never taken from the command line. Every change is in the event log
with the actor `cli`.
"""
import argparse
import getpass
import json
import os
import ssl
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import auth  # noqa: E402
import config  # noqa: E402
import directory  # noqa: E402
import ldap_client  # noqa: E402
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
    commands.add_parser("ldap-cert").add_argument("url")
    commands.add_parser("ldap-password").add_argument("--password-stdin", action="store_true")
    group_map = commands.add_parser("ldap-map")
    group_map.add_argument("action", choices=("import", "show"))
    group_map.add_argument("file", nargs="?")
    commands.add_parser("ldap-check").add_argument("username", nargs="?")
    return parser


def ldap_command(args, settings, out, stdin):
    """The ldap-* commands; they do not open the user database."""
    if args.command == "ldap-cert":
        der = ldap_client.fetch_certificate(args.url, settings.ldap_timeout_seconds)
        out.write("SHA-256 fingerprint: %s\n" % ldap_client.format_fingerprint(ldap_client.fingerprint(der)))
        out.write("Compare it with the certificate on the domain controller before pinning it "
                  "(AUTH_LDAP_TLS_PINS).\n")
        out.write(ssl.DER_cert_to_PEM_cert(der))
        return 0
    if args.command == "ldap-password":
        password = read_password(args.password_stdin, stdin)
        if not password:
            raise auth.AuthError("password_empty", 400)
        directory.write_private(settings.ldap_bind_password_file, (password + "\n").encode("utf-8"))
        out.write("service account password written to %s\n" % settings.ldap_bind_password_file)
        return 0
    if args.command == "ldap-map":
        if args.action == "show":
            group_map = directory.read_group_map(settings.ldap_map_file)
            out.write(json.dumps({"file": settings.ldap_map_file,
                                  "groups": [{"dn": dn, "name": name, "roles": roles}
                                             for dn, name, roles in group_map.groups],
                                  "roles": group_map.roles}, indent=2, ensure_ascii=False) + "\n")
            return 0
        if not args.file:
            raise SystemExit("ldap-map import needs a file (or - for standard input)")
        if args.file == "-":
            raw = (stdin or sys.stdin).read()
        else:
            with open(args.file, encoding="utf-8") as handle:
                raw = handle.read()
        data = json.loads(raw)
        group_map = directory.parse_group_map(data)
        directory.write_private(settings.ldap_map_file,
                                (json.dumps(group_map.as_dict(), indent=2, ensure_ascii=False) + "\n")
                                .encode("utf-8"))
        out.write("group map written to %s: %d groups, %d roles\n"
                  % (settings.ldap_map_file, len(group_map.groups), len(group_map.roles)))
        return 0
    checker = directory.Directory(settings, log=lambda message: sys.stderr.write("directory: %s\n" % message))
    report = {"state": checker.state, "problem": checker.problem or None,
              "urls": settings.ldap_urls, "map_file": settings.ldap_map_file}
    if checker.enabled:
        try:
            report.update(checker.check(auth.normalize_username(args.username) if args.username else None))
        except directory.Unavailable as e:
            report["error"] = e.reason
    out.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return 0 if checker.enabled and "error" not in report else 1


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
    if args.command.startswith("ldap-"):
        return ldap_command(args, settings, out, stdin)
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
    except (ValueError, OSError, ldap_client.LdapError) as e:
        sys.stderr.write("error: %s\n" % e)
        return 1
    except (config.ConfigError, tokens.KeyFileError) as e:
        sys.stderr.write("error: %s (run `manage.py init` first?)\n" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
