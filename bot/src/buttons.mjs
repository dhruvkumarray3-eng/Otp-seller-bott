import { config } from "./config.mjs";

/**
 * Telegram clients that support Bot API button styles can render these as
 * colored controls. Older clients reject the field, so github/index retries
 * the same request without it. This keeps the colorful reference behavior
 * without making the bot unusable on older clients.
 */
export const buttonStyles = Object.freeze({
  primary: "primary",
  success: "success",
  secondary: "secondary",
  danger: "danger",
});

export const inlineButton = (text, action, style = buttonStyles.secondary) => {
  const button = { text };
  if (action.callbackData) button.callback_data = action.callbackData;
  if (action.url) button.url = action.url;
  if (config.colorButtons && style) button.style = style;
  return button;
};

export const termsKeyboard = () => {
  const rows = [
    [inlineButton("✅ Accept & Continue", { callbackData: "terms:accept" }, buttonStyles.success)],
  ];
  rows.push([
    config.termsUrl
      ? inlineButton("📜 Read Full Terms", { url: config.termsUrl }, buttonStyles.secondary)
      : inlineButton("📜 Read Terms Here", { callbackData: "terms:read" }, buttonStyles.secondary),
  ]);
  return { inline_keyboard: rows };
};

export const mainKeyboard = () => ({
  inline_keyboard: [
    [inlineButton("🚀 Push to GitHub", { callbackData: "push:start" }, buttonStyles.primary)],
    [
      inlineButton("📁 Repository", { callbackData: "repo:status" }, buttonStyles.secondary),
      inlineButton("📢 Channels", { callbackData: "channels" }, buttonStyles.secondary),
    ],
    [
      inlineButton("📜 Terms", { callbackData: "terms:read" }, buttonStyles.secondary),
      inlineButton("ℹ️ Help", { callbackData: "help" }, buttonStyles.secondary),
    ],
  ],
});

export const channelsKeyboard = () => {
  const rows = config.channels
    .filter((channel) => channel.url)
    .map((channel) => [inlineButton(channel.label, { url: channel.url }, buttonStyles.secondary)]);
  rows.push([inlineButton("↩️ Back to Menu", { callbackData: "menu" }, buttonStyles.secondary)]);
  return { inline_keyboard: rows };
};

export const backKeyboard = (callbackData = "menu") => ({
  inline_keyboard: [[inlineButton("↩️ Back to Menu", { callbackData }, buttonStyles.secondary)]],
});