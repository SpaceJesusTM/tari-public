"""Small local launcher; no global OpenClaw installation or state is consulted."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import urllib.error
import urllib.request
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"


def safe(path):
    """Reject symlinks before reading or creating any installation-owned path."""
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_relative_to(ROOT) or ".." in path.relative_to(ROOT).parts:
        raise ValueError("Path must be inside this checkout")
    current = ROOT
    for part in path.relative_to(ROOT).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Symlink refused in installation path")
    return path


def private_write(path, data):
    path = safe(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(data)
    path.chmod(0o600)


def private_replace(path, data, mode=0o600):
    """Replace one installation file atomically, using a same-directory temp file."""
    path = safe(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".tari-write-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data.encode() if isinstance(data, str) else data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def exact_text(path):
    with safe(path).open(encoding="utf-8", newline="") as stream:
        return stream.read()


def validate_timezone(zone):
    try:
        ZoneInfo(zone)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError("Use a valid IANA timezone, such as UTC or Europe/London") from None
    return zone


def read_env(validate_zone=True):
    path = safe(LOCAL / "runtime.env")
    if not path.exists():
        raise ValueError("Run ./tari init first")
    result = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError("Invalid runtime.env; use simple KEY=value entries")
        if any(char in value for char in ["\r", "\n", "\x00", "$", "'", '"']):
            raise ValueError("runtime.env values must be unquoted and contain no interpolation")
        result[key] = value
    port = int(result.get("TARI_PORT", "18899"))
    if not 1024 <= port <= 65535:
        raise ValueError("TARI_PORT must be 1024..65535")
    result.setdefault("TARI_TIMEZONE", "UTC")
    if validate_zone:
        validate_timezone(result["TARI_TIMEZONE"])
    return result


def initialize(args):
    validate_timezone(args.timezone)
    if not 1024 <= args.port <= 65535:
        raise ValueError("Port must be 1024..65535")
    if not re.fullmatch(r"[A-Za-z0-9 _-]{1,60}", args.name):
        raise ValueError("Name must be 1..60 simple letters, numbers, spaces, underscores or hyphens")
    if not re.fullmatch(r"[A-Za-z0-9_.:/-]+", args.model):
        raise ValueError("Use a provider/model identifier")
    safe(LOCAL).mkdir(mode=0o700, exist_ok=True)
    for rel in ["state", "state/workspace", "state/workspace/inbox", "google"]:
        safe(LOCAL / rel).mkdir(parents=True, mode=0o700, exist_ok=True)
    env_path = safe(LOCAL / "runtime.env")
    if not env_path.exists():
        env = {
            "COMPOSE_PROJECT_NAME": "tari-public-" + uuid.uuid4().hex[:12],
            "TARI_UID": str(os.getuid()), "TARI_GID": str(os.getgid()),
            "TARI_PORT": str(args.port), "TARI_NAME": args.name,
            "TARI_TIMEZONE": args.timezone, "TARI_MODEL": args.model,
            "OPENCLAW_GATEWAY_TOKEN": secrets.token_hex(32),
            "GOG_KEYRING_PASSWORD": secrets.token_hex(32),
            "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "", "OPENROUTER_API_KEY": "",
            "TARI_GOOGLE_ACCOUNT": "", "TARI_CALENDAR_WRITES": "0",
        }
        private_write(env_path, "# Private to this installation. Do not share.\n" + "".join(f"{k}={v}\n" for k, v in env.items()))
    env = read_env()
    config_path = safe(LOCAL / "state/openclaw.json")
    if not config_path.exists():
        config = json.loads(safe(ROOT / "config/openclaw.json").read_text())
        config["agents"]["defaults"]["model"]["primary"] = env["TARI_MODEL"]
        config["agents"]["defaults"]["userTimezone"] = env["TARI_TIMEZONE"]
        config["agents"]["entries"]["tari"]["name"] = env["TARI_NAME"]
        config["agents"]["entries"]["tari"]["identity"]["name"] = env["TARI_NAME"]
        config["gateway"]["controlUi"]["allowedOrigins"] = [f"http://{host}:{env['TARI_PORT']}" for host in ["localhost", "127.0.0.1"]]
        private_write(config_path, json.dumps(config, indent=2) + "\n")
    count = 0
    for source in sorted(safe(ROOT / "workspace").rglob("*")):
        safe(source)
        if source.is_file():
            target = safe(LOCAL / "state/workspace" / source.relative_to(ROOT / "workspace"))
            if not target.exists():
                data = source.read_text()
                if source.name in {"IDENTITY.md", "USER.md"}:
                    data = data.replace("{{TARI_NAME}}", env["TARI_NAME"]).replace("{{TARI_TIMEZONE}}", env["TARI_TIMEZONE"])
                private_write(target, data)
                if source.parent.name == "bin":
                    target.chmod(0o700)
                count += 1
    print(f"Initialized private .local/; seeded {count} missing workspace files. Existing files preserved.")
    print("Configure model authentication, then ./tari start; see docs/setup.md")


def compose_invocation(*args, validate_zone=True):
    env = read_env(validate_zone=validate_zone)
    project = env.get("COMPOSE_PROJECT_NAME", "")
    if not re.fullmatch(r"tari-public-[a-f0-9]{12}", project):
        raise ValueError("Invalid private Compose project identifier")
    # Do not inherit caller Compose overrides or provider/production credentials.
    process_env = {key: value for key, value in os.environ.items() if not key.startswith(("COMPOSE_", "OPENCLAW_", "GOG_", "TARI_"))}
    process_env.update(env)
    command = ["docker", "compose", "--project-directory", str(ROOT), "--env-file", str(LOCAL / "runtime.env"), "-f", str(ROOT / "compose.yaml")]
    dailygrad = dailygrad_cli()
    if dailygrad and hasattr(dailygrad, "compose_files"):
        for overlay in dailygrad.compose_files(SimpleNamespace(**globals())):
            command.extend(["-f", str(safe(overlay))])
    command.extend(["-p", project, *args])
    return command, process_env


def compose(*args):
    command, process_env = compose_invocation(*args)
    return subprocess.call(command, cwd=ROOT, env=process_env)


def running_service(service="tari"):
    """True/False for this installation only; None if Docker cannot answer."""
    if service not in {"tari", "dailygrad-runner"}:
        raise ValueError("Unknown installation service")
    if not shutil.which("docker"):
        return None
    profile = ["--profile", "dailygrad-runner"] if service == "dailygrad-runner" else []
    command, process_env = compose_invocation(*profile, "ps", "--status", "running", "-q", service, validate_zone=False)
    try:
        result = subprocess.run(command, cwd=ROOT, env=process_env, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return bool(result.stdout.strip()) if result.returncode == 0 else None


def recreate_running(service="tari"):
    """Apply settings only to a running service; None means no restart attempted."""
    if running_service(service) is True:
        profile = ["--profile", "dailygrad-runner"] if service == "dailygrad-runner" else []
        return compose(*profile, "up", "-d", "--no-build", "--force-recreate", service)
    return None


def timezone_user_text(text, zone):
    """Change only our explicit managed stanza; retain the user's other notes."""
    newline = "\r\n" if "\r\n" in text else "\n"
    directive = newline.join([
        f"Prefer {zone} for local dates, scheduling, and daily summaries unless",
        "the user requests another timezone.",
    ])
    directive_pattern = (r"Prefer [A-Za-z0-9_+./-]+ for local dates, scheduling, and daily summaries unless\r?\n"
                         r"the user requests another timezone\.")
    stanza = newline.join([
        "<!-- tari:timezone:start -->",
        "<!-- observed: installation | status: active -->",
        directive,
        "<!-- tari:timezone:end -->",
    ])
    start, end = "<!-- tari:timezone:start -->", "<!-- tari:timezone:end -->"
    if start in text or end in text:
        if text.count(start) != 1 or text.count(end) != 1 or text.index(end) < text.index(start):
            raise ValueError("USER.md has an incomplete timezone marker; repair it before changing timezone")
        lo, hi = text.index(start) + len(start), text.index(end)
        body, count = re.subn(directive_pattern, lambda _: directive, text[lo:hi], count=1)
        if not count:
            body += ("" if body.endswith("\n") else newline) + directive + newline
        return text[:lo] + body + text[hi:]
    legacy = (r"<!-- observed: installation \| status: active -->\r?\n"
              r"Prefer [A-Za-z0-9_+./-]+ for local dates, scheduling, and daily summaries unless\r?\n"
              r"the user requests another timezone\.")
    updated, count = re.subn(legacy, lambda _: stanza, text, count=1)
    if count:
        return updated
    separator = "" if not text else (newline if text.endswith(("\n", "\r")) else newline * 2)
    return text + separator + stanza + newline


