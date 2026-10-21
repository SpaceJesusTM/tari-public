// Typed Unix-socket client for the optional isolated DailyGrad runner.
//
// The runner listens on a socket explicitly mounted into the gateway. A run is
// the fixed line {"op":"run"}: there is no
// parameter to pass, so nothing here can choose what the host executes. After a successful
// run, the new latest.json is read back through digest.js (the same validator as
// dailygrad_digest) and must match the run the host reported before it counts as verified.
// The runner's other requests (news sources, the LeetCode hint preference and exercise, the
// repair of a degraded digest) are below; each is a typed request the runner maps to a fixed
// command of its own.

import net from "node:net";
import path from "node:path";
import { isDeepStrictEqual } from "node:util";
import { localDate, readDigest, revisionOf } from "./digest.js";

export const DEFAULT_SOCKET = "/run/tari-dailygrad/runner.sock";
export const REQUEST = '{"op":"run"}\n';
const MAX_REPLY = 64 * 1024;

const failure = (result, code, message, extra = {}) => ({ ok: false, result, error: { code, message }, ...extra });

const UNAVAILABLE =
  "The optional DailyGrad runner is unavailable; no request was accepted on this connection. " +
  "An existing published digest can still be read with dailygrad_digest.";

/** Send one request line and wait for the runner's single-line JSON reply. Never throws. */
function exchange(socketPath, line, { timeoutMs, signal, timeoutMessage }) {
  if (typeof socketPath !== "string" || !path.isAbsolute(socketPath)) {
    return Promise.resolve(failure("unavailable", "runner_not_configured",
      "No DailyGrad runner socket is configured. Published digest reading is still available."));
  }
  if (signal?.aborted) {
    return Promise.resolve(failure("unknown", "aborted", "The request was cancelled before connecting to the runner."));
  }
  return new Promise((resolve) => {
    let reply = "";
    let settled = false;
    const done = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener?.("abort", onAbort);
      sock.destroy();
      resolve(value);
    };
    const sock = net.createConnection(socketPath);
    const timer = setTimeout(() => done(failure("unknown", "runner_timeout", timeoutMessage)), timeoutMs);
    const onAbort = () => done(failure("unknown", "aborted",
      "Stopped waiting for the DailyGrad host runner. A run already started finishes on the host."));
    signal?.addEventListener?.("abort", onAbort, { once: true });

    sock.setEncoding("utf8");
    sock.on("connect", () => sock.write(line));
    sock.on("data", (chunk) => {
      reply += chunk;
      if (Buffer.byteLength(reply) > MAX_REPLY) done(failure("unknown", "bad_reply", "The DailyGrad host runner sent an oversized reply."));
    });
    sock.on("end", () => {
      try {
        const parsed = JSON.parse(reply.trim());
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed) || typeof parsed.ok !== "boolean") throw new Error("shape");
        done(parsed);
      } catch {
        done(failure("unknown", "bad_reply", "The DailyGrad host runner sent an unreadable reply."));
      }
    });
    sock.on("error", (err) => {
      if (err.code === "ENOENT" || err.code === "ECONNREFUSED") {
        done(failure("unavailable", "runner_unavailable", UNAVAILABLE));
      } else if (err.code === "EACCES") {
        done(failure("unavailable", "runner_unavailable",
          "The DailyGrad host runner's socket is not accessible from the container (permissions)."));
      } else {
        done(failure("unknown", "runner_error", `Talking to the DailyGrad host runner failed (${err.code ?? "error"}).`));
      }
    });
    sock.on("close", () => {
      if (!settled) done(failure("unknown", "runner_closed", "Runner connection closed without a complete result; verify before retrying."));
    });
  });
}

/** Send the one fixed run request and wait for the runner's single-line JSON reply. */
export function requestRun(socketPath = DEFAULT_SOCKET, { timeoutMs = 45 * 60_000, signal } = {}) {
  return exchange(socketPath, REQUEST, { timeoutMs, signal, timeoutMessage:
    "No answer from the DailyGrad host runner in time. The run may still finish on the host; "
    + "check later with dailygrad_digest." });
}

