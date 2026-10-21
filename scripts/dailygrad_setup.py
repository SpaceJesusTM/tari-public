"""Owner-side optional integration setup. Never imported by the agent plugin."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import sys
from urllib.parse import urlsplit

try:
    import fcntl
except ImportError:  # Core commands must still import when optional Unix locking is unavailable.
    fcntl = None

PLUGIN_ID = "tari-dailygrad"
PLUGIN_PATH = "/opt/tari-plugins/tari-dailygrad"
CAPABILITIES = {"allow_run": "allowRun", "allow_repair": "allowRepair",
                "allow_sources_update": "allowSourcesUpdate", "allow_leetcode_update": "allowLeetcodeUpdate"}


def register_parser(sub):
    commands = sub.add_parser("dailygrad", help="Configure the optional standalone DailyGrad integration").add_subparsers(dest="dailygrad_command", required=True)
    configure = commands.add_parser("configure", help="Enable digest reads; mutations require individual opt-ins")
    configure.add_argument("--data-dir", required=True, help="Selected DailyGrad output directory; existing external directory or a path inside .local/")
    configure.add_argument("--ollama-url", default="http://host.docker.internal:11434")
    configure.add_argument("--ollama-model", default="qwen3.5:4b-q4_K_M")
    for option in CAPABILITIES:
        configure.add_argument("--" + option.replace("_", "-"), action="store_true")
    for name in ["status", "disable", "runner-start", "runner-stop", "runner-logs"]:
        commands.add_parser(name)


def location(cli):
    return cli.safe(cli.LOCAL / "dailygrad")


def write_json(cli, path, value):
    path = cli.safe(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, indent=2)
        stream.write("\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def settings(cli):
    path = cli.safe(location(cli) / "settings.json")
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or not isinstance(value.get("enabled"), bool):
        raise ValueError("Invalid optional DailyGrad settings; configure it again")
    if value["enabled"]:
        if not isinstance(value.get("dataDir"), str) or not value["dataDir"]:
            raise ValueError("Invalid optional DailyGrad data directory; configure it again")
        capabilities = value.get("capabilities", {})
        if not isinstance(capabilities, dict) or any(not isinstance(capabilities.get(key, False), bool)
                                                    for key in CAPABILITIES.values()):
            raise ValueError("Invalid optional DailyGrad capabilities; configure it again")
        value["capabilities"] = {key: capabilities.get(key, False) for key in CAPABILITIES.values()}
    return value


def selected_directory(raw, cli, create=False):
    if not isinstance(raw, str) or "$" in raw or any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise ValueError("DailyGrad data path must not contain interpolation or control characters")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = cli.ROOT / path
    if ".." in path.parts:
        raise ValueError("DailyGrad data path must not contain parent traversal")
    if cli.ROOT.is_relative_to(path) or cli.LOCAL.is_relative_to(path):
        raise ValueError("Select only the DailyGrad output directory, not an installation root or its ancestor")
    for parent in [*reversed(path.parents), path]:
        if parent.is_symlink():
            raise ValueError("DailyGrad data path must not use symlinks")
    for marker in (".git", "pyproject.toml", "package.json", "openclaw.json", "runtime.env", ".env", "AGENTS.md"):
        candidate = path / marker
        if candidate.is_symlink() or candidate.exists():
            raise ValueError("Select only the DailyGrad output directory, not a checkout or configuration directory")
    if not path.exists() and create:
        # Setup never creates directories in an unrelated project.
        if not path.is_relative_to(cli.LOCAL):
            raise ValueError("External DailyGrad data directory must already exist; select only its output directory")
        cli.safe(path).mkdir(mode=0o700, parents=True)
    return path


def runtime_configuration(cli):
    path = cli.safe(cli.LOCAL / "state/openclaw.json")
    runtime = json.loads(path.read_text())
    if not isinstance(runtime, dict) or not isinstance(runtime.get("plugins", {}), dict):
        raise ValueError("OpenClaw plugins configuration must be an object")
    plugins = runtime.setdefault("plugins", {})
    if not isinstance(plugins.get("load", {}), dict) or not isinstance(plugins.get("entries", {}), dict) \
            or not isinstance(plugins.get("load", {}).get("paths", []), list) \
            or not isinstance(plugins.get("allow", []), list):
        raise ValueError("OpenClaw plugin entries, load paths, and allow list have invalid types")
    return path, runtime, plugins


@contextmanager
def configuration_lock(cli):
    """Serialize owner configuration so one caller cannot restart another's old policy."""
    if fcntl is None:
        raise ValueError("DailyGrad configuration requires Unix file locking; use macOS, Linux, or WSL")
    lock_path = cli.safe(cli.LOCAL / "dailygrad.configure.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("DailyGrad configuration is already being changed; retry after it finishes") from error
        # Retain the inode: deleting a lock file lets concurrent callers lock different files.
        yield


def runner_state(cli):
    try:
        return cli.running_service("dailygrad-runner")
    except (OSError, ValueError):
        return None


def stop_runner_verified(cli, overlay=None):
    """Stop this installation only; a zero command status alone does not prove revocation."""
    prefix = ["-f", str(overlay)] if overlay is not None else []
    try:
        cli.compose(*prefix, "--profile", "dailygrad-runner", "stop", "dailygrad-runner")
    except (OSError, ValueError):
        pass
    return runner_state(cli) is False


def configure(args, cli):
    # Reject bad input before changing services, creating a lock, or replacing saved policy.
    cli.read_env()
    if any(ord(char) < 32 or ord(char) == 127 for char in args.ollama_url):
        raise ValueError("Ollama URL must not contain control characters")
    url = urlsplit(args.ollama_url)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("Use an http(s) Ollama base URL without embedded credentials")
    if not args.ollama_model or len(args.ollama_model) > 200 or any(ord(c) < 32 for c in args.ollama_model):
        raise ValueError("Invalid Ollama model identifier")
    runtime_configuration(cli)
    data = selected_directory(args.data_dir, cli, create=True)
    if not data.is_dir():
        raise ValueError("Select a DailyGrad output directory")
    with configuration_lock(cli):
        return configure_locked(args, cli, data)


def configure_locked(args, cli, data):
    env = cli.read_env()
    path, runtime, plugins = runtime_configuration(cli)
    base = location(cli)
    saved_settings = cli.safe(base / "settings.json")
    old_overlay = cli.safe(base / "compose.json")
    prior_setup = saved_settings.exists() or old_overlay.exists() or PLUGIN_ID in plugins.get("entries", {})
    was_running = runner_state(cli)
    # First-time offline configuration is safe: this installation has never configured
    # a runner. Once any retained setup exists, unknown state must be quiesced too.
    if was_running is True or (was_running is None and prior_setup):
        if not stop_runner_verified(cli, old_overlay if old_overlay.is_file() else None):
            print("DailyGrad configuration was not applied: the optional runner could not be confirmed stopped. "
                  "Existing runner permissions may still be active; restore Docker access and stop it before retrying.",
                  file=sys.stderr)
            return 1

    try:
        apply_saved_configuration(args, cli, data, env, path, runtime, plugins, base)
    except (OSError, ValueError, KeyError, TypeError):
        print("DailyGrad configuration could not be fully saved or applied. The optional runner was not restarted; "
              "correct the setup and run configure again.", file=sys.stderr)
        return 1

    try:
        applied = cli.recreate_running()
    except (OSError, ValueError):
        applied = 1
    if applied:
        print("Saved DailyGrad settings, but Tari container recreation failed. The optional runner was not restarted; "
              "retry ./tari start and configure when Docker is available.", file=sys.stderr)
        return applied

    if was_running is True:
        # Force recreation also refreshes the single-file TOML bind mount after atomic
        # replacement, even when capability flags and the Compose path did not change.
        overlay = cli.safe(base / "compose.json")
        try:
            result = cli.compose("-f", str(overlay), "--profile", "dailygrad-runner", "up", "-d",
                                 "--no-build", "--force-recreate", "dailygrad-runner")
        except (OSError, ValueError):
            result = 1
        if result or runner_state(cli) is not True:
            stopped = stop_runner_verified(cli, overlay)
            message = "DailyGrad runner reconciliation failed. "
            message += ("The optional runner is confirmed stopped; saved settings remain for a later configure attempt."
                        if stopped else "Runner shutdown could not be verified; do not rely on permission revocation "
                        "until this installation's optional runner is confirmed stopped.")
            print(message, file=sys.stderr)
            return result or 1

    print("DailyGrad configured. Digest reads are enabled; runner operations require their individual opt-ins.")
    if applied is None:
        print("No running Tari container was recreated; ./tari start applies the saved integration settings.")
    else:
        print("Recreated the running Tari container to apply integration settings.")
    if was_running is True:
        print("Recreated the optional runner with the saved settings; no digest generation was requested.")
    else:
        print("No optional runner was started. Start it explicitly with ./tari dailygrad runner-start.")
    return 0


def apply_saved_configuration(args, cli, data, env, path, runtime, plugins, base):
    base.mkdir(mode=0o700, exist_ok=True)
    cli.safe(base / "runner-state").mkdir(mode=0o700, exist_ok=True)
    # JSON string literals are valid TOML basic string literals for these values.
    config_text = "data_dir = \"/data\"\n\n[ollama]\nurl = " + json.dumps(args.ollama_url) + "\nmodel = " + json.dumps(args.ollama_model) + "\n"
    config_file = cli.safe(base / "dailygrad.toml")
    # Only this integration's managed config is replaced; never the standalone checkout's file.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=base, delete=False) as stream:
        temp = Path(stream.name)
        stream.write(config_text)
    temp.chmod(0o600)
    temp.replace(config_file)
    value = {"enabled": True, "dataDir": str(data),
             "capabilities": {key: bool(getattr(args, option)) for option, key in CAPABILITIES.items()}}
    write_json(cli, base / "settings.json", value)
    paths = plugins.setdefault("load", {}).setdefault("paths", [])
    if PLUGIN_PATH not in paths:
        paths.append(PLUGIN_PATH)
    if "allow" in plugins and PLUGIN_ID not in plugins["allow"]:
        plugins["allow"].append(PLUGIN_ID)
    plugins.setdefault("entries", {})[PLUGIN_ID] = {"enabled": True, "config": {
        "enabled": True, "dataDir": "/mnt/dailygrad", "runnerSocket": "/run/tari-dailygrad/runner.sock",
        "runWaitSeconds": 570, "timeZone": env.get("TARI_TIMEZONE", "UTC"), **value["capabilities"]}}
    write_json(cli, path, runtime)
    # Configuration must validate fully here; the core-only degraded overlay is for
    # ordinary startup, not evidence that runner policy was successfully reconciled.
    configured_compose_files(cli)


def compose_files(cli):
    try:
        return configured_compose_files(cli)
    except (ValueError, OSError, KeyError, TypeError):
        # Keep the discovered plugin code available even if optional settings or
        # data disappeared. It can then report an honest unavailable tool result.
        print("DailyGrad optional setup is unavailable; core Tari will start without its runner/data mount.", file=sys.stderr)
        # Keep this outside dailygrad/ so a broken/symlinked optional directory
        # cannot prevent a safe core-only fallback or require following the link.
        path = cli.safe(cli.LOCAL / "dailygrad.degraded-compose.json")
        overlay = {"services": {"tari": {"volumes": [{"type": "bind", "source": str(cli.safe(cli.ROOT / "plugins/tari-dailygrad")),
                    "target": PLUGIN_PATH, "read_only": True, "bind": {"create_host_path": False}}]}}}
        write_json(cli, path, overlay)
        return [str(path)]


def configured_compose_files(cli):
    value = settings(cli)
    if not value or not value["enabled"]:
        runtime_path = cli.safe(cli.LOCAL / "state/openclaw.json")
        runtime = json.loads(runtime_path.read_text())
        if runtime.get("plugins", {}).get("entries", {}).get(PLUGIN_ID, {}).get("enabled"):
            raise ValueError("Optional integration settings are absent but the plugin is configured")
        return []
    base, env = location(cli), cli.read_env()
    data = selected_directory(value["dataDir"], cli)
    plugin = cli.safe(cli.ROOT / "plugins/tari-dailygrad")
    config_file = cli.safe(base / "dailygrad.toml")
    state = cli.safe(base / "runner-state")
    capabilities = value.get("capabilities", {})
    agent_volumes = [{"type": "bind", "source": str(plugin), "target": PLUGIN_PATH, "read_only": True,
                      "bind": {"create_host_path": False}},
                     {"type": "volume", "source": "dailygrad-socket", "target": "/run/tari-dailygrad", "read_only": True}]
    # A disappearing optional output directory must not stop the core Gateway.
    if data.is_dir():
        agent_volumes.append({"type": "bind", "source": str(data), "target": "/mnt/dailygrad", "read_only": True,
                              "bind": {"create_host_path": False}})
    command = ["--repo", "/opt/dailygrad", "--bin", "/usr/local/bin/dailygrad", "--data-dir", "/data",
               "--runner-dir", "/var/lib/tari-dailygrad/private", "--socket-dir", "/run/tari-dailygrad",
               "--timezone", env.get("TARI_TIMEZONE", "UTC")]
    for option, key in CAPABILITIES.items():
        if capabilities.get(key) is True:
            command.append("--" + option.replace("_", "-"))
    runner = {"profiles": ["dailygrad-runner"], "image": "tari-public-dailygrad:0.2.0",
              "build": {"context": str(cli.ROOT), "dockerfile": "docker/Dockerfile.dailygrad"},
              "user": env.get("TARI_UID", "1000") + ":" + env.get("TARI_GID", "1000"),
              "init": True, "command": command, "environment": {"TZ": env.get("TARI_TIMEZONE", "UTC")},
              "volumes": [{"type": "bind", "source": str(data), "target": "/data", "bind": {"create_host_path": False}},
                          {"type": "bind", "source": str(config_file), "target": "/opt/dailygrad/dailygrad.toml", "read_only": True,
                           "bind": {"create_host_path": False}},
                          {"type": "bind", "source": str(state), "target": "/var/lib/tari-dailygrad", "bind": {"create_host_path": False}},
                          {"type": "volume", "source": "dailygrad-socket", "target": "/run/tari-dailygrad"}],
              "read_only": True, "tmpfs": ["/tmp:rw,nosuid,nodev,size=268435456,mode=1777"],
              "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"], "stop_grace_period": "30s",
              "extra_hosts": ["host.docker.internal:host-gateway"], "restart": "unless-stopped"}
    overlay = {"services": {"tari": {"volumes": agent_volumes}, "dailygrad-runner": runner},
               "volumes": {"dailygrad-socket": {}}}
    path = base / "compose.json"
    write_json(cli, path, overlay)
    return [str(path)]


