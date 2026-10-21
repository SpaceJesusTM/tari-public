import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, symlink, writeFile } from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { localDate, readDigest } from "../../plugins/tari-dailygrad/digest.js";
import { register } from "../../plugins/tari-dailygrad/tools.js";
import { requestRun } from "../../plugins/tari-dailygrad/runner-client.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const BASE = path.join(ROOT, ".test-tmp");
const DAY = "2026-10-20";
const document = (changes = {}) => ({
  schema_version: 1, date: DAY, run_id: 1, generated_at: `${DAY}T13:00:00Z`,
  status: "ok", model: "synthetic-fixture", stories: [{ title: "Synthetic story", source: "Fixture",
    url: "https://example.org/story", what_happened: "Synthetic summary", why_it_matters: "For isolated testing" }],
  lesson: { title: "Synthetic lesson", lesson: "A test lesson." }, recall: { question: "A test question?" },
  ...changes,
});
const stamp = (doc) => new Date(doc.generated_at).toISOString().replace(/[-:]/gu, "").replace(/\.\d{3}Z$/u, "Z");

async function setup(t) {
  await mkdir(BASE, { recursive: true });
  const root = await mkdtemp(path.join(BASE, "dg-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const data = path.join(root, "data");
  await mkdir(data);
  return { root, data, socket: path.join(root, "s") };
}

async function publish(data, doc, { archive = true, latest = true, dated = true } = {}) {
  if (latest) await writeFile(path.join(data, "latest.json"), JSON.stringify(doc));
  if (dated) {
    await mkdir(path.join(data, "digests"), { recursive: true });
    await writeFile(path.join(data, "digests", `${doc.date}.json`), JSON.stringify(doc));
  }
  if (archive) {
    const revision = doc.repair?.revision;
    const dir = path.join(data, "digests", revision ? "repairs" : "runs", doc.date);
    await mkdir(dir, { recursive: true });
    await writeFile(path.join(dir, `run-${doc.run_id}-${stamp(doc)}${revision ? `-r${revision}` : ""}.json`), JSON.stringify(doc));
  }
}

function tools(config = {}) {
  const found = new Map();
  register({ pluginConfig: config, registerTool(tool) { found.set(tool.name, tool); } });
  return found;
}

function configured(data, socket, changes = {}) {
  return tools({ enabled: true, dataDir: data, runnerSocket: socket, timeZone: "America/Toronto",
    allowRun: true, allowRepair: true, allowSourcesUpdate: true, allowLeetcodeUpdate: true, ...changes });
}

async function invoke(tools, name, params = {}) {
  const result = await tools.get(name).execute("isolated-test", params);
  assert.deepEqual(JSON.parse(result.content[0].text), JSON.parse(JSON.stringify(result.details)));
  return result.details;
}

async function runner(t, socketPath, handler) {
  const requests = [];
  const server = net.createServer((connection) => {
    let buffer = "";
    connection.setEncoding("utf8");
    connection.on("error", () => {});
    connection.on("data", async (chunk) => {
      buffer += chunk;
      if (!buffer.includes("\n")) return;
      const request = JSON.parse(buffer.trim());
      requests.push(request);
      try {
        const reply = await handler(request);
        if (reply !== undefined) connection.end(typeof reply === "string" ? reply : JSON.stringify(reply) + "\n");
      } catch (error) {
        connection.destroy();
        throw error;
      }
    });
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(socketPath, resolve);
  });
  t.after(() => new Promise((resolve) => server.close(resolve)));
  return requests;
}

test("plugin is disabled until explicit config and six tools register only when enabled", () => {
  assert.equal(tools().size, 0);
  assert.equal(tools({ enabled: false }).size, 0);
  assert.deepEqual([...configured("/unused", null).keys()].sort(), ["dailygrad_digest", "dailygrad_leetcode",
    "dailygrad_repair", "dailygrad_run", "dailygrad_sources", "dailygrad_sources_update"]);
});

test("digest works with no runner and preserves missing, stale, degraded, date and run versions", async (t) => {
  const { data } = await setup(t);
  const api = configured(data, null);
  assert.equal((await invoke(api, "dailygrad_digest")).error.code, "not_found");
  const first = document({ status: "degraded", lesson: null, stories: [{ title: "Missing summary", model_failed: true }] });
  const second = document({ run_id: 2, generated_at: `${DAY}T15:00:00Z` });
  await publish(data, first);
  await publish(data, second);
  const latest = await invoke(api, "dailygrad_digest");
  assert.equal(latest.digest.run_id, 2);
  assert.equal(latest.is_stale, true);
  assert.equal(latest.is_today, false);
  assert.equal(latest.is_latest_run, true);
  assert.equal(latest.versions.length, 2);
  assert.equal(latest.versions_complete, true);
  const older = await invoke(api, "dailygrad_digest", { date: DAY, run_id: 1 });
  assert.equal(older.digest.status, "degraded");
  assert.equal(older.digest.lesson, null);
  assert.equal(older.digest.stories[0].number, 1);
  assert.equal(older.digest.stories[0].what_happened, undefined);
  assert.equal(older.is_latest_run, false);
  assert.equal((await invoke(api, "dailygrad_digest", { date: DAY })).digest.run_id, 2);
  assert.equal((await invoke(api, "dailygrad_run", { timeoutSeconds: 570 })).error.code, "runner_not_configured");
});

test("digest timezone is explicit across a UTC date boundary", async (t) => {
  const { data } = await setup(t);
  await publish(data, document());
  const now = new Date("2026-10-21T02:00:00Z");
  assert.equal(localDate(now, "America/Toronto"), DAY);
  assert.equal((await readDigest(data, {}, now, "America/Toronto")).is_today, true);
  assert.equal((await readDigest(data, {}, now, "UTC")).is_today, false);
});

test("digest refuses corrupt JSON, unsupported schema, traversal and escaping symlinks", async (t) => {
  const { root, data } = await setup(t);
  const api = configured(data, null);
  await writeFile(path.join(data, "latest.json"), "{ broken");
  assert.equal((await invoke(api, "dailygrad_digest")).error.code, "invalid_json");
  await publish(data, document({ schema_version: 999 }));
  assert.equal((await invoke(api, "dailygrad_digest")).error.code, "unsupported_schema");
  assert.equal((await invoke(api, "dailygrad_digest", { date: "../../private" })).error.code, "bad_date");
  assert.equal((await invoke(api, "dailygrad_digest", { extra: "do not accept" })).error.code, "bad_arguments");
  await rm(path.join(data, "latest.json"));
  const outside = path.join(root, "outside.json");
  await writeFile(outside, JSON.stringify(document()));
  await symlink(outside, path.join(data, "latest.json"));
  assert.equal((await invoke(api, "dailygrad_digest")).error.code, "outside_data_dir");
});

test("run verifies the exact mounted digest before reporting success", async (t) => {
  const { data, socket } = await setup(t);
  await publish(data, document());
  const next = document({ run_id: 2, generated_at: `${DAY}T15:00:00Z`, status: "degraded", lesson: null });
  const requests = await runner(t, socket, async () => {
    await publish(data, next);
    return { ok: true, result: "completed", new_run: true, is_today: true, digest: {
      run_id: next.run_id, generated_at: next.generated_at, date: next.date, status: next.status,
      story_count: next.stories.length, has_lesson: false } };
  });
  const result = await invoke(configured(data, socket), "dailygrad_run", { timeoutSeconds: 570 });
  assert.equal(result.ok, true);
  assert.equal(result.verified, true);
  assert.equal(result.digest.status, "degraded");
  assert.equal(result.is_today, false); // Recomputed from configured timezone; runner's claim is not trusted.
  assert.deepEqual(requests, [{ op: "run" }]);
});

test("run mismatch and malformed replies remain unverified", async (t) => {
  const { data, socket } = await setup(t);
  await publish(data, document());
  let malformed = false;
  await runner(t, socket, () => malformed ? "{bad reply\n" : {
    ok: true, new_run: true, digest: { run_id: 99, date: DAY, generated_at: `${DAY}T14:00:00Z`, status: "ok" },
  });
  const api = configured(data, socket);
  const result = await invoke(api, "dailygrad_run", { timeoutSeconds: 570 });
  assert.equal(result.ok, false);
  assert.equal(result.verified, false);
  assert.equal(result.error.code, "unverified");
  malformed = true;
  assert.equal((await invoke(api, "dailygrad_run", { timeoutSeconds: 570 })).error.code, "bad_reply");
});

test("repair rereads and verifies a new revision of the same run without publishing", async (t) => {
  const { data, socket } = await setup(t);
  const before = document({ status: "degraded", lesson: null });
  await publish(data, before);
  const repaired = document({ repair: { revision: 1, repaired_at: `${DAY}T14:00:00Z` } });
  const requests = await runner(t, socket, async () => {
    await publish(data, repaired);
    return { ok: true, repair: { run_id: 1, date: DAY, generated_at: before.generated_at,
      revision: 1, previous_revision: 0, written: true, status: "ok", outcome: "repaired", remaining: {} } };
  });
  const result = await invoke(configured(data, socket), "dailygrad_repair", { run_id: 1, date: DAY, timeoutSeconds: 570 });
  assert.equal(result.ok, true);
  assert.equal(result.verified, true);
  assert.equal(result.revision, 1);
  assert.equal(result.next_step, undefined);
  assert.deepEqual(requests, [{ op: "repair", run_id: 1, date: DAY }]);
  const reading = await invoke(configured(data, null), "dailygrad_digest", { date: DAY, run_id: 1 });
  assert.equal(reading.digest.repair.revision, 1);
  assert.equal(reading.versions.length, 1);
});

test("repair cannot verify changed story identities, order, successful summaries or exercise", async (t) => {
  const changes = {
    identity: (doc) => { doc.stories[0].url = "https://example.org/replacement"; },
    order: (doc) => { doc.stories.reverse(); },
    summary: (doc) => { doc.stories[0].what_happened = "Unexpected rewrite of an existing summary"; },
    exercise: (doc) => { doc.leetcode.exercise_id = 8; },
  };
  for (const [name, change] of Object.entries(changes)) {
    await t.test(name, async (subtest) => {
      const { data, socket } = await setup(subtest);
      const before = document({ status: "degraded", lesson: null,
        stories: [...document().stories, { title: "Second synthetic story", url: "https://example.org/second", model_failed: true }],
        leetcode: { exercise_id: 7, title: "Existing exercise", hints_enabled: false } });
      await publish(data, before);
      const after = structuredClone({ ...before, status: "ok", lesson: { title: "Completed lesson", lesson: "Repaired content" },
        repair: { revision: 1, repaired_at: `${DAY}T14:00:00Z` } });
      change(after);
      const requests = await runner(subtest, socket, async () => {
        await publish(data, after);
        return { ok: true, repair: { run_id: 1, date: DAY, generated_at: before.generated_at,
          revision: 1, previous_revision: 0, written: true, status: "ok", outcome: "repaired", remaining: {} } };
      });
      const result = await invoke(configured(data, socket), "dailygrad_repair", { run_id: 1, date: DAY, timeoutSeconds: 570 });
      assert.equal(result.ok, false);
      assert.equal(result.verified, false);
      assert.equal(result.error.code, "unverified");
      assert.equal(requests.length, 1);
    });
  }
});

test("source list and updates use exact typed IDs and preserve runner refusal", async (t) => {
  const { data, socket } = await setup(t);
  const state = { ok: true, changed: false, mode: "all_except", sources: [
    { id: "fixture", name: "Synthetic feed", enabled: true }], groups: [] };
  const requests = await runner(t, socket, (request) => request.action === "disable"
    ? { ok: false, result: "rejected", error: { code: "capability_disabled", message: "Disabled by owner" } }
    : state);
  const api = configured(data, socket);
  assert.deepEqual((await invoke(api, "dailygrad_sources")).enabled, ["fixture"]);
  assert.equal((await invoke(api, "dailygrad_sources_update", { action: "disable", ids: ["fixture"] })).error.code,
    "capability_disabled");
  const invalid = await invoke(api, "dailygrad_sources_update", { action: "set", ids: ["../../private"] });
  assert.equal(invalid.error.code, "bad_arguments");
  assert.deepEqual(requests, [{ op: "sources", action: "list" }, { op: "sources", action: "disable", ids: ["fixture"] }]);
});

test("exercise status, hint preference, completion and next preserve their distinct meaning", async (t) => {
  const { data, socket } = await setup(t);
  const requests = await runner(t, socket, (request) => {
    if (request.action === "next") return { ok: true, changed: true, previous: { id: 7, outcome: "skipped" },
      active: { id: 8, title: "Synthetic next problem", days_shown: 0 } };
    if (request.action === "complete") return { ok: true, changed: true, exercise: { id: 7 }, completion_pending: false };
    return { ok: true, changed: request.action !== "status", hints_enabled: request.action === "hints_on",
      completion_pending: true, active_exercise_id: 7, exercise: { id: 7, days_shown: 0 } };
  });
  const api = configured(data, socket);
  assert.equal((await invoke(api, "dailygrad_leetcode", { action: "status" })).active_exercise_id, 7);
  assert.equal((await invoke(api, "dailygrad_leetcode", { action: "hints_on" })).hints_enabled, true);
  assert.match((await invoke(api, "dailygrad_leetcode", { action: "complete", exercise_id: 7 })).meaning, /not a mark/u);
  assert.match((await invoke(api, "dailygrad_leetcode", { action: "next", exercise_id: 7 })).meaning, /skipped/u);
  assert.equal((await invoke(api, "dailygrad_leetcode", { action: "complete" })).error.code, "bad_arguments");
  assert.deepEqual(requests, [{ op: "leetcode", action: "status" }, { op: "leetcode", action: "hints_on" },
    { op: "leetcode", action: "complete", exercise: 7 }, { op: "leetcode", action: "next", exercise: 7 }]);
});

test("mutations default off and schema bypass arguments never contact runner", async (t) => {
  const { data, socket } = await setup(t);
  const requests = await runner(t, socket, () => { throw new Error("A disabled mutation reached the runner"); });
  const api = configured(data, socket, { allowRun: false, allowRepair: false, allowSourcesUpdate: false, allowLeetcodeUpdate: false });
  for (const [name, args] of [["dailygrad_run", { timeoutSeconds: 570 }],
    ["dailygrad_repair", { timeoutSeconds: 570, date: DAY, run_id: 1 }],
    ["dailygrad_sources_update", { action: "set", ids: ["all"] }],
    ["dailygrad_leetcode", { action: "hints_on" }]]) {
    assert.equal((await invoke(api, name, args)).error.code, "capability_disabled");
  }
  for (const [name, args] of [["dailygrad_run", { timeoutSeconds: 1 }],
    ["dailygrad_run", { timeoutSeconds: 570, command: "arbitrary" }],
    ["dailygrad_sources", []], ["dailygrad_leetcode", { action: "status", exercise_id: 7 }]]) {
    assert.equal((await invoke(api, name, args)).ok, false);
  }
  assert.equal(requests.length, 0);
});

test("busy, cooldown and unavailable responses remain honest", async (t) => {
  const { data, socket } = await setup(t);
  let code = "busy";
  await runner(t, socket, () => ({ ok: false, result: code, new_run: false,
    retry_after_seconds: code === "cooldown" ? 10 : undefined, error: { code, message: "Synthetic refusal" } }));
  const api = configured(data, socket);
  assert.equal((await invoke(api, "dailygrad_run", { timeoutSeconds: 570 })).result, "busy");
  code = "cooldown";
  assert.equal((await invoke(api, "dailygrad_run", { timeoutSeconds: 570 })).retry_after_seconds, 10);
  const missing = configured(data, path.join(path.dirname(socket), "missing"));
  assert.equal((await invoke(missing, "dailygrad_run", { timeoutSeconds: 570 })).error.code, "runner_unavailable");
});

test("timeout remains unknown and never retries; pre-aborted request never connects", async (t) => {
  const { socket } = await setup(t);
  const requests = await runner(t, socket, () => undefined);
  const result = await requestRun(socket, { timeoutMs: 15 });
  assert.equal(result.ok, false);
  assert.equal(result.result, "unknown");
  assert.equal(result.error.code, "runner_timeout");
  assert.match(result.error.message, /may still finish/u);
  assert.equal(requests.length, 1);
  const controller = new AbortController();
  controller.abort();
  assert.equal((await requestRun(socket, { signal: controller.signal })).error.code, "aborted");
  assert.equal(requests.length, 1);
});
