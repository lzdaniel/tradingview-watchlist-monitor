from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 fallback if needed.
    ZoneInfo = None  # type: ignore

DEFAULT_URL = ""
DEFAULT_NAME = "TradingView Watchlist"
UNCATEGORIZED = "未分类"
MAX_MESSAGE_CONTENT = 1900
CURRENCY_CODES = {"USD", "KRW", "JPY", "TWD", "HKD", "CAD", "EUR", "GBP", "AUD", "CNY", "CNH"}

EXCHANGE_ALIASES = {
    "AMEX",
    "ASX",
    "BINANCE",
    "BITSTAMP",
    "BYBIT",
    "CBOE",
    "CBOT",
    "CME",
    "COINBASE",
    "COMEX",
    "CRYPTO",
    "FOREXCOM",
    "HKEX",
    "ICEUS",
    "NASDAQ",
    "NYSE",
    "NYSEARCA",
    "NYSEAMERICAN",
    "OANDA",
    "OTC",
    "OTCBB",
    "OTCQB",
    "OTCQX",
    "TVC",
}

NOISE_WORDS = {
    "ADD",
    "ALL",
    "BUY",
    "CLOSE",
    "COPY",
    "CREATE",
    "DELETE",
    "EDIT",
    "EXPORT",
    "FOLLOW",
    "IDEAS",
    "IMPORT",
    "INVITE",
    "LOGIN",
    "MENU",
    "MORE",
    "OPEN",
    "PRICE",
    "REMOVE",
    "SAVE",
    "SELL",
    "SHARE",
    "SIGN",
    "SORT",
    "SYMBOL",
    "SYMBOLS",
}


