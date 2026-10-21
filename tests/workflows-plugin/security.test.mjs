import test from "node:test";
import assert from "node:assert/strict";
import { containsSensitive, protectBrief, registerDiscordGuard, reminderText } from "../../plugins/tari-workflows/security.js";

const secret = "ghp_" + "A".repeat(36);

test("clear credentials, authentication codes and recovery links are sensitive", () => {
  for (const value of [secret, "1//" + "a".repeat(30), "API_KEY=abcdefghijk12345", "Authorization: Bearer abcdefghijklmnop", "Your verification code", "Your backup codes", "Verification code: 123456", "Verification code: ABCD-EFGH", "Recovery codes: ABCD-EFGH", "Backup code: 4567-8901", "https://accounts.example.test/oauth/authorize?code=abcdef", "https://example.test/reset-password?ticket=abc", "Open https://example.test/login.", "https://example.test/callback?access_token=abcdef", "https://example.test/callback#access_token=abcdef", "-----BEGIN " + "PRIVATE KEY-----\nexample"]) {
    assert.equal(containsSensitive(value), true, value);
  }
});

test("ordinary dates, numbers and discussion of authentication do not trigger", () => {
  for (const value of ["Meet at 10:30 on 2026-10-21, room 123456", "Review verification code requirements", "Discuss the password reset project", "Read https://example.test/articles/security", "API_KEY=<your-key>", "password: [redacted]", "Finish exercise 1234 and drink 250 ml water"]) {
    assert.equal(containsSensitive(value), false, value);
  }
});

test("brief projection omits sensitive Gmail candidates, bodies, snippets, IDs and opaque paging tokens", () => {
  const value = { date: "2026-10-21", read_only: true, sources: {
    gmail: { status: "partial", quality: "incomplete", scope: { complete_inbox_recap: false, max_messages: 20 }, data: { integration: "google", operation: "gmail.search", data: { messages: [
      { id: "message-1", from: "Team <team@example.test>", subject: "Agenda", date: "2026-10-21", snippet: "Ordinary preview", body: "Never include raw body" },
      { fromName: "Service", subject: "Sign in", snippet: "Your verification code is 123456" },
      { from: "Service", subject: "Account", body: secret },
      { from: "Service", subject: "Password reset request", snippet: "Click the button in this message" },
    ], nextPageToken: "opaque-page-token" } } },
    calendar: { status: "not_configured" },
  } };
  const original = structuredClone(value);
  const result = protectBrief(value);
  assert.deepEqual(result.sources.gmail.data.data.messages, [{ sender: "Team <team@example.test>", subject: "Agenda", date: "2026-10-21" }]);
  assert.equal(result.sources.gmail.status, "partial");
  assert.equal(result.sources.gmail.quality, "incomplete");
  assert.equal(result.sources.gmail.scope.complete_inbox_recap, false);
  assert.equal(result.sources.gmail.pagination_remaining, true);
  assert.equal(result.sources.calendar.status, "not_configured");
  assert.equal(result.security.omittedItems, 3);
  assert.doesNotMatch(JSON.stringify(result), /Never include|Ordinary preview|message-1|opaque-page-token|123456|ghp_/);
  assert.deepEqual(value, original);
});

test("actual Google envelope and unavailable states remain honest", () => {
  const result = protectBrief({ sources: { gmail: { status: "available", data: { read_only: true, data: { messages: [{ fromName: "Colleague", subject: "Lunch", internalDate: 1_792_598_400_000 }] } } }, calendar: { status: "unavailable", reason: "Source retrieval failed; do not infer it is empty." }, memory: { status: "not_requested" } } });
  assert.deepEqual(result.sources.gmail.data.data.messages, [{ sender: "Colleague", subject: "Lunch", date: 1_792_598_400_000 }]);
  assert.equal(result.sources.calendar.status, "unavailable");
  assert.equal(result.sources.memory.status, "not_requested");
  const malformed = protectBrief({ sources: { gmail: { status: "available", data: { unexpected: [] } } } });
  assert.equal(malformed.sources.gmail.status, "partial");
  assert.equal(malformed.sources.gmail.quality, "malformed");
  assert.match(malformed.sources.gmail.reason, /do not infer an empty inbox/);
});

