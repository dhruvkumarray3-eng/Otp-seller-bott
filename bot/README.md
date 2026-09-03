# GitHub Workflow Telegram Bot

A Telegram bot with a terms-first onboarding flow and an explicit, one-time GitHub push workflow.

## Included

- Terms & Conditions gate before any feature is available.
- Color-capable inline buttons with a compatibility retry for older Telegram clients.
- Premium-style emoji labels enabled by default.
- Centralized updates, support, and community channel links.
- No automatic login, OAuth connect, background credential collection, or permanent repository profile.
- Manual flow: `/push` → repository URL → GitHub token in private chat → one-time source sync.
- Token is not logged or written to disk. The bot attempts to delete the token message after receipt.
- `/healthz` and `/ping` endpoints for uptime monitors.
- `terms.html` ready to host from a GitHub Pages site owned by you.

## Run later

1. Copy `.env.example` to your deployment environment.
2. Add `BOT_TOKEN` from BotFather as a secret. Do not put it in GitHub.
3. Add your own channel URLs and the public GitHub Pages URL for `terms.html`.
4. Start with `pnpm --filter @workspace/github-workflow-bot run start`.

The bot intentionally starts only its health server when `BOT_TOKEN` is absent. This lets you finish code and push it before bringing the bot alive.

## GitHub token guidance

When the user taps Push to GitHub, the bot asks for the token only after the repository URL. Use a fine-grained GitHub token with **Contents: Read and write** on the selected repository, and revoke it after use.

## References used

The button style compatibility idea is inspired by the public `DHRUV_X_RADHA` reference. The broader colorful/premium presentation direction is inspired by the public `Sukanya-music` reference. This project does not copy either application.