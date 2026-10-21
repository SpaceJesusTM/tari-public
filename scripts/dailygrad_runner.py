#!/usr/bin/env python3
"""Optional constrained DailyGrad Unix-socket runner (Python 3.11+).

Adapted from the tracked Tari host runner: fixed subprocess argv, bounded JSON
requests/results, separate run/repair cooldowns, strict upstream result validation.
DailyGrad itself remains a separately installed public upstream dependency.
Run --help for owner-only startup configuration. Nothing starts automatically.
"""
import argparse, fcntl, json, os, re, signal, socket, socketserver, stat, struct, subprocess, sys, threading, time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from collections import deque
from datetime import date, datetime, timezone

MAX_REQUEST = 2048           # bytes; a run request is 14, a source change with 16 IDs under 1.2 KB
REQUEST_TIMEOUT = 5          # seconds to send that request
MAX_DIGEST = 256 * 1024      # same cap as the plugin's reader
STDERR_KEEP = 64 * 1024      # DailyGrad stderr kept for the host log; never returned
DIAG_LINES, DIAG_CHARS = 20, 300   # a degraded run's diagnostics in the host log: lines, and characters per line
# What a degraded run's diagnostics keep of DailyGrad's log: its warnings and errors, and its
# one-line count of model requests. Anything else (progress messages, a stray traceback) is left out.
DIAG_LINE = re.compile(r"^(?:ERROR|WARNING): |^INFO: \d+ model requests")
GRACE_INT, GRACE_TERM = 30, 10
MAX_CLIENTS = 8
CLOCK_SKEW = 5               # seconds of tolerance when matching generated_at to the launch
SOURCES_OUTPUT = 256 * 1024  # bytes of its stdout read; more is malformed output
SOURCE_ACTIONS = ("enable", "disable", "set")
MAX_IDS = 16
SOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")   # never starts with "-"; DailyGrad decides validity
CLI_ERRORS = ("unknown_source", "no_sources_enabled", "invalid_preferences", "invalid_config", "write_failed")
PREFS_ERROR = re.compile(r"not a valid source preferences file")
# The whole of what a leetcode request can run: the action picks one of these fixed tuples.
LEETCODE_ARGS = {"status": ("leetcode", "status", "--json"),
                 "hints_on": ("leetcode", "hints", "on", "--json"),
                 "hints_off": ("leetcode", "hints", "off", "--json")}
# The two that end an exercise take one more thing, its ID, which goes where the None is: an
# integer in range, written out here in digits, so it can be neither an option nor a path.
LEETCODE_TARGETED = {"complete": ("leetcode", "mark", "complete", "--exercise", None, "--json"),
                     "next": ("leetcode", "next", "--exercise", None, "--json")}
MAX_EXERCISE = 999_999_999
PROBLEM_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")   # DailyGrad's catalog IDs: LeetCode's slugs
LEETCODE_ERRORS = ("invalid_config", "failed")
# DailyGrad's refusals of complete and next. Each is passed on by its code with a sentence of
# the runner's own: nothing DailyGrad printed reaches the reply.
LEETCODE_REFUSALS = {
    "no_exercise": "DailyGrad has not assigned a LeetCode exercise yet. Nothing was changed.",
    "unknown_exercise": "DailyGrad has no LeetCode exercise with that ID. Nothing was changed; read the status again.",
    "stale_exercise": "That is no longer DailyGrad's current LeetCode exercise. Nothing was changed; "
                      "read the status again for the current one.",
    "leetcode_disabled": "LeetCode exercises are switched off in DailyGrad's configuration. Nothing was changed.",
}
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_RUN_ID = 999_999_999
REPAIR_OUTCOMES = ("repaired", "partial", "unchanged", "nothing_to_repair")
# DailyGrad's refusals of a repair, passed on by code with a sentence of the runner's own.
REPAIR_REFUSALS = {
    "not_found": "DailyGrad has no archived run with that ID on that date. Nothing was changed.",
    "ambiguous_run": "DailyGrad has more than one run with that ID on that date. Nothing was changed.",
    "no_markdown": "That run was archived without its Markdown, so DailyGrad cannot repair it. Nothing was changed.",
    "markdown_mismatch": "That run's archived Markdown is not DailyGrad's rendering of it, so it was not repaired. "
                         "Nothing was changed.",
    "invalid_digest": "That run's archived digest is not usable, so it was not repaired. Nothing was changed.",
    "concurrent_repair": "Another repair of that run finished first. Nothing was written; read the digest again.",
    "archive_conflict": "DailyGrad found a repair archive it would not overwrite. Nothing was changed; see the runner log.",
    "invalid_config": "DailyGrad rejected its configuration. Nothing was changed; see the runner log on the host.",
}
# The runner's own files (log, locks, results, socket) are private; DailyGrad keeps the
# umask it would have had, so its output permissions do not change.
CHILD_UMASK = 0o077


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg):
    print(f"{now_iso()} {msg}", flush=True)


def fatal(msg):
    log(f"refusing to start: {msg}")
    sys.exit(2)


