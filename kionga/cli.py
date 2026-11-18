"""The ``kionga`` command: manage applications from a terminal.

    kionga apps deploy billing --project p-1 --image ghcr.io/acme/billing:1.4 --wait
    kionga apps list
    kionga apps logs billing --follow

It reads the gateway address from ``KIONGA_URL`` and the API key from
``KIONGA_TOKEN``, like ``KiongaClient``. Errors print as ``kionga: <reason>`` and
exit with status 1. ``--json`` prints the gateway's response instead of a table.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from typing import Any

from .applications import (
    SCALING_METRICS,
    VISIBILITIES,
    Application,
    ApplicationFailed,
    ApplicationTimeout,
)
from .errors import KiongaError

__all__ = ["main", "parser"]


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="kionga", description="Kionga command line")
    root.add_argument("--url", default=None, help="gateway address (default: KIONGA_URL)")
    commands = root.add_subparsers(dest="command", required=True)

    apps = commands.add_parser("apps", help="deploy and operate applications (Kubernetes installs)")
    app_commands = apps.add_subparsers(dest="apps_command", required=True)

    deploy = app_commands.add_parser("deploy", help="deploy a new application")
    deploy.add_argument("name", help="DNS label; the application is served at <name>.<install domain>")
    deploy.add_argument("--project", required=True, help="project id")
    _spec_arguments(deploy, create=True)
    _wait_arguments(deploy)
    deploy.set_defaults(run=apps_deploy)

    update = app_commands.add_parser("update", help="change an application's settings")
    update.add_argument("application", help="application id or name")
    _spec_arguments(update, create=False)
    _wait_arguments(update)
    update.set_defaults(run=apps_update)

    listing = app_commands.add_parser("list", help="list applications")
    listing.add_argument("--project", default="", help="only this project")
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(run=apps_list)

    get = app_commands.add_parser("get", help="show one application")
    get.add_argument("application")
    get.add_argument("--json", action="store_true")
    get.set_defaults(run=apps_get)

    logs = app_commands.add_parser("logs", help="print an application's logs")
    logs.add_argument("application")
    logs.add_argument("-f", "--follow", action="store_true", help="keep printing new lines")
    logs.add_argument("--tail", type=_non_negative, default=0, help="lines per replica")
    logs.set_defaults(run=apps_logs)

    events = app_commands.add_parser("events", help="print an application's Kubernetes events")
    events.add_argument("application")
    events.add_argument("--json", action="store_true")
    events.set_defaults(run=apps_events)

    redeploy = app_commands.add_parser("redeploy", help="roll an application again")
    redeploy.add_argument("application")
    _wait_arguments(redeploy)
    redeploy.set_defaults(run=apps_redeploy)

    delete = app_commands.add_parser("delete", help="delete an application")
    delete.add_argument("application")
    delete.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
    delete.set_defaults(run=apps_delete)
    return root


def _spec_arguments(command: argparse.ArgumentParser, *, create: bool) -> None:
    source = command.add_mutually_exclusive_group(required=create)
    source.add_argument("--image", help="container image to run")
    source.add_argument("--git", help="Git repository to build instead of an image")
    command.add_argument("--ref", default="", help="branch, tag or commit to build (with --git)")
    command.add_argument("--context-dir", default="", help="build context inside the repository")
    command.add_argument("--dockerfile", default="", help="Dockerfile path inside the context")
    default = (lambda value: value) if create else (lambda value: None)
    command.add_argument("--port", type=int, default=default(8080), help="port the container listens on")
    command.add_argument("--env", action="append", metavar="KEY=VALUE", help="environment variable; repeat")
    command.add_argument("--secret", action="append", dest="secrets", metavar="NAME",
                         help="Kionga secret to expose as environment; repeat")
    command.add_argument("--cpu", help="CPU request, for example 500m")
    command.add_argument("--memory", help="memory request, for example 512Mi")
    command.add_argument("--min-replicas", type=_non_negative, default=default(1),
                         help="0 scales to zero when idle")
    command.add_argument("--max-replicas", type=int, default=None)
    command.add_argument("--scale-on", choices=SCALING_METRICS, help="autoscaling trigger")
    command.add_argument("--target", type=int, help="requests per replica, or CPU percent")
    command.add_argument("--health-path", default=default("/"), help="readiness and liveness path")
    command.add_argument("--visibility", choices=VISIBILITIES, default=default("sso"),
                         help="sso keeps it behind Kionga sign-in")


def _wait_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--wait", action="store_true", help="wait until the application is ready")
    command.add_argument("--timeout", type=float, default=600.0, help="seconds to wait (default 600)")
    command.add_argument("--json", action="store_true")


def _non_negative(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be 0 or more")
    return number


def _spec(args: argparse.Namespace) -> dict[str, Any]:
    if (args.scale_on is None) != (args.target is None):
        raise ValueError("--scale-on and --target go together")
    if not args.git and (args.ref or args.context_dir or args.dockerfile):
        raise ValueError("--ref, --context-dir and --dockerfile need --git")
    env = {}
    for item in args.env or []:
        key, sep, value = item.partition("=")
        if not sep:
            raise ValueError(f"--env {item!r} must be KEY=VALUE")
        env[key] = value
    source = None
    if args.git:
        source = {"git": args.git, "ref": args.ref, "context_dir": args.context_dir,
                  "dockerfile": args.dockerfile}
    return {
        "image": args.image, "source": source, "port": args.port, "env": env or None,
        "secret_refs": args.secrets, "cpu": args.cpu, "memory": args.memory,
        "min_replicas": args.min_replicas, "max_replicas": args.max_replicas,
        "scaling": {"metric": args.scale_on, "target": args.target} if args.scale_on else None,
        "health_path": args.health_path, "visibility": args.visibility,
    }


def apps_deploy(client: Any, args: argparse.Namespace) -> None:
    spec = _spec(args)
    _finish(client, args, client.create_application(args.project, args.name, **spec))


def apps_update(client: Any, args: argparse.Namespace) -> None:
    _finish(client, args, client.update_application(args.application, **_spec(args)))


def apps_redeploy(client: Any, args: argparse.Namespace) -> None:
    _finish(client, args, client.redeploy_application(args.application))


def apps_list(client: Any, args: argparse.Namespace) -> None:
    applications = client.list_applications(args.project)
    if args.json:
        _print_json([application.model_dump(mode="json") for application in applications])
        return
    rows = [(a.name, a.phase or "-", _replicas(a), a.url or "-") for a in applications]
    _table(("NAME", "PHASE", "READY", "URL"), rows)


def apps_get(client: Any, args: argparse.Namespace) -> None:
    application = client.get_application(args.application)
    if args.json:
        _print_json(application.model_dump(mode="json"))
        return
    _describe(application)


def apps_logs(client: Any, args: argparse.Namespace) -> None:
    if args.follow:
        for line in client.stream_application_logs(args.application, tail=args.tail):
            _print_log(line)
        return
    for line in client.application_logs(args.application, tail=args.tail):
        _print_log(line)


def apps_events(client: Any, args: argparse.Namespace) -> None:
    events = client.application_events(args.application)
    if args.json:
        _print_json([event.model_dump(mode="json") for event in events])
        return
    rows = [(e.timestamp.isoformat() if e.timestamp else "-", e.type or "-", e.reason or "-", e.message)
            for e in events]
    _table(("TIME", "TYPE", "REASON", "MESSAGE"), rows)


def apps_delete(client: Any, args: argparse.Namespace) -> None:
    if not args.yes:
        if not sys.stdin.isatty():
            raise ValueError("pass --yes to delete without a terminal to confirm in")
        answer = input(f"Delete application {args.application}? This removes it and its URL. [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Not deleted.")
            return
    client.delete_application(args.application)
    print(f"Deleted {args.application}")


def _finish(client: Any, args: argparse.Namespace, application: Application) -> None:
    if args.wait:
        if not args.json:
            print(f"Waiting for {application.name} to be ready...", file=sys.stderr)
        application = client.wait_until_ready(application.id, timeout=args.timeout)
    if args.json:
        _print_json(application.model_dump(mode="json"))
        return
    _describe(application)


def _describe(application: Application) -> None:
    status = application.status
    source = application.image or (application.source.git if application.source else "-")
    rows = [
        ("Name", application.name), ("ID", application.id), ("Project", application.project_id or "-"),
        ("Phase", status.phase or "-"), ("URL", status.url or "-"), ("Ready", _replicas(application)),
        ("Source", source), ("Visibility", application.visibility or "-"),
    ]
    if application.scaling:
        rows.append(("Scaling", f"{application.scaling.metric} target {application.scaling.target}"))
    if application.failure_reason:
        rows.append(("Reason", application.failure_reason))
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"{label + ':':{width + 1}} {value}")


def _replicas(application: Application) -> str:
    maximum = application.replicas.get("max")
    ready = application.status.ready_replicas
    return f"{ready}/{maximum}" if maximum is not None else str(ready)


def _print_log(line: Any) -> None:
    prefix = f"[{line.pod}] " if line.pod else ""
    print(f"{prefix}{line.message}", flush=True)


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    widths = [max(len(str(row[i])) for row in (header, *rows)) for i in range(len(header))]
    for row in (header, *rows):
        print("  ".join(str(cell).ljust(width) for cell, width in zip(row, widths)).rstrip())


def main(argv: Sequence[str] | None = None, *,
         client_factory: Callable[..., Any] | None = None) -> int:
    """Run the command. ``client_factory`` lets tests pass a client with a mock transport."""
    args = parser().parse_args(argv)
    if client_factory is None:
        from .client import KiongaClient

        client_factory = KiongaClient
    try:
        with client_factory(args.url) as client:
            args.run(client, args)
    except (KiongaError, ApplicationFailed, ApplicationTimeout, ValueError, LookupError) as error:
        print(f"kionga: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
