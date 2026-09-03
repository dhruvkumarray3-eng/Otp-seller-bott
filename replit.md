# Running the bot

This project is a Telegram background bot. Replit runs it with:

```text
python james.py
```

Set the environment variables listed in `.env.example` in Replit Secrets or
environment variables before starting the workflow. The same command is used
as the Render background-worker start command.

The bot keeps its existing SQLite database file, session files, and screenshots
directories unchanged.

## Required environment variable names

`BOT_TOKEN`, `API_ID`, `API_HASH`, `ADMIN_ID`, `LOG_CHANNEL_ID`,
`CHECK_CHANNELS`, `JOIN_URLS`, `TERMS_URL`, `UPI_ID`, `UPI_QR`, `CWALLET_QR`,
`CWALLET_ID`, `SUPPORT_USERNAME_1`, `SUPPORT_USERNAME_2`, `OTP_REGEX`,
`AUTO_CANCEL_SECONDS`, `DEFAULT_USDT_RATE`, and `DEFAULT_SUPPORT_URL`.

Custom premium emojis are enabled by default. Set `USE_PREMIUM_EMOJIS=0` only
when the bot account cannot render the configured `PREMIUM_EMOJI_*` IDs.

Inline and reply-keyboard buttons use a color-coded emoji system inspired by
the supplied visual reference: blue for navigation/info, green for positive
actions, and red for cancel/destructive actions. Telegram does not allow bots
to set actual button background colors.

The first-run flow is intentionally ordered as:

1. Terms & Conditions acceptance
2. Required channel join verification
3. Main menu

Channel links are configured through `JOIN_URLS` and can also be updated by an
admin from **General Settings → Update Channel Links**. Keep `CHECK_CHANNELS`
aligned with the corresponding channel IDs.

Optional GitHub automation settings are named `GITHUB_TOKEN`,
`GITHUB_REPO_URL`, and `GITHUB_PUSH_BRANCH`. Keep the token only in
Replit/hosting Secrets; never commit it or paste it into chat.

The bot runs as a Render web service so its health endpoint is reachable:

- `/` — basic liveness response
- `/health` — JSON status including Telegram connection state
- `/ping` — lightweight liveness response for free-host keep-alive checks

The process reconnects automatically after transient Telegram or network
disconnects and runs a 3-minute heartbeat. A GitHub Actions scheduled health
check can monitor `/ping` and `/health`,
and the workflow uses the `BOT_HEALTH_URL` GitHub Actions secret when it is
configured. Pushing to `main` triggers Render auto-deploy when this Blueprint
is connected. GitHub Actions is not a permanent process host; the Render web
service defined in `render.yaml` is the process host.