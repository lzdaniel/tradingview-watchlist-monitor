# TradingView Watchlist Discord Monitor

Monitor a public TradingView watchlist and send Discord notifications when symbols are added or removed.

This project was built for public TradingView watchlists that do not provide an official member-change webhook. It uses Playwright to read the watchlist page, stores the last snapshot locally or in the repository state file, and compares each new scan against that snapshot.

## Features

- Monitors a public TradingView watchlist URL.
- Sends Discord notifications only when symbols are added or removed.
- Shows a summary count at the top of each message, even when the count is zero.
- Lists added and removed tickers by TradingView section/category.
- Displays ticker codes only in Discord messages, while keeping exchange-qualified symbols internally for accurate diffing.
- Sends the full watchlist at market open and market close.
- Supports local daemon mode with different market-hours and off-hours intervals.
- Supports GitHub Actions scheduled runs for cloud execution.

## Example Message

```text
🚨 Huang Watchlist Watchlist Update 🚨
🕒 2026-06-21 09:45:00 EDT
📊 🟢 Added: 2 | 🔴 Removed: 1

🟢🆕 Added 2
📂 WATCHLIST
🟢 + `ABC` - Example Company Inc.
🟢 + `XYZ` - Another Company Ltd.

🔴🗑️ Removed 1
📂 SWINGING
🔴 - `NVDA` - NVIDIA Corporation

🔗 https://www.tradingview.com/watchlists/326877343/
```

The TradingView link is placed at the end of the message so Discord link previews appear after the actionable content.

## How It Works

Each run performs the same basic flow:

```text
Fetch current TradingView watchlist
-> Normalize symbols
-> Read previous snapshot from state/watchlist_326877343.json
-> added = current - previous
-> removed = previous - current
-> notify Discord only if needed
-> write the current snapshot back to state
```

Discord output shows plain tickers such as `AAPL`, but the internal comparison uses exchange-qualified symbols such as `NASDAQ:AAPL`. This prevents false matches when the same ticker exists on multiple exchanges.

## Requirements

- Python 3.9+
- Playwright
- Chromium installed through Playwright
- A Discord channel webhook

## Installation

```bash
git clone https://github.com/lzdaniel/tradingview-watchlist-discord.git
cd tradingview-watchlist-discord

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e .
python -m playwright install chromium

cp .env.example .env
```

Edit `.env` and set:

```text
DISCORD_WEBHOOK_URL=<your-discord-webhook-url>
```

## Configuration

The main configuration is environment-variable based:

| Variable | Default | Description |
| --- | --- | --- |
| `DISCORD_WEBHOOK_URL` | required | Discord webhook URL used for notifications. |
| `WATCHLIST_NAME` | `黄哥的观察` | Human-readable name shown in Discord messages. |
| `WATCHLIST_URL` | `https://www.tradingview.com/watchlists/326877343/` | Public TradingView watchlist URL. |
| `STATE_FILE` | `state/watchlist_326877343.json` | Snapshot used to detect additions and removals. |
| `MARKET_TIMEZONE` | `America/New_York` | Time zone for market-hours scheduling. |
| `MARKET_OPEN` | `09:30` | Regular-session open time. |
| `MARKET_CLOSE` | `16:00` | Regular-session close time. |
| `CHECK_INTERVAL_MARKET_SECONDS` | `900` | Local daemon scan interval during market hours. |
| `CHECK_INTERVAL_OFFHOURS_SECONDS` | `1800` | Local daemon scan interval outside market hours. |
| `HEADLESS` | `true` | Runs Chromium in headless mode. |
| `SEND_INITIAL_BASELINE` | `false` | If true, sends the full list when no previous state exists. |
| `PERSIST_LAST_SEEN` | `true` | If false, unchanged runs do not modify state timestamps. Useful for GitHub Actions. |

## Usage

Run one scan. If there are no changes, no Discord message is sent:

```bash
set -a
source .env
set +a

python -m tv_watchlist_discord.watcher run-once
```

Dry-run one scan without sending Discord messages:

```bash
python -m tv_watchlist_discord.watcher run-once --dry-run
```

Send the full watchlist now:

```bash
python -m tv_watchlist_discord.watcher send-full --label test
```

Run as a local daemon:

```bash
python -m tv_watchlist_discord.watcher daemon
```

The daemon uses `CHECK_INTERVAL_MARKET_SECONDS` during regular market hours and `CHECK_INTERVAL_OFFHOURS_SECONDS` outside regular market hours.

## GitHub Actions

This repository includes a scheduled workflow:

```text
.github/workflows/tradingview-watchlist.yml
```

The workflow supports:

- Scheduled market-hours scans.
- Scheduled off-hours scans.
- Manual runs through `workflow_dispatch`.
- Repository state commits so each GitHub Actions run can compare against the previous snapshot.

### Required Secret

Set this repository secret:

```text
DISCORD_WEBHOOK_URL
```

In GitHub:

```text
Settings -> Secrets and variables -> Actions -> New repository secret
```

### Schedule

The workflow uses two cron schedules:

```yaml
- cron: "*/15 13-21 * * 1-5"
- cron: "7,37 * * * *"
```

The Python code still checks `America/New_York` market hours:

- `market` mode scans only during `09:30-16:00`.
- `offhours` mode skips regular market hours.
- `always` mode is available for manual runs.

GitHub cron schedules run in UTC and may be delayed by GitHub Actions queueing.

### Public Repository State

If this project is hosted as a public repository, `state/watchlist_326877343.json` is also public. It contains only the watchlist snapshot used for diffing. The Discord webhook must stay in GitHub Secrets and must not be committed.

## macOS launchd

An example launchd plist is included:

```text
launchd/com.algowatch.tradingview-watchlist-discord.plist.example
```

Use it as a template if you want the watcher to run on a Mac. A sleeping Mac will not run scheduled scans; use GitHub Actions or a server if you need monitoring while the computer is asleep.

## Limitations

- TradingView does not provide an official public-watchlist member-change webhook.
- This project reads a rendered TradingView page and scanner responses through Playwright, so page or API changes may require parser updates.
- The schedule treats weekdays as trading days and does not include a US market holiday calendar.
- GitHub Actions scheduled workflows are not guaranteed to run exactly on time.
- Avoid high-frequency scraping. The default 15/30 minute cadence is intentionally conservative.

## Security

- Never commit `.env`.
- Store Discord webhooks in GitHub Secrets or another secret manager.
- Rotate the Discord webhook if it is accidentally exposed.
- The repository `.gitignore` excludes local environment files, virtualenvs, logs, and debug artifacts.

## Development

Compile-check the package:

```bash
python -m py_compile src/tv_watchlist_discord/watcher.py
```

Run a scheduled-mode dry run:

```bash
python -m tv_watchlist_discord.watcher run-scheduled --mode always --dry-run
```

Inspect the current state:

```bash
python - <<'PY'
import json
from pathlib import Path

state = json.loads(Path("state/watchlist_326877343.json").read_text())
print(len(state["items"]))
print(state.get("last_seen_at"))
PY
```

## License

No license has been specified yet.