def configure_timezone(zone=None):
    if zone is not None:
        validate_timezone(zone)  # Fail before reading or writing installation files.
    env = read_env(validate_zone=zone is None)
    if zone is None:
        print(env["TARI_TIMEZONE"])
        return 0
    env_path = safe(LOCAL / "runtime.env")
    env_text = exact_text(env_path)
    key = "TARI_TIMEZONE=" + zone
    if re.search(r"(?m)^TARI_TIMEZONE=", env_text):
        updated_env = re.sub(r"(?m)^TARI_TIMEZONE=[^\r\n]*", lambda _: key, env_text)
    else:
        newline = "\r\n" if "\r\n" in env_text else "\n"
        updated_env = env_text + ("" if not env_text or env_text.endswith("\n") else newline) + key + newline
    config_path = safe(LOCAL / "state/openclaw.json")
    if not config_path.is_file():
        raise ValueError("Missing installation configuration; run ./tari init first")
    config = json.loads(config_path.read_text())
    if not isinstance(config, dict) or not isinstance(config.get("agents", {}), dict):
        raise ValueError("OpenClaw configuration and agents must be objects")
    defaults = config.setdefault("agents", {}).setdefault("defaults", {})
    if not isinstance(defaults, dict):
        raise ValueError("OpenClaw agents.defaults must be an object")
    defaults["userTimezone"] = zone
    plugins = config.get("plugins", {})
    if not isinstance(plugins, dict) or not isinstance(plugins.get("entries", {}), dict):
        raise ValueError("OpenClaw plugins.entries must be an object")
    dailygrad = plugins.get("entries", {}).get("tari-dailygrad")
    if dailygrad is not None:
        if not isinstance(dailygrad, dict) or not isinstance(dailygrad.get("config", {}), dict):
            raise ValueError("DailyGrad plugin configuration must be an object")
        dailygrad.setdefault("config", {})["timeZone"] = zone
    user_path = safe(LOCAL / "state/workspace/USER.md")
    user_text = exact_text(user_path) if user_path.exists() else "# User preferences\n"
    updated_user = timezone_user_text(user_text, zone)
    # All parsing and path checks happen before the first write. No secrets are printed.
    running = running_service()
    dailygrad_enabled = dailygrad is not None and dailygrad.get("enabled") is True \
        and dailygrad.get("config", {}).get("enabled", True) is not False
    runner_running = running_service("dailygrad-runner") if dailygrad_enabled else False
    changes = [(env_path, updated_env), (config_path, json.dumps(config, indent=2) + "\n"),
               (user_path, updated_user)]
    for path, content in changes:
        if not path.exists() or exact_text(path) != content:
            private_replace(path, content)
    result = 0
    if running:
        result = compose("up", "-d", "--no-build", "--force-recreate", "tari")
        if result:
            print(f"Saved timezone {zone}; container recreation failed. Retry ./tari timezone {zone}.", file=sys.stderr)
        else:
            print(f"Timezone: {zone}. Recreated the running Tari container.")
    elif running is False:
        print(f"Timezone: {zone}. The stopped container will use it on ./tari start.")
    else:
        print(f"Timezone: {zone}. Docker status unavailable; ./tari start will apply it to the container.")
    if runner_running:
        runner_result = compose("--profile", "dailygrad-runner", "up", "-d", "--no-build", "--force-recreate", "dailygrad-runner")
        if runner_result:
            print(f"Saved timezone {zone}; optional runner recreation failed. Retry ./tari timezone {zone}.", file=sys.stderr)
        else:
            print("Recreated the running DailyGrad runner with the configured timezone; no DailyGrad job was requested.")
        result = result or runner_result
    return result


