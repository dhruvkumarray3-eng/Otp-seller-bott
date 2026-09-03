import http from "node:http";
import process from "node:process";
import { config, termsText } from "./config.mjs";
import {
  backKeyboard,
  channelsKeyboard,
  mainKeyboard,
  termsKeyboard,
} from "./buttons.mjs";
import { formatGithubError, parseRepositoryUrl, validateAndPush } from "./github.mjs";

const acceptedUsers = new Set();
const sessions = new Map();
let updateOffset = 0;

const helpText = `ℹ️ <b>GitHub Workflow Bot</b>

Terms are always the first step for a new user.

<b>How Push works</b>
1. Tap 🚀 Push to GitHub.
2. Send your repository URL.
3. In this private chat, send a GitHub token with repository contents write access.
4. The bot validates the repo and syncs the bot source files.

No automatic login, OAuth connect, or background credential collection is used.

Commands: /start · /push · /channels · /terms · /cancel`;

const jsonHeaders = { "Content-Type": "application/json" };

const telegram = async (method, payload, { retryWithoutStyles = true } = {}) => {
  if (!config.botToken) throw new Error("BOT_TOKEN is not configured.");
  const response = await fetch(`https://api.telegram.org/bot${config.botToken}/${method}`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  const hasButtonStyles = JSON.stringify(payload).includes('"style"');
  if (!result.ok && retryWithoutStyles && hasButtonStyles && result.error_code === 400) {
    return telegram(method, stripButtonStyles(payload), { retryWithoutStyles: false });
  }
  if (!result.ok) throw new Error(result.description || `Telegram API error in ${method}`);
  return result.result;
};

const stripButtonStyles = (payload) => {
  const clone = structuredClone(payload);
  const walk = (value) => {
    if (!value || typeof value !== "object") return;
    if (Array.isArray(value)) return value.forEach(walk);
    delete value.style;
    Object.values(value).forEach(walk);
  };
  walk(clone);
  return clone;
};

const send = async (chatId, text, replyMarkup) => telegram("sendMessage", {
  chat_id: chatId,
  text,
  parse_mode: "HTML",
  disable_web_page_preview: true,
  ...(replyMarkup ? { reply_markup: replyMarkup } : {}),
});

const edit = async (chatId, messageId, text, replyMarkup) => telegram("editMessageText", {
  chat_id: chatId,
  message_id: messageId,
  text,
  parse_mode: "HTML",
  disable_web_page_preview: true,
  ...(replyMarkup ? { reply_markup: replyMarkup } : {}),
});

const answerCallback = (id, text = "") => telegram("answerCallbackQuery", {
  callback_query_id: id,
  ...(text ? { text } : {}),
});

const ensurePrivate = (chat) => chat?.type === "private";

const showTerms = (chatId, messageId) => {
  const payload = { text: termsText, replyMarkup: termsKeyboard() };
  return messageId ? edit(chatId, messageId, payload.text, payload.replyMarkup) : send(chatId, payload.text, payload.replyMarkup);
};

const showMenu = (chatId, messageId, firstName = "") => {
  const greeting = firstName ? `, ${firstName}` : "";
  const text = `🌈 <b>Welcome${greeting}!</b>

Your colorful GitHub workflow assistant is ready.
Premium-style emojis and button colors are enabled by default.`;
  return messageId ? edit(chatId, messageId, text, mainKeyboard()) : send(chatId, text, mainKeyboard());
};

const isAccepted = (userId) => acceptedUsers.has(userId);

const requireTerms = async (chatId, userId, messageId) => {
  if (isAccepted(userId)) return true;
  await showTerms(chatId, messageId);
  return false;
};

const startPush = async (chatId, userId, messageId) => {
  if (!(await requireTerms(chatId, userId, messageId))) return;
  sessions.set(userId, { state: "awaiting_repo" });
  await edit(chatId, messageId, `🚀 <b>GitHub Push Setup</b>

Step 1 of 2: send the full repository URL.

Example: <code>https://github.com/your-name/your-repo</code>

Nothing is pushed until you complete both steps. Use /cancel to stop.`, backKeyboard());
};

const handleText = async (message) => {
  const chatId = message.chat?.id;
  const userId = message.from?.id;
  const text = message.text?.trim() || "";
  if (!chatId || !userId || !text) return;

  if (text === "/start") {
    sessions.delete(userId);
    if (!isAccepted(userId)) return showTerms(chatId);
    return showMenu(chatId, undefined, message.from?.first_name);
  }
  if (text === "/terms") return showTerms(chatId);
  if (text === "/help") return requireTerms(chatId, userId).then((ok) => ok && send(chatId, helpText, backKeyboard()));
  if (text === "/channels") return requireTerms(chatId, userId).then((ok) => ok && send(chatId, "🔗 <b>Official Channels</b>\n\nUse the configured links below for updates and support.", channelsKeyboard()));
  if (text === "/cancel") {
    sessions.delete(userId);
    return send(chatId, "🛑 Setup cancelled. Your token was not requested or stored.", backKeyboard());
  }
  if (text === "/push") return startPush(chatId, userId);

  if (!ensurePrivate(message.chat)) {
    return send(chatId, "🔒 For safety, repository URLs and tokens are accepted only in a private chat with this bot.");
  }
  const session = sessions.get(userId);
  if (!session) {
    if (!(await requireTerms(chatId, userId))) return;
    return send(chatId, "Choose an action from the menu below.", mainKeyboard());
  }
  if (session.state === "awaiting_repo") {
    const repository = parseRepositoryUrl(text);
    if (!repository) return send(chatId, "❌ That URL is not valid. Send a URL like:\n<code>https://github.com/your-name/your-repo</code>", backKeyboard("push:start"));
    sessions.set(userId, { state: "awaiting_token", repositoryUrl: text });
    return send(chatId, `✅ Repository saved: <code>${repository.owner}/${repository.name}</code>

Step 2 of 2: send your GitHub token here in this private chat.

Use a fine-grained token limited to this repository with <b>Contents: Read and write</b>. The bot will use it for this push only, will attempt to delete the message, and will never log or save it.

If you do not want to continue, send /cancel.`, backKeyboard("push:start"));
  }
  if (session.state === "awaiting_token") {
    sessions.delete(userId);
    await telegram("deleteMessage", { chat_id: chatId, message_id: message.message_id }).catch(() => {});
    await send(chatId, "⏳ Token received privately. I am validating the repository and preparing the push…");
    try {
      const result = await validateAndPush({ repositoryUrl: session.repositoryUrl, token: text });
      return send(chatId, `✅ <b>Push complete</b>

📦 Repository: <code>${result.owner}/${result.name}</code>
🌿 Branch: <code>${result.branch}</code>
📄 Files synced: <b>${result.pushed}</b>${result.skipped ? `\n⏭️ Files skipped: <b>${result.skipped}</b>` : ""}

🔗 <a href="${result.url}">Open repository</a>`, mainKeyboard());
    } catch (error) {
      return send(chatId, `❌ <b>Push could not be completed</b>

${formatGithubError(error)}

Your token was not stored. Check repository access and try again from the menu.`, mainKeyboard());
    }
  }
};

const handleCallback = async (query) => {
  const chatId = query.message?.chat?.id;
  const messageId = query.message?.message_id;
  const userId = query.from?.id;
  if (!chatId || !messageId || !userId) return;
  await answerCallback(query.id).catch(() => {});
  const action = query.data;

  if (action === "terms:read") return edit(chatId, messageId, termsText, termsKeyboard());
  if (action === "terms:accept") {
    acceptedUsers.add(userId);
    sessions.delete(userId);
    return edit(chatId, messageId, "✅ <b>Terms accepted.</b>\n\nYou can now use the GitHub workflow menu.", mainKeyboard());
  }
  if (!(await requireTerms(chatId, userId, messageId))) return;
  if (action === "menu") return showMenu(chatId, messageId, query.from?.first_name);
  if (action === "help") return edit(chatId, messageId, helpText, backKeyboard());
  if (action === "channels") return edit(chatId, messageId, "🔗 <b>Official Channels</b>\n\nUse the configured links below for updates and support.", channelsKeyboard());
  if (action === "push:start") return startPush(chatId, userId, messageId);
  if (action === "repo:status") return edit(chatId, messageId, "📁 <b>Repository</b>\n\nNo repository is saved permanently. Start a push to provide a repository URL for this one-time workflow.", backKeyboard());
};

const poll = async () => {
  while (true) {
    try {
      const updates = await telegram("getUpdates", {
        offset: updateOffset,
        timeout: 25,
        allowed_updates: ["message", "callback_query"],
      });
      for (const update of updates || []) {
        updateOffset = update.update_id + 1;
        if (update.callback_query) await handleCallback(update.callback_query).catch((error) => console.error("callback failed", error.message));
        if (update.message) await handleText(update.message).catch((error) => console.error("message failed", error.message));
      }
    } catch (error) {
      console.error("poll failed:", error.message);
      await new Promise((resolve) => setTimeout(resolve, 5000));
    }
  }
};

const server = http.createServer((request, response) => {
  if (request.url === "/healthz" || request.url === "/ping") {
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ status: "ok", bot: "github-workflow-bot", telegramConfigured: Boolean(config.botToken) }));
    return;
  }
  response.writeHead(404, { "Content-Type": "application/json" });
  response.end(JSON.stringify({ error: "not_found" }));
});

server.listen(config.port, "0.0.0.0", () => {
  console.log(`Health server listening on ${config.port}`);
  if (!config.botToken) {
    console.log("BOT_TOKEN is not set; Telegram polling is intentionally paused.");
    return;
  }
  poll();
});