// Small, deterministic filters for text workflows. These are not a complete DLP system.
const MAX_SCAN = 100_000;
const MAX_REMINDER = 1_500;
const MAX_NODES = 10_000;
const OMITTED = "[Sensitive content omitted; review the source privately]";
const ROOT_FIELDS = ["date", "time_zone", "as_of", "calendar_window", "read_only", "instruction", "untrusted_content"];
const SOURCE_FIELDS = ["status", "quality", "reason", "warnings", "warning_details", "untrusted_content", "truncated", "scope", "pagination_remaining", "guidance"];
const NOTE_FIELDS = ["status", "source", "path", "text", "truncated", "untrusted_content", "reason"];
const SOURCE_NAMES = ["domains", "preferences", "memory", "planner", "calendar", "gmail", "dailygrad"];
const BLOCKED_FIELDS = /^(?:__proto__|prototype|constructor|raw|raw_body|body|body_html|html|stderr|stdout|diagnostics|credentials|password|passwd|passphrase|client_secret|access_token|refresh_token|api_key|bot_token)$/i;

function record(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function own(value, key) {
  if (!record(value)) return undefined;
  // The collector returns JSON. Do not invoke unexpected getters supplied by a caller.
  return Object.getOwnPropertyDescriptor(value, key)?.value;
}

function select(value, fields) {
  const result = {};
  for (const field of fields) {
    const item = own(value, field);
    if (item !== undefined) result[field] = item;
  }
  return result;
}

function placeholder(value) {
  return /^(?:[<$\[{]|redacted\b|omitted\b|example\b|dummy\b|changeme\b|not[-_ ]?set\b|none\b|undefined\b)/i.test(value);
}

/** High-confidence secrets or actionable authentication/recovery content, not arbitrary numbers. */
export function containsSensitive(text) {
  if (typeof text !== "string") return false;
  if (text.length > MAX_SCAN) return true; // Never inspect just a harmless prefix.
  if (/-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----/.test(text)) return true;
  if (/\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|xox[baprs]-[A-Za-z0-9-]{15,}|ya29\.[A-Za-z0-9_-]{15,}|sk-(?:(?:proj|svcacct)-)?[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16})\b/.test(text)) return true;
  if (/\b1\/\/[A-Za-z0-9_-]{20,}\b/.test(text)) return true;
  if (/\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{8,}\b/.test(text)) return true;
  if (/\b(?:M|N|O)[A-Za-z0-9_-]{20,30}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{25,}\b/.test(text)) return true;
  if (/\bBearer\s+[A-Za-z0-9._~+/-]{12,}/i.test(text)) return true;
  if (/\byour\s+(?:(?:one[- ]time|verification|security|authentication|sign[- ]in|log[- ]in|login|recovery|backup|device)\s+(?:codes?|keys?)|(?:recovery|seed)\s+phrase)\b/i.test(text)) return true;
  if (/\b(?:(?:one[- ]time|verification|security|authentication|authorization|auth|sign[- ]in|log[- ]in|login|recovery|backup|device|2fa|mfa)[\s_-]+(?:code|key|password)|otp|passcode)\b[^\n]{0,100}?\b(?=[A-Z0-9-]{4,32}\b)(?=[A-Z0-9-]*\d)[A-Z0-9]+(?:-[A-Z0-9]+)*\b/i.test(text)) return true;
  const letterCodes = /\b(?:(?:one[- ]time|verification|security|authentication|authorization|auth|sign[- ]in|log[- ]in|login|recovery|backup|device|2fa|mfa)[\s_-]+(?:codes?|keys?|password)|otp|passcode)\b\s*(?::|=|is)\s*["']?([a-z0-9]{4,10}(?:-[a-z0-9]{4,10})*)\b/gi;
  for (const match of text.matchAll(letterCodes)) if (/^[A-Z0-9-]+$/.test(match[1])) return true;
  if (/\b(?:seed|recovery|secret)\s+(?:phrase|mnemonic)\s*(?::|=|is)\s*(?:[a-z]+\s+){7,}[a-z]+/i.test(text)) return true;

  const assignments = /\b(?:[A-Z0-9_]+_)?(?:password|passwd|passphrase|client_secret|api_key|access_token|refresh_token|bot_token|auth_token)["']?\s*[:=]\s*(?:["']([^"'\n]{4,})["']|([^\s,;"']{8,}))/gi;
  for (const match of text.matchAll(assignments)) {
    if (!placeholder(match[1] ?? match[2])) return true;
  }
  for (const match of text.matchAll(/https?:\/\/[^\s<>"'`]{1,4096}/gi)) {
    let url;
    try { url = new URL(match[0].replace(/[.,;!]+$/, "")); } catch { continue; }
    if (url.username || url.password) return true;
    const authPath = /(?:^|\/)(?:oauth2?|authorize|auth|login|signin|sign-in|reset[-_]?password|password[-_]?reset|recover(?:y)?|verify|activate|magic[-_]?link)(?:\/|$)/i.test(url.pathname);
    if (authPath) return true;
    const parameters = [...url.searchParams, ...new URLSearchParams(url.hash.replace(/^#/, ""))];
    for (const [key, value] of parameters) {
      if (value && /^(?:access_token|refresh_token|id_token|auth_token|recovery_token|reset_token|password|api_key|client_secret|otp)$/i.test(key) && !placeholder(value)) return true;
    }
  }
  return false;
}

function sensitiveTree(value, state = { nodes: 0, seen: new WeakSet() }, depth = 0) {
  if (++state.nodes > MAX_NODES || depth > 12) return true;
  if (typeof value === "string") return containsSensitive(value);
  if (value === null || typeof value !== "object") return false;
  if (state.seen.has(value)) return true;
  state.seen.add(value);
  const entries = Array.isArray(value) ? value : record(value) ? Object.values(Object.getOwnPropertyDescriptors(value)).map((item) => item.value) : [];
  for (const item of entries) if (sensitiveTree(item, state, depth + 1)) return true;
  return false;
}

function gmailProjection(source, security) {
  const projected = select(source, SOURCE_FIELDS);
  const envelope = own(source, "data");
  if (envelope === undefined) return projected;
  const nested = own(envelope, "data");
  const mail = record(nested) ? nested : envelope;
  const messages = own(mail, "messages");
  if (!Array.isArray(messages)) {
    return { ...projected, status: "partial", quality: "malformed", reason: "Gmail metadata could not be projected safely; do not infer an empty inbox." };
  }
  const rows = [];
  for (const row of messages.slice(0, 100)) {
    const subject = own(row, "subject");
    const securityNotice = typeof subject === "string" && /(?:\b(?:reset|recover|verify|confirm)\s+your\s+(?:password|account|identity)\b|\b(?:password[- ]reset|account[- ]recovery|sign[- ]in|login|verification|authentication|security)\s+(?:code|link|request|attempt|alert)\b)/i.test(subject);
    if (!record(row) || securityNotice || sensitiveTree(row)) {
      security.omittedItems++;
      continue;
    }
    const safe = {};
    for (const [field, aliases] of [["sender", ["sender", "from", "fromName"]], ["subject", ["subject"]], ["date", ["date", "receivedAt", "received_at", "internalDate"]]]) {
      for (const alias of aliases) {
        const item = own(row, alias);
        if (typeof item === "string" || (field === "date" && typeof item === "number" && Number.isFinite(item))) {
          safe[field] = item;
          break;
        }
      }
    }
    rows.push(safe);
  }
  if (messages.length > 100) security.truncated = true;
  if (own(mail, "nextPageToken")) projected.pagination_remaining = true;
  const data = { messages: rows };
  projected.data = record(nested) ? { ...select(envelope, ["integration", "operation", "read_only", "untrusted_content", "complete"]), data } : data;
  return projected;
}

/** Preserve source status/scope, expose Gmail metadata only, and bound/redact other brief text. */
export function protectBrief(value) {
  if (!record(value) || !record(own(value, "sources"))) throw new TypeError("Expected a brief collector object with sources");
  const security = { omittedItems: 0, redactedStrings: 0, truncated: false, gmail_metadata_only: true };
  const result = select(value, ROOT_FIELDS);
  result.sources = {};
  const sources = own(value, "sources");
  for (const name of SOURCE_NAMES) {
    const source = own(sources, name);
    if (!record(source)) continue;
    if (name === "gmail") {
      result.sources.gmail = gmailProjection(source, security);
    } else if (name === "preferences" || name === "memory") {
      result.sources[name] = select(source, NOTE_FIELDS);
    } else if (name === "domains") {
      result.sources.domains = select(source, SOURCE_FIELDS);
      const files = own(source, "files");
      if (Array.isArray(files)) result.sources.domains.files = files.slice(0, 32).map((file) => select(file, NOTE_FIELDS));
      if (Array.isArray(files) && files.length > 32) security.truncated = true;
    } else {
      result.sources[name] = select(source, [...SOURCE_FIELDS, "data"]);
    }
  }

  const state = { remaining: 90_000, nodes: 0, seen: new WeakSet() };
  function bounded(item, depth = 0) {
    if (++state.nodes > MAX_NODES || depth > 12 || state.remaining < 64) {
      security.truncated = true;
      return undefined;
    }
    state.remaining -= 8;
    if (typeof item === "string") {
      if (containsSensitive(item)) {
        security.redactedStrings++;
        state.remaining -= OMITTED.length;
        return OMITTED;
      }
      const limit = Math.min(4_000, Math.floor(state.remaining / 6)); // JSON escaping may expand each code unit sixfold.
      const text = item.slice(0, limit);
      if (text.length < item.length) security.truncated = true;
      state.remaining -= JSON.stringify(text).length;
      return text;
    }
    if (typeof item === "number" && Number.isFinite(item)) {
      state.remaining -= JSON.stringify(item).length;
      return item;
    }
    if (item === null || typeof item === "boolean") return item;
    if (typeof item !== "object" || state.seen.has(item)) {
      security.truncated = true;
      return undefined;
    }
    state.seen.add(item);
    const output = Array.isArray(item) ? [] : {};
    const keys = Array.isArray(item) ? item.keys() : record(item) ? Object.keys(item) : [];
    for (const key of keys) {
      if (state.nodes >= MAX_NODES || state.remaining < 128) { security.truncated = true; break; }
      if (typeof key === "string" && (BLOCKED_FIELDS.test(key) || key.length > 128)) continue;
      if (typeof key === "string" && containsSensitive(key)) { security.omittedItems++; continue; }
      state.remaining -= JSON.stringify(String(key)).length + 2;
      const child = bounded(Array.isArray(item) ? item[key] : own(item, key), depth + 1);
      if (child !== undefined) {
        if (Array.isArray(output)) output.push(child); else output[key] = child;
      }
    }
    return output;
  }
  const safe = bounded(result);
  // A large early source must not hide a later source's unavailable/not-requested status.
  safe.sources ??= {};
  for (const name of SOURCE_NAMES) {
    const original = result.sources[name];
    if (!original || typeof safe.sources[name]?.status === "string") continue;
    const summary = {};
    for (const field of ["status", "quality", "reason"]) {
      const item = own(original, field);
      if (typeof item === "string") summary[field] = containsSensitive(item) ? OMITTED : item.slice(0, field === "reason" ? 300 : 64);
    }
    const scope = own(original, "scope");
    if (typeof scope === "string") summary.scope = containsSensitive(scope) ? OMITTED : scope.slice(0, 300);
    if (record(scope)) {
      summary.scope = {};
      for (const field of ["query", "max_messages", "complete_inbox_recap", "description"]) {
        const item = own(scope, field);
        if (typeof item === "string") summary.scope[field] = containsSensitive(item) ? OMITTED : item.slice(0, 150);
        else if (typeof item === "boolean" || (typeof item === "number" && Number.isFinite(item))) summary.scope[field] = item;
      }
    }
    if (summary.status === "available" || summary.status === "partial") {
      summary.status = "partial";
      summary.quality = "incomplete";
      summary.reason = "Source payload omitted by the brief output limit; do not infer it is empty.";
    }
    summary.truncated = true;
    safe.sources[name] = summary;
    security.truncated = true;
  }
  safe.security = security;
  return safe;
}

/** Validated user text remains data; the workflow supplies the fixed printf argv and prefix. */
export function reminderText(text) {
  if (typeof text !== "string") throw new TypeError("Reminder text must be a string");
  const value = text.trim();
  if (!value || value.length > MAX_REMINDER || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(value)) throw new Error("Reminder text must be 1–1500 characters without control characters");
  if (containsSensitive(value)) throw new Error("Sensitive authentication or recovery content cannot be used in reminders; review it privately");
  return value;
}

/** Native outbound hooks can fail open; this synchronous text guard is defense in depth. */
export function registerDiscordGuard(api) {
  if (typeof api?.on !== "function") throw new TypeError("OpenClaw hook registration is unavailable");
  api.on("message_sending", (event, context) => {
    if (context?.channelId !== "discord") return undefined;
    if (typeof event?.content !== "string" || containsSensitive(event.content)) {
      return { cancel: true, cancelReason: "tari_sensitive_content", metadata: { tari_security_guard: "blocked" } };
    }
    return undefined;
  }, { priority: 1_000, registrationId: "tari-workflows.discord-sensitive-content", timeoutMs: 1_000 });
}
