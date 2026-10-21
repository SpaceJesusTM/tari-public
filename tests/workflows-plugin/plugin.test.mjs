import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { BRIEF_HELPER, BRIEF_MAX_BYTES, BRIEF_TIMEOUT_MS, REMINDER_HELPER, REMINDER_MAX_BYTES,
  REMINDER_TIMEOUT_MS, WORKSPACE, briefEnvironment, reminderEnvironment, register }
  from "../../plugins/tari-workflows/tools.js";

const context = (changes = {}) => ({ agentId: "tari", workspaceDir: WORKSPACE,
  sessionKey: "agent:tari:main", assertInvocationCurrent() {}, ...changes });
const document = (changes = {}) => ({ date: "2026-10-21", time_zone: "UTC", read_only: true,
  sources: {
    planner: { status: "available", quality: "complete", data: { schedule: [], checklist: [] } },
    domains: { status: "available", files: [] },
    memory: { status: "not_requested" },
    gmail: { status: "not_configured" },
    calendar: { status: "not_configured" },
  }, ...changes });

function tools({ config = {}, run, environment = { PATH: "/usr/bin", TARI_TIMEZONE: "UTC" } } = {}) {
  const registrations = [], hooks = [], calls = [];
  const runStub = run ?? (async (...args) => {
    calls.push(args);
    return { stdout: JSON.stringify(document()), stderr: "" };
  });
  register({ pluginConfig: config,
    registerTool(factory, options) { registrations.push({ factory, options }); },
    on(name, handler) { hooks.push({ name, handler }); },
  }, { run: runStub, environment });
  return { registrations, hooks, calls, tool: (trusted = context()) => registrations[0]?.factory(trusted),
    reminders: (trusted = context()) => {
      const definitions = registrations[1]?.factory(trusted);
      return definitions ? new Map(definitions.map((tool) => [tool.name, tool])) : null;
    } };
}

async function invoke(tool, params, signal) {
  const response = await tool.execute("fixture-call", params, signal);
  assert.deepEqual(JSON.parse(response.content[0].text), JSON.parse(JSON.stringify(response.details)));
  return response.details;
}

test("native factories register the brief and reminder contracts and one Discord guard, with explicit disable support", () => {
  const registered = tools();
  assert.equal(registered.registrations.length, 2);
  assert.deepEqual(registered.registrations[0].options, { names: ["tari_brief_context"] });
  assert.equal(registered.tool().name, "tari_brief_context");
  assert.equal(registered.tool().parameters.additionalProperties, false);
  assert.equal(registered.hooks.filter((hook) => hook.name === "message_sending").length, 1);
  const disabled = tools({ config: { enabled: false } });
  assert.equal(disabled.registrations.length, 0);
  assert.equal(disabled.hooks.length, 0);
});

test("only the runtime-selected Tari workspace is eligible; argument text cannot grant authority", async () => {
  const registered = tools();
  for (const trusted of [undefined, {}, context({ agentId: "other" }), context({ workspaceDir: "/outside" }),
    context({ workspaceDir: undefined }), context({ agentId: undefined })]) {
    assert.equal(registered.registrations[0].factory(trusted), null);
  }
  assert.equal(registered.calls.length, 0);
  const rejected = await invoke(registered.tool(), { agentId: "tari", workspaceDir: WORKSPACE });
  assert.equal(rejected.error.code, "bad_arguments");
  assert.equal(registered.calls.length, 0);
});

test("native-authorized CLI, scheduled and Discord DM/guild contexts use the same read-only tool", async () => {
  const registered = tools();
  for (const trusted of [context({ oneShotCliRun: true }),
    context({ sessionKey: "agent:tari:cron:fixture" }),
    context({ messageChannel: "discord", requesterSenderId: "fixture-user", senderIsOwner: false,
      sessionKey: "agent:tari:discord:direct:fixture" }),
    context({ messageChannel: "discord", nativeChannelId: "fixture-channel", senderIsOwner: false,
      sessionKey: "agent:tari:discord:channel:fixture" })]) {
    assert.equal((await invoke(registered.tool(trusted), {})).ok, true);
  }
  assert.equal(registered.calls.length, 4);
});