def workspace_update(instructions=False):
    """Refresh distributed helpers and skills, leaving personal state alone."""
    read_env()  # Require an existing installation; do not create one implicitly.
    template = safe(ROOT / "workspace")
    candidates = []
    for section in ("bin", "skills"):
        directory = safe(template / section)
        if directory.exists():
            for source in sorted(directory.rglob("*")):
                safe(source)
                if source.is_file():
                    candidates.append(source)
    if instructions:
        candidates.extend(safe(template / name) for name in ("AGENTS.md", "SOUL.md", "TOOLS.md", "HEARTBEAT.md")
                          if safe(template / name).is_file())
    updates = []
    for source in candidates:
        target = safe(LOCAL / "state/workspace" / source.relative_to(template))
        if target.exists() and not target.is_file():
            raise ValueError("A workspace update target is not a file")
        data = source.read_bytes()
        mode = 0o700 if source.parent.name == "bin" else 0o600
        if not target.exists() or target.read_bytes() != data or target.stat().st_mode & 0o777 != mode:
            updates.append((target, data, mode))
    for target, data, mode in updates:
        private_replace(target, data, mode)
    print(f"Updated {len(updates)} distributed workspace files. Personal state and user-added files preserved.")
    if instructions:
        print("Replaced AGENTS.md, SOUL.md, TOOLS.md, and HEARTBEAT.md from this checkout's templates.")
    return 0


