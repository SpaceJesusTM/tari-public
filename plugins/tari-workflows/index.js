import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { register } from "./tools.js";

export default definePluginEntry({
  id: "tari-workflows",
  name: "Tari Workflows",
  description: "Constrained brief context and owner-authorized native deterministic reminders.",
  register,
});