// ------------------------------------------------------------------ sources --
// The runner also lists and changes DailyGrad's news sources, through `dailygrad sources`
// on the host (the data mount stays read-only here). DailyGrad decides which IDs exist; this
// only checks their shape, the same way the runner does, so nothing malformed is sent.
export const SOURCE_ACTIONS = ["enable", "disable", "set"];
export const MAX_IDS = 16;
const SOURCE_ID = /^[A-Za-z0-9][A-Za-z0-9-]{0,63}$/u;

/** The request line for a sources operation, or { error } for an invalid one. */
export function sourcesRequest(action, ids) {
  if (action === "list") return { line: '{"op":"sources","action":"list"}\n' };
  if (!SOURCE_ACTIONS.includes(action)) {
    return { error: `action must be one of ${SOURCE_ACTIONS.join(", ")}` };
  }
  if (!Array.isArray(ids) || ids.length < 1 || ids.length > MAX_IDS) {
    return { error: `ids must be a list of 1 to ${MAX_IDS} source or group IDs` };
  }
  if (!ids.every((id) => typeof id === "string" && SOURCE_ID.test(id))) {
    return { error: "each ID must be 1-64 letters, digits and hyphens (for example hugging-face or openai)" };
  }
  return { line: `${JSON.stringify({ op: "sources", action, ids })}\n` };
}

/** List (action "list") or change DailyGrad's sources. Never throws, never starts a run. */
export async function requestSources(socketPath = DEFAULT_SOCKET, action, ids, { timeoutMs = 90_000, signal } = {}) {
  const req = sourcesRequest(action, ids);
  if (req.error) return failure("rejected", "bad_arguments", `${req.error}; nothing was sent or changed.`);
  const r = await exchange(socketPath, req.line, { timeoutMs, signal, timeoutMessage:
    "No answer from the DailyGrad host runner in time; whether the sources changed is unknown. List them again." });
  if (r.ok && (!Array.isArray(r.sources) || !Array.isArray(r.groups)
      || !["all_except", "only"].includes(r.mode)
      || r.sources.some((source) => !source || typeof source.id !== "string" || typeof source.enabled !== "boolean"))) {
    return failure("unknown", "bad_reply", "Runner returned invalid source state; the change is unverified.");
  }
  return r;
}

/** The parts of a sources result worth showing a model or an operator. */
export function sourcesSummary(r, action) {
  if (!r.ok) return { ok: false, action, error: r.error ?? { code: "unknown", message: "No result." } };
  const enabled = r.sources.filter((x) => x.enabled).map((x) => x.id);
  return {
    ok: true, action, changed: Boolean(r.changed), mode: r.mode,
    mode_meaning: r.mode === "only"
      ? "only: an exclusive selection; a source added to DailyGrad later stays disabled until enabled"
      : "all_except: every source except those disabled; a source added to DailyGrad later is enabled",
    enabled, disabled: r.sources.filter((x) => !x.enabled).map((x) => x.id),
    sources: r.sources, groups: r.groups,
    ...(action === "list" ? {} : { applies: "From the next DailyGrad run; no run was started and today's digest is unchanged." }),
  };
}

// ----------------------------------------------------------------- leetcode --
// DailyGrad's LeetCode exercise, through the runner. Three actions are about whether digests
// show a hint: read it (with which exercise is current), switch it on, switch it off; for
// those the action is the whole request. Two end the current exercise, which DailyGrad
// otherwise shows again every day: `complete` (the user is finished with it) and `next` (move
// on now). Those two also send the exercise's ID, one integer, and DailyGrad acts only if
// that is still its current exercise. DailyGrad keeps all of this state; the runner runs
// `dailygrad leetcode status`, `hints on|off`, `mark complete --exercise N` or
// `next --exercise N` for it.
export const LEETCODE_ACTIONS = ["status", "hints_on", "hints_off", "complete", "next"];
export const LEETCODE_TARGETED = ["complete", "next"]; // the actions that name an exercise
export const MAX_EXERCISE_ID = 999_999_999;