test("execution uses only the fixed Python helper, bounded options and validated timezone environment", async () => {
  const registered = tools({ environment: {
    PATH: "/usr/bin", HOME: "/home/node", LANG: "C.UTF-8", TARI_TIMEZONE: "UTC", TARI_WORKSPACE: "/ignored",
    TARI_GOOGLE_ACCOUNT: "fixture@example.test", GOG_ACCOUNT: "fixture@example.test", GOG_HOME: "/isolated-google",
    GOG_KEYRING_BACKEND: "file", GOG_KEYRING_PASSWORD: "fixture-keyring-secret", XDG_CACHE_HOME: "/tmp/cache",
    OPENAI_API_KEY: "fixture-provider-secret", OPENCLAW_GATEWAY_TOKEN: "fixture-operator-secret",
    DISCORD_BOT_TOKEN: "fixture-discord-secret", GOG_ACCESS_TOKEN: "fixture-unscoped-token",
    NODE_OPTIONS: "--import=arbitrary-module", PYTHONPATH: "/arbitrary-imports", XDG_PROVIDER_SECRET: "do-not-forward",
  } });
  assert.equal((await invoke(registered.tool(), { day: "tomorrow", time_zone: "Europe/Berlin" })).ok, true);
  const [executable, args, options] = registered.calls[0];
  assert.equal(executable, "/usr/bin/python3");
  assert.deepEqual(args, ["-B", BRIEF_HELPER, "tomorrow"]);
  assert.equal(options.cwd, WORKSPACE);
  assert.equal(options.timeout, BRIEF_TIMEOUT_MS);
  assert.equal(options.maxBuffer, BRIEF_MAX_BYTES);
  assert.equal(options.shell, false);
  assert.equal(options.env.TARI_WORKSPACE, WORKSPACE);
  assert.equal(options.env.TARI_TIMEZONE, "Europe/Berlin");
  assert.equal(options.env.TZ, "Europe/Berlin");
  assert.equal(options.env.GOG_KEYRING_PASSWORD, "fixture-keyring-secret");
  assert.equal(options.env.GOG_HOME, "/isolated-google");
  for (const forbidden of ["OPENAI_API_KEY", "OPENCLAW_GATEWAY_TOKEN", "DISCORD_BOT_TOKEN", "GOG_ACCESS_TOKEN",
    "NODE_OPTIONS", "PYTHONPATH", "XDG_PROVIDER_SECRET"]) assert.equal(options.env[forbidden], undefined);
  assert.equal(Object.hasOwn(options, "input"), false);
  assert.equal(args.includes("--private-memory"), false);
});

test("arguments cannot add commands, flags, stdin, private-memory or ambiguous dates", async () => {
  const registered = tools();
  for (const params of [null, [], "today", { day: "2026-10-21" }, { day: "today; arbitrary" },
    { command: "arbitrary" }, { path: "/outside" }, { args: ["--private-memory"] },
    { private_memory: true }, { stdin: "arbitrary" }, { time_zone: "Invalid/Zone" },
    { time_zone: "UTC\n--private-memory" }, { time_zone: 5 }]) {
    const result = await invoke(registered.tool(), params);
    assert.equal(result.error.code, "bad_arguments");
  }
  assert.equal(registered.calls.length, 0);
});

test("missing timezone defaults to UTC, while a broken configured zone fails before collection", async () => {
  assert.equal(briefEnvironment({}, "UTC").TARI_TIMEZONE, "UTC");
  const defaults = tools({ environment: {} });
  assert.equal((await invoke(defaults.tool())).ok, true);
  assert.equal(defaults.calls[0][2].env.TARI_TIMEZONE, "UTC");
  const invalid = tools({ environment: { TARI_TIMEZONE: "Invalid/Zone" } });
  assert.equal((await invoke(invalid.tool())).error.code, "invalid_timezone");
  assert.equal(invalid.calls.length, 0);
});

test("cancelled or expired invocations start no child and post-collection authority is rechecked", async () => {
  const registered = tools();
  const controller = new AbortController();
  controller.abort();
  assert.equal((await invoke(registered.tool(), {}, controller.signal)).error.code, "cancelled");
  assert.equal((await invoke(registered.tool(context({ assertInvocationCurrent() { throw Error("PRIVATE"); } })), {})).error.code,
    "invocation_expired");
  assert.equal(registered.calls.length, 0);
  let calls = 0;
  const result = await invoke(registered.tool(context({ assertInvocationCurrent() {
    calls += 1; if (calls === 2) throw Error("PRIVATE CLOSED TURN");
  } })), {});
  assert.equal(result.ok, false);
  assert.equal(calls, 2);
  assert.equal(JSON.stringify(result).includes("PRIVATE"), false);
});

