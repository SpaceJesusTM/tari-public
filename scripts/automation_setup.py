"""Owner CLI for optional native OpenClaw morning briefs and reminders."""
import importlib.util
import json
from pathlib import Path
import sys

path = Path(__file__).with_name("tari_automation.py")
if path.is_symlink():
    raise ValueError("Workflow source must not be a symlink")
spec = importlib.util.spec_from_file_location("tari_workflow_backend", path)
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)

PLUGIN = "tari-workflows"
PLUGIN_PATH = "/opt/tari/plugins/tari-workflows"


def register_parser(sub):
    setup = sub.add_parser("automation", help="Install the fixed-purpose native scheduler adapter").add_subparsers(
        dest="automation_command", required=True)
    setup.add_parser("setup", help="Enable the read-only briefing/reminder tools; create no jobs")
    brief = sub.add_parser("morning-brief", help="Optional native scheduled daily brief").add_subparsers(
        dest="brief_command", required=True)
    enable = brief.add_parser("enable", help="Explicitly register/update and enable the daily brief")
    enable.add_argument("--time", default="09:00", help="Daily local HH:MM")
    enable.add_argument("--timezone", help="IANA timezone; defaults to this installation's timezone")
    enable.add_argument("--model", default="openai/gpt-6.1-sol")
    enable.add_argument("--thinking", choices=backend.THINKING, default="medium")
    enable.add_argument("--channel", default="discord")
    enable.add_argument("--to", required=True, help="Explicit authorized user:ID or channel:ID")
    enable.add_argument("--account", help="Native channel account ID, if using multiple bot accounts")
    for command in ("status", "disable", "history"):
        brief.add_parser(command)
    reminders = sub.add_parser("reminders", help="Opt-in deterministic native one-shot reminders").add_subparsers(
        dest="reminder_command", required=True)
    configure = reminders.add_parser("configure", help="Authorize the default destination; create no jobs")
    configure.add_argument("--channel", default="discord")
    configure.add_argument("--to", required=True, help="Explicit authorized native user:ID or channel:ID")
    configure.add_argument("--account")
    configure.add_argument("--timezone", help="Optional IANA override; otherwise use the installation timezone")
    create = reminders.add_parser("create", help="Create a bounded reminder; delivery uses no model")
    create.add_argument("when", help="ISO date/time or positive duration such as 20m")
    create.add_argument("text")
    create.add_argument("--request-key", help="Reuse for a safely identifiable registration retry")
    listing = reminders.add_parser("list", help="Show upcoming managed reminders and current native status")
    listing.add_argument("--history", action="store_true", help="Also show retained completed/cancelled reminders")
    for command in ("cancel", "history"):
        item = reminders.add_parser(command)
        item.add_argument("reference", help="Actual job ID, unique ID prefix or exact unambiguous reminder text")


def runtime(cli):
    try:
        value = json.loads(cli.safe(cli.LOCAL / "state/openclaw.json").read_text())
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, ValueError, UnicodeError):
        raise ValueError("Run ./tari init and review this installation's configuration") from None


def setup(cli):
    cli.read_env()
    value = runtime(cli)
    plugins = value.setdefault("plugins", {})
    if not isinstance(plugins, dict) or plugins.get("enabled") is False or PLUGIN in plugins.get("deny", []):
        raise ValueError("Workflow tools are blocked by native plugin policy; review that policy explicitly")
    entries = plugins.setdefault("entries", {})
    load = plugins.setdefault("load", {})
    if not isinstance(entries, dict) or not isinstance(load, dict) or not isinstance(load.get("paths", []), list):
        raise ValueError("Invalid native plugin configuration")
    paths = load.setdefault("paths", [])
    if PLUGIN_PATH not in paths:
        paths.append(PLUGIN_PATH)
    if "allow" in plugins:
        if not isinstance(plugins["allow"], list):
            raise ValueError("Invalid native plugin allowlist")
        if PLUGIN not in plugins["allow"]:
            plugins["allow"].append(PLUGIN)
    entries[PLUGIN] = {"enabled": True, "config": {"enabled": True}}
    cli.private_replace(cli.LOCAL / "state/openclaw.json", json.dumps(value, indent=2) + "\n")
    print("Workflow tools configured. No jobs were created. Rebuild/restart this installation with ./tari start to load the updated image/configuration.")
    return 0


def manager(cli, require_tools=False):
    env = cli.read_env()
    value = runtime(cli)
    if require_tools:
        plugins = value.get("plugins", {})
        entry = plugins.get("entries", {}).get(PLUGIN, {}) if isinstance(plugins, dict) else {}
        if plugins.get("enabled") is False or PLUGIN in plugins.get("deny", []) or entry.get("enabled") is not True:
            raise ValueError("Run ./tari automation setup, then ./tari start, before enabling workflow tools")
    return backend.Manager(cli.safe(cli.LOCAL / "state/tari-workflows"), value,
                           env.get("TARI_TIMEZONE", "UTC"), cli.openclaw_json, cli.private_replace)


def dispatch(args, cli):
    if args.command == "automation":
        return setup(cli)
    if args.command == "reminders":
        active = manager(cli, require_tools=args.reminder_command == "configure")
        if args.reminder_command == "configure":
            result = active.configure_reminders(args.channel, args.to, args.account, args.timezone)
        elif args.reminder_command == "create":
            result = active.create_reminder(args.when, args.text, args.request_key)
        elif args.reminder_command == "cancel":
            result = active.cancel_reminder(args.reference)
        elif args.reminder_command == "history":
            result = active.reminder_history(args.reference)
        else:
            result = active.list_reminders(args.history)
        print(json.dumps(result, indent=2))
        return 0
    active = manager(cli, require_tools=args.brief_command == "enable")
    if args.brief_command == "enable":
        result = active.enable_brief(args.time, args.timezone, args.model, args.thinking,
                                     args.channel, args.to, args.account)
    elif args.brief_command == "disable":
        result = active.disable_brief()
    elif args.brief_command == "history":
        record = active.settings().get("morning")
        result = {"entries": active.history(record["job_id"], record["key"])} if record else {"entries": [], "configured": False}
    else:
        result = active.brief_status()
    print(json.dumps(result, indent=2))
    return 1 if result.get("registered") == "unverified" else 0