test("other source strings redact known secrets and projection drops unexpected diagnostic fields", () => {
  const result = protectBrief({ date: "2026-10-21", unexpected: secret, sources: {
    planner: { status: "available", data: { schedule: [{ title: secret }], checklist: [{ text: "Buy bread", done: false }], [secret]: "not copied", stderr: "private diagnostic" } },
    preferences: { status: "available", text: "password=actual-password-value", source: "USER.md", unexpected: "private diagnostic" },
    domains: { status: "available", files: [{ path: "domains/health.md", status: "available", text: "Walk today", raw: secret }] },
    unknown: { data: secret },
  } });
  assert.match(result.sources.planner.data.schedule[0].title, /Sensitive content omitted/);
  assert.match(result.sources.preferences.text, /Sensitive content omitted/);
  assert.equal(result.sources.planner.data.checklist[0].text, "Buy bread");
  assert.equal(result.sources.domains.files[0].text, "Walk today");
  assert.equal(result.security.redactedStrings, 2);
  assert.doesNotMatch(JSON.stringify(result), /actual-password|private diagnostic|ghp_|"unexpected"|"unknown"/);
});

test("brief output is bounded for deep, huge and escaping-heavy input", () => {
  let nested = { text: "ordinary" };
  for (let index = 0; index < 30; index++) nested = { next: nested };
  const result = protectBrief({ sources: { planner: { status: "available", data: { schedule: Array.from({ length: 20_000 }, () => ({ title: "\\\"\n".repeat(5_000) })), nested } }, gmail: { status: "not_configured", scope: { complete_inbox_recap: false } }, calendar: { status: "unavailable" } } });
  assert.equal(result.security.truncated, true);
  assert.ok(JSON.stringify(result).length <= 100_000);
  assert.equal(result.sources.gmail.status, "not_configured");
  assert.equal(result.sources.gmail.scope.complete_inbox_recap, false);
  assert.equal(result.sources.calendar.status, "unavailable");
  const numbers = protectBrief({ sources: { planner: { status: "available", data: { values: Array(20_000).fill(1.2345678901234567e+300) } } } });
  assert.ok(JSON.stringify(numbers).length <= 100_000);
  assert.equal(containsSensitive("x".repeat(100_001)), true);
});

test("cycles and getters cannot run arbitrary reads or crash source protection", () => {
  const data = { checklist: [] };
  data.cycle = data;
  Object.defineProperty(data, "unexpected", { enumerable: true, get() { throw new Error("must not run"); } });
  const result = protectBrief({ sources: { planner: { status: "available", data } } });
  assert.equal(result.security.truncated, true);
  assert.deepEqual(result.sources.planner.data.checklist, []);
});

test("reminder validation leaves shell syntax and percent signs as inert text", () => {
  assert.equal(reminderText("  Buy milk; $(echo hi) `date` 100% done  "), "Buy milk; $(echo hi) `date` 100% done");
  for (const value of ["", " ", "x".repeat(1501), "a\u0000b", secret, "Recovery code: 1234-5678", "https://example.test/login"]) assert.throws(() => reminderText(value));
  assert.throws(() => reminderText(42));
});

test("native Discord guard synchronously cancels clear secrets without echoing them", () => {
  let registered;
  registerDiscordGuard({ on(name, handler, options) { registered = { name, handler, options }; } });
  assert.equal(registered.name, "message_sending");
  assert.equal(registered.options.registrationId, "tari-workflows.discord-sensitive-content");
  const denied = registered.handler({ to: "user:owner", content: secret }, { channelId: "discord" });
  assert.equal(denied.cancel, true);
  assert.equal(denied.cancelReason, "tari_sensitive_content");
  assert.equal(typeof denied?.then, "undefined");
  assert.doesNotMatch(JSON.stringify(denied), /ghp_/);
  assert.equal(registered.handler({ content: "Review task 123456 at 10:30" }, { channelId: "discord" }), undefined);
  assert.equal(registered.handler({ content: secret }, { channelId: "webchat" }), undefined);
  assert.equal(registered.handler({ content: "x".repeat(100_001) }, { channelId: "discord" }).cancel, true);
});
