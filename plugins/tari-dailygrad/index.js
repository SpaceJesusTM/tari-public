import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { register } from "./tools.js";

export default definePluginEntry({
  id: "tari-dailygrad",
  name: "Tari DailyGrad",
  description: "Optional DailyGrad digest, run, repair, source and exercise tools.",
  register,
});