def disable(cli):
    try:
        value = settings(cli)
    except (ValueError, OSError, TypeError):
        value = None
    path = cli.safe(cli.LOCAL / "state/openclaw.json")
    runtime = json.loads(path.read_text())
    if not isinstance(runtime, dict):
        raise ValueError("OpenClaw configuration must be an object; no integration settings changed")
    plugins = runtime.get("plugins", {})
    if not isinstance(plugins, dict) or not isinstance(plugins.get("entries", {}), dict) \
            or not isinstance(plugins.get("load", {}), dict) \
            or not isinstance(plugins.get("load", {}).get("paths", []), list) \
            or not isinstance(plugins.get("allow", []), list):
        raise ValueError("OpenClaw plugin configuration has invalid types; no integration settings changed")
    configured = PLUGIN_ID in plugins.get("entries", {}) \
        or PLUGIN_PATH in plugins.get("load", {}).get("paths", []) or PLUGIN_ID in plugins.get("allow", [])
    if not configured and (not value or not value["enabled"]):
        print("DailyGrad is not enabled; core Tari is unaffected.")
        return 0
    # Stop only the isolated optional service. No other DailyGrad installation is touched.
    stop_prefix = []
    if not value or not value["enabled"]:
        retained = cli.safe(location(cli) / "compose.json")
        try:
            overlay = json.loads(retained.read_text())
            if isinstance(overlay.get("services", {}).get("dailygrad-runner"), dict):
                stop_prefix = ["-f", str(retained)]
        except (ValueError, OSError, AttributeError):
            pass
    try:
        code = cli.compose(*stop_prefix, "--profile", "dailygrad-runner", "stop", "dailygrad-runner")
    except (ValueError, OSError):
        code = 1
    if code:
        print("Runner stop could not be verified; stop this installation's optional service when Docker is available.")
    value = value or {}
    value["enabled"] = False
    write_json(cli, location(cli) / "settings.json", value)
    plugins.get("entries", {}).pop(PLUGIN_ID, None)
    paths = plugins.get("load", {}).get("paths", [])
    if PLUGIN_PATH in paths:
        paths.remove(PLUGIN_PATH)
    if PLUGIN_ID in plugins.get("allow", []):
        plugins["allow"].remove(PLUGIN_ID)
    write_json(cli, path, runtime)
    applied = cli.recreate_running()
    if applied:
        print("DailyGrad was removed from saved configuration, but Tari container recreation failed; retry ./tari start.", file=sys.stderr)
    elif applied is None:
        print("DailyGrad disabled in saved configuration; ./tari start applies disconnected mounts. Data is preserved.")
    else:
        print("DailyGrad disabled; recreated the running Tari container without integration mounts. Data is preserved.")
    return applied or code or 0