test("collection failures, stderr and malformed or oversized output never leak raw diagnostics", async () => {
  for (const failure of [Object.assign(new Error("PRIVATE RUN ERROR"), { stderr: "PRIVATE STDERR" }),
    { stdout: "{PRIVATE INVALID", stderr: "PRIVATE STDERR" },
    { stdout: JSON.stringify({ read_only: true, sources: [] }), stderr: "PRIVATE STDERR" },
    { stdout: JSON.stringify({ read_only: false, sources: {} }), stderr: "PRIVATE STDERR" },
    { stdout: "x".repeat(BRIEF_MAX_BYTES + 1), stderr: "PRIVATE STDERR" }]) {
    const registered = tools({ run: async () => { if (failure instanceof Error) throw failure; return failure; } });
    const result = await invoke(registered.tool(), {});
    assert.equal(result.ok, false);
    assert.equal(JSON.stringify(result).includes("PRIVATE"), false);
    assert.equal(JSON.stringify(result).length < 512, true);
  }
});

test("structured source status and warning quality survive and curated memory is always omitted", async () => {
  const data = document();
  data.sources.planner = { status: "partial", quality: "malformed",
    warnings: [{ code: "PLANNER_CHECKLIST_MALFORMED", message: "Some entries were not parsed." }],
    data: { schedule: [], checklist: [] } };
  data.sources.memory = { status: "available", text: "fixture curated memory must never leave this tool" };
  const registered = tools({ run: async () => ({ stdout: JSON.stringify(data), stderr: "PRIVATE RAW DIAGNOSTIC" }) });
  const result = await invoke(registered.tool(), {});
  assert.equal(result.ok, true);
  assert.equal(result.sources.planner.status, "partial");
  assert.equal(result.sources.planner.quality, "malformed");
  assert.equal(result.sources.planner.warnings[0].code, "PLANNER_CHECKLIST_MALFORMED");
  assert.equal(result.sources.memory.status, "not_requested");
  assert.equal(JSON.stringify(result).includes("fixture curated memory"), false);
  assert.equal(JSON.stringify(result).includes("PRIVATE RAW DIAGNOSTIC"), false);
  assert.equal(result.security.gmail_metadata_only, true);
});

test("manifest and package declare the same narrow native tool contract", async () => {
  const manifest = JSON.parse(await readFile(new URL("../../plugins/tari-workflows/openclaw.plugin.json", import.meta.url), "utf8"));
  const pkg = JSON.parse(await readFile(new URL("../../plugins/tari-workflows/package.json", import.meta.url), "utf8"));
  assert.equal(manifest.id, "tari-workflows");
  assert.deepEqual(manifest.contracts.tools, ["tari_brief_context", "tari_reminder_create", "tari_reminder_list", "tari_reminder_cancel"]);
  assert.equal(manifest.configSchema.additionalProperties, false);
  assert.equal(manifest.configSchema.properties.enabled.default, true);
  assert.deepEqual(pkg.openclaw.extensions, ["./index.js"]);
});

function reminderRunner(response) {
  const calls = [];
  const run = (...args) => {
    const call = { executable: args[0], args: args[1], options: args[2] };
    calls.push(call);
    const execution = Promise.resolve().then(() => {
      if (response instanceof Error) throw response;
      const value = typeof response === "function" ? response(call) : response;
      return typeof value?.stdout === "string" ? value : { stdout: JSON.stringify(value), stderr: "PRIVATE BACKEND STDERR" };
    });
    execution.child = { stdin: { end(body) { call.input = body; } }, kill() { call.killed = true; } };
    return execution;
  };
  return { run, calls };
}

const created = () => ({ ok: true, registered: true, job_id: "fixture-reminder-job", at: "2027-01-01T12:00:00Z",
  text: "Review presentation", destination: { channel: "discord", to: "user:fixture" }, enabled: true,
  delivered: false, registration_note: "Registration is confirmed; delivery remains pending." });

test("reminder factories permit trusted local operators and native Discord owners, including guild owners", () => {
  const registered = tools();
  assert.deepEqual(registered.registrations[1].options,
    { names: ["tari_reminder_create", "tari_reminder_list", "tari_reminder_cancel"] });
  for (const trusted of [context(), context({ messageChannel: "webchat" }),
    context({ messageChannel: "discord", senderIsOwner: true, sessionKey: "agent:tari:discord:direct:owner" }),
    context({ messageChannel: "discord", senderIsOwner: true, sessionKey: "agent:tari:discord:channel:private-guild" })]) {
    assert.equal(registered.reminders(trusted).size, 3);
  }
  for (const trusted of [context({ senderIsOwner: false }), context({ messageChannel: "webchat", senderIsOwner: false }),
    context({ messageChannel: "discord", senderIsOwner: false }),
    context({ messageChannel: "discord", senderIsOwner: undefined }), context({ messageChannel: "telegram", senderIsOwner: true }),
    context({ sessionKey: "agent:tari:cron:fixture", senderIsOwner: true }),
    context({ sessionKey: "agent:tari:subagent:fixture", senderIsOwner: true }), context({ assertInvocationCurrent: undefined }),
    context({ agentId: "other" }), context({ workspaceDir: "/outside" })]) assert.equal(registered.reminders(trusted), null);
});

