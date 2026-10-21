#!/usr/bin/env python3
"""Opt-in Docker checks in a disposable checkout-local fixture, without credentials.

Build the Tari image first. --dailygrad also requires its optional runner image.
No installed .local/ state is read. No model, news run, repair, or Google API is called.
"""
import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from types import SimpleNamespace
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dailygrad", action="store_true")
    args = parser.parse_args()
    base = ROOT / ".test-tmp"
    base.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="readiness-", dir=base) as temporary:
        root = Path(temporary)
        for directory in ["workspace", "plugins", "config", "scripts"]:
            shutil.copytree(ROOT / directory, root / directory, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copyfile(ROOT / "compose.yaml", root / "compose.yaml")
        spec = importlib.util.spec_from_file_location("readiness_cli", ROOT / "scripts/tari_cli.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        cli.ROOT, cli.LOCAL = root, root / ".local"
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        cli.initialize(SimpleNamespace(timezone="Europe/Berlin", port=port, name="Tari", model="openai/gpt-6.1-sol"))
        env = cli.read_env()
        project = env["COMPOSE_PROJECT_NAME"]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def compose(*arguments, check=True):
            command, environment = cli.compose_invocation(*arguments)
            result = subprocess.run(command, cwd=root, env=environment, capture_output=True, text=True, timeout=180)
            if check and result.returncode:
                # All fixture output is fictional; generated token is never printed.
                raise RuntimeError("Fixture Compose command failed: " + " ".join(arguments[:4]) + "\n" + result.stderr[-3000:])
            return result

        def request(path, payload=None, auth=True):
            headers = {"Authorization": "Bearer " + env["OPENCLAW_GATEWAY_TOKEN"]} if auth else {}
            if payload is not None:
                headers["Content-Type"] = "application/json"
            call = urllib.request.Request(f"http://127.0.0.1:{port}" + path,
                data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
            with opener.open(call, timeout=60) as response:
                return json.load(response)

        def ready():
            for _ in range(45):
                try:
                    request("/healthz", auth=False)
                    return
                except (OSError, ValueError):
                    time.sleep(1)
            logs = compose("logs", "--tail", "40", "tari", check=False)
            raise RuntimeError("Fixture Gateway did not become healthy\n" + logs.stdout[-5000:])

        def invoke(name, arguments):
            envelope = request("/tools/invoke", {"tool": name, "args": arguments, "sessionKey": "agent:tari:main"})
            if not envelope.get("ok"):
                raise RuntimeError("Gateway tool invocation failed: " + name)
            return envelope["result"]["details"]

        try:
            compose("up", "-d", "--no-build", "tari")
            ready()
            try:
                request("/v1/models", auth=False)
                raise AssertionError("Anonymous API access unexpectedly succeeded")
            except urllib.error.HTTPError as error:
                assert error.code == 401
            assert "openclaw/tari" in [item["id"] for item in request("/v1/models")["data"]]
            compose("exec", "-T", "tari", "openclaw", "config", "validate")
            identity = compose("exec", "-T", "tari", "cat", "IDENTITY.md").stdout
            assert "Tari" in identity
            compose("exec", "-T", "tari", "./bin/day-planner", "add", "tomorrow", "13:00-14:00", "Fictional prototype", "--no-calendar")
            shown = json.loads(compose("exec", "-T", "tari", "./bin/day-planner", "show", "tomorrow", "--json", "--no-calendar").stdout)
            assert shown["schedule"][0]["title"] == "Fictional prototype"
            inbox = cli.LOCAL / "state/workspace/inbox"
            (inbox / "demo.md").write_text("# Fictional source\nA tentative prototype review.\n")
            imported = json.loads(compose("exec", "-T", "tari", "./bin/document-import", "import", "inbox/demo.md").stdout)
            assert imported["document"]["memory_saved"] is False
            context = json.loads(compose("exec", "-T", "tari", "./bin/tari-brief-context", "tomorrow").stdout)
            assert context["sources"]["calendar"]["status"] == "not_configured"
            assert context["sources"]["planner"]["data"]["schedule"]
            print("PASS no DailyGrad: startup, identity file, API auth/routing, planner, documents, brief context")

            if args.dailygrad:
                setup = cli.dailygrad_cli()
                data = cli.LOCAL / "dailygrad/data"
                setup.configure(SimpleNamespace(data_dir=str(data), ollama_url="http://host.docker.internal:11434",
                    ollama_model="fictional-uncontacted-model", allow_run=False, allow_repair=False,
                    allow_sources_update=False, allow_leetcode_update=False), cli)
                ready()
                old = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
                doc = {"schema_version": 1, "run_id": 7, "date": old, "status": "degraded",
                       "generated_at": old + "T10:00:00Z", "stories": [{"title": "Fictional story"}],
                       "lesson": None, "recall": None, "leetcode": None}
                (data / "latest.json").write_text(json.dumps(doc))
                digest = invoke("dailygrad_digest", {})
                assert digest["ok"] and digest["is_stale"] and digest["digest"]["run_id"] == 7
                absent = invoke("dailygrad_sources", {})
                assert absent["ok"] is False
                blocked = invoke("dailygrad_run", {"timeoutSeconds": 570})
                assert blocked["error"]["code"] == "capability_disabled"
                print("PASS read-only DailyGrad: plugin SDK registration, stale digest, unavailable runner, disabled mutation")
                compose("--profile", "dailygrad-runner", "up", "-d", "--no-build", "dailygrad-runner")
                for _ in range(20):
                    sources = invoke("dailygrad_sources", {})
                    if sources.get("ok"):
                        break
                    time.sleep(1)
                else:
                    logs = compose("--profile", "dailygrad-runner", "logs", "--tail", "30", "dailygrad-runner", check=False)
                    raise RuntimeError("Fixture runner not ready: " + json.dumps(sources) + logs.stdout[-4000:])
                assert sources["sources"]
                exercise = invoke("dailygrad_leetcode", {"action": "status"})
                assert exercise["ok"]
                print("PASS actual standalone DailyGrad package: constrained socket, source catalog, exercise status (no model/news calls)")
                compose("--profile", "dailygrad-runner", "stop", "dailygrad-runner")
                hidden = data.with_name("data-hidden")
                data.rename(hidden)
                compose("up", "-d", "--no-build", "--force-recreate", "tari")
                ready()
                assert invoke("dailygrad_digest", {})["ok"] is False
                print("PASS configured missing DailyGrad directory: core Gateway remains healthy")
        finally:
            compose("--profile", "dailygrad-runner", "down", "--timeout", "30", "--volumes", "--remove-orphans", check=False)
            # Covers a named socket volume whose last overlay may have been degraded.
            subprocess.run(["docker", "volume", "rm", project + "_dailygrad-socket"], cwd=root, capture_output=True, timeout=30)
    print("Disposable fixture removed; no installed state, model credentials, or Google credentials were used.")


if __name__ == "__main__":
    main()
