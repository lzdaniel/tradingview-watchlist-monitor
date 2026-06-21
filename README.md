# TradingView Watchlist Monitor

Monitor public TradingView watchlists and send notifications when symbols are added or removed.

TradingView does not provide an official webhook for public watchlist membership changes. This project uses Playwright to read a public watchlist page, stores a snapshot, and compares each new scan against the previous snapshot.

## Features

- Monitors a public TradingView watchlist URL.
- Detects added and removed symbols by comparing snapshots.
- Sends notifications only when changes are detected.
- Supports multiple notification targets:
  - Discord webhook
  - Telegram bot message
- Can send to one or more notifiers with `NOTIFIERS=discord,telegram`.
- Shows added/removed counts at the top of every message, even when the count is zero.
- Lists changed tickers by TradingView section/category.
- Shows plain tickers in notifications while comparing exchange-qualified symbols internally.
- Sends the full watchlist at market open, market close, and the daily snapshot hour.
- Runs locally as a daemon or in GitHub Actions on a schedule.

## Example Message

```text
🚨 TradingView Watchlist Update 🚨
🕒 2026-06-21 09:45:00 EDT
📊 🟢 Added: 2 | 🔴 Removed: 1

🟢🆕 Added 2
📂 WATCHLIST
🟢 + `ABC` - Example Company Inc.
🟢 + `XYZ` - Another Company Ltd.

🔴🗑️ Removed 1
📂 SWINGING
🔴 - `NVDA` - NVIDIA Corporation

🔗 https://www.tradingview.com/watchlists/<watchlist-id>/
```

The TradingView link is placed at the end of the message so rich previews appear after the actionable content when the destination app supports previews.

## How It Works

```text
Fetch current TradingView watchlist
-> Normalize symbols
-> Read previous snapshot from state/watchlist_<watchlist-id>.json
-> added = current - previous
-> removed = previous - current
-> notify configured destinations only if needed
-> write the current snapshot back to state
```

Notifications display plain tickers such as `AAPL`, but internal diffing uses exchange-qualified symbols such as `NASDAQ:AAPL`. This avoids false matches when the same ticker exists on multiple exchanges.

## Requirements

- Python 3.9+
- Playwright
- Chromium installed through Playwright
- At least one notification destination:
  - Discord webhook URL, or
  - Telegram bot token and chat id

## Installation

```bash
git clone https://github.com/lzdaniel/tradingview-watchlist-monitor.git
cd tradingview-watchlist-monitor

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e .
python -m playwright install chromium

cp .env.example .env
```

Edit `.env` and configure your notifier.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `NOTIFIERS` | `discord` | Comma-separated notification targets: `discord`, `telegram`, or `discord,telegram`. |
| `DISCORD_WEBHOOK_URL` | empty | Required when `NOTIFIERS` includes `discord`. |
| `TELEGRAM_BOT_TOKEN` | empty | Required when `NOTIFIERS` includes `telegram`. |
| `TELEGRAM_CHAT_ID` | empty | Required when `NOTIFIERS` includes `telegram`. |
| `TELEGRAM_DISABLE_WEB_PAGE_PREVIEW` | `false` | Whether Telegram should suppress link previews. |
| `WATCHLIST_NAME` | `TradingView Watchlist` | Human-readable name shown in notifications. |
| `WATCHLIST_URL` | sample public URL | Public TradingView watchlist URL. |
| `STATE_FILE` | `state/watchlist_<watchlist-id>.json` | Snapshot used to detect additions and removals. |
| `MARKET_TIMEZONE` | `America/New_York` | Time zone for market-hours scheduling. |
| `MARKET_OPEN` | `09:30` | Regular-session open time. |
| `MARKET_CLOSE` | `16:00` | Regular-session close time. |
| `CHECK_INTERVAL_MARKET_SECONDS` | `900` | Local daemon scan interval during market hours. |
| `CHECK_INTERVAL_OFFHOURS_SECONDS` | `3600` | Local daemon scan interval outside market hours. |
| `SNAPSHOT_HOURS` | `12` | Comma-separated full-snapshot hours in `MARKET_TIMEZONE`. |
| `SNAPSHOT_WINDOW_MINUTES` | `45` | Grace window for scheduled full snapshots and open/close snapshots. |
| `HEADLESS` | `true` | Runs Chromium in headless mode. |
| `SEND_INITIAL_BASELINE` | `false` | If true, sends the full list when no previous state exists. |
| `PERSIST_LAST_SEEN` | `true` | If false, unchanged scans do not modify state timestamps. Useful for GitHub Actions. |

### Discord

```text
NOTIFIERS=discord
DISCORD_WEBHOOK_URL=<your-discord-webhook-url>
```

### Telegram

Create a bot with `@BotFather`, add it to the target chat or channel, and provide the bot token and chat id:

```text
NOTIFIERS=telegram
TELEGRAM_BOT_TOKEN=<your-telegram-bot-token>
TELEGRAM_CHAT_ID=<your-telegram-chat-id>
```

To send to both destinations:

```text
NOTIFIERS=discord,telegram
DISCORD_WEBHOOK_URL=<your-discord-webhook-url>
TELEGRAM_BOT_TOKEN=<your-telegram-bot-token>
TELEGRAM_CHAT_ID=<your-telegram-chat-id>
```