# ------------------------------------------------------------- configuration --
# Values are fixed by the owner before accepting any request. No request may
# select commands, executables, paths, environment variables or model settings.
def configure(args):
    global REPO_DIR, BIN, DATA_DIR, RUNNER_DIR, TIMEOUT, COOLDOWN
    global SOURCES_TIMEOUT, LATEST, SOCK_DIR, SOCK, ARGV, CHILD_ENV
    global REPAIR_COOLDOWN, USER_ZONE, CAPABILITIES
    def owned_path(value, name, exists=True):
        if not value or not os.path.isabs(value):
            raise ValueError(name + " must be an explicit absolute path")
        path = Path(value)
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise ValueError(name + " must not use symlinks")
        if exists and not path.exists():
            raise ValueError(name + " does not exist")
        return str(path.absolute())
    REPO_DIR = owned_path(args.repo, "DailyGrad working directory")
    BIN = owned_path(args.bin, "DailyGrad executable")
    DATA_DIR = owned_path(args.data_dir, "DailyGrad data directory")
    RUNNER_DIR = owned_path(args.runner_dir, "Runner state directory", exists=False)
    if not os.path.isdir(REPO_DIR) or not os.path.isdir(DATA_DIR):
        raise ValueError("DailyGrad working and data paths must be directories")
    if not os.path.isfile(BIN) or not os.access(BIN, os.X_OK):
        raise ValueError("DailyGrad executable must be an executable regular file")
    for name, value, low, high in (("run timeout", args.run_timeout, 1, 7200),
                                  ("run cooldown", args.run_cooldown, 0, 86400),
                                  ("source timeout", args.sources_timeout, 1, 300)):
        if type(value) is not int or not low <= value <= high:
            raise ValueError(name + " is outside its supported range")
    TIMEOUT, COOLDOWN, SOURCES_TIMEOUT = args.run_timeout, args.run_cooldown, args.sources_timeout
    REPAIR_COOLDOWN = min(COOLDOWN, FAILED_COOLDOWN)
    try:
        USER_ZONE = ZoneInfo(args.timezone)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError("Use a valid IANA timezone for the DailyGrad runner") from error
    LATEST = os.path.join(DATA_DIR, "latest.json")
    SOCK_DIR = owned_path(args.socket_dir or os.path.join(RUNNER_DIR, "sock"),
                          "Runner socket directory", exists=False)
    SOCK = os.path.join(SOCK_DIR, "runner.sock")
    if len(SOCK.encode()) > 103:
        raise ValueError("Unix socket path exceeds 103 bytes; choose a shorter runner directory")
    ARGV = (BIN, "run")
    child_home = owned_path(os.path.join(RUNNER_DIR, "home"), "Runner child home", exists=False)
    CHILD_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": child_home,
                 "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": args.timezone}
    os.makedirs(CHILD_ENV["HOME"], mode=0o700, exist_ok=True)
    STATE.update(running_since=None, activity=None, next_allowed=0.0,
                 next_repair_allowed=0.0, proc=None)
    CAPABILITIES = {
        "run": args.allow_run, "repair": args.allow_repair,
        "sources_update": args.allow_sources_update,
        "leetcode_update": args.allow_leetcode_update,
    }


def local_day():
    return datetime.now(USER_ZONE).date().isoformat()


