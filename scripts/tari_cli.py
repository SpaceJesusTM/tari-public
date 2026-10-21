"""Small local launcher; no global OpenClaw installation or state is consulted."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
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


def read_env():
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
    return result


def initialize(args):
    ZoneInfo(args.timezone)
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
    print("Add your model API key to .local/runtime.env, then ./tari start")


def compose(*args):
    env = read_env()
    project = env.get("COMPOSE_PROJECT_NAME", "")
    if not re.fullmatch(r"tari-public-[a-f0-9]{12}", project):
        raise ValueError("Invalid private Compose project identifier")
    # Do not inherit caller Compose overrides or provider/production credentials.
    process_env = {key: value for key, value in os.environ.items() if not key.startswith(("COMPOSE_", "OPENCLAW_", "GOG_", "TARI_"))}
    process_env.update(env)
    command = ["docker", "compose", "--project-directory", str(ROOT), "--env-file", str(LOCAL / "runtime.env"), "-f", str(ROOT / "compose.yaml"), "-p", project, *args]
    return subprocess.call(command, cwd=ROOT, env=process_env)


def chat_request(message, session, env=None):
    env = env or read_env()
    payload = {"model": "openclaw/tari", "user": session, "messages": [{"role": "user", "content": message}]}
    request = urllib.request.Request(
        f"http://127.0.0.1:{env['TARI_PORT']}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + env["OPENCLAW_GATEWAY_TOKEN"], "Content-Type": "application/json"},
    )
    # Ignore proxy environment settings for local credentials.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=300) as response:
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
    if not any(env.get(key) for key in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"]):
        failures.append("No model API key configured (use OpenClaw provider auth for other models)")
    for rel in ["state/openclaw.json", "state/workspace/AGENTS.md", "state/workspace/bin/day-planner"]:
        if not safe(LOCAL / rel).is_file():
            failures.append("Missing runtime file: " + rel)
    print("Private state: .local/ | agent: tari | loopback port: " + env["TARI_PORT"])
    print("Google account configured: " + ("yes" if env.get("TARI_GOOGLE_ACCOUNT") else "no (optional)"))
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
        env = read_env()
        provider = env["TARI_MODEL"].split("/", 1)[0]
        key_name = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "openrouter": "OPENROUTER_API_KEY"}.get(provider)
        if key_name and not env.get(key_name):
            print(f"No {key_name} in .local/runtime.env; chat needs your provider credentials.", file=sys.stderr)
        return compose("up", "-d", "--build", "tari")
    else:
        commands = {"build": ["build", "tari"], "stop": ["down", "--timeout", "360"], "status": ["ps"], "logs": ["logs", "--tail", "100", "tari"]}
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