## Usage

Run one scan. If there are no changes, no notification is sent:

```bash
set -a
source .env
set +a

python -m tv_watchlist_monitor.watcher run-once
```

Dry-run one scan without sending notifications:

```bash
python -m tv_watchlist_monitor.watcher run-once --dry-run
```

Send the full watchlist now:

```bash
python -m tv_watchlist_monitor.watcher send-full --label test
```

Run as a local daemon:

```bash
python -m tv_watchlist_monitor.watcher daemon
```

The daemon uses `CHECK_INTERVAL_MARKET_SECONDS` during regular market hours and `CHECK_INTERVAL_OFFHOURS_SECONDS` outside regular market hours. It also wakes early for market open, market close, and configured `SNAPSHOT_HOURS`.

## GitHub Actions

This repository includes a scheduled workflow:

```text
.github/workflows/tradingview-watchlist.yml
```

The workflow supports:

- Market-hours change scans every 15 minutes.
- Off-hours change scans every hour.
- Full snapshots at market open, market close, and the configured daily snapshot hour, defaulting to `12:00` in `MARKET_TIMEZONE`.
- Manual runs through `workflow_dispatch`.
- Repository state commits so each GitHub Actions run can compare against the previous snapshot.

### GitHub Secrets

Set secrets for the notifiers you use:

```text
DISCORD_WEBHOOK_URL
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

In GitHub:

```text
Settings -> Secrets and variables -> Actions -> New repository secret
```

### GitHub Variables

Required repository variable:

```text
WATCHLIST_URL=https://www.tradingview.com/watchlists/<watchlist-id>/
```

Optional repository variables:

```text
NOTIFIERS=discord
WATCHLIST_NAME=TradingView Watchlist
STATE_FILE=state/watchlist_<watchlist-id>.json
MARKET_TIMEZONE=America/New_York
MARKET_OPEN=09:30
MARKET_CLOSE=16:00
SNAPSHOT_HOURS=12
SNAPSHOT_WINDOW_MINUTES=45
TELEGRAM_DISABLE_WEB_PAGE_PREVIEW=false
```

If `WATCHLIST_URL` is not set, the workflow fails with a clear configuration error.

### Schedule

The workflow uses two cron schedules:

```yaml
- cron: "*/15 13-21 * * 1-5"
- cron: "0 * * * *"
```

GitHub cron schedules run in UTC. The Python code converts the current time to `MARKET_TIMEZONE` and applies the final rules:

- `market` mode scans only during `09:30-16:00`.
- `offhours` mode scans only outside regular market hours.
- The first scan inside the open window sends a full market-open snapshot.
- The first scan inside the close window sends a full market-close snapshot.
- The configured daily snapshot hour defaults to `12:00`.
- All other scheduled scans send a notification only when symbols are added or removed.
- `always` mode is available for manual runs.

GitHub Actions scheduled workflows can be delayed by GitHub's queue. Increase `SNAPSHOT_WINDOW_MINUTES` if you want a wider grace window.

### Public Repository State

If this project is hosted as a public repository, `state/watchlist_<watchlist-id>.json` is also public. It contains only the watchlist snapshot used for diffing. Notification secrets must stay in GitHub Secrets and must not be committed.

## Multiple Watchlists

The current implementation monitors one watchlist per run. To monitor multiple watchlists, run the workflow or daemon with different values for:

```text
WATCHLIST_NAME
WATCHLIST_URL
STATE_FILE
```

Each watchlist must have its own `STATE_FILE`; otherwise added/removed comparisons will be mixed together.

## macOS launchd

An example launchd plist is included:

```text
launchd/com.tradingview-watchlist-monitor.plist.example
```

Use it as a template if you want the monitor to run on a Mac. A sleeping Mac will not run scheduled scans; use GitHub Actions or a server if you need monitoring while the computer is asleep.

## Limitations

- TradingView does not provide an official public-watchlist member-change webhook.
- This project reads a rendered TradingView page and scanner responses through Playwright, so page or API changes may require parser updates.
- The schedule treats weekdays as trading days and does not include a US market holiday calendar.
- GitHub Actions scheduled workflows are not guaranteed to run exactly on time.
- Avoid high-frequency scraping. The default 15-minute market cadence and 1-hour off-hours cadence are intentionally conservative.

## Security

- Never commit `.env`.
- Store Discord webhooks and Telegram bot tokens in GitHub Secrets or another secret manager.
- Rotate any notifier secret if it is accidentally exposed.
- The repository `.gitignore` excludes local environment files, virtualenvs, logs, and debug artifacts.

## Development

Compile-check the package:

```bash
python -m py_compile src/tv_watchlist_monitor/watcher.py
```

Run a scheduled-mode dry run:

```bash
python -m tv_watchlist_monitor.watcher run-scheduled --mode market --dry-run
python -m tv_watchlist_monitor.watcher run-scheduled --mode snapshot --dry-run
```

Inspect the current state:

```bash
python - <<'PY'
import json
from pathlib import Path

state = json.loads(Path("state/watchlist_<watchlist-id>.json").read_text())
print(len(state["items"]))
print(state.get("last_seen_at"))
PY
```

## License

No license has been specified yet.