@dataclass(frozen=True)
class WatchItem:
    symbol: str
    ticker: str
    exchange: str
    category: str = UNCATEGORIZED
    description: str = ""

    def as_dict(self) -> Dict[str, str]:
        return {
            "symbol": self.symbol,
            "ticker": self.ticker,
            "exchange": self.exchange,
            "category": self.category or UNCATEGORIZED,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WatchItem":
        symbol = normalize_symbol(str(data.get("symbol") or data.get("proName") or data.get("name") or ""))
        ticker = str(data.get("ticker") or symbol.split(":")[-1]).upper()
        exchange = str(data.get("exchange") or (symbol.split(":")[0] if ":" in symbol else "")).upper()
        return cls(
            symbol=symbol,
            ticker=ticker,
            exchange=exchange,
            category=clean_category(str(data.get("category") or UNCATEGORIZED)),
            description=clean_text(str(data.get("description") or "")),
        )


@dataclass
class Config:
    watchlist_url: str
    watchlist_name: str
    notifiers: List[str]
    discord_webhook_url: str
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_disable_web_page_preview: bool
    state_file: Path
    market_timezone: str
    market_open: dtime
    market_close: dtime
    market_interval_seconds: int
    offhours_interval_seconds: int
    snapshot_hours: List[int]
    snapshot_window_minutes: int
    headless: bool
    dry_run: bool
    send_initial_baseline: bool
    persist_last_seen: bool
    storage_state: Optional[Path]

    @classmethod
    def from_env(cls, args: argparse.Namespace) -> "Config":
        project_root = Path(__file__).resolve().parents[2]
        state_file = Path(os.getenv("STATE_FILE", "state/watchlist.json"))
        if not state_file.is_absolute():
            state_file = project_root / state_file

        storage_state_raw = os.getenv("TV_STORAGE_STATE", "").strip()
        storage_state = Path(storage_state_raw).expanduser() if storage_state_raw else None
        if storage_state and not storage_state.is_absolute():
            storage_state = project_root / storage_state

        watchlist_url = os.getenv("WATCHLIST_URL", DEFAULT_URL).strip()
        if not watchlist_url:
            raise ValueError("WATCHLIST_URL is required.")

        return cls(
            watchlist_url=watchlist_url,
            watchlist_name=os.getenv("WATCHLIST_NAME", DEFAULT_NAME),
            notifiers=parse_notifiers(os.getenv("NOTIFIERS", "")),
            discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", "").strip(),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
            telegram_disable_web_page_preview=str_to_bool(os.getenv("TELEGRAM_DISABLE_WEB_PAGE_PREVIEW", "false")),
            state_file=state_file,
            market_timezone=os.getenv("MARKET_TIMEZONE", "America/New_York"),
            market_open=parse_hhmm(os.getenv("MARKET_OPEN", "09:30")),
            market_close=parse_hhmm(os.getenv("MARKET_CLOSE", "16:00")),
            market_interval_seconds=int(os.getenv("CHECK_INTERVAL_MARKET_SECONDS", "900")),
            offhours_interval_seconds=int(os.getenv("CHECK_INTERVAL_OFFHOURS_SECONDS", "21600")),
            snapshot_hours=parse_snapshot_hours(os.getenv("SNAPSHOT_HOURS", "0,6,12,18")),
            snapshot_window_minutes=int(os.getenv("SNAPSHOT_WINDOW_MINUTES", "45")),
            headless=str_to_bool(os.getenv("HEADLESS", "true")),
            dry_run=bool(getattr(args, "dry_run", False)) or str_to_bool(os.getenv("DRY_RUN", "false")),
            send_initial_baseline=str_to_bool(os.getenv("SEND_INITIAL_BASELINE", "false")),
            persist_last_seen=str_to_bool(os.getenv("PERSIST_LAST_SEEN", "true")),
            storage_state=storage_state,
        )



def parse_notifiers(value: str) -> List[str]:
    raw = [part.strip().lower() for part in value.split(",") if part.strip()]
    if not raw:
        return ["discord"]
    allowed = {"discord", "telegram"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"Unsupported notifier(s): {', '.join(unknown)}")
    return list(dict.fromkeys(raw))


def parse_snapshot_hours(value: str) -> List[int]:
    hours: List[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        hour = int(part)
        if hour < 0 or hour > 23:
            raise ValueError(f"Snapshot hour out of range: {hour}")
        hours.append(hour)
    return sorted(set(hours))


def parse_hhmm(value: str) -> dtime:
    hour, minute = value.strip().split(":", 1)
    return dtime(hour=int(hour), minute=int(minute))


def str_to_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def clean_category(value: str) -> str:
    value = clean_text(value)
    if not value or len(value) > 80:
        return UNCATEGORIZED
    if value.upper() in NOISE_WORDS:
        return UNCATEGORIZED
    return value


def normalize_symbol(value: str) -> str:
    value = clean_text(value).upper().replace(" ", "")
    value = value.replace("/", "") if value.startswith("/") else value
    if not value:
        return ""

    slug_match = re.search(r"/symbols/([^/?#]+)/?", value, flags=re.IGNORECASE)
    if slug_match:
        value = slug_match.group(1).upper()

    value = value.replace("-", ":", 1) if ":" not in value and "-" in value else value
    value = value.strip(":")
    return value


def split_symbol(symbol: str) -> Tuple[str, str]:
    symbol = normalize_symbol(symbol)
    if ":" in symbol:
        exchange, ticker = symbol.split(":", 1)
        return exchange.upper(), ticker.upper()
    return "", symbol.upper()


def is_probable_symbol(symbol: str) -> bool:
    symbol = normalize_symbol(symbol)
    if not symbol or len(symbol) > 30:
        return False
    exchange, ticker = split_symbol(symbol)
    if ticker in NOISE_WORDS:
        return False
    if ticker.isdigit() and not exchange:
        return False
    if exchange and exchange not in EXCHANGE_ALIASES and len(exchange) > 12:
        return False
    has_letter = bool(re.search(r"[A-Z]", ticker))
    has_numeric_listing = bool(exchange and ticker.isdigit() and 1 <= len(ticker) <= 8)
    return bool(re.fullmatch(r"[A-Z0-9._:=!-]{1,30}", symbol)) and (has_letter or has_numeric_listing)


def dedupe_items(items: Iterable[WatchItem]) -> List[WatchItem]:
    seen: Dict[str, WatchItem] = {}
    for item in items:
        if not is_probable_symbol(item.symbol):
            continue
        symbol = normalize_symbol(item.symbol)
        exchange, ticker = split_symbol(symbol)
        normalized = f"{exchange}:{ticker}" if exchange else ticker
        category = clean_category(item.category)
        candidate = WatchItem(
            symbol=normalized,
            ticker=ticker,
            exchange=exchange,
            category=category,
            description=item.description,
        )
        if normalized not in seen or seen[normalized].category == UNCATEGORIZED:
            seen[normalized] = candidate
    return sorted(seen.values(), key=lambda x: (x.category, x.exchange, x.ticker))


def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"items": [], "open_full_sent": [], "close_full_sent": []}
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    tmp.replace(path)


def fetch_watchlist(config: Config) -> List[WatchItem]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: python -m pip install -e . && python -m playwright install chromium"
        ) from exc

    json_payloads: List[Any] = []

    def maybe_capture_json(response: Any) -> None:
        url = response.url.lower()
        if "watch" not in url and "symbol" not in url and "scanner" not in url:
            return
        content_type = (response.headers or {}).get("content-type", "")
        if "json" not in content_type and "application" not in content_type:
            return
        try:
            payload = response.json()
        except Exception:
            return
        json_payloads.append(payload)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=config.headless)
        context_kwargs: Dict[str, Any] = {
            "viewport": {"width": 1440, "height": 9000},
            "locale": "en-US",
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
            ),
        }
        if config.storage_state and config.storage_state.exists():
            context_kwargs["storage_state"] = str(config.storage_state)
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        page.on("response", maybe_capture_json)
        page.goto(config.watchlist_url, wait_until="domcontentloaded", timeout=45_000)
        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        dom_items = collect_rendered_items(page)
        context.close()
        browser.close()

    json_items = extract_items_from_json_payloads(json_payloads)
    base_items = json_items if json_items else dom_items
    items = enrich_items_from_rendered_text(dedupe_items(base_items), dom_items)
    if not items:
        raise RuntimeError("No watchlist symbols were extracted. TradingView may have changed the page or may require login.")
    return items