def dispatch(args, cli):
    action = args.dailygrad_command
    if action == "configure":
        return configure(args, cli)
    if action == "disable":
        return disable(cli)
    value = settings(cli)
    if action == "status":
        if not value or not value["enabled"]:
            print("DailyGrad: disabled (optional)")
        else:
            data = selected_directory(value["dataDir"], cli)
            latest = data / "latest.json"
            print(json.dumps({"enabled": True, "data_available": data.is_dir(),
                              "latest_file_present": not latest.is_symlink() and latest.is_file(),
                              "capabilities": value["capabilities"],
                              "runner": "Use ./tari status; digest reads do not require the runner"}, indent=2))
        return 0
    if not value or not value["enabled"]:
        raise ValueError("DailyGrad is disabled; configure it explicitly first")
    if action == "runner-start":
        if not selected_directory(value["dataDir"], cli).is_dir():
            raise ValueError("DailyGrad data directory is unavailable; core Tari remains usable")
        return cli.compose("--profile", "dailygrad-runner", "up", "-d", "--build", "dailygrad-runner")
    if action == "runner-stop":
        return cli.compose("--profile", "dailygrad-runner", "stop", "dailygrad-runner")
    return cli.compose("--profile", "dailygrad-runner", "logs", "--tail", "100", "dailygrad-runner")