/** The request line for a leetcode action, or { error } for an invalid one. */
export function leetcodeRequest(action, exerciseId) {
  if (!LEETCODE_ACTIONS.includes(action)) return { error: `action must be one of ${LEETCODE_ACTIONS.join(", ")}` };
  if (!LEETCODE_TARGETED.includes(action)) {
    return exerciseId === undefined ? { line: `${JSON.stringify({ op: "leetcode", action })}\n` }
      : { error: `${action} takes no exercise_id` };
  }
  if (!Number.isInteger(exerciseId) || exerciseId < 1 || exerciseId > MAX_EXERCISE_ID) {
    return { error: `${action} needs exercise_id, the ID of DailyGrad's current exercise (a whole number from status)` };
  }
  return { line: `${JSON.stringify({ op: "leetcode", action, exercise: exerciseId })}\n` };
}

/** Carry out one leetcode action. Never throws, never starts a run or rewrites a digest. */
export async function requestLeetcode(socketPath = DEFAULT_SOCKET, action, { exerciseId, timeoutMs = 90_000, signal } = {}) {
  const req = leetcodeRequest(action, exerciseId);
  if (req.error) return failure("rejected", "bad_arguments", `${req.error}; nothing was sent or changed.`);
  const r = await exchange(socketPath, req.line, { timeoutMs, signal, timeoutMessage:
    "No answer from the DailyGrad host runner in time; whether anything changed is unknown. Ask for the status again." });
  if (r.ok && (typeof r.changed !== "boolean"
      || (["status", "hints_on", "hints_off"].includes(action) && typeof r.hints_enabled !== "boolean")
      || (action === "complete" && typeof r.completion_pending !== "boolean")
      || (action === "next" && (typeof r.previous !== "object" || !Object.hasOwn(r, "active"))))) {
    return failure("unknown", "bad_reply", "Runner returned invalid exercise state; the change is unverified.");
  }
  return r;
}

const NO_CHANGE_ELSEWHERE = "No run was started and existing digests are unchanged.";

