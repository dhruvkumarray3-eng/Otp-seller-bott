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

Custom premium emojis are disabled by default, so the bot works with a
standard Telegram account. Set `USE_PREMIUM_EMOJIS=1` only when the bot
account can use the configured `PREMIUM_EMOJI_*` IDs.

The bot runs as a Render web service so its health endpoint is reachable:

- `/` — basic liveness response
- `/health` — JSON status including Telegram connection state

The process reconnects automatically after transient Telegram or network
disconnects. A GitHub Actions scheduled health check can monitor `/health`,
and the workflow uses the `BOT_HEALTH_URL` GitHub Actions secret when it is
configured. GitHub Actions is not a permanent process host; the Render web
service defined in `render.yaml` is the process host.