def dailygrad_cli():
    path = safe(ROOT / "scripts/dailygrad_setup.py")
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("tari_dailygrad_setup", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def chat_request(message, session, env=None):
    env = env or read_env()
    timeout = int(env.get("TARI_CHAT_TIMEOUT_SECONDS", "660"))
    if not 1 <= timeout <= 900:
        raise ValueError("TARI_CHAT_TIMEOUT_SECONDS must be 1..900")
    payload = {"model": "openclaw/tari", "user": session, "messages": [{"role": "user", "content": message}]}
    request = urllib.request.Request(
        f"http://127.0.0.1:{env['TARI_PORT']}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + env["OPENCLAW_GATEWAY_TOKEN"], "Content-Type": "application/json"},
    )
    # Ignore proxy environment settings for local credentials.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            result = json.load(response)
        try:
            content = result["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError()
            return content
        except (KeyError, IndexError, TypeError):
            raise ValueError("Gateway returned an unexpected chat response; inspect ./tari logs locally") from None
    except urllib.error.HTTPError as exc:
        raise ValueError(f"Gateway returned HTTP {exc.code}; inspect ./tari logs locally") from None


def model_auth_status():
    """Inspect this installation's configured routes, never probe model inference.

    OpenClaw supports env/config secrets, managed OAuth/API-key profiles, and local
    providers. Its --check result is authoritative; env-key presence alone is not.
    Never relay raw stdout/stderr, which can contain credential-source details.
    """
    try:
        command, environment = compose_invocation(
            "exec", "-T", "tari", "openclaw", "models", "status",
            "--agent", "tari", "--json", "--check",
        )
        result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=45)
        status = json.loads(result.stdout)
        auth = status.get("auth") if isinstance(status, dict) else None
        if not isinstance(auth, dict) or not isinstance(status.get("resolvedDefault"), str):
            raise ValueError()
        routes = auth.get("runtimeAuthRoutes", [])
        issues = auth.get("modelRouteIssues", [])
        if not isinstance(routes, list) or not isinstance(issues, list):
            raise ValueError()
        states = {row.get("status") for row in routes if isinstance(row, dict) and isinstance(row.get("status"), str)}
        issue_codes = {row.get("code") for row in issues if isinstance(row, dict) and isinstance(row.get("code"), str)}
        if "incompatible" in states or issue_codes & {"incompatible", "unknown_provider", "unknown_model", "unsupported_provider", "unsupported_model"}:
            return "unsupported", "a configured model route is unsupported or incompatible"
        if "unavailable" in states:
            return "unavailable", "a configured model runtime is unavailable"
        if "expired" in states:
            return "expired", "authentication for a configured route has expired"
        if auth.get("missingProvidersInUse") or "missing" in states:
            return "missing", "credentials are missing for a configured model route"
        if result.returncode == 2 or "expiring" in states:
            return "expiring", "a selected credential expires soon"
        if result.returncode == 0 and not issues and not states & {"indeterminate", "unknown"}:
            return "configured", "OpenClaw auth/config check passed; live inference unverified"
        return "unverified", "OpenClaw could not confirm configured-route readiness"
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return "unverified", "could not inspect OpenClaw model status; start Tari and retry"


def doctor():
    env = read_env()
    failures = []
    if not shutil.which("docker"):
        failures.append("Docker CLI missing")
    else:
        try:
            version = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"], cwd=ROOT, capture_output=True, text=True, timeout=10)
            daemon_ok = version.returncode == 0 and bool(version.stdout.strip())
        except (OSError, subprocess.TimeoutExpired):
            daemon_ok = False
        if not daemon_ok:
            failures.append("Docker daemon unavailable")
    for rel in ["state/openclaw.json", "state/workspace/AGENTS.md", "state/workspace/bin/day-planner"]:
        if not safe(LOCAL / rel).is_file():
            failures.append("Missing runtime file: " + rel)
    config_path = safe(LOCAL / "state/openclaw.json")
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text())
            if config.get("agents", {}).get("defaults", {}).get("userTimezone") != env["TARI_TIMEZONE"]:
                failures.append("OpenClaw timezone differs from TARI_TIMEZONE; run ./tari timezone " + env["TARI_TIMEZONE"])
            dailygrad = config.get("plugins", {}).get("entries", {}).get("tari-dailygrad")
            if dailygrad and dailygrad.get("config", {}).get("timeZone") != env["TARI_TIMEZONE"]:
                failures.append("DailyGrad timezone differs from TARI_TIMEZONE; run ./tari timezone " + env["TARI_TIMEZONE"])
        except (ValueError, AttributeError):
            failures.append("Installation configuration is not valid JSON object configuration")
    print("Private state: .local/ | agent: tari | loopback port: " + env["TARI_PORT"])
    print("Timezone: " + env["TARI_TIMEZONE"] + " (TARI_TIMEZONE; default UTC)")
    print("Google account configured: " + ("yes" if env.get("TARI_GOOGLE_ACCOUNT") else "no (optional)"))
    auth_state, auth_detail = model_auth_status()
    print("Model auth: " + auth_state + " — " + auth_detail)
    if auth_state != "configured":
        failures.append("Model readiness needs attention; inspect ./tari openclaw models status --agent tari")
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{env['TARI_PORT']}/healthz", timeout=3) as response:
            if response.status != 200:
                failures.append("Gateway health probe failed")
        print("Gateway health: reachable (model and Google access require separate verification)")
    except OSError:
        failures.append("Gateway unavailable; start it with ./tari start")
    for failure in failures:
        print("CHECK: " + failure)
    return 1 if failures else 0


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Create missing private files, never overwrite")
    init.add_argument("--timezone", default="UTC")
    init.add_argument("--model", default="openai/gpt-6.1-sol")
    init.add_argument("--name", default="Tari")
    init.add_argument("--port", type=int, default=18899)
    timezone_parser = sub.add_parser("timezone", help="Show or change the installation's IANA timezone (default UTC)")
    timezone_parser.add_argument("zone", nargs="?")
    update_parser = sub.add_parser("workspace-update", help="Replace distributed helpers and skills; preserve personal state")
    update_parser.add_argument("--instructions", action="store_true",
                               help="Also replace AGENTS.md, SOUL.md, TOOLS.md and HEARTBEAT.md templates")
    dailygrad = dailygrad_cli()
    if dailygrad:
        dailygrad.register_parser(sub)
    for name in ["build", "start", "stop", "status", "logs", "doctor", "token"]:
        sub.add_parser(name)
    chat = sub.add_parser("chat", help="Interactive chat; /quit exits")
    chat.add_argument("message", nargs="?")
    chat.add_argument("--session", default="local-cli", help="Reuse per conversation; choose another value for a separate thread")
    for name in ["exec", "openclaw", "gog"]:
        command = sub.add_parser(name, help="Run inside this installation's running container")
        command.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command == "init":
        initialize(args)
    elif args.command == "timezone":
        return configure_timezone(args.zone)
    elif args.command == "workspace-update":
        return workspace_update(args.instructions)
    elif args.command == "dailygrad":
        return dailygrad.dispatch(args, SimpleNamespace(**globals()))
    elif args.command == "doctor":
        return doctor()
    elif args.command == "token":
        print(read_env()["OPENCLAW_GATEWAY_TOKEN"])
    elif args.command == "chat":
        if args.message:
            print(chat_request(args.message, args.session))
        else:
            print("Tari chat. /quit exits. Session: " + args.session)
            while True:
                try:
                    message = input("You: ").strip()
                except EOFError:
                    break
                if message == "/quit":
                    break
                if message:
                    print("Tari: " + chat_request(message, args.session))
    elif args.command in {"exec", "openclaw", "gog"}:
        extra = args.args[1:] if args.args[:1] == ["--"] else args.args
        executable = [] if args.command == "exec" else [args.command]
        return compose("exec", "tari", *executable, *extra)
    elif args.command == "start":
        return compose("up", "-d", "--build", "tari")
    else:
        commands = {"build": ["build", "tari"], "stop": ["down", "--remove-orphans", "--timeout", "360"], "status": ["ps"], "logs": ["logs", "--tail", "100", "tari"]}
        return compose(*commands[args.command])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, ZoneInfoNotFoundError) as error:
        print(f"Tari: {error}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)