test("reminder creation sends only bounded JSON stdin to the fixed backend, with no bot/provider/Google credentials", async () => {
  const runner = reminderRunner(created());
  const registered = tools({ run: runner.run, environment: {
    PATH: "/usr/bin", HOME: "/home/node", LANG: "C.UTF-8", TARI_TIMEZONE: "Europe/Berlin",
    OPENCLAW_GATEWAY_TOKEN: "fixture-gateway-authority", OPENCLAW_STATE_DIR: "/ignored-state",
    OPENCLAW_CONFIG_PATH: "/ignored-config", OPENAI_API_KEY: "fixture-provider-secret",
    TARI_DISCORD_BOT_TOKEN: "fixture-bot-secret", GOG_KEYRING_PASSWORD: "fixture-google-secret",
    PYTHONPATH: "/injected-imports", NODE_OPTIONS: "--import=arbitrary", TARI_WORKSPACE: "/outside",
  } });
  const text = "Review $(arbitrary) `text`; --destination other\nEverything stays data.";
  const response = await invoke(registered.reminders().get("tari_reminder_create"),
    { when: "20m", text, request_key: "fixture-request:1" });
  assert.equal(response.registered, true);
  assert.equal(response.delivered, false);
  const call = runner.calls[0];
  assert.equal(call.executable, "/usr/bin/python3");
  assert.deepEqual(call.args, ["-B", REMINDER_HELPER, "runtime"]);
  assert.deepEqual(JSON.parse(call.input), { action: "reminder_create", when: "20m", text, request_key: "fixture-request:1" });
  assert.equal(call.options.timeout, REMINDER_TIMEOUT_MS);
  assert.equal(call.options.maxBuffer, REMINDER_MAX_BYTES);
  assert.equal(call.options.shell, false);
  assert.equal(call.options.cwd, WORKSPACE);
  assert.equal(call.options.env.OPENCLAW_GATEWAY_TOKEN, "fixture-gateway-authority");
  assert.equal(call.options.env.OPENCLAW_STATE_DIR, "/home/node/.openclaw");
  assert.equal(call.options.env.OPENCLAW_CONFIG_PATH, "/home/node/.openclaw/openclaw.json");
  for (const key of ["OPENAI_API_KEY", "TARI_DISCORD_BOT_TOKEN", "GOG_KEYRING_PASSWORD", "PYTHONPATH", "NODE_OPTIONS", "TARI_WORKSPACE"]) {
    assert.equal(call.options.env[key], undefined);
  }
  assert.equal(response.untrusted_content, true);
  assert.equal(JSON.stringify(response).includes("fixture-gateway-authority"), false);
});

test("reminder list and cancellation use only their typed backend actions and preserve delivery uncertainty", async () => {
  const runner = reminderRunner((call) => {
    const request = JSON.parse(call.input);
    return request.action === "reminder_list"
      ? { ok: true, configured: true, timezone: "UTC", now: "2027-01-01T12:00:00Z", reminders: [{ job_id: "fixture", state: "delivery_unknown" }] }
      : { ok: true, job_id: "fixture", cancelled: true, future_runs_disabled: true, delivery_note: "Previously delivered messages cannot be undone." };
  });
  const reminderTools = tools({ run: runner.run }).reminders();
  const listed = await invoke(reminderTools.get("tari_reminder_list"), { history: true });
  assert.equal(listed.reminders[0].state, "delivery_unknown");
  assert.deepEqual(JSON.parse(runner.calls[0].input), { action: "reminder_list", history: true });
  const cancelled = await invoke(reminderTools.get("tari_reminder_cancel"), { reference: "fixture" });
  assert.equal(cancelled.future_runs_disabled, true);
  assert.deepEqual(JSON.parse(runner.calls[1].input), { action: "reminder_cancel", reference: "fixture" });
});

