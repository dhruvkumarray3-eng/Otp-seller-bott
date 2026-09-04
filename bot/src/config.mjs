import process from "node:process";

const asBoolean = (value, fallback) => {
  if (value === undefined || value === "") return fallback;
  return !["0", "false", "no", "off"].includes(String(value).toLowerCase());
};

export const config = {
  botToken: process.env.BOT_TOKEN?.trim() || "",
  termsUrl: process.env.TERMS_URL?.trim() || "",
  colorButtons: asBoolean(process.env.COLOR_BUTTONS, true),
  port: Number.parseInt(process.env.PORT || "8099", 10) || 8099,
  pushRoot: process.env.PUSH_ROOT?.trim() || "bot",
  pushRemoteRoot: process.env.PUSH_REMOTE_ROOT?.trim().replace(/^\/+|\/+$/g, "") || "bot",
  channels: [
    { label: "📢 Updates Channel", url: process.env.UPDATES_CHANNEL_URL?.trim() || "" },
    { label: "🛟 Support Channel", url: process.env.SUPPORT_CHANNEL_URL?.trim() || "" },
    { label: "👥 Community Channel", url: process.env.COMMUNITY_CHANNEL_URL?.trim() || "" },
  ],
};

export const termsText = `📜 <b>Terms &amp; Conditions</b>

By using this bot, you agree to use it only for lawful, authorized GitHub repositories and workflows.

• You are responsible for the repository URL, files, and access token you provide.
• Never share a token in a group. Use the bot in a private chat only.
• Tokens are used only for the requested push and are not written to disk or logs.
• Do not use the bot to upload malware, secrets, copyrighted material without permission, or content that violates GitHub rules.
• GitHub can rate-limit, suspend, or reject a request; the bot owner cannot guarantee third-party availability.
• You can cancel any setup flow with /cancel.

Tap <b>Accept &amp; Continue</b> only if you understand and agree.`;