/** The parts of a leetcode result worth showing a model or an operator. */
export function leetcodeSummary(r, action) {
  if (!r.ok) return { ok: false, action, error: r.error ?? { code: "unknown", message: "No result." } };
  if (action === "complete") {
    return {
      ok: true, action, changed: Boolean(r.changed), exercise: r.exercise,
      completion_pending: Boolean(r.completion_pending), next_track: r.next_track ?? null,
      meaning: r.changed ? "The exercise is recorded as completed. That is not a mark that the user solved it in code."
        : "The exercise was completed already; nothing changed.",
      applies: r.completion_pending ? `Another exercise is already current. ${NO_CHANGE_ELSEWHERE}`
        : `The next DailyGrad digest brings the next exercise${r.next_track ? `, from the ${r.next_track.name} track` : ""}; `
          + `none has been chosen yet. ${NO_CHANGE_ELSEWHERE}`,
    };
  }
  if (action === "next") {
    const skipped = r.previous?.outcome === "skipped";
    return {
      ok: true, action, changed: Boolean(r.changed), previous: r.previous, active: r.active ?? null,
      meaning: !r.changed ? "That exercise had been moved on from already; nothing changed, and nothing more was skipped."
        : skipped ? "The previous exercise is recorded as skipped: not completed, and not solved."
          : "The previous exercise stays completed; nothing was skipped.",
      applies: r.active
        ? `\`active\` is DailyGrad's current exercise now, and may be shown to the user as it is; it has no hint yet and `
          + "no reference solution. It is a new exercise, not a carry-over: no digest has shown it. Until the next "
          + "DailyGrad digest does, dailygrad_digest still carries the previous exercise, whose hint and reference "
          + `solution are not for this one. ${NO_CHANGE_ELSEWHERE}`
        : `There is no open exercise at the moment; read the status. ${NO_CHANGE_ELSEWHERE}`,
    };
  }
  const on = r.hints_enabled === true;
  const out = {
    ok: true, action, changed: Boolean(r.changed), hints_enabled: on,
    meaning: on
      ? "on: each new digest's LeetCode exercise includes one optional hint"
      : "off: new digests carry no hint; a hint the user asks for in conversation is still fine",
  };
  if (action !== "status") {
    out.applies = "From the next DailyGrad run; no run was started, and digests and posts that exist are unchanged.";
    return out;
  }
  out.exercises_enabled = r.exercises_enabled !== false;
  if (r.preferences_invalid) {
    out.preferences_invalid = true;
    out.note = "DailyGrad's hint preference file is damaged, so it shows no hint. Switching hints on or off replaces the file.";
  }
  // Which exercise is current: what `complete` and `next` must name. A DailyGrad from before
  // exercises were kept until ended reports none of this.
  out.completion_supported = typeof r.completion_pending === "boolean";
  if (out.completion_supported) {
    out.completion_pending = r.completion_pending;
    out.active_exercise_id = r.active_exercise_id ?? null;
    out.exercise = r.exercise ?? null;
    out.next_track = r.next_track ?? null;
    // An exercise assigned by `next` has been in no digest yet: the digest Tari can read is
    // still about the one before it.
    const unseen = r.completion_pending && r.exercise?.days_shown === 0;
    out.in_a_digest_yet = r.exercise ? !unseen : null;
    out.exercise_meaning = r.completion_pending
      ? "exercise is DailyGrad's current exercise: every digest shows it until the user completes it or moves on. "
        + "Pass active_exercise_id as exercise_id to complete or next."
        + (unseen ? " No digest has shown it yet: it was assigned when the user moved on, so it is a new exercise, not "
          + "a carry-over, and dailygrad_digest still carries the previous exercise (another exercise_id), whose hint "
          + "and reference solution are not for this one." : "")
      : r.exercise
        ? "exercise is the newest one and is already closed (see its outcome). No exercise is open: the next digest "
          + "assigns one, or next with its exercise_id does now."
        : "DailyGrad has not assigned an exercise yet.";
  }
  return out;
}

/**
 * The host's result, cross-checked inside the container: a reported new run must be
 * readable through digest.js and be the same run (run_id and generated_at).
 */
export async function verify(reply, dataDir, now = new Date(), timeZone = "UTC") {
  if (!reply.ok || !reply.new_run || !reply.digest) return { ...reply, verified: false };
  const read = await readDigest(dataDir, {}, now, timeZone);
  if (!read.ok) {
    return { ...reply, ok: false, verified: false,
      error: { code: "unverified", message: `The host reported a new run, but it cannot be read here (${read.error.code}).` } };
  }
  const same = read.digest.run_id === reply.digest.run_id && read.digest.generated_at === reply.digest.generated_at
    && read.digest.date === reply.digest.date && read.digest.status === reply.digest.status;
  if (!same) {
    return { ...reply, ok: false, verified: false,
      error: { code: "unverified", message: "latest.json no longer matches the run the host reported." } };
  }
  return { ...reply, verified: true, is_today: read.is_today, local_date: localDate(now, timeZone) };
}

/** Request a run and verify it. Never throws. */
export async function runDailyGrad({ socketPath = DEFAULT_SOCKET, dataDir, timeoutMs, signal, now, timeZone } = {}) {
  return verify(await requestRun(socketPath, { timeoutMs, signal }), dataDir, now, timeZone);
}

/** The parts of a result worth showing a model or an operator. */
export function summary(r) {
  const pick = (d) => d && { date: d.date, run_id: d.run_id, generated_at: d.generated_at, status: d.status,
    story_count: d.story_count, has_lesson: d.has_lesson };
  return {
    ok: r.ok, result: r.result, error: r.error ?? null,
    new_run: Boolean(r.new_run), is_today: Boolean(r.is_today), verified: Boolean(r.verified),
    ...(r.local_date ? { local_date: r.local_date } : {}),
    ...(r.exit_status !== undefined ? { exit_status: r.exit_status, timed_out: Boolean(r.timed_out),
      duration_seconds: r.duration_seconds } : {}),
    ...(r.digest ? { digest: pick(r.digest) } : {}),
    ...(r.latest && !r.new_run ? { latest_unchanged: pick(r.latest) } : {}),
    ...(r.retry_after_seconds !== undefined ? { retry_after_seconds: r.retry_after_seconds } : {}),
    ...(r.running_since ? { running_since: r.running_since } : {}),
  };
}