def check_data_dir():
    """DailyGrad must write the latest.json this runner watches, or every run would look like
    'no new digest'. `dailygrad config` (fixed argv, no model, no network) says where it writes."""
    try:
        p = subprocess.run((BIN, "config"), cwd=REPO_DIR, env=CHILD_ENV, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=60, shell=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        fatal(f"`dailygrad config` did not run: {e}")
    m = re.search(r"^Latest JSON:\s*(\S.*)$", p.stdout, re.M)
    if p.returncode != 0 or not m:
        fatal(f"`dailygrad config` gave no 'Latest JSON' path (exit {p.returncode})")
    if os.path.realpath(m.group(1).strip()) != os.path.realpath(LATEST):
        fatal(f"DailyGrad writes {m.group(1).strip()} but DAILYGRAD_DATA_DIR is {DATA_DIR}")


# ------------------------------------------------------------- digest identity --
def identity(path):
    """(info, None) for a valid schema-1 digest, (None, None) if there is none,
    (None, code) if it is unusable. Only metadata leaves this function."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None, None
    except OSError:
        return None, "unreadable_digest"
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None, "unreadable_digest"
        if st.st_size > MAX_DIGEST:
            return None, "invalid_digest"
        with os.fdopen(fd, "rb", closefd=False) as f:
            raw = f.read(MAX_DIGEST + 1)
    finally:
        os.close(fd)
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "invalid_digest"
    if not isinstance(doc, dict) or type(doc.get("schema_version")) is not int:
        return None, "invalid_digest"
    if doc["schema_version"] != 1:
        return None, "unsupported_schema"
    rid, day, gen = doc.get("run_id"), doc.get("date"), doc.get("generated_at")
    try:
        ok_day = isinstance(day, str) and bool(DATE.match(day)) and date.fromisoformat(day).isoformat() == day
        gen_date = datetime.fromisoformat(gen.replace("Z", "+00:00")) if isinstance(gen, str) else None
        gen_ts = gen_date.timestamp() if gen_date and gen_date.tzinfo is not None else None
    except ValueError:
        ok_day, gen_ts = False, None
    if (type(rid) is not int or not 1 <= rid <= MAX_RUN_ID or not ok_day or gen_ts is None
            or doc.get("status") not in ("ok", "degraded") or not isinstance(doc.get("stories"), list)):
        return None, "invalid_digest"
    return {"date": day, "run_id": rid, "generated_at": gen, "status": doc["status"],
            "story_count": len(doc["stories"]), "has_lesson": isinstance(doc.get("lesson"), dict),
            "_ts": gen_ts}, None


def public(info):
    return None if info is None else {k: v for k, v in info.items() if not k.startswith("_")}


# ---------------------------------------------------------------------- run --
RUN_LOCK = threading.Lock()          # one run or repair per runner: both use the model and DailyGrad's database
STATE = {"running_since": None, "activity": None, "next_allowed": 0.0, "next_repair_allowed": 0.0, "proc": None}
FAILED_COOLDOWN = 60
MESSAGES = {
    "timeout": "DailyGrad did not finish within {t} seconds and was stopped.",
    "dailygrad_config_error": "DailyGrad rejected its configuration (exit 2); see the runner log on the host.",
    "invalid_source_preferences": "DailyGrad's source preferences file is invalid, so it fetched nothing (exit 2). "
                                  "Choosing the sources again (dailygrad sources set ...) replaces the file.",
    "dailygrad_failed": "DailyGrad failed (exit {rc}) without producing a new digest; see the runner log on the host.",
    "no_new_digest": "DailyGrad exited normally but latest.json is not a new run.",
    "invalid_digest": "DailyGrad's latest.json is not a valid digest.",
    "unreadable_digest": "DailyGrad's latest.json could not be read.",
    "unsupported_schema": "DailyGrad's latest.json uses a schema version this runner does not read.",
    "launch_failed": "The DailyGrad executable could not be started; see the runner log on the host.",
}


def stop_process(proc):
    """SIGINT first: DailyGrad's own cleanup unloads the model. Then SIGTERM, then SIGKILL."""
    for sig, grace in ((signal.SIGINT, GRACE_INT), (signal.SIGTERM, GRACE_TERM), (signal.SIGKILL, 5)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


def diagnostics(stderr_text):
    """DailyGrad's own warnings and errors from one run, bounded for the host log: at most
    DIAG_LINES lines, each cut to DIAG_CHARS characters and stripped of control characters.
    Errors and the model-request count come first; the latest warnings fill what is left.
    This is why a degraded run was degraded (a timeout, an HTTP 500, a page that would not
    load). For runner.log only; it is never put in a reply."""
    found = [l for l in stderr_text.splitlines() if DIAG_LINE.match(l)]
    errors = [i for i, l in enumerate(found) if not l.startswith("WARNING: ")][-DIAG_LINES:]
    warnings = [i for i, l in enumerate(found) if l.startswith("WARNING: ")]
    room = DIAG_LINES - len(errors)
    kept = []
    for i in sorted(errors + (warnings[-room:] if room else [])):
        line = "".join(c if c.isprintable() else " " for c in found[i])
        kept.append(line if len(line) <= DIAG_CHARS else line[:DIAG_CHARS] + " [cut]")
    if len(found) > DIAG_LINES:
        kept.insert(0, f"[{len(found) - DIAG_LINES} other lines not shown]")
    return "\n".join("    " + l for l in kept) or "    [DailyGrad logged no warning or error]"


def run_once():
    before, _ = identity(LATEST)
    started, started_iso = time.time(), now_iso()
    tail = deque()
    size = [0]

    try:
        proc = subprocess.Popen(ARGV, cwd=REPO_DIR, env=CHILD_ENV, shell=False, close_fds=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, start_new_session=True, umask=CHILD_UMASK)
    except OSError as e:
        log(f"launch failed: {e}")
        return {"ok": False, "result": "completed", "error": {"code": "launch_failed", "message": MESSAGES["launch_failed"]},
                "exit_status": None, "timed_out": False, "duration_seconds": 0.0, "new_run": False,
                "local_date": local_day(), "is_today": False, "previous": public(before),
                "digest": None, "latest": public(before), "started_at": started_iso, "finished_at": now_iso()}
    STATE["proc"] = proc

    def drain():
        for chunk in iter(lambda: proc.stderr.read(4096), b""):
            tail.append(chunk)
            size[0] += len(chunk)
            while size[0] > STDERR_KEEP and len(tail) > 1:
                size[0] -= len(tail.popleft())
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    timed_out = False
    try:
        rc = proc.wait(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        timed_out = True
        stop_process(proc)
        rc = proc.returncode
    reader.join(timeout=5)
    STATE["proc"] = None
    finished = time.time()
    stderr_text = b"".join(tail).decode("utf-8", "replace")

    after, after_err = identity(LATEST)
    new_run = bool(after) and (before is None or (after["run_id"], after["generated_at"]) !=
                               (before["run_id"], before["generated_at"])) \
        and started - CLOCK_SKEW <= after["_ts"] <= finished + CLOCK_SKEW
    today = local_day()
    if timed_out:
        code = "timeout"
    elif rc == 2:
        # The one configuration error worth naming: it is fixable from Tari (sources set).
        code = "invalid_source_preferences" if PREFS_ERROR.search(stderr_text) else "dailygrad_config_error"
    elif rc not in (0, 1):
        code = "dailygrad_failed"
    elif after_err:
        code = after_err
    elif not new_run:
        code = "no_new_digest" if rc == 0 else "dailygrad_failed"
    else:
        code = None   # exit 0 (ok) or 1 (degraded, outputs still written) with a new digest
    result = {
        "ok": code is None, "result": "completed",
        "error": None if code is None else {"code": code, "message": MESSAGES[code].format(t=TIMEOUT, rc=rc)},
        "exit_status": rc, "timed_out": timed_out, "duration_seconds": round(finished - started, 1),
        # is_today: a NEW digest was produced for the configured user's local date. An old
        # digest, however recent, is never "today's run".
        "new_run": new_run, "local_date": today, "is_today": new_run and after["date"] == today,
        "previous": public(before), "digest": public(after) if new_run else None,
        "latest": public(after),
        "started_at": started_iso, "finished_at": now_iso(),
    }
    if code:
        log(f"run failed ({code}); last DailyGrad stderr:\n{stderr_text[-4000:]}")
    elif rc != 0 or after["status"] != "ok":
        # An acceptable run, so nothing above logs it; without this the reason would be lost.
        log(f"run degraded (exit {rc}, status {after['status']}, run {after['run_id']}); DailyGrad reported:\n"
            f"{diagnostics(stderr_text)}")
    return result


def busy_reply():
    what = "repair" if STATE["activity"] == "repair" else "run"
    return {"ok": False, "result": "busy", "running_since": STATE["running_since"],
            "error": {"code": "busy", "message": f"A DailyGrad {what} is already in progress (started {STATE['running_since']})."}}


def do_run():
    if not CAPABILITIES.get("run"):
        return disabled_reply("run")
    if not RUN_LOCK.acquire(blocking=False):
        return busy_reply()
    try:
        if time.monotonic() < STATE["next_allowed"]:
            wait = int(STATE["next_allowed"] - time.monotonic()) + 1
            return {"ok": False, "result": "cooldown", "retry_after_seconds": wait,
                    "error": {"code": "cooldown", "message": f"DailyGrad ran recently; the next run is allowed in {wait} seconds."}}
        # Across processes too: a second runner on the same directory cannot start a run.
        lock_fd = os.open(os.path.join(RUNNER_DIR, "run.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"ok": False, "result": "busy", "running_since": None,
                        "error": {"code": "busy", "message": "A DailyGrad run is already in progress."}}
            STATE["running_since"], STATE["activity"] = now_iso(), "run"
            result = None
            try:
                result = run_once()
            finally:
                STATE["running_since"] = STATE["activity"] = None
                pause = COOLDOWN if result and result.get("new_run") else min(COOLDOWN, FAILED_COOLDOWN)
                STATE["next_allowed"] = time.monotonic() + pause
        finally:
            os.close(lock_fd)
        with open(os.path.join(RUNNER_DIR, "last-result.json"), "w") as f:
            json.dump(result, f, indent=1)
        return result
    finally:
        RUN_LOCK.release()


# ------------------------------------------------------------------ sources --
# `dailygrad sources` lists and changes which news sources DailyGrad fetches. Its preferences
# file lives in DailyGrad's data directory, which the container can only read; only this
# command, run here with the same fixed directory and environment as `run`, writes it.
# DailyGrad validates the IDs; the runner only checks their shape. A change never starts a run.
SOURCES_LOCK = threading.Lock()


def run_bounded(argv):
    """(exit status or None, stdout bytes or None if over the cap, stderr tail, timed out)."""
    proc = subprocess.Popen(argv, cwd=REPO_DIR, env=CHILD_ENV, shell=False, close_fds=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True, umask=CHILD_UMASK)
    out, err, over = bytearray(), deque(maxlen=16), [False]

    def read_out():
        for chunk in iter(lambda: proc.stdout.read(4096), b""):
            if len(out) + len(chunk) > SOURCES_OUTPUT:
                over[0] = True   # keep draining so the child cannot block, but keep nothing more
            else:
                out.extend(chunk)

    def read_err():
        for chunk in iter(lambda: proc.stderr.read(4096), b""):
            err.append(chunk)
    threads = [threading.Thread(target=f, daemon=True) for f in (read_out, read_err)]
    for t in threads:
        t.start()
    timed_out = False
    try:
        rc = proc.wait(timeout=SOURCES_TIMEOUT)
    except subprocess.TimeoutExpired:
        timed_out = True
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
                proc.wait(timeout=5)
                break
            except (ProcessLookupError, subprocess.TimeoutExpired):
                continue
        rc = None
    for t in threads:
        t.join(timeout=5)
    return rc, None if over[0] else bytes(out), b"".join(err).decode("utf-8", "replace")[-2000:], timed_out


def clean_sources(doc):
    """The parts of a successful `dailygrad sources --json` result Tari needs, or None if the
    document is not what DailyGrad's contract (docs/sources.md) promises."""
    if not isinstance(doc, dict) or doc.get("ok") is not True or type(doc.get("changed")) is not bool \
            or doc.get("mode") not in ("all_except", "only"):
        return None
    srcs, groups = doc.get("sources"), doc.get("groups")
    if not isinstance(srcs, list) or not 1 <= len(srcs) <= 64 or not isinstance(groups, list) or len(groups) > 64:
        return None
    out_sources, out_groups = [], []
    for x in srcs:
        if not (isinstance(x, dict) and isinstance(x.get("id"), str) and SOURCE_ID.fullmatch(x["id"])
                and isinstance(x.get("name"), str) and len(x["name"]) <= 200
                and "group" in x and (x["group"] is None or isinstance(x["group"], str))
                and type(x.get("enabled")) is bool):
            return None
        out_sources.append({k: x[k] for k in ("id", "name", "group", "enabled")})
    for g in groups:
        if not (isinstance(g, dict) and isinstance(g.get("id"), str) and SOURCE_ID.fullmatch(g["id"])
                and isinstance(g.get("name"), str) and len(g["name"]) <= 200 and isinstance(g.get("sources"), list)
                and all(isinstance(m, str) and SOURCE_ID.fullmatch(m) for m in g["sources"])):
            return None
        out_groups.append({"id": g["id"], "name": g["name"], "sources": list(g["sources"])})
    return {"changed": doc["changed"], "mode": doc["mode"], "sources": out_sources, "groups": out_groups}


def do_sources(action, ids):
    if action != "list" and not CAPABILITIES.get("sources_update"):
        return disabled_reply("sources_update")
    def fail(code, message):
        return {"ok": False, "result": "sources", "action": action, "error": {"code": code, "message": message}}
    if not SOURCES_LOCK.acquire(timeout=SOURCES_TIMEOUT + 15):
        return fail("busy", "Another source change is still in progress.")
    try:
        # Fixed argv: the action is one of four literals; IDs follow "--", so none can be an option.
        argv = (BIN, "sources", "list", "--json") if action == "list" else (BIN, "sources", action, "--json", "--", *ids)
        try:
            rc, out, err, timed_out = run_bounded(argv)
        except OSError as e:
            log(f"sources {action}: launch failed: {e}")
            return fail("launch_failed", "The DailyGrad executable could not be started; see the runner log on the host.")
        if timed_out:
            log(f"sources {action}: timed out after {SOURCES_TIMEOUT}s")
            return fail("sources_timeout", f"`dailygrad sources` did not finish within {SOURCES_TIMEOUT} seconds.")
        try:
            doc = json.loads(out.decode("utf-8")) if out is not None else None
        except (UnicodeDecodeError, ValueError):
            doc = None
        if rc == 0:
            clean = clean_sources(doc)
            if clean is None:
                log(f"sources {action}: exit 0 but unexpected output; stderr: {err}")
                return fail("bad_cli_output", "`dailygrad sources` exited normally but its output was not the "
                                              "expected JSON; whether anything changed is unknown, so list the sources again.")
            return {"ok": True, "result": "sources", "action": action, **clean}
        e = doc.get("error") if isinstance(doc, dict) and doc.get("ok") is False else None
        if isinstance(e, dict) and not isinstance(e.get("code"), str):
            return fail("bad_cli_output", "DailyGrad returned a malformed source error; no change was verified.")
        if isinstance(e, dict) and isinstance(e.get("code"), str) and e["code"] in CLI_ERRORS and isinstance(e.get("message"), str):
            return fail(e["code"], "DailyGrad refused the source request. Check the source IDs or owner configuration; nothing is known to have changed.")
        log(f"sources {action}: exit {rc} without a JSON error; stderr: {err}")
        return fail("cli_error", f"`dailygrad sources` failed (exit {rc}) without a usable error; "
                                 "see the runner log on the host. Nothing is known to have changed.")
    finally:
        SOURCES_LOCK.release()


# ----------------------------------------------------------------- leetcode --
# `dailygrad leetcode status` says whether digests show a hint for the LeetCode exercise and
# which exercise is current, and `leetcode hints on|off` changes the hint preference.
# `leetcode mark complete --exercise N` and `leetcode next --exercise N` end the current
# exercise, which DailyGrad otherwise shows again every day. All of that state is in
# DailyGrad's data directory, read-only in the container, so only these commands, run here,
# write it. None runs DailyGrad, uses the model or rewrites a digest. Only `next` chooses a
# problem, and DailyGrad does the choosing.
LEETCODE_LOCK = threading.Lock()


def is_exercise_id(value):
    return type(value) is int and 1 <= value <= MAX_EXERCISE   # type(): True is not an ID


def short(value, limit):
    return isinstance(value, str) and len(value) <= limit


def clean_exercise(x, days_key):
    """Which exercise a reply is about and how it stands: its ID, its problem by name, its track
    and its outcome. Nothing of the problem's content. None if it is not as promised."""
    if not isinstance(x, dict):
        return None
    pid, url, days, when = x.get("problem_id"), x.get("url"), x.get(days_key), x.get("assigned_on")
    if not (is_exercise_id(x.get("exercise_id")) and short(pid, 100) and PROBLEM_ID.fullmatch(pid)
            and (x.get("title") is None or short(x["title"], 200))
            and (x.get("number") is None or (type(x["number"]) is int and 0 < x["number"] < 1_000_000))
            and (url is None or (short(url, 200) and url.startswith("https://leetcode.com/problems/")))
            and short(x.get("track"), 64) and SOURCE_ID.fullmatch(x["track"]) and type(x.get("review")) is bool
            and (when is None or (isinstance(when, str) and DATE.fullmatch(when)))
            and type(days) is int and 0 <= days <= 100_000
            and x.get("outcome") in (None, "completed", "skipped")):
        return None
    return {"exercise_id": x["exercise_id"], "problem_id": pid, "number": x.get("number"), "title": x.get("title"),
            "url": url, "track": x["track"], "review": x["review"], "assigned_on": when, "days_shown": days,
            "outcome": x.get("outcome")}


def clean_track(x):
    if x is None:
        return None
    if isinstance(x, dict) and short(x.get("id"), 64) and SOURCE_ID.fullmatch(x["id"]) and short(x.get("name"), 200):
        return {"id": x["id"], "name": x["name"]}
    return False   # present, and not a track


def clean_problem(x):
    """A newly assigned exercise as it may be shown to the user: its identity, then the problem,
    its example, its constraints and the questions it asks. Built key by key, so that a hint
    or a reference solution could not come through even if DailyGrad sent one."""
    base = clean_exercise(x, "day")
    if base is None or base["outcome"] is not None:
        return None
    example, constraints, prompts, sources = x.get("example"), x.get("constraints"), x.get("prompts"), x.get("sources")
    texts = lambda v, n, limit: isinstance(v, list) and len(v) <= n and all(short(i, limit) for i in v)
    if not (x.get("difficulty") in ("easy", "medium", "hard") and type(x.get("premium")) is bool
            and short(x.get("statement"), 2000) and isinstance(example, dict)
            and short(example.get("input"), 1000) and short(example.get("output"), 1000)
            and texts(constraints, 10, 500) and texts(prompts, 10, 300) and type(x.get("hints_enabled")) is bool
            and isinstance(sources, list) and len(sources) <= 10
            and all(isinstance(s, dict) and short(s.get("id"), 64) and SOURCE_ID.fullmatch(s["id"]) and short(s.get("name"), 200)
                    and s.get("kind") in ("curriculum", "company") and short(s.get("publisher"), 200) for s in sources)):
        return None
    return {**base, "difficulty": x["difficulty"], "premium": x["premium"], "statement": x["statement"],
            "example": {"input": example["input"], "output": example["output"]},
            "constraints": list(constraints), "prompts": list(prompts), "hints_enabled": x["hints_enabled"],
            "sources": [{k: s[k] for k in ("id", "name", "kind", "publisher")} for s in sources]}


def clean_leetcode(action, doc, exercise=None):
    """The parts of a successful `dailygrad leetcode ... --json` result Tari needs, or None if the
    document is not what DailyGrad's contract (docs/leetcode.md) promises. The preferences
    file's host path, every hint and every reference solution stay here."""
    if not isinstance(doc, dict) or doc.get("ok") is not True:
        return None
    if action == "complete":
        done, track = clean_exercise(doc.get("exercise"), "days_shown"), clean_track(doc.get("next_track"))
        # It must be the exercise that was named, now completed, or it is not the answer to this request.
        if (done is None or done["exercise_id"] != exercise or done["outcome"] != "completed" or track is False
                or type(doc.get("changed")) is not bool or type(doc.get("completion_pending")) is not bool):
            return None
        return {"changed": doc["changed"], "exercise": done, "completion_pending": doc["completion_pending"],
                "next_track": track}
    if action == "next":
        previous = clean_exercise(doc.get("previous"), "days_shown")
        active = None if doc.get("active") is None else clean_problem(doc["active"])
        if (previous is None or previous["exercise_id"] != exercise or previous["outcome"] is None
                or type(doc.get("changed")) is not bool or (doc.get("active") is not None and active is None)
                or (active is not None and active["exercise_id"] <= exercise)):
            return None
        return {"changed": doc["changed"], "previous": previous, "active": active}
    if type(doc.get("hints_enabled")) is not bool:
        return None
    if action == "status":
        problem = doc.get("preferences_problem")
        if type(doc.get("enabled")) is not bool or not (problem is None or isinstance(problem, str)):
            return None
        # preferences_problem is a sentence naming a host path; whether there is one is enough.
        out = {"changed": False, "hints_enabled": doc["hints_enabled"], "exercises_enabled": doc["enabled"],
               "preferences_invalid": problem is not None}
        if "completion_pending" not in doc:   # a DailyGrad from before exercises were kept until ended
            return out
        current = None if doc.get("current") is None else clean_exercise(doc["current"], "day")
        active, track = doc.get("active_exercise_id"), clean_track(doc.get("next_track"))
        if (type(doc["completion_pending"]) is not bool or (doc.get("current") is not None and current is None)
                or track is False or not (active is None or is_exercise_id(active))
                or doc["completion_pending"] is not (active is not None)
                or (active is not None and (current is None or current["exercise_id"] != active))):
            return None
        return {**out, "completion_pending": doc["completion_pending"], "active_exercise_id": active,
                "exercise": current, "next_track": track}
    # A change must report the state that was asked for, or it is not the answer to this request.
    if type(doc.get("changed")) is not bool or doc["hints_enabled"] is not (action == "hints_on"):
        return None
    return {"changed": doc["changed"], "hints_enabled": doc["hints_enabled"]}


def leetcode_argv(action, exercise):
    if action in LEETCODE_ARGS:
        return (BIN, *LEETCODE_ARGS[action])
    return (BIN, *(str(exercise) if part is None else part for part in LEETCODE_TARGETED[action]))


def do_leetcode(action, exercise=None):
    if action != "status" and not CAPABILITIES.get("leetcode_update"):
        return disabled_reply("leetcode_update")
    def fail(code, message):
        return {"ok": False, "result": "leetcode", "action": action, "error": {"code": code, "message": message}}
    if not LEETCODE_LOCK.acquire(timeout=SOURCES_TIMEOUT + 15):
        return fail("busy", "Another LeetCode request is still in progress.")
    try:
        try:
            rc, out, err, timed_out = run_bounded(leetcode_argv(action, exercise))
        except OSError as e:
            log(f"leetcode {action}: launch failed: {e}")
            return fail("launch_failed", "The DailyGrad executable could not be started; see the runner log on the host.")
        if timed_out:
            log(f"leetcode {action}: timed out after {SOURCES_TIMEOUT}s")
            return fail("leetcode_timeout", f"`dailygrad leetcode` did not finish within {SOURCES_TIMEOUT} seconds; "
                                            "whether anything changed is unknown, so ask for the status again.")
        try:
            doc = json.loads(out.decode("utf-8")) if out is not None else None
        except (UnicodeDecodeError, ValueError):
            doc = None
        if rc == 0:
            clean = clean_leetcode(action, doc, exercise)
            if clean is None:
                log(f"leetcode {action}: exit 0 but unexpected output; stderr: {err}")
                return fail("bad_cli_output", "`dailygrad leetcode` exited normally but its output was not the expected "
                                              "JSON; whether anything changed is unknown, so ask for the status again.")
            return {"ok": True, "result": "leetcode", "action": action, **clean}
        e = doc.get("error") if isinstance(doc, dict) and doc.get("ok") is False else None
        if isinstance(e, dict) and not isinstance(e.get("code"), str):
            return fail("bad_cli_output", "DailyGrad returned a malformed exercise error; no change was verified.")
        if isinstance(e, dict) and isinstance(e.get("code"), str) and action in LEETCODE_TARGETED and e["code"] in LEETCODE_REFUSALS:
            log(f"leetcode {action} {exercise}: DailyGrad refused ({e['code']}): {str(e.get('message'))[:500]}")
            return fail(e["code"], LEETCODE_REFUSALS[e["code"]])
        if isinstance(e, dict) and e.get("code") in LEETCODE_ERRORS:
            # DailyGrad's message names host paths, so the code is passed on and the message is not.
            log(f"leetcode {action}: DailyGrad refused ({e['code']}): {str(e.get('message'))[:500]}")
            return fail(e["code"], "DailyGrad could not carry out the request; see the runner log on the host. "
                                   "Nothing is known to have changed.")
        log(f"leetcode {action}: exit {rc} without a JSON error; stderr: {err}")
        return fail("cli_error", f"`dailygrad leetcode` failed (exit {rc}) without a usable error; see the runner "
                                 "log on the host. It may be a DailyGrad without the LeetCode exercise. "
                                 "Nothing is known to have changed.")
    finally:
        LEETCODE_LOCK.release()


# ------------------------------------------------------------------- repair --
# `dailygrad repair --run N --date D --json` completes the digest of run N of day D that the
# model failed on, as a new revision of that run. It uses the model, so it runs under the run
# lock and the run timeout (SIGINT first, so DailyGrad unloads the model), but it never selects
# news, so it has its own short cooldown and leaves the run cooldown alone: a later run is
# never delayed by a repair, only made to wait while one is in progress (busy).
REPAIR_COOLDOWN = 60  # replaced from the owner configuration before serving


def is_run_id(value):
    return type(value) is int and 1 <= value <= MAX_RUN_ID   # type(): True is not an ID


def is_day(value):
    try:
        return isinstance(value, str) and bool(DATE.match(value)) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def numbers(value):
    return isinstance(value, list) and len(value) <= 100 and all(type(n) is int and 1 <= n <= 100 for n in value)


def parts(value, *extra):
    keys = ("stories", "lesson", *extra)
    return (isinstance(value, dict) and set(value) >= set(keys) and numbers(value["stories"])
            and all(type(value[k]) is bool for k in keys[1:]))


def clean_repair(doc, run_id, day):
    """The parts of a successful `dailygrad repair --json` result Tari needs, or None if it is not
    what DailyGrad's contract (docs/output.md) promises for this run. Host paths stay here."""
    if not isinstance(doc, dict) or doc.get("ok") is not True or doc.get("outcome") not in REPAIR_OUTCOMES:
        return None
    revision, previous = doc.get("revision"), doc.get("previous_revision")
    if not (doc.get("run_id") == run_id and type(doc.get("run_id")) is int and doc.get("date") == day
            and short(doc.get("generated_at"), 64) and type(revision) is int and type(previous) is int
            and 0 <= previous <= revision <= 100_000 and type(doc.get("written")) is bool
            and doc["written"] is (revision == previous + 1) and doc.get("status") in ("ok", "degraded")
            and parts(doc.get("attempted")) and parts(doc.get("repaired")) and parts(doc.get("remaining"), "leetcode")
            and "lesson_note" in doc and doc["lesson_note"] in (None, "curriculum_moved_on")
            and isinstance(doc.get("current_files"), list)
            and all(item in ("dated", "latest") for item in doc["current_files"])):
        return None
    try:
        datetime.fromisoformat(doc["generated_at"])
    except ValueError:
        return None
    pick = lambda d, *keys: {k: (list(d[k]) if k == "stories" else d[k]) for k in ("stories", "lesson", *keys)}
    return {"outcome": doc["outcome"], "run_id": run_id, "date": day, "generated_at": doc["generated_at"],
            "revision": revision, "previous_revision": previous, "written": doc["written"], "status": doc["status"],
            "attempted": pick(doc["attempted"]), "repaired": pick(doc["repaired"]),
            "remaining": pick(doc["remaining"], "leetcode"), "lesson_note": doc["lesson_note"],
            "current_files": list(doc["current_files"]), "current_files_failed": "current_files_error" in doc}


def repair_once(run_id, day):
    """Run `dailygrad repair` under the run timeout. -> the reply."""
    argv = (BIN, "repair", "--run", str(run_id), "--date", day, "--json")   # both checked above: never an option or a path
    started = time.time()
    base = {"result": "repair", "run_id": run_id, "date": day, "started_at": now_iso()}
    try:
        proc = subprocess.Popen(argv, cwd=REPO_DIR, env=CHILD_ENV, shell=False, close_fds=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True, umask=CHILD_UMASK)
    except OSError as e:
        log(f"repair {run_id} {day}: launch failed: {e}")
        return {"ok": False, **base, "error": {"code": "launch_failed", "message": MESSAGES["launch_failed"]},
                "exit_status": None, "timed_out": False, "duration_seconds": 0.0}
    STATE["proc"] = proc
    out, tail, size, over = bytearray(), deque(), [0], [False]

    def read_out():
        for chunk in iter(lambda: proc.stdout.read(4096), b""):
            if len(out) + len(chunk) > SOURCES_OUTPUT:
                over[0] = True   # keep draining so the child cannot block, but keep nothing more
            else:
                out.extend(chunk)

    def read_err():
        for chunk in iter(lambda: proc.stderr.read(4096), b""):
            tail.append(chunk)
            size[0] += len(chunk)
            while size[0] > STDERR_KEEP and len(tail) > 1:
                size[0] -= len(tail.popleft())
    readers = [threading.Thread(target=f, daemon=True) for f in (read_out, read_err)]
    for t in readers:
        t.start()
    timed_out = False
    try:
        rc = proc.wait(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        timed_out = True
        stop_process(proc)
        rc = proc.returncode
    for t in readers:
        t.join(timeout=5)
    STATE["proc"] = None
    stderr_text = b"".join(tail).decode("utf-8", "replace")
    result = {**base, "exit_status": rc, "timed_out": timed_out, "duration_seconds": round(time.time() - started, 1),
              "finished_at": now_iso()}

    def fail(code, message):
        log(f"repair {run_id} {day} failed ({code}); last DailyGrad stderr:\n{stderr_text[-4000:]}")
        return {"ok": False, **result, "error": {"code": code, "message": message}}
    if timed_out:
        return fail("timeout", f"The repair did not finish within {TIMEOUT} seconds and was stopped. Whatever it had not "
                               "written yet was not written; read the digest again.")
    try:
        doc = json.loads(out.decode("utf-8")) if not over[0] else None
    except (UnicodeDecodeError, ValueError):
        doc = None
    if rc in (0, 1) and isinstance(doc, dict) and doc.get("ok") is True:
        clean = clean_repair(doc, run_id, day)
        if clean is None:
            return fail("bad_cli_output", "`dailygrad repair` finished but its output was not the expected JSON; "
                                          "whether anything was written is unknown, so read the digest again.")
        if clean["status"] != "ok" or clean["current_files_failed"]:
            log(f"repair {run_id} {day}: {clean['outcome']}, revision {clean['revision']}, {clean['status']}; "
                f"DailyGrad reported:\n{diagnostics(stderr_text)}")
        return {"ok": True, **result, "error": None, "repair": clean}
    e = doc.get("error") if isinstance(doc, dict) and doc.get("ok") is False else None
    if isinstance(e, dict) and not isinstance(e.get("code"), str):
        return fail("bad_cli_output", "DailyGrad returned a malformed repair error; no change was verified.")
    if rc == 2 and isinstance(e, dict) and isinstance(e.get("code"), str) and e["code"] in REPAIR_REFUSALS:
        return fail(e["code"], REPAIR_REFUSALS[e["code"]])
    if rc == 2 and isinstance(e, dict) and e.get("code") == "bad_date":
        return fail("bad_date", "DailyGrad did not accept that date. Nothing was changed.")
    if rc == 0:   # a clean exit without the JSON the contract promises
        return fail("bad_cli_output", "`dailygrad repair` exited normally but its output was not the expected JSON; "
                                      "whether anything was written is unknown, so read the digest again.")
    if rc == 2 and doc is None:
        return fail("dailygrad_outdated", "DailyGrad did not recognise the repair command; it may predate it. "
                                          "Nothing was changed; see the runner log on the host.")
    return fail("repair_failed", f"`dailygrad repair` failed (exit {rc}); see the runner log on the host. "
                                 "Nothing is known to have been written; read the digest again.")


def do_repair(run_id, day):
    if not CAPABILITIES.get("repair"):
        return disabled_reply("repair")
    if not RUN_LOCK.acquire(blocking=False):
        return busy_reply()
    try:
        if time.monotonic() < STATE["next_repair_allowed"]:
            wait = int(STATE["next_repair_allowed"] - time.monotonic()) + 1
            return {"ok": False, "result": "cooldown", "retry_after_seconds": wait,
                    "error": {"code": "cooldown", "message": f"A DailyGrad repair ran recently; the next is allowed in {wait} seconds."}}
        lock_fd = os.open(os.path.join(RUNNER_DIR, "run.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"ok": False, "result": "busy", "running_since": None,
                        "error": {"code": "busy", "message": "A DailyGrad run or repair is already in progress."}}
            STATE["running_since"], STATE["activity"] = now_iso(), "repair"
            result = None
            try:
                result = repair_once(run_id, day)
            finally:
                STATE["running_since"] = STATE["activity"] = None
                STATE["next_repair_allowed"] = time.monotonic() + REPAIR_COOLDOWN   # the run cooldown is left as it is
        finally:
            os.close(lock_fd)
        with open(os.path.join(RUNNER_DIR, "last-repair.json"), "w") as f:
            json.dump(result, f, indent=1)
        return result
    finally:
        RUN_LOCK.release()


# ------------------------------------------------------------------- server --
def parse(raw):
    """(request, None), or (None, rejection reason). One JSON line, exactly one of:
         {"op":"run"}
         {"op":"sources","action":"list"}
         {"op":"sources","action":"enable"|"disable"|"set","ids":[1-16 IDs]}
         {"op":"leetcode","action":"status"|"hints_on"|"hints_off"}
         {"op":"leetcode","action":"complete"|"next","exercise":N}
         {"op":"repair","run_id":N,"date":"YYYY-MM-DD"}
    A source ID is 1-64 letters, digits and hyphens, not starting with a hyphen. N is an
    integer from 1 to 999999999: not a string, a float or a boolean. The date is a real one."""
    if len(raw) > MAX_REQUEST:
        return None, "request too large"
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        return None, "request must be exactly one line"
    try:
        req = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "request is not JSON"
    if not isinstance(req, dict) or "op" not in req:
        return None, "request must be an object with an op field"
    if req["op"] == "run":
        return (req, None) if set(req) == {"op"} else (None, "run takes no other field")
    if req["op"] == "repair":
        if set(req) != {"op", "run_id", "date"}:
            return None, "repair takes exactly op, run_id and date"
        if not is_run_id(req["run_id"]):
            return None, f"run_id must be an integer from 1 to {MAX_RUN_ID}"
        if not is_day(req["date"]):
            return None, "date must be a real calendar date written YYYY-MM-DD"
        return req, None
    if req["op"] == "leetcode":
        action = req.get("action")
        if isinstance(action, str) and action in LEETCODE_TARGETED:
            if set(req) != {"op", "action", "exercise"}:
                return None, f"leetcode {action} takes exactly op, action and exercise"
            if not is_exercise_id(req["exercise"]):
                return None, f"exercise must be an integer from 1 to {MAX_EXERCISE}"
            return req, None
        if not isinstance(action, str) or action not in LEETCODE_ARGS:
            return None, "unknown leetcode action"
        return (req, None) if set(req) == {"op", "action"} else (None, f"leetcode {action} takes no field but op and action")
    if req["op"] != "sources":
        return None, "unknown operation"
    action = req.get("action")
    if action == "list":
        return (req, None) if set(req) == {"op", "action"} else (None, "sources list takes no other field")
    if action not in SOURCE_ACTIONS:
        return None, "unknown sources action"
    if set(req) != {"op", "action", "ids"}:
        return None, "sources enable/disable/set takes exactly op, action and ids"
    ids = req["ids"]
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_IDS:
        return None, f"ids must be a list of 1 to {MAX_IDS} IDs"
    if not all(isinstance(i, str) and SOURCE_ID.fullmatch(i) for i in ids):
        return None, "an ID must be 1-64 letters, digits and hyphens, not starting with a hyphen"
    return req, None


def disabled_reply(capability):
    return {"ok": False, "result": "disabled", "error": {
        "code": "capability_disabled",
        "message": "The owner has not enabled DailyGrad " + capability + ". Nothing was changed."}}


CAPABILITIES = {}


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        conn = self.request
        try:
            pid, uid, _ = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
        except (OSError, AttributeError):
            pid = uid = -1
        conn.settimeout(REQUEST_TIMEOUT)
        raw = b""
        try:
            while b"\n" not in raw and len(raw) <= MAX_REQUEST:
                chunk = conn.recv(MAX_REQUEST + 1 - len(raw))
                if not chunk:
                    break
                raw += chunk
        except (socket.timeout, OSError):
            pass
        if not raw:
            return   # a connect-and-close probe (`status`)
        req, problem = parse(raw)
        if problem:
            log(f"rejected request from pid {pid} uid {uid}: {problem} ({len(raw)} bytes)")
            reply = {"ok": False, "result": "rejected", "error": {"code": "bad_request", "message": problem}}
        elif req["op"] == "sources":
            log(f"sources {req['action']} {req.get('ids', [])} requested by pid {pid} uid {uid}")
            reply = do_sources(req["action"], req.get("ids", []))
            log(f"sources {req['action']}: " + (f"ok, changed={reply['changed']}, mode={reply['mode']}, enabled="
                + ",".join(x["id"] for x in reply["sources"] if x["enabled"]) if reply["ok"] else
                f"refused: {reply['error']['code']}"))
        elif req["op"] == "leetcode":
            named = f" exercise {req['exercise']}" if "exercise" in req else ""
            log(f"leetcode {req['action']}{named} requested by pid {pid} uid {uid}")
            reply = do_leetcode(req["action"], req.get("exercise"))
            if not reply["ok"]:
                outcome = f"refused: {reply['error']['code']}"
            elif req["action"] == "complete":
                outcome = f"ok, changed={reply['changed']}, completion_pending={reply['completion_pending']}"
            elif req["action"] == "next":
                active = reply["active"]
                outcome = (f"ok, changed={reply['changed']}, previous {reply['previous']['outcome']}, active exercise "
                           + (f"{active['exercise_id']} ({active['problem_id']})" if active else "none"))
            else:
                outcome = f"ok, changed={reply['changed']}, hints_enabled={reply['hints_enabled']}"
            log(f"leetcode {req['action']}{named}: {outcome}")
        elif req["op"] == "repair":
            log(f"repair of run {req['run_id']} of {req['date']} requested by pid {pid} uid {uid}")
            conn.settimeout(None)
            reply = do_repair(req["run_id"], req["date"])
            log(f"repair {reply['result']}: " + json.dumps({k: reply.get(k) for k in
                ("ok", "error", "exit_status", "timed_out", "duration_seconds", "repair")}))
        else:
            log(f"run requested by pid {pid} uid {uid}")
            conn.settimeout(None)
            reply = do_run()
            log(f"run {reply['result']}: " + json.dumps({k: reply.get(k) for k in
                ("ok", "error", "exit_status", "timed_out", "duration_seconds", "new_run", "is_today", "previous", "latest")}))
        try:
            conn.sendall((json.dumps(reply) + "\n").encode())
        except OSError:
            log("caller disconnected before the result was sent")


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    slots = threading.BoundedSemaphore(MAX_CLIENTS)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def main():
    os.makedirs(SOCK_DIR, mode=0o700, exist_ok=True)
    os.chmod(RUNNER_DIR, 0o700)
    # A Docker socket volume can have an existing root-owned sticky directory.
    # Socket mode 0600 still restricts connection to this installation's UID.
    if os.stat(SOCK_DIR).st_uid == os.getuid():
        os.chmod(SOCK_DIR, 0o700)
    life = open(os.path.join(RUNNER_DIR, "runner.lock"), "w")
    try:
        fcntl.flock(life, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fatal("another runner is already running for this directory")
    # The socket can be mounted separately from private runner state. Lock it too
    # so a second state directory cannot unlink an active runner's socket.
    socket_lock = os.path.join(SOCK_DIR, ".runner.lock")
    if os.path.islink(socket_lock):
        fatal("socket lock must not be a symlink")
    socket_life = open(socket_lock, "a")
    try:
        fcntl.flock(socket_life, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fatal("another runner already owns this socket directory")
    check_data_dir()
    if os.path.lexists(SOCK):
        if not stat.S_ISSOCK(os.lstat(SOCK).st_mode):
            fatal(f"{SOCK} exists and is not a socket")
        os.unlink(SOCK)   # stale: the lifetime lock proves no runner owns it
    server = Server(SOCK, Handler)
    os.chmod(SOCK, 0o600)
    with open(os.path.join(RUNNER_DIR, "runner.pid"), "w") as f:
        f.write(f"{os.getpid()}\n")

    def stop(signum, _frame):
        log(f"signal {signum}: stopping")
        proc = STATE["proc"]
        if proc and proc.poll() is None:
            threading.Thread(target=stop_process, args=(proc,), daemon=True).start()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log(f"listening on {SOCK}; command {list(ARGV)} in {REPO_DIR}; watching {LATEST}; "
        f"timeout {TIMEOUT}s, cooldown {COOLDOWN}s")
    try:
        server.serve_forever()
    finally:
        proc = STATE["proc"]
        if proc and proc.poll() is None:
            stop_process(proc)
        server.server_close()
        for p in (SOCK, os.path.join(RUNNER_DIR, "runner.pid")):
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass
        log("stopped")


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    env = os.environ
    parser.add_argument("--repo", default=env.get("DAILYGRAD_REPO_DIR"))
    parser.add_argument("--bin", default=env.get("DAILYGRAD_BIN"))
    parser.add_argument("--data-dir", default=env.get("DAILYGRAD_DATA_DIR"))
    parser.add_argument("--runner-dir", default=env.get("DAILYGRAD_RUNNER_DIR"))
    parser.add_argument("--socket-dir", default=env.get("DAILYGRAD_SOCKET_DIR"))
    parser.add_argument("--timezone", default=env.get("TARI_TIMEZONE", "UTC"))
    parser.add_argument("--run-timeout", type=int, default=env.get("DAILYGRAD_RUN_TIMEOUT", "1200"))
    parser.add_argument("--run-cooldown", type=int, default=env.get("DAILYGRAD_RUN_COOLDOWN", "600"))
    parser.add_argument("--sources-timeout", type=int, default=env.get("DAILYGRAD_SOURCES_TIMEOUT", "30"))
    for capability in ("run", "repair", "sources-update", "leetcode-update"):
        parser.add_argument("--allow-" + capability, action="store_true")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        configure(args)
        main()
    except (ValueError, OSError) as error:
        print("DailyGrad runner: " + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(cli())