def scroll_page_to_bottom(page: Any) -> None:
    previous_height = -1
    stable_rounds = 0
    for _ in range(40):
        height = page.evaluate("() => Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)")
        page.mouse.wheel(0, 1800)
        page.wait_for_timeout(600)
        if height == previous_height:
            stable_rounds += 1
        else:
            stable_rounds = 0
        previous_height = height
        if stable_rounds >= 3:
            break


def collect_rendered_items(page: Any) -> List[WatchItem]:
    items: List[WatchItem] = []
    previous_height = -1
    stable_rounds = 0
    for _ in range(40):
        items.extend(extract_rendered_items(page))
        height = page.evaluate("() => Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)")
        page.mouse.wheel(0, 1800)
        page.wait_for_timeout(600)
        if height == previous_height:
            stable_rounds += 1
        else:
            stable_rounds = 0
        previous_height = height
        if stable_rounds >= 3:
            break
    items.extend(extract_rendered_items(page))
    return dedupe_items(items)


def extract_rendered_items(page: Any) -> List[WatchItem]:
    """Extract symbols from the rendered public watchlist page.

    TradingView's table is easier to read from rendered text and symbol links than
    from brittle DOM class names. Links provide exchange:ticker; text order gives
    us the section/category labels.
    """
    body_text = page.locator("body").inner_text(timeout=10_000)
    links = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('a[href*="/symbols/"]')).map((a) => ({
          text: (a.innerText || '').trim(),
          href: a.href || ''
        }))
        """
    )
    link_items: List[WatchItem] = []
    for link in links:
        symbol = normalize_symbol(str(link.get("href") or ""))
        if not symbol:
            symbol = normalize_symbol(str(link.get("text") or ""))
        if not is_probable_symbol(symbol):
            continue
        exchange, ticker = split_symbol(symbol)
        link_items.append(WatchItem(symbol=f"{exchange}:{ticker}" if exchange else ticker, ticker=ticker, exchange=exchange))

    category_by_ticker, description_by_ticker = parse_categories_from_body_text(body_text)
    items: List[WatchItem] = []
    for item in link_items:
        category = category_by_ticker.get(item.ticker, UNCATEGORIZED)
        description = description_by_ticker.get(item.ticker, "")
        items.append(
            WatchItem(
                symbol=item.symbol,
                ticker=item.ticker,
                exchange=item.exchange,
                category=category,
                description=description,
            )
        )

    for ticker, category in category_by_ticker.items():
        items.append(
            WatchItem(
                symbol=ticker,
                ticker=ticker,
                exchange="",
                category=category,
                description=description_by_ticker.get(ticker, ""),
            )
        )
    return items


def enrich_items_from_rendered_text(items: List[WatchItem], rendered_items: Sequence[WatchItem]) -> List[WatchItem]:
    category_by_ticker = {item.ticker: item.category for item in rendered_items if item.category != UNCATEGORIZED}
    description_by_ticker = {item.ticker: item.description for item in rendered_items if item.description}
    enriched: List[WatchItem] = []
    for item in items:
        enriched.append(
            WatchItem(
                symbol=item.symbol,
                ticker=item.ticker,
                exchange=item.exchange,
                category=category_by_ticker.get(item.ticker, item.category),
                description=description_by_ticker.get(item.ticker, item.description),
            )
        )
    return sorted(enriched, key=lambda x: (x.category, x.exchange, x.ticker))


def parse_categories_from_body_text(body_text: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    lines = [clean_text(line) for line in body_text.splitlines()]
    lines = [line for line in lines if line]

    table_start = 0
    for index, line in enumerate(lines):
        if re.fullmatch(r"\d+\s+symbols?", line, flags=re.IGNORECASE):
            table_start = index + 1
            break

    table_lines: List[str] = []
    for line in lines[table_start:]:
        if line.startswith("Select market data provided") or line == "Products":
            break
        table_lines.append(line)

    category = UNCATEGORIZED
    category_by_ticker: Dict[str, str] = {}
    description_by_ticker: Dict[str, str] = {}

    for index, line in enumerate(table_lines):
        next_line = table_lines[index + 1] if index + 1 < len(table_lines) else ""
        next_next = table_lines[index + 2] if index + 2 < len(table_lines) else ""

        if looks_like_category(line, next_line, next_next):
            category = clean_category(line)
            continue

        # TradingView renders rows in advanced watchlist view roughly as:
        # avatar/initial, ticker, company name, last price, currency, ...
        if line.upper() in CURRENCY_CODES:
            continue

        if looks_like_row_avatar(line) and index + 2 < len(table_lines):
            ticker = normalize_ticker_from_text(next_line)
            if ticker:
                category_by_ticker.setdefault(ticker, category)
                description_by_ticker.setdefault(ticker, next_next if not looks_like_numeric_cell(next_next) else "")
                continue

    return category_by_ticker, description_by_ticker


def normalize_ticker_from_text(value: str) -> str:
    value = clean_text(value).upper()
    if not value or value in NOISE_WORDS or value in CURRENCY_CODES:
        return ""
    if len(value) > 16:
        return ""
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,15}", value):
        return ""
    if not re.search(r"[A-Z]", value) and len(value) > 6:
        return ""
    return value


def looks_like_row_avatar(value: str) -> bool:
    value = clean_text(value).upper()
    return bool(re.fullmatch(r"[A-Z0-9]{1,2}", value))


def looks_like_numeric_cell(value: str) -> bool:
    value = clean_text(value).replace("−", "-").replace("‪", "").replace("‬", "")
    return bool(re.fullmatch(r"[-+]?\d[\d,.]*%?", value)) or value in CURRENCY_CODES


def looks_like_category(value: str, next_line: str, next_next: str) -> bool:
    cleaned = clean_category(value)
    if cleaned == UNCATEGORIZED:
        return False
    if value != value.upper():
        return False
    if not re.search(r"[A-Z]", value):
        return False
    if value.upper() in CURRENCY_CODES:
        return False
    if len(value) > 40:
        return False
    return looks_like_row_avatar(next_line) and bool(normalize_ticker_from_text(next_next))


def extract_dom_items(page: Any) -> List[WatchItem]:
    raw_items = page.evaluate(
        """
        () => {
          const visible = (el) => {
            const style = window.getComputedStyle(el);
            const box = el.getBoundingClientRect();
            return style && style.visibility !== 'hidden' && style.display !== 'none' && box.width > 0 && box.height > 0;
          };
          const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
          const badCategory = new Set(['ADD','ALL','BUY','CLOSE','COPY','CREATE','DELETE','EDIT','EXPORT','FOLLOW','IDEAS','IMPORT','LOGIN','MENU','MORE','OPEN','PRICE','REMOVE','SAVE','SELL','SHARE','SIGN','SORT','SYMBOL','SYMBOLS','WATCHLIST']);
          const normalizeHrefSymbol = (href) => {
            const match = (href || '').match(/\/symbols\/([^\/?#]+)\/?/i);
            if (!match) return '';
            const slug = match[1].toUpperCase();
            const parts = slug.split('-');
            if (parts.length >= 2) return `${parts[0]}:${parts.slice(1).join('-')}`;
            return slug;
          };
          const rowFor = (el) => {
            let node = el;
            for (let i = 0; i < 7 && node && node !== document.body; i++) {
              const text = clean(node.innerText);
              const links = node.querySelectorAll ? node.querySelectorAll('a[href*="/symbols/"]').length : 0;
              if (links >= 1 && text.length > 0 && text.length < 260) return node;
              node = node.parentElement;
            }
            return el;
          };
          const categoryBefore = (row) => {
            let node = row;
            for (let depth = 0; depth < 5 && node && node !== document.body; depth++) {
              let prev = node.previousElementSibling;
              let checks = 0;
              while (prev && checks < 20) {
                checks += 1;
                if (visible(prev)) {
                  const text = clean(prev.innerText).split('\n').map(clean).filter(Boolean)[0] || '';
                  const hasSymbolLink = prev.querySelector && prev.querySelector('a[href*="/symbols/"]');
                  if (text && !hasSymbolLink && text.length <= 80 && !badCategory.has(text.toUpperCase())) return text;
                }
                prev = prev.previousElementSibling;
              }
              node = node.parentElement;
            }
            return '未分类';
          };
          const rows = [];
          const links = Array.from(document.querySelectorAll('a[href*="/symbols/"]')).filter(visible);
          for (const link of links) {
            const symbol = normalizeHrefSymbol(link.href);
            if (!symbol) continue;
            const row = rowFor(link);
            const rowText = clean(row.innerText);
            rows.push({
              symbol,
              category: categoryBefore(row),
              description: rowText.replace(clean(link.innerText), '').slice(0, 120)
            });
          }
          return rows;
        }
        """
    )
    return [WatchItem.from_dict(item) for item in raw_items]


def extract_items_from_json_payloads(payloads: Sequence[Any]) -> List[WatchItem]:
    items: List[WatchItem] = []
    for payload in payloads:
        items.extend(extract_items_from_json(payload))
    return items


def extract_items_from_json(node: Any, category: str = UNCATEGORIZED) -> List[WatchItem]:
    found: List[WatchItem] = []
    if isinstance(node, dict):
        current_category = clean_category(str(node.get("category") or node.get("section") or node.get("group") or node.get("title") or node.get("name") or category))
        symbol_value = node.get("symbol") or node.get("proName") or node.get("shortName") or node.get("name") or node.get("s")
        if isinstance(symbol_value, str) and is_probable_symbol(symbol_value):
            exchange, ticker = split_symbol(symbol_value)
            found.append(
                WatchItem(
                    symbol=f"{exchange}:{ticker}" if exchange else ticker,
                    ticker=ticker,
                    exchange=exchange,
                    category=current_category,
                    description=clean_text(str(node.get("description") or node.get("fullName") or "")),
                )
            )
        symbols = node.get("symbols")
        if isinstance(symbols, list):
            for raw in symbols:
                if isinstance(raw, str) and is_probable_symbol(raw):
                    exchange, ticker = split_symbol(raw)
                    found.append(WatchItem(symbol=f"{exchange}:{ticker}" if exchange else ticker, ticker=ticker, exchange=exchange, category=current_category))
                else:
                    found.extend(extract_items_from_json(raw, current_category))
        for key, value in node.items():
            if key == "symbols":
                continue
            if isinstance(value, (dict, list)):
                found.extend(extract_items_from_json(value, current_category))
    elif isinstance(node, list):
        for item in node:
            found.extend(extract_items_from_json(item, category))
    return found


def item_map(items: Iterable[WatchItem]) -> Dict[str, WatchItem]:
    return {item.symbol: item for item in items}


def group_by_category(items: Iterable[WatchItem]) -> Dict[str, List[WatchItem]]:
    grouped: Dict[str, List[WatchItem]] = defaultdict(list)
    for item in sorted(items, key=lambda x: (x.category, x.exchange, x.ticker)):
        grouped[item.category or UNCATEGORIZED].append(item)
    return dict(grouped)


def format_item(item: WatchItem) -> str:
    symbol = item.ticker
    if item.description:
        return f"`{symbol}` - {item.description}"
    return f"`{symbol}`"


def format_grouped_items(items: Sequence[WatchItem], bullet: str) -> List[str]:
    lines: List[str] = []
    for category, group in group_by_category(items).items():
        lines.append(f"**📂 {category}**")
        for item in group:
            lines.append(f"{bullet} {format_item(item)}")
    return lines


def chunk_lines(lines: Sequence[str], limit: int = MAX_MESSAGE_CONTENT) -> List[str]:
    chunks: List[str] = []
    current = ""
    for line in lines:
        addition = line if not current else "\n" + line
        if len(current) + len(addition) > limit and current:
            chunks.append(current)
            current = line
        else:
            current += addition
    if current:
        chunks.append(current)
    return chunks


def send_discord(config: Config, content: str) -> None:
    if not config.discord_webhook_url:
        raise RuntimeError("DISCORD_WEBHOOK_URL is required when the discord notifier is enabled.")

    payload = json.dumps({"content": content}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        config.discord_webhook_url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "tradingview-watchlist-monitor/0.1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status >= 300:
                raise RuntimeError(f"Discord webhook returned HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Discord webhook failed: HTTP {exc.code}: {body}") from exc


def send_telegram(config: Config, content: str) -> None:
    if not config.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required when the telegram notifier is enabled.")
    if not config.telegram_chat_id:
        raise RuntimeError("TELEGRAM_CHAT_ID is required when the telegram notifier is enabled.")

    payload = json.dumps(
        {
            "chat_id": config.telegram_chat_id,
            "text": content,
            "disable_web_page_preview": config.telegram_disable_web_page_preview,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "tradingview-watchlist-monitor/0.1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status >= 300:
                raise RuntimeError(f"Telegram sendMessage returned HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram sendMessage failed: HTTP {exc.code}: {body}") from exc


def send_notification(config: Config, notifier: str, content: str) -> None:
    if config.dry_run:
        print(f"\n--- DRY RUN {notifier.upper()} MESSAGE ---")
        print(content)
        print("--- END MESSAGE ---\n")
        return

    if notifier == "discord":
        send_discord(config, content)
    elif notifier == "telegram":
        send_telegram(config, content)
    else:  # pragma: no cover - parse_notifiers prevents this.
        raise ValueError(f"Unsupported notifier: {notifier}")


def send_messages(config: Config, lines: Sequence[str]) -> None:
    for chunk in chunk_lines(lines):
        for notifier in config.notifiers:
            send_notification(config, notifier, chunk)

def now_in_market_tz(config: Config) -> datetime:
    if ZoneInfo is None:
        return datetime.now()
    return datetime.now(ZoneInfo(config.market_timezone))


def is_weekday_trading_day(day: date) -> bool:
    return day.weekday() < 5


def is_market_open_now(config: Config, now: Optional[datetime] = None) -> bool:
    now = now or now_in_market_tz(config)
    return is_weekday_trading_day(now.date()) and config.market_open <= now.time() < config.market_close


def should_send_open_full(config: Config, state: Dict[str, Any], now: datetime) -> bool:
    today = now.date().isoformat()
    return (
        is_weekday_trading_day(now.date())
        and is_time_window(now.time(), config.market_open, config.snapshot_window_minutes)
        and today not in set(state.get("open_full_sent", []))
    )


def should_send_close_full(config: Config, state: Dict[str, Any], now: datetime) -> bool:
    today = now.date().isoformat()
    return (
        is_weekday_trading_day(now.date())
        and is_time_window(now.time(), config.market_close, config.snapshot_window_minutes)
        and today not in set(state.get("close_full_sent", []))
    )


def mark_sent(state: Dict[str, Any], key: str, day: date) -> None:
    mark_sent_value(state, key, day.isoformat())


def mark_sent_value(state: Dict[str, Any], key: str, value: str, keep: int = 90) -> None:
    values = list(dict.fromkeys(state.get(key, [])))
    if value not in values:
        values.append(value)
    state[key] = values[-keep:]


def minutes_since_midnight(value: dtime) -> int:
    return value.hour * 60 + value.minute


def is_time_window(now_time: dtime, start: dtime, window_minutes: int) -> bool:
    now_minutes = minutes_since_midnight(now_time)
    start_minutes = minutes_since_midnight(start)
    return start_minutes <= now_minutes < start_minutes + window_minutes


def periodic_snapshot_marker(now: datetime) -> str:
    return f"{now.date().isoformat()}T{now.hour:02d}"


def is_periodic_snapshot_window(config: Config, now: datetime) -> bool:
    return now.hour in config.snapshot_hours and now.minute < config.snapshot_window_minutes


def is_periodic_snapshot_due(config: Config, state: Dict[str, Any], now: datetime) -> bool:
    marker = periodic_snapshot_marker(now)
    sent = set(state.get("periodic_full_sent", []))
    return is_periodic_snapshot_window(config, now) and marker not in sent


def is_close_snapshot_due(config: Config, state: Dict[str, Any], now: datetime) -> bool:
    today = now.date().isoformat()
    sent = set(state.get("close_full_sent", []))
    return (
        is_weekday_trading_day(now.date())
        and is_time_window(now.time(), config.market_close, config.snapshot_window_minutes)
        and today not in sent
    )


def update_state_snapshot(config: Config, state: Dict[str, Any], items: Sequence[WatchItem], now: datetime) -> None:
    state_update = {
        "source_url": config.watchlist_url,
        "watchlist_name": config.watchlist_name,
        "items": [item.as_dict() for item in items],
    }
    if config.persist_last_seen:
        state_update["last_seen_at"] = now.isoformat()
    state.update(state_update)


def build_diff_message(config: Config, added: Sequence[WatchItem], removed: Sequence[WatchItem], now: datetime) -> List[str]:
    lines = [
        f"🚨 **{config.watchlist_name} Update** 🚨",
        f"🕒 {now.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"📊 🟢 Added: {len(added)} | 🔴 Removed: {len(removed)}",
        "",
    ]
    if added:
        lines.append(f"🟢🆕 **Added {len(added)}**")
        lines.extend(format_grouped_items(added, "🟢 +"))
        lines.append("")
    if removed:
        lines.append(f"🔴🗑️ **Removed {len(removed)}**")
        lines.extend(format_grouped_items(removed, "🔴 -"))
    lines.extend(["", f"🔗 {config.watchlist_url}"])
    return lines


def build_full_message(
    config: Config,
    items: Sequence[WatchItem],
    label: str,
    now: datetime,
    added_count: int = 0,
    removed_count: int = 0,
) -> List[str]:
    emoji = "🌅" if label == "open" else "🌙" if label == "close" else "📋"
    display_labels = {
        "open": "Market Open Full List",
        "close": "Market Close Full List",
        "snapshot": "Scheduled Full List",
        "baseline": "Initial Full List",
        "test": "Full List",
    }
    display_label = display_labels.get(label, "Full List")
    lines = [
        f"{emoji} **{config.watchlist_name} - {display_label}**",
        f"🕒 {now.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"📊 🟢 Added: {added_count} | 🔴 Removed: {removed_count}",
        f"📌 Total: {len(items)} symbols",
        "",
    ]
    lines.extend(format_grouped_items(items, "•"))
    lines.extend(["", f"🔗 {config.watchlist_url}"])
    return lines


def run_once(config: Config, force_full_label: Optional[str] = None) -> Dict[str, Any]:
    now = now_in_market_tz(config)
    items = fetch_watchlist(config)
    state = load_state(config.state_file)
    added, removed, had_previous = diff_items(state, items)
    open_due = should_send_open_full(config, state, now)
    close_due = should_send_close_full(config, state, now)

    if force_full_label:
        send_messages(config, build_full_message(config, items, force_full_label, now, len(added), len(removed)))
    elif open_due:
        send_messages(config, build_full_message(config, items, "open", now, len(added), len(removed)))
        mark_sent(state, "open_full_sent", now.date())
    elif close_due:
        send_messages(config, build_full_message(config, items, "close", now, len(added), len(removed)))
        mark_sent(state, "close_full_sent", now.date())
    elif had_previous and (added or removed):
        send_messages(config, build_diff_message(config, added, removed, now))
    elif not had_previous and config.send_initial_baseline:
        send_messages(config, build_full_message(config, items, "baseline", now, len(added), len(removed)))
    else:
        print(f"No notification needed. symbols={len(items)} added={len(added)} removed={len(removed)}")

    update_state_snapshot(config, state, items, now)
    save_state(config.state_file, state)
    return {"items": len(items), "added": len(added), "removed": len(removed)}


def diff_items(state: Dict[str, Any], items: Sequence[WatchItem]) -> Tuple[List[WatchItem], List[WatchItem], bool]:
    previous_items = [WatchItem.from_dict(item) for item in state.get("items", [])]
    previous = item_map(previous_items)
    current = item_map(items)
    added = [current[symbol] for symbol in sorted(set(current) - set(previous))]
    removed = [previous[symbol] for symbol in sorted(set(previous) - set(current))]
    return added, removed, bool(previous)


def run_snapshot(config: Config) -> Dict[str, Any]:
    now = now_in_market_tz(config)
    state = load_state(config.state_file)
    due_labels: List[str] = []

    if is_close_snapshot_due(config, state, now):
        due_labels.append("close")
    if is_periodic_snapshot_due(config, state, now):
        due_labels.append("snapshot")

    if not due_labels:
        print(f"Skipping snapshot; no full-list snapshot due at {now.isoformat()}")
        return {"skipped": True, "mode": "snapshot"}

    items = fetch_watchlist(config)
    added, removed, _ = diff_items(state, items)

    for label in due_labels:
        send_messages(config, build_full_message(config, items, label, now, len(added), len(removed)))
        if label == "close":
            mark_sent(state, "close_full_sent", now.date())
        elif label == "snapshot":
            mark_sent_value(state, "periodic_full_sent", periodic_snapshot_marker(now))

    update_state_snapshot(config, state, items, now)
    save_state(config.state_file, state)
    return {"items": len(items), "added": len(added), "removed": len(removed), "snapshots": due_labels}

def should_run_scheduled_mode(config: Config, mode: str, now: Optional[datetime] = None) -> bool:
    now = now or now_in_market_tz(config)
    market_open = is_market_open_now(config, now)
    if mode == "always":
        return True
    if mode == "market":
        return market_open
    if mode == "offhours":
        return not market_open
    if mode == "snapshot":
        return True
    raise ValueError(f"Unsupported schedule mode: {mode}")


def run_scheduled(config: Config, mode: str) -> Dict[str, Any]:
    now = now_in_market_tz(config)
    if mode == "snapshot":
        return run_snapshot(config)
    if not should_run_scheduled_mode(config, mode, now):
        print(f"Skipping scan for mode={mode}; market_open={is_market_open_now(config, now)} time={now.isoformat()}")
        return {"skipped": True, "mode": mode}
    if mode == "market" and is_periodic_snapshot_due(config, load_state(config.state_file), now):
        return run_snapshot(config)
    return run_once(config)


def next_event_sleep_seconds(config: Config, now: datetime) -> int:
    candidates: List[datetime] = []
    for day_offset in range(8):
        current_day = now.date() + timedelta(days=day_offset)
        for hour in config.snapshot_hours:
            candidates.append(datetime.combine(current_day, dtime(hour=hour), tzinfo=now.tzinfo))
        if is_weekday_trading_day(current_day):
            candidates.append(datetime.combine(current_day, config.market_open, tzinfo=now.tzinfo))
            candidates.append(datetime.combine(current_day, config.market_close, tzinfo=now.tzinfo))

    future = [candidate for candidate in candidates if candidate > now]
    if not future:
        return config.offhours_interval_seconds
    return max(60, int((min(future) - now).total_seconds()))


def daemon_sleep_seconds(config: Config, now: Optional[datetime] = None) -> int:
    now = now or now_in_market_tz(config)
    base_interval = config.market_interval_seconds if is_market_open_now(config, now) else config.offhours_interval_seconds
    return min(base_interval, next_event_sleep_seconds(config, now))


def run_daemon(config: Config) -> None:
    print(f"Monitoring {config.watchlist_name}: {config.watchlist_url}")
    print(f"State file: {config.state_file}")
    while True:
        try:
            result = run_snapshot(config)
            if result.get("skipped"):
                result = run_once(config)
            print(f"{datetime.now().isoformat(timespec='seconds')} scan ok: {result}")
        except Exception as exc:
            print(f"{datetime.now().isoformat(timespec='seconds')} scan failed: {exc}", file=sys.stderr)
        time.sleep(daemon_sleep_seconds(config))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor a TradingView public watchlist and send notifications on changes.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    once = subparsers.add_parser("run-once", help="Fetch once, diff against state, and notify only if needed.")
    once.add_argument("--dry-run", action="store_true", help="Print notification messages instead of sending them.")

    daemon = subparsers.add_parser("daemon", help="Run forever with market/off-hours intervals.")
    daemon.add_argument("--dry-run", action="store_true", help="Print notification messages instead of sending them.")

    scheduled = subparsers.add_parser("run-scheduled", help="Run once only when the selected schedule mode should scan now.")
    scheduled.add_argument("--mode", choices=["always", "market", "offhours", "snapshot"], default=os.getenv("SCHEDULE_MODE", "always"))
    scheduled.add_argument("--dry-run", action="store_true", help="Print notification messages instead of sending them.")

    full = subparsers.add_parser("send-full", help="Fetch and send the full watchlist now.")
    full.add_argument("--label", choices=["open", "close", "test", "baseline"], default="test")
    full.add_argument("--dry-run", action="store_true", help="Print notification messages instead of sending them.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = Config.from_env(args)

    try:
        if args.command == "run-once":
            run_once(config)
        elif args.command == "send-full":
            run_once(config, force_full_label=args.label)
        elif args.command == "daemon":
            run_daemon(config)
        elif args.command == "run-scheduled":
            run_scheduled(config, args.mode)
        else:  # pragma: no cover
            parser.error(f"Unknown command: {args.command}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