// ------------------------------------------------------------------- repair --
// `dailygrad repair` completes one earlier run's digest that the model failed on: the same
// stories, only the failed summaries and the lesson asked for again, archived as a new revision
// of that run (DailyGrad's docs/output.md). The request names the run by its ID and its date,
// the only things a caller can vary, and the runner maps it to the fixed command
// `dailygrad repair --run N --date D --json`. It shares the run lock and timeout. After a
// repair that wrote a revision, the run is read back through digest.js and must be that
// revision before it counts as verified. This plugin has no posting capability.
export const MAX_RUN_ID = 999_999_999;
const DAY = /^\d{4}-\d{2}-\d{2}$/u;
const realDay = (d) => {
  const t = typeof d === "string" && DAY.test(d) ? Date.parse(`${d}T00:00:00Z`) : NaN;
  return !Number.isNaN(t) && new Date(t).toISOString().slice(0, 10) === d;
};

/** The request line for a repair, or { error } for an invalid one. */
export function repairRequest(runId, date) {
  if (!Number.isInteger(runId) || runId < 1 || runId > MAX_RUN_ID) {
    return { error: "run_id must be the digest's run_id, a whole number (from dailygrad_digest)" };
  }
  if (!realDay(date)) return { error: "date must be the digest's date, a real calendar date written YYYY-MM-DD" };
  return { line: `${JSON.stringify({ op: "repair", run_id: runId, date })}\n` };
}

/** Ask the host to repair run `runId` of `date` and wait for its answer. Never throws. */
export async function requestRepair(socketPath = DEFAULT_SOCKET, runId, date, { timeoutMs = 45 * 60_000, signal } = {}) {
  const req = repairRequest(runId, date);
  if (req.error) return failure("rejected", "bad_arguments", `${req.error}; nothing was sent or changed.`);
  const r = await exchange(socketPath, req.line, { timeoutMs, signal, timeoutMessage:
    "No answer from the DailyGrad host runner in time. The repair may still finish on the host; "
    + "read the digest again later with dailygrad_digest." });
  if (r.ok && (!r.repair || typeof r.repair !== "object" || Array.isArray(r.repair)
      || !Number.isInteger(r.repair.revision) || typeof r.repair.outcome !== "string")) {
    return failure("unknown", "bad_reply", "Runner returned an invalid repair result; nothing is verified.");
  }
  return r;
}

/** A repair may fill failed content, but cannot change the selected run or completed work. */
function preservesRepair(before, after) {
  if (!before || !after || before.run_id !== after.run_id || before.date !== after.date
      || before.generated_at !== after.generated_at || before.stories.length !== after.stories.length) return false;
  for (let index = 0; index < before.stories.length; index++) {
    const original = before.stories[index];
    const repaired = after.stories[index];
    // Compare every stable identity in order, including absence of optional IDs.
    if (["id", "article_id", "url", "title", "source", "source_id"].some((key) =>
      !isDeepStrictEqual(original[key], repaired[key]))) return false;
    for (const key of ["what_happened", "why_it_matters"]) {
      if (typeof original[key] === "string" && original[key].trim()
          && original[key] !== repaired[key]) return false;
    }
  }
  if (before.lesson && !isDeepStrictEqual(before.lesson, after.lesson)) return false;
  if (before.recall && !isDeepStrictEqual(before.recall, after.recall)) return false;
  return isDeepStrictEqual(before.leetcode, after.leetcode);
}

/**
 * The host's repair result, cross-checked inside the container: the run, read back through
 * digest.js, must be the run that was named, at the revision and status the host reported.
 */