test("reminder fields cannot select destinations, executables, flags or authority; known sensitive text is rejected", async () => {
  const runner = reminderRunner(created());
  const reminderTools = tools({ run: runner.run }).reminders();
  const valid = { when: "20m", text: "Review presentation" };
  for (const params of [null, [], { ...valid, destination: "user:other" }, { ...valid, senderIsOwner: true },
    { ...valid, command: "arbitrary" }, { ...valid, argv: ["arbitrary"] }, { ...valid, request_key: "../outside" },
    { ...valid, when: "x".repeat(101) }, { ...valid, when: "20m\n--flag" }, { ...valid, text: " " },
    { ...valid, text: "Your verification code is 123456" }, { ...valid, text: "x".repeat(1501) }]) {
    assert.equal((await invoke(reminderTools.get("tari_reminder_create"), params)).error.code, "bad_arguments");
  }
  assert.equal((await invoke(reminderTools.get("tari_reminder_list"), { history: "true" })).error.code, "bad_arguments");
  assert.equal((await invoke(reminderTools.get("tari_reminder_list"), { destination: "other" })).error.code, "bad_arguments");
  assert.equal((await invoke(reminderTools.get("tari_reminder_cancel"), { reference: "" })).error.code, "bad_arguments");
  assert.equal((await invoke(reminderTools.get("tari_reminder_cancel"), { reference: "x".repeat(1501) })).error.code, "bad_arguments");
  assert.equal(runner.calls.length, 0);
});

test("expired, revoked or pre-cancelled reminder contexts start no backend; a closed turn after launch is unknown", async () => {
  const runner = reminderRunner(created());
  const registered = tools({ run: runner.run });
  const stale = context({ assertInvocationCurrent() { throw Error("PRIVATE AUTHORITY ERROR"); } });
  assert.equal((await invoke(registered.reminders(stale).get("tari_reminder_create"), { when: "20m", text: "Review" })).error.code,
    "invocation_expired");
  const revoked = context();
  const tool = registered.reminders(revoked).get("tari_reminder_list");
  revoked.messageChannel = "discord";
  revoked.senderIsOwner = false;
  assert.equal((await invoke(tool, {})).error.code, "not_authorized");
  const controller = new AbortController(); controller.abort();
  assert.equal((await invoke(registered.reminders().get("tari_reminder_create"), { when: "20m", text: "Review" }, controller.signal)).error.code,
    "cancelled");
  assert.equal(runner.calls.length, 0);
  let checks = 0;
  const postClosed = context({ assertInvocationCurrent() { checks += 1; if (checks === 2) throw Error("PRIVATE CLOSED"); } });
  const result = await invoke(registered.reminders(postClosed).get("tari_reminder_create"), { when: "20m", text: "Review" });
  assert.equal(result.outcome_unknown, true);
  assert.equal(runner.calls.length, 1);
  assert.equal(JSON.stringify(result).includes("PRIVATE"), false);
});

test("reminder backend timeout, malformed output and failed application remain generic unknown outcomes with no retry", async () => {
  for (const response of [new Error("PRIVATE TOKEN TIMEOUT"), { stdout: "PRIVATE INVALID JSON", stderr: "PRIVATE STDERR" },
    { stdout: "x".repeat(REMINDER_MAX_BYTES + 1), stderr: "PRIVATE STDERR" },
    { ok: true, registered: true, delivered: true, job_id: "fixture" },
    { ok: false, outcome_unknown: true, error: { code: "workflow_unavailable", message: "PRIVATE RAW EXCEPTION" } }]) {
    const runner = reminderRunner(response);
    const result = await invoke(tools({ run: runner.run }).reminders().get("tari_reminder_create"), { when: "20m", text: "Review" });
    assert.equal(result.ok, false);
    assert.equal(result.outcome_unknown, true);
    assert.equal(result.error.code, "workflow_unavailable");
    assert.equal(JSON.stringify(result).includes("PRIVATE"), false);
    assert.equal(runner.calls.length, 1);
  }
  assert.equal(reminderEnvironment({}).TARI_TIMEZONE, "UTC");
});

test("only explicitly vetted bounded non-sensitive backend validation messages are preserved", async () => {
  const message = "That local time is ambiguous; specify the intended UTC offset.";
  const safe = reminderRunner({ ok: false, safe_error: true, outcome_unknown: false,
    error: { code: "workflow_unavailable", message } });
  const result = await invoke(tools({ run: safe.run }).reminders().get("tari_reminder_create"), { when: "20m", text: "Review" });
  assert.equal(result.error.message, message);
  assert.equal(result.outcome_unknown, false);
  for (const unsafe of [{ safe_error: false, message: "PRIVATE RAW EXCEPTION" },
    { safe_error: true, message: "Your verification code is 123456" }, { safe_error: true, message: "x".repeat(501) }]) {
    const runner = reminderRunner({ ok: false, safe_error: unsafe.safe_error, error: { message: unsafe.message } });
    const rejected = await invoke(tools({ run: runner.run }).reminders().get("tari_reminder_create"), { when: "20m", text: "Review" });
    assert.notEqual(rejected.error.message, unsafe.message);
  }
});