export async function verifyRepair(reply, dataDir, runId, date, now = new Date(), timeZone = "UTC", beforeDigest) {
  const rep = reply.repair;
  if (!reply.ok || !rep) return { ...reply, verified: false };
  if (rep.run_id !== runId || rep.date !== date) {
    return { ...reply, ok: false, verified: false,
      error: { code: "unverified", message: "The host reported a repair of another run than the one asked for." } };
  }
  const read = await readDigest(dataDir, { runId, date }, now, timeZone);
  if (!read.ok) {
    return { ...reply, ok: false, verified: false,
      error: { code: "unverified", message: `The repaired run cannot be read here (${read.error.code}).` } };
  }
  const d = read.digest;
  if (d.generated_at !== rep.generated_at || revisionOf(d) !== rep.revision || d.status !== rep.status) {
    return { ...reply, ok: false, verified: false,
      error: { code: "unverified", message: `DailyGrad's digest of run ${runId} is not the revision the host reported.` } };
  }
  if (!preservesRepair(beforeDigest, d)) {
    return { ...reply, ok: false, verified: false,
      error: { code: "unverified", message: "The repaired digest did not preserve the selected stories, "
        + "previously successful content, or LeetCode exercise. Inspect the digest before retrying." } };
  }
  const stories = Array.isArray(d.stories) ? d.stories : [];
  return { ...reply, verified: true, digest: { date: d.date, run_id: d.run_id, generated_at: d.generated_at,
    revision: revisionOf(d), status: d.status, story_count: stories.length,
    summarised: stories.filter((s) => typeof s?.what_happened === "string" && s.what_happened.trim()).length,
    has_lesson: Boolean(d.lesson && typeof d.lesson === "object") } };
}

/** Request a repair and verify it. Never throws. */
export async function repairDailyGrad({ socketPath = DEFAULT_SOCKET, dataDir, runId, date, timeoutMs, signal, now, timeZone, beforeDigest } = {}) {
  if (!beforeDigest) {
    const before = await readDigest(dataDir, { runId, date }, now, timeZone);
    if (!before.ok) return { ...before, result: "rejected", verified: false };
    beforeDigest = before.digest;
  }
  return verifyRepair(await requestRepair(socketPath, runId, date, { timeoutMs, signal }), dataDir, runId, date, now, timeZone, beforeDigest);
}

const MEANING = {
  repaired: "Everything the model had failed on was generated again: the digest is complete.",
  partial: "Some of what the model had failed on was generated again; `remaining` is still missing. A later repair retries only that.",
  unchanged: "Nothing could be generated again (the local model failed again, or a story's page gave no text). Nothing was written.",
  nothing_to_repair: "The digest is not missing anything a repair can generate. Nothing was written.",
};

/** The parts of a repair result worth showing a model or an operator, and what to do next. */
export function repairSummary(r) {
  if (!r.ok) {
    return { ok: false, result: r.result, error: r.error ?? { code: "unknown", message: "No result." }, verified: false,
      ...(r.retry_after_seconds !== undefined ? { retry_after_seconds: r.retry_after_seconds } : {}),
      ...(r.running_since ? { running_since: r.running_since } : {}),
      ...(r.exit_status !== undefined ? { exit_status: r.exit_status, timed_out: Boolean(r.timed_out) } : {}) };
  }
  const x = r.repair;
  return {
    ok: true, verified: Boolean(r.verified), outcome: x.outcome, meaning: MEANING[x.outcome],
    run_id: x.run_id, date: x.date, generated_at: x.generated_at, revision: x.revision,
    previous_revision: x.previous_revision, written: x.written, status: x.status,
    repaired: x.repaired, remaining: x.remaining,
    ...(x.lesson_note === "curriculum_moved_on" ? { lesson_note: "The lesson was not generated: a later DailyGrad run "
      + "has already given the next lesson, and this one would come out of order." } : {}),
    ...(x.remaining?.leetcode ? { leetcode_note: "The LeetCode exercise is missing from this digest; a repair does not "
      + "generate it." } : {}),
    ...(r.digest ? { digest: r.digest } : {}),
    duration_seconds: r.duration_seconds,
  };
}
