import argparse
import concurrent.futures
import json
import math
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html import escape
from typing import Dict, Iterable, List, Optional, Tuple


REPORT2_INPUT_STOCKS = [
    "AA",
    "ACHR",
    "BABA",
    "AFRM",
    "TER",
    "AI",
    "ALB",
    "AAPL",
    "AMAT",
    "AMPX",
    "AMD",
    "ANET",
    "APLD",
    "APO",
    "APP",
    "ASTS",
    "AMZN",
    "AVGO",
    "AXTI",
    "BE",
    "BROS",
    "BULL",
    "CAVA",
    "CCJ",
    "CCL",
    "ENPH",
    "CEG",
    "CELH",
    "CIFR",
    "CLSK",
    "CMG",
    "COIN",
    "CPNG",
    "CORZ",
    "CRM",
    "CRCL",
    "CRWD",
    "CRWV",
    "CVNA",
    "DAL",
    "DASH",
    "DDOG",
    "FSLY",
    "DELL",
    "DKNG",
    "DUOL",
    "ASML",
    "EL",
    "EOSE",
    "ETSY",
    "EXPE",
    "FCX",
    "FIGR",
    "FISV",
    "FLY",
    "NFLX",
    "FSLR",
    "FTNT",
    "SPOT",
    "GLW",
    "GLXY",
    "HIMS",
    "ARM",
    "HOOD",
    "HPE",
    "AAOI",
    "HUT",
    "SNDK",
    "IONQ",
    "INTC",
    "IOT",
    "IREN",
    "JOBY",
    "KKR",
    "LUMN",
    "LUNR",
    "LRCX",
    "LUV",
    "LYFT",
    "MBLY",
    "MCHP",
    "MP",
    "MRVL",
    "MSTR",
    "MSFT",
    "MU",
    "NBIS",
    "NET",
    "NOW",
    "NVDA",
    "NU",
    "NVTS",
    "OKLO",
    "OKTA",
    "ONDS",
    "ONON",
    "WDC",
    "ORCL",
    "OSCR",
    "KTOS",
    "PANW",
    "PATH",
    "PINS",
    "PL",
    "PLTR",
    "POET",
    "PYPL",
    "QBTS",
    "QCOM",
    "QS",
    "QUBT",
    "RCAT",
    "RBLX",
    "RDDT",
    "RDW",
    "RGTI",
    "RIVN",
    "RKLB",
    "RKT",
    "ROKU",
    "RCL",
    "SATS",
    "SHOP",
    "SMCI",
    "SMR",
    "AMD",
    "SNOW",
    "SOFI",
    "SOUN",
    "TE",
    "TEM",
    "TSM",
    "TSLA",
    "TOST",
    "TTD",
    "U",
    "UAL",
    "UEC",
    "UBER",
    "UPST",
    "USAR",
    "UUUU",
    "VRT",
    "VST",
    "W",
    "WULF",
    "XYZ",
    "GOOGL",
    "META",
    "ZETA",
    "ZS",
]


def dedupe_keep_order(symbols: List[str]) -> List[str]:
    return list(dict.fromkeys(symbols))


REPORT2_STOCKS = dedupe_keep_order(REPORT2_INPUT_STOCKS)
OPTIONS_REPORT_STOCKS = REPORT2_STOCKS
MY_PORTFOLIO_REPORT_FILE = "my_portfolio_report.txt"
EARNINGS_CACHE_FILE = "earnings_calendar_cache.json"
REPORT_HISTORY_FILE = "report_history.json"

DATA_BASE_URL = "https://data.alpaca.markets"
STOCK_FEED = "iex"
STOCK_ANALYSIS_BASE_URL = "https://stockanalysis.com/stocks"


@dataclass
class OptionRow:
    stock: str
    price: float
    trend: str
    pct_otm: float
    strike: float
    last_price: Optional[float]
    action: str


@dataclass
class ExcludedTickerRow:
    stock: str
    price: Optional[float]
    earnings_date: Optional[date]
    options_label: str
    premium: Optional[float]
    roi: Optional[float]


@dataclass
class RecommendationRow:
    label: str
    row: OptionRow


@dataclass
class PmccRow:
    stock: str
    price: float
    pct_otm: float
    below_52w_high_pct: float
    leaps_expiration: date
    leaps_strike: float
    leaps_delta: float
    leaps_price: float
    short_call_expiration: date
    short_call_strike: float
    short_call_delta: Optional[float]
    short_call_price: Optional[float]
    score: int


def api_get(path: str, api_key: str, api_secret: str, params: Optional[Dict[str, str]] = None) -> Dict:
    query = ""
    if params:
        query = "?" + urllib.parse.urlencode(params)
    url = DATA_BASE_URL + path + query
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
        "Accept": "application/json",
    }
    last_error: Optional[Exception] = None
    for attempt in range(5):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 429 and attempt < 4:
                retry_after = exc.headers.get("Retry-After")
                sleep_seconds = float(retry_after) if retry_after else (2 + attempt * 2)
                time.sleep(sleep_seconds)
                last_error = exc
                continue
            raise RuntimeError(f"HTTP {exc.code} for {path}: {body}") from exc
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(1 + attempt)
                continue
            raise RuntimeError(f"Network error for {path}: {exc}") from exc
    raise RuntimeError(f"Failed request for {path}: {last_error}")


def http_get_text(url: str) -> str:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "User-Agent": "Mozilla/5.0",
    }
    last_error: Optional[Exception] = None
    for attempt in range(4):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as exc:
            if exc.code in {429, 500, 502, 503, 504} and attempt < 3:
                time.sleep(1 + attempt)
                last_error = exc
                continue
            raise RuntimeError(f"HTTP {exc.code} for {url}") from exc
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(1 + attempt)
                continue
            raise RuntimeError(f"Network error for {url}: {exc}") from exc
    raise RuntimeError(f"Failed request for {url}: {last_error}")


def load_earnings_cache() -> Dict[str, Dict[str, str]]:
    if not os.path.exists(EARNINGS_CACHE_FILE):
        return {}
    try:
        with open(EARNINGS_CACHE_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            return payload
    except Exception:
        return {}
    return {}


def save_earnings_cache(cache: Dict[str, Dict[str, str]]) -> None:
    try:
        with open(EARNINGS_CACHE_FILE, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(cache, handle, indent=2, sort_keys=True)
    except OSError:
        return


def parse_earnings_date_from_html(html: str) -> Optional[date]:
    patterns = [
        r'Earnings Date<!----></span>.*?<td[^>]*title=\"([A-Za-z]+ \d{1,2}, \d{4})\"',
        r'The last earnings date was .*?([A-Za-z]+ \d{1,2}, \d{4})',
        r'"id":"earningsdate","title":"Earnings Date","value":"([A-Za-z]+ \d{1,2}, \d{4})"',
        r'"id":"earningsdate","title":"Earnings Date","value":"[^"]+","hover":"([A-Za-z]+ \d{1,2}, \d{4})"',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        parsed = match.group(1).strip()
        for fmt in ("%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(parsed, fmt).date()
            except ValueError:
                continue
    return None


def get_earnings_date(symbol: str, cache: Dict[str, Dict[str, str]], today: date) -> Optional[date]:
    cached = cache.get(symbol)
    if cached and cached.get("fetched_on") == today.isoformat():
        earnings_iso = cached.get("earnings_date")
        return date.fromisoformat(earnings_iso) if earnings_iso else None

    url = f"{STOCK_ANALYSIS_BASE_URL}/{symbol.lower()}/statistics/"
    html = http_get_text(url)
    earnings_date = parse_earnings_date_from_html(html)
    cache[symbol] = {
        "fetched_on": today.isoformat(),
        "earnings_date": earnings_date.isoformat() if earnings_date else "",
    }
    return earnings_date


def monday_of_week(day_value: date) -> date:
    return day_value - timedelta(days=day_value.weekday())


def earnings_in_report_week(earnings_date: Optional[date], report_start: date, expiration_date: date) -> bool:
    if earnings_date is None:
        return False
    return report_start <= earnings_date <= expiration_date


def isoformat_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_latest_prices(symbols: List[str], api_key: str, api_secret: str) -> Dict[str, float]:
    payload = api_get(
        "/v2/stocks/trades/latest",
        api_key,
        api_secret,
        {
            "symbols": ",".join(symbols),
            "feed": STOCK_FEED,
        },
    )
    trades = payload.get("trades", {})
    prices = {}
    for symbol in symbols:
        trade = trades.get(symbol)
        if trade and trade.get("p") is not None:
            prices[symbol] = float(trade["p"])
    return prices


def get_weekly_bars(symbol: str, start: date, end: date, api_key: str, api_secret: str) -> List[Dict]:
    payload = api_get(
        f"/v2/stocks/{symbol}/bars",
        api_key,
        api_secret,
        {
            "timeframe": "1Week",
            "start": isoformat_utc(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)),
            "end": isoformat_utc(datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)),
            "adjustment": "all",
            "feed": STOCK_FEED,
            "limit": "60",
        },
    )
    bars = payload.get("bars", [])
    if len(bars) < 8:
        raise RuntimeError(f"Expected at least 8 weekly bars for {symbol}, received {len(bars)}")
    return bars[-52:]


def get_daily_bars(symbol: str, start: date, end: date, api_key: str, api_secret: str) -> List[Dict]:
    payload = api_get(
        f"/v2/stocks/{symbol}/bars",
        api_key,
        api_secret,
        {
            "timeframe": "1Day",
            "start": isoformat_utc(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)),
            "end": isoformat_utc(datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)),
            "adjustment": "all",
            "feed": STOCK_FEED,
            "limit": "500",
        },
    )
    bars = payload.get("bars", [])
    if len(bars) < 20:
        raise RuntimeError(f"Expected at least 20 daily bars for {symbol}, received {len(bars)}")
    return bars


def average_weekly_move_pct(bars: List[Dict]) -> float:
    moves = []
    for bar in bars:
        high = float(bar["h"])
        low = float(bar["l"])
        close = float(bar["c"])
        if close <= 0:
            continue
        moves.append((high - low) / close)
    if not moves:
        raise RuntimeError("Could not calculate weekly move percentage from bars")
    return sum(moves) / len(moves)


def ema_series(values: List[float], length: int) -> List[float]:
    if not values:
        return []
    alpha = 2.0 / (length + 1.0)
    results = [values[0]]
    for value in values[1:]:
        results.append(alpha * value + (1.0 - alpha) * results[-1])
    return results


def sma_series(values: List[float], length: int) -> List[float]:
    if not values:
        return []
    results: List[float] = []
    window_sum = 0.0
    for index, value in enumerate(values):
        window_sum += value
        if index >= length:
            window_sum -= values[index - length]
        window_length = min(index + 1, length)
        results.append(window_sum / window_length)
    return results


def wavetrend_last_signal(bars: List[Dict]) -> str:
    closes = [float(bar["c"]) for bar in bars if bar.get("c") is not None]
    if len(closes) < 20:
        return "N/A"

    channel_length = 6
    average_length = 14
    signal_length = 2

    esa = ema_series(closes, channel_length)
    deviations = [abs(close - esa_value) for close, esa_value in zip(closes, esa)]
    d_values = ema_series(deviations, channel_length)
    ci_values = []
    for close, esa_value, d_value in zip(closes, esa, d_values):
        ci_values.append((close - esa_value) / (0.015 * d_value) if d_value != 0 else 0.0)
    wt1 = ema_series(ci_values, average_length)
    wt2 = sma_series(wt1, signal_length)

    for index in range(len(wt1) - 1, 0, -1):
        prev_wt1 = wt1[index - 1]
        prev_wt2 = wt2[index - 1]
        curr_wt1 = wt1[index]
        curr_wt2 = wt2[index]
        if prev_wt1 <= prev_wt2 and curr_wt1 > curr_wt2:
            return "Buy"
        if prev_wt1 >= prev_wt2 and curr_wt1 < curr_wt2:
            return "Sell"
    return "N/A"


def fridays_from(start_on_or_after: date, count: int) -> List[date]:
    days_until_friday = (4 - start_on_or_after.weekday()) % 7
    first = start_on_or_after + timedelta(days=days_until_friday)
    return [first + timedelta(days=7 * i) for i in range(count)]


def display_expiration(expiration_date: date) -> str:
    return f"{expiration_date.month}/{expiration_date.day}"


def paged_option_chain(
    symbol: str,
    expiration_date: date,
    option_type: str,
    api_key: str,
    api_secret: str,
) -> List[Dict]:
    rows: List[Dict] = []
    page_token: Optional[str] = None
    while True:
        params = {
            "expiration_date": expiration_date.isoformat(),
            "type": option_type,
            "feed": "indicative",
            "limit": "1000",
        }
        if page_token:
            params["page_token"] = page_token
        payload = api_get(
            f"/v1beta1/options/snapshots/{symbol}",
            api_key,
            api_secret,
            params,
        )
        snapshots = payload.get("snapshots", {})
        for contract_symbol, snapshot in snapshots.items():
            enriched = dict(snapshot)
            enriched["contract_symbol"] = contract_symbol
            rows.append(enriched)
        page_token = payload.get("next_page_token")
        if not page_token:
            break
    return rows


def strike_from_contract_symbol(contract_symbol: str) -> Optional[float]:
    if len(contract_symbol) < 8:
        return None
    strike_digits = contract_symbol[-8:]
    if not strike_digits.isdigit():
        return None
    return int(strike_digits) / 1000.0


def choose_option_contract(contracts: Iterable[Dict], price: float, target_strike: float, option_type: str) -> Tuple[float, Optional[float]]:
    parsed = []
    for snapshot in contracts:
        trade = snapshot.get("latestTrade") or {}
        greeks = snapshot.get("greeks") or {}
        contract = snapshot.get("option_contract") or snapshot.get("contract") or {}
        strike_value = contract.get("strike_price")
        strike = float(strike_value) if strike_value is not None else strike_from_contract_symbol(snapshot.get("contract_symbol", ""))
        if strike is None:
            continue
        if option_type == "call" and strike < price:
            continue
        if option_type == "put" and strike > price:
            continue
        last = trade.get("p")
        parsed.append(
            {
                "strike": strike,
                "last": float(last) if last is not None else None,
                "delta_target_distance": abs(strike - target_strike),
                "has_last": last is not None,
                "open_interest": float(contract.get("open_interest") or 0),
                "delta": abs(float(greeks.get("delta"))) if greeks.get("delta") is not None else math.inf,
            }
        )

    if not parsed:
        raise RuntimeError(f"No OTM {option_type} contracts available")

    parsed.sort(
        key=lambda item: (
            item["delta_target_distance"],
            0 if item["has_last"] else 1,
            -item["open_interest"],
            item["delta"],
        )
    )
    best = parsed[0]
    return best["strike"], best["last"]


def format_money(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.2f}"


def format_premium_range(low: Optional[float], high: Optional[float], mid: Optional[float]) -> str:
    if low is not None and high is not None:
        return f"{format_money(low)} - {format_money(high)}"
    if mid is not None:
        return format_money(mid)
    return "N/A"


def action_for_premium(last_price: Optional[float], stock_price: float) -> str:
    current_roi = roi_pct(last_price, stock_price)
    if current_roi is None:
        return "Wait"
    return "Sell" if current_roi >= 1.0 else "Wait"


def roi_pct(last_price: Optional[float], stock_price: float) -> Optional[float]:
    if last_price is None or stock_price <= 0:
        return None
    return (last_price / stock_price) * 100.0


def option_row_to_dict(row: OptionRow) -> Dict[str, object]:
    return {
        "ticker": row.stock,
        "price": row.price,
        "priceText": format_money(row.price),
        "trend": row.trend,
        "avgWeeklyMovePct": row.pct_otm * 100.0,
        "avgWeeklyMovePctText": f"{row.pct_otm * 100:.2f}%",
        "strike": row.strike,
        "strikeText": format_money(row.strike),
        "premium": row.last_price * 100.0 if row.last_price is not None else None,
        "premiumText": format_money(row.last_price * 100.0) if row.last_price is not None else "N/A",
        "roiPct": roi_pct(row.last_price, row.price),
        "roiPctText": f"{roi_pct(row.last_price, row.price):.2f}%" if roi_pct(row.last_price, row.price) is not None else "N/A",
        "action": row.action,
    }


def option_snapshot_price(snapshot: Dict) -> Optional[float]:
    trade = snapshot.get("latestTrade") or {}
    last = trade.get("p")
    if last is not None:
        return float(last)

    quote = snapshot.get("latestQuote") or {}
    bid = quote.get("bp")
    ask = quote.get("ap")
    if bid is not None and ask is not None and float(bid) > 0 and float(ask) > 0:
        return (float(bid) + float(ask)) / 2.0
    if bid is not None and float(bid) > 0:
        return float(bid)
    if ask is not None and float(ask) > 0:
        return float(ask)
    return None


def option_snapshot_strike(snapshot: Dict) -> Optional[float]:
    contract = snapshot.get("option_contract") or snapshot.get("contract") or {}
    strike_value = contract.get("strike_price")
    if strike_value is not None:
        return float(strike_value)
    return strike_from_contract_symbol(snapshot.get("contract_symbol", ""))


def option_snapshot_delta(snapshot: Dict) -> Optional[float]:
    greeks = snapshot.get("greeks") or {}
    delta = greeks.get("delta")
    return float(delta) if delta is not None else None


def option_snapshot_open_interest(snapshot: Dict) -> float:
    contract = snapshot.get("option_contract") or snapshot.get("contract") or {}
    return float(contract.get("open_interest") or 0.0)


def option_snapshot_spread(snapshot: Dict) -> float:
    quote = snapshot.get("latestQuote") or {}
    bid = quote.get("bp")
    ask = quote.get("ap")
    if bid is None or ask is None:
        return math.inf
    return max(0.0, float(ask) - float(bid))


def nearest_weekly_expiration(today: date, expiration_override: Optional[date]) -> date:
    return expiration_override or fridays_from(today, 1)[0]


def leap_expiration_candidates(today: date) -> List[date]:
    candidates: List[date] = []
    for year_offset in (1, 2, 3):
        year = today.year + year_offset
        for month in (1, 6, 12):
            candidates.extend(fridays_from(date(year, month, 15), 1))
    sorted_candidates = sorted({item for item in candidates if item >= today + timedelta(days=500)})
    practical_window = [item for item in sorted_candidates if (item - today).days <= 760]
    first_longer = next((item for item in sorted_candidates if (item - today).days > 760), None)
    if first_longer:
        practical_window.append(first_longer)
    return practical_window


def choose_leaps_contract(symbol: str, price: float, today: date, api_key: str, api_secret: str) -> Tuple[date, Dict]:
    parsed: List[Tuple[date, Dict[str, object]]] = []
    for expiration_date in leap_expiration_candidates(today):
        try:
            contracts = paged_option_chain(symbol, expiration_date, "call", api_key, api_secret)
        except Exception:
            continue
        best_for_expiration: Optional[Dict[str, object]] = None
        for snapshot in contracts:
            strike = option_snapshot_strike(snapshot)
            delta = option_snapshot_delta(snapshot)
            option_price = option_snapshot_price(snapshot)
            if strike is None or delta is None or option_price is None:
                continue
            abs_delta = abs(delta)
            if not 0.75 <= abs_delta <= 0.95:
                continue
            item = {
                "snapshot": snapshot,
                "strike": strike,
                "delta": abs_delta,
                "price": option_price,
                "delta_distance": abs(abs_delta - 0.85),
                "open_interest": option_snapshot_open_interest(snapshot),
                "spread": option_snapshot_spread(snapshot),
            }
            if best_for_expiration is None or (
                item["delta_distance"],
                item["spread"],
                -item["open_interest"],
            ) < (
                best_for_expiration["delta_distance"],
                best_for_expiration["spread"],
                -best_for_expiration["open_interest"],
            ):
                best_for_expiration = item
        if best_for_expiration:
            parsed.append((expiration_date, best_for_expiration))

    if not parsed:
        raise RuntimeError(f"No usable 0.85-delta LEAPS found for {symbol}")

    parsed.sort(key=lambda item: item[0])
    best_expiration, best = parsed[0]
    for expiration_date, candidate in parsed[1:]:
        extra_cost_pct = (candidate["price"] - best["price"]) / best["price"] if best["price"] > 0 else math.inf
        extra_time_pct = ((expiration_date - today).days - (best_expiration - today).days) / max((best_expiration - today).days, 1)
        if extra_time_pct > 0 and extra_cost_pct <= extra_time_pct * 0.75:
            best_expiration, best = expiration_date, candidate

    return best_expiration, best["snapshot"]


def choose_pmcc_short_call(
    symbol: str,
    price: float,
    pct_otm: float,
    expiration_date: date,
    api_key: str,
    api_secret: str,
) -> Dict:
    min_strike = price * (1 + pct_otm)
    contracts = paged_option_chain(symbol, expiration_date, "call", api_key, api_secret)
    parsed = []
    for snapshot in contracts:
        strike = option_snapshot_strike(snapshot)
        if strike is None or strike < min_strike:
            continue
        delta = option_snapshot_delta(snapshot)
        option_price = option_snapshot_price(snapshot)
        abs_delta = abs(delta) if delta is not None else math.inf
        if abs_delta < 0.05 or abs_delta > 0.30:
            continue
        parsed.append(
            {
                "snapshot": snapshot,
                "strike": strike,
                "delta": abs_delta if math.isfinite(abs_delta) else None,
                "price": option_price,
                "has_price": option_price is not None,
                "delta_distance": abs(abs_delta - 0.20) if math.isfinite(abs_delta) else math.inf,
                "strike_distance": abs(strike - min_strike),
                "open_interest": option_snapshot_open_interest(snapshot),
                "spread": option_snapshot_spread(snapshot),
            }
        )

    if not parsed:
        raise RuntimeError(f"No PMCC weekly short call found for {symbol}")

    parsed.sort(
        key=lambda item: (
            0 if item["has_price"] else 1,
            item["delta_distance"],
            item["strike_distance"],
            item["spread"],
            -item["open_interest"],
        )
    )
    return parsed[0]["snapshot"]


def pmcc_score(price: float, pct_otm: float, below_52w_high_pct: float, short_call_price: Optional[float]) -> int:
    score = 0
    if 75 <= price <= 500:
        score += 10
    elif price >= 50:
        score += 5

    weekly_move_pct = pct_otm * 100.0
    if 2 <= weekly_move_pct <= 5:
        score += 20
    elif 5 < weekly_move_pct <= 8:
        score += 12
    elif 0 < weekly_move_pct < 2:
        score += 8

    if -30 <= below_52w_high_pct <= -10:
        score += 20
    elif -40 <= below_52w_high_pct < -30:
        score += 10
    elif -10 < below_52w_high_pct <= 0:
        score += 8

    score += 15
    score += 15
    score += 10
    if short_call_price is not None and short_call_price > 0:
        score += 10
    return min(score, 100)


def pmcc_row_to_dict(row: PmccRow) -> Dict[str, object]:
    return {
        "ticker": row.stock,
        "price": row.price,
        "priceText": format_money(row.price),
        "below52wHighPct": row.below_52w_high_pct,
        "below52wHighPctText": f"{row.below_52w_high_pct:.2f}%",
        "avgWeeklyMovePct": row.pct_otm * 100.0,
        "avgWeeklyMovePctText": f"{row.pct_otm * 100:.2f}%",
        "leapsExpiration": row.leaps_expiration.isoformat(),
        "leapsExpirationText": display_expiration(row.leaps_expiration),
        "leapsStrike": row.leaps_strike,
        "leapsStrikeText": format_money(row.leaps_strike),
        "leapsDelta": row.leaps_delta,
        "leapsDeltaText": f"{row.leaps_delta:.2f}",
        "leapsCost": row.leaps_price * 100.0,
        "leapsCostText": format_money(row.leaps_price * 100.0),
        "weeklyCallExpiration": row.short_call_expiration.isoformat(),
        "weeklyCallExpirationText": display_expiration(row.short_call_expiration),
        "weeklyCallStrike": row.short_call_strike,
        "weeklyCallStrikeText": format_money(row.short_call_strike),
        "weeklyCallDelta": row.short_call_delta,
        "weeklyCallDeltaText": f"{row.short_call_delta:.2f}" if row.short_call_delta is not None else "N/A",
        "weeklyCallPremium": row.short_call_price * 100.0 if row.short_call_price is not None else None,
        "weeklyCallPremiumText": format_money(row.short_call_price * 100.0) if row.short_call_price is not None else "N/A",
        "score": row.score,
        "scoreText": f"{row.score}",
    }


def excluded_row_to_dict(row: ExcludedTickerRow) -> Dict[str, object]:
    return {
        "ticker": row.stock,
        "price": row.price,
        "priceText": format_money(row.price) if row.price is not None else "N/A",
        "earningsDate": row.earnings_date.isoformat() if row.earnings_date else None,
        "earningsDateText": f"{row.earnings_date.month}/{row.earnings_date.day}" if row.earnings_date else "N/A",
        "action": row.options_label,
        "premium": row.premium,
        "premiumText": format_money(row.premium) if row.premium is not None else "N/A",
        "roiPct": row.roi,
        "roiPctText": f"{row.roi:.2f}%" if row.roi is not None else "N/A",
    }


def render_markdown_table(headers: List[str], rows: List[List[str]], aligns: List[str]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def pad_cell(text: str, width: int, align: str) -> str:
        if align == "right":
            return text.rjust(width)
        if align == "center":
            return text.center(width)
        return text.ljust(width)

    header_line = "| " + " | ".join(
        pad_cell(header, widths[index], "left") for index, header in enumerate(headers)
    ) + " |"

    separator_cells = []
    for index, align in enumerate(aligns):
        width = max(widths[index], 3)
        if align == "right":
            separator_cells.append("-" * (width - 1) + ":")
        elif align == "center":
            separator_cells.append(":" + "-" * (width - 2) + ":")
        else:
            separator_cells.append(":" + "-" * (width - 1) if False else "-" * width)
    separator_line = "| " + " | ".join(separator_cells) + " |"

    body_lines = []
    for row in rows:
        body_lines.append(
            "| " + " | ".join(
                pad_cell(cell, widths[index], aligns[index]) for index, cell in enumerate(row)
            ) + " |"
        )

    return "\n".join([header_line, separator_line, *body_lines])


def build_row(
    symbol: str,
    price: float,
    trend: str,
    pct_otm: float,
    option_type: str,
    api_key: str,
    api_secret: str,
    expiration_override: Optional[date] = None,
) -> Tuple[OptionRow, date]:
    target_strike = price * (1 + pct_otm) if option_type == "call" else price * (1 - pct_otm)

    contracts = []
    expiration_used = expiration_override
    fridays_to_check = [expiration_override] if expiration_override else fridays_from(date.today(), 8)
    for friday in fridays_to_check:
        found = paged_option_chain(symbol, friday, option_type, api_key, api_secret)
        if found:
            contracts = found
            expiration_used = friday
            break

    if not contracts or expiration_used is None:
        if expiration_override:
            raise RuntimeError(f"No Friday {option_type} contracts found for {symbol} on {expiration_override.isoformat()}")
        raise RuntimeError(f"No Friday {option_type} contracts found for {symbol} in the next 8 weeks")

    strike, last_price = choose_option_contract(
        contracts=contracts,
        price=price,
        target_strike=target_strike,
        option_type=option_type,
    )
    return OptionRow(
        stock=symbol,
        price=price,
        trend=trend,
        pct_otm=pct_otm,
        strike=strike,
        last_price=last_price,
        action=action_for_premium(last_price, price),
    ), expiration_used


def render_table(title: str, rows: List[OptionRow], expiration_label: str) -> str:
    sell_rows = [row for row in rows if row.action == "Sell"]
    sorted_rows = sorted(
        sell_rows,
        key=lambda row: roi_pct(row.last_price, row.price) if roi_pct(row.last_price, row.price) is not None else -1.0,
        reverse=True,
    )
    lines = [f"## {title} - Expiration {expiration_label}", ""]
    if not sorted_rows:
        table_rows = [["None", "N/A", "N/A", "N/A", "N/A", "N/A", "N/A"]]
        lines.append("")
    else:
        table_rows = []
        for row in sorted_rows:
            table_rows.append([
                row.stock,
                format_money(row.price),
                row.trend,
                f"{row.pct_otm * 100:.2f}%",
                format_money(row.strike),
                format_money(row.last_price * 100.0) if row.last_price is not None else "N/A",
                f"{roi_pct(row.last_price, row.price):.2f}%" if roi_pct(row.last_price, row.price) is not None else "N/A",
            ])
    lines.append(
        render_markdown_table(
            ["Ticker", "Price", "Trend", "Avg Weekly Move %", "OTM Strike", "Premium", "ROI %"],
            table_rows,
            ["left", "right", "left", "right", "right", "right", "right"],
        )
    )
    lines.append("")
    return "\n".join(lines)


def render_pmcc_table(rows: List[PmccRow]) -> str:
    lines = ["## PMCC Candidates", ""]
    sorted_rows = sorted(rows, key=lambda row: row.score, reverse=True)
    if not sorted_rows:
        table_rows = [["None", "N/A", "N/A", "N/A", "N/A", "N/A", "N/A", "N/A", "N/A", "N/A", "N/A"]]
    else:
        table_rows = []
        for row in sorted_rows:
            table_rows.append([
                row.stock,
                format_money(row.price),
                f"{row.below_52w_high_pct:.2f}%",
                f"{row.pct_otm * 100:.2f}%",
                display_expiration(row.leaps_expiration),
                format_money(row.leaps_strike),
                f"{row.leaps_delta:.2f}",
                format_money(row.leaps_price * 100.0),
                format_money(row.short_call_strike),
                format_money(row.short_call_price * 100.0) if row.short_call_price is not None else "N/A",
                str(row.score),
            ])
    lines.append(
        render_markdown_table(
            [
                "Ticker",
                "Price",
                "Below 52W High",
                "Avg Weekly Move %",
                "LEAPS Exp",
                "LEAPS Strike",
                "LEAPS Delta",
                "LEAPS Cost",
                "Weekly Call Strike",
                "Weekly Call Premium",
                "Score",
            ],
            table_rows,
            ["left", "right", "right", "right", "left", "right", "right", "right", "right", "right", "right"],
        )
    )
    lines.append("")
    return "\n".join(lines)


def render_summary_card(label: str, value: str) -> str:
    return (
        '<div style="background:#f7f3eb;border:1px solid #e7dcc7;border-radius:14px;'
        'padding:14px 16px;min-width:160px;">'
        f'<div style="font-size:11px;letter-spacing:0.08em;text-transform:uppercase;color:#7b6a4b;">{escape(label)}</div>'
        f'<div style="margin-top:6px;font-size:22px;font-weight:700;color:#1f2a37;">{escape(value)}</div>'
        "</div>"
    )


def render_html_table(title: str, rows: List[OptionRow], expiration_label: str) -> str:
    sell_rows = [row for row in rows if row.action == "Sell"]
    sorted_rows = sorted(
        sell_rows,
        key=lambda row: roi_pct(row.last_price, row.price) if roi_pct(row.last_price, row.price) is not None else -1.0,
        reverse=True,
    )
    table_rows = []
    if not sorted_rows:
        table_rows.append(
            "<tr>"
            '<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;color:#6b7280;">None</td>'
            '<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;text-align:right;color:#6b7280;" colspan="6">No sell candidates</td>'
            "</tr>"
        )
    else:
        for row in sorted_rows:
            premium = format_money(row.last_price * 100.0) if row.last_price is not None else "N/A"
            roi = f"{roi_pct(row.last_price, row.price):.2f}%" if roi_pct(row.last_price, row.price) is not None else "N/A"
            table_rows.append(
                "<tr>"
                f'<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;font-weight:700;color:#111827;">{escape(row.stock)}</td>'
                f'<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;text-align:right;color:#111827;">{escape(format_money(row.price))}</td>'
                f'<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;text-align:right;color:#111827;">{row.pct_otm * 100:.2f}%</td>'
                f'<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;text-align:right;font-weight:700;color:#0f766e;">{escape(format_money(row.strike))}</td>'
                f'<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;text-align:right;color:#111827;">{escape(premium)}</td>'
                f'<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;text-align:right;color:#111827;">{escape(roi)}</td>'
                '<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;text-align:center;">'
                '<span style="display:inline-block;background:#dcfce7;color:#166534;font-weight:700;'
                'padding:4px 10px;border-radius:999px;font-size:12px;">Sell</span>'
                "</td>"
                "</tr>"
            )

    return (
        '<section style="margin-top:28px;">'
        f'<div style="display:flex;justify-content:space-between;align-items:end;gap:12px;margin-bottom:10px;">'
        f'<h2 style="margin:0;font-size:22px;color:#111827;">{escape(title)}</h2>'
        f'<div style="font-size:13px;color:#6b7280;">Expiration {escape(expiration_label)}</div>'
        "</div>"
        '<div style="border:1px solid #e5e7eb;border-radius:16px;overflow:hidden;background:#ffffff;">'
        '<table style="width:100%;border-collapse:collapse;font-family:Arial,Helvetica,sans-serif;">'
        "<thead>"
        '<tr style="background:#111827;color:#f9fafb;">'
        '<th style="padding:12px 14px;text-align:left;font-size:12px;letter-spacing:0.04em;">Ticker</th>'
        '<th style="padding:12px 14px;text-align:right;font-size:12px;letter-spacing:0.04em;">Price</th>'
        '<th style="padding:12px 14px;text-align:right;font-size:12px;letter-spacing:0.04em;">Avg Weekly Move %</th>'
        '<th style="padding:12px 14px;text-align:right;font-size:12px;letter-spacing:0.04em;">OTM Strike</th>'
        '<th style="padding:12px 14px;text-align:right;font-size:12px;letter-spacing:0.04em;">Premium</th>'
        '<th style="padding:12px 14px;text-align:right;font-size:12px;letter-spacing:0.04em;">ROI %</th>'
        '<th style="padding:12px 14px;text-align:center;font-size:12px;letter-spacing:0.04em;">Action</th>'
        "</tr>"
        "</thead>"
        f"<tbody>{''.join(table_rows)}</tbody>"
        "</table>"
        "</div>"
        "</section>"
    )


def render_pmcc_html_table(rows: List[PmccRow]) -> str:
    table_rows = []
    sorted_rows = sorted(rows, key=lambda row: row.score, reverse=True)
    if not sorted_rows:
        table_rows.append(
            "<tr>"
            '<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;color:#6b7280;">None</td>'
            '<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;text-align:right;color:#6b7280;" colspan="10">No PMCC candidates</td>'
            "</tr>"
        )
    else:
        for row in sorted_rows:
            short_premium = format_money(row.short_call_price * 100.0) if row.short_call_price is not None else "N/A"
            table_rows.append(
                "<tr>"
                f'<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;font-weight:700;color:#111827;">{escape(row.stock)}</td>'
                f'<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;text-align:right;color:#111827;">{escape(format_money(row.price))}</td>'
                f'<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;text-align:right;color:#111827;">{row.below_52w_high_pct:.2f}%</td>'
                f'<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;text-align:right;color:#111827;">{row.pct_otm * 100:.2f}%</td>'
                f'<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;text-align:right;color:#111827;">{escape(display_expiration(row.leaps_expiration))}</td>'
                f'<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;text-align:right;font-weight:700;color:#0f766e;">{escape(format_money(row.leaps_strike))}</td>'
                f'<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;text-align:right;color:#111827;">{row.leaps_delta:.2f}</td>'
                f'<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;text-align:right;color:#111827;">{escape(format_money(row.leaps_price * 100.0))}</td>'
                f'<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;text-align:right;font-weight:700;color:#0f766e;">{escape(format_money(row.short_call_strike))}</td>'
                f'<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;text-align:right;color:#111827;">{escape(short_premium)}</td>'
                f'<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;text-align:right;font-weight:700;color:#111827;">{row.score}</td>'
                "</tr>"
            )
    return (
        '<section style="margin-top:28px;">'
        '<div style="display:flex;justify-content:space-between;align-items:end;gap:12px;margin-bottom:10px;">'
        '<h2 style="margin:0;font-size:22px;color:#111827;">PMCC Candidates</h2>'
        '<div style="font-size:13px;color:#6b7280;">Weekly short call outside avg weekly move</div>'
        "</div>"
        '<div style="border:1px solid #e5e7eb;border-radius:16px;overflow:hidden;background:#ffffff;">'
        '<table style="width:100%;border-collapse:collapse;font-family:Arial,Helvetica,sans-serif;">'
        "<thead>"
        '<tr style="background:#111827;color:#f9fafb;">'
        '<th style="padding:12px 14px;text-align:left;font-size:12px;letter-spacing:0.04em;">Ticker</th>'
        '<th style="padding:12px 14px;text-align:right;font-size:12px;letter-spacing:0.04em;">Price</th>'
        '<th style="padding:12px 14px;text-align:right;font-size:12px;letter-spacing:0.04em;">Below 52W High</th>'
        '<th style="padding:12px 14px;text-align:right;font-size:12px;letter-spacing:0.04em;">Avg Weekly Move %</th>'
        '<th style="padding:12px 14px;text-align:right;font-size:12px;letter-spacing:0.04em;">LEAPS Exp</th>'
        '<th style="padding:12px 14px;text-align:right;font-size:12px;letter-spacing:0.04em;">LEAPS Strike</th>'
        '<th style="padding:12px 14px;text-align:right;font-size:12px;letter-spacing:0.04em;">LEAPS Delta</th>'
        '<th style="padding:12px 14px;text-align:right;font-size:12px;letter-spacing:0.04em;">LEAPS Cost</th>'
        '<th style="padding:12px 14px;text-align:right;font-size:12px;letter-spacing:0.04em;">Weekly Call Strike</th>'
        '<th style="padding:12px 14px;text-align:right;font-size:12px;letter-spacing:0.04em;">Weekly Call Premium</th>'
        '<th style="padding:12px 14px;text-align:right;font-size:12px;letter-spacing:0.04em;">Score</th>'
        "</tr>"
        "</thead>"
        f"<tbody>{''.join(table_rows)}</tbody>"
        "</table>"
        "</div>"
        "</section>"
    )


def load_my_portfolio_tickers() -> List[str]:
    if not os.path.exists(MY_PORTFOLIO_REPORT_FILE):
        return []
    with open(MY_PORTFOLIO_REPORT_FILE, "r", encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]


def render_portfolio_table(rows: List[OptionRow], expiration_label: str) -> str:
    lines = [f"## My Portfolio Report - Expiration {expiration_label}", ""]
    total_premium = 0.0
    table_rows = []
    for row in rows:
        premium_value = row.last_price * 100.0 if row.last_price is not None else 0.0
        total_premium += premium_value
        table_rows.append([
            row.stock,
            format_money(row.price),
            f"{row.pct_otm * 100:.2f}%",
            format_money(row.strike),
            format_money(premium_value) if row.last_price is not None else "N/A",
        ])
    table_rows.append(["**Total**", "", "", "", f"**{format_money(total_premium)}**"])
    lines.append(
        render_markdown_table(
            ["Ticker", "Price", "Avg Weekly Move %", "Covered Call Strike", "Premium"],
            table_rows,
            ["left", "right", "right", "right", "right"],
        )
    )
    lines.append("")
    return "\n".join(lines)


def render_portfolio_html_table(rows: List[OptionRow], expiration_label: str) -> str:
    total_premium = 0.0
    table_rows = []
    for row in rows:
        premium_value = row.last_price * 100.0 if row.last_price is not None else 0.0
        total_premium += premium_value
        table_rows.append(
            "<tr>"
            f'<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;font-weight:700;color:#111827;">{escape(row.stock)}</td>'
            f'<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;text-align:right;color:#111827;">{escape(format_money(row.price))}</td>'
            f'<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;text-align:right;color:#111827;">{row.pct_otm * 100:.2f}%</td>'
            f'<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;text-align:right;font-weight:700;color:#0f766e;">{escape(format_money(row.strike))}</td>'
            f'<td style="padding:12px 14px;border-bottom:1px solid #e5e7eb;text-align:right;color:#111827;">{escape(format_money(premium_value)) if row.last_price is not None else "N/A"}</td>'
            "</tr>"
        )
    table_rows.append(
        "<tr>"
        '<td style="padding:12px 14px;font-weight:700;color:#111827;">Total</td>'
        '<td style="padding:12px 14px;"></td>'
        '<td style="padding:12px 14px;"></td>'
        '<td style="padding:12px 14px;"></td>'
        f'<td style="padding:12px 14px;text-align:right;font-weight:700;color:#111827;">{escape(format_money(total_premium))}</td>'
        "</tr>"
    )
    return (
        '<section style="margin-top:28px;">'
        f'<div style="display:flex;justify-content:space-between;align-items:end;gap:12px;margin-bottom:10px;">'
        '<h2 style="margin:0;font-size:22px;color:#111827;">My Portfolio Report</h2>'
        f'<div style="font-size:13px;color:#6b7280;">Expiration {escape(expiration_label)}</div>'
        "</div>"
        '<div style="border:1px solid #e5e7eb;border-radius:16px;overflow:hidden;background:#ffffff;">'
        '<table style="width:100%;border-collapse:collapse;font-family:Arial,Helvetica,sans-serif;">'
        "<thead>"
        '<tr style="background:#0f766e;color:#f9fafb;">'
        '<th style="padding:12px 14px;text-align:left;font-size:12px;letter-spacing:0.04em;">Ticker</th>'
        '<th style="padding:12px 14px;text-align:right;font-size:12px;letter-spacing:0.04em;">Price</th>'
        '<th style="padding:12px 14px;text-align:right;font-size:12px;letter-spacing:0.04em;">Avg Weekly Move %</th>'
        '<th style="padding:12px 14px;text-align:right;font-size:12px;letter-spacing:0.04em;">Covered Call Strike</th>'
        '<th style="padding:12px 14px;text-align:right;font-size:12px;letter-spacing:0.04em;">Premium Collected</th>'
        "</tr>"
        "</thead>"
        f"<tbody>{''.join(table_rows)}</tbody>"
        "</table>"
        "</div>"
        "</section>"
    )


def render_excluded_table(rows: List[ExcludedTickerRow]) -> str:
    filtered_rows = [row for row in rows if row.roi is not None and row.roi >= 2.0]
    lines = ["## Earnings this Week", ""]
    if not filtered_rows:
        table_rows = [["None", "N/A", "N/A", "N/A", "N/A", "N/A"]]
        lines.append("")
    else:
        sorted_rows = sorted(
            filtered_rows,
            key=lambda row: (
                row.roi if row.roi is not None else -1.0,
                row.stock,
            ),
            reverse=True,
        )
        table_rows = []
        for row in sorted_rows:
            earnings_text = f"{row.earnings_date.month}/{row.earnings_date.day}" if row.earnings_date else "N/A"
            premium_text = format_money(row.premium) if row.premium is not None else "N/A"
            roi_text = f"{row.roi:.2f}%" if row.roi is not None else "N/A"
            table_rows.append([
                row.stock,
                format_money(row.price) if row.price is not None else "N/A",
                earnings_text,
                row.options_label,
                premium_text,
                roi_text,
            ])
    lines.append(
        render_markdown_table(
            ["Ticker", "Price", "Earnings Date", "Action", "Premium", "ROI %"],
            table_rows,
            ["left", "right", "left", "left", "right", "right"],
        )
    )
    lines.append("")
    return "\n".join(lines)


def sorted_sell_rows(rows: List[OptionRow]) -> List[OptionRow]:
    return sorted(
        [row for row in rows if row.action == "Sell"],
        key=lambda row: roi_pct(row.last_price, row.price) if roi_pct(row.last_price, row.price) is not None else -1.0,
        reverse=True,
    )


def recommendation_score(row: OptionRow) -> float:
    roi = roi_pct(row.last_price, row.price)
    if roi is None:
        return math.inf
    return abs(roi - 1.5) + (row.pct_otm * 100.0) / 25.0


def format_recommendation_line(label: str, row: OptionRow) -> str:
    roi_text = f"{roi_pct(row.last_price, row.price):.2f}%" if roi_pct(row.last_price, row.price) is not None else "N/A"
    premium_text = format_money(row.last_price * 100.0) if row.last_price is not None else "N/A"
    return (
        f"- {label}: `{row.stock}` | price `{format_money(row.price)}` | avg weekly move `{row.pct_otm * 100:.2f}%` | "
        f"OTM strike `{format_money(row.strike)}` | premium `{premium_text}` | ROI `{roi_text}`"
    )


def build_recommendation_groups(
    covered_calls: List[OptionRow], cash_secured_puts: List[OptionRow]
) -> Tuple[List[RecommendationRow], List[RecommendationRow]]:
    sell_covereds = sorted_sell_rows(covered_calls)
    sell_puts = sorted_sell_rows(cash_secured_puts)

    candidates: List[RecommendationRow] = [RecommendationRow("Covered Call", row) for row in sell_covereds] + [
        RecommendationRow("Cash Secured Put", row) for row in sell_puts
    ]
    best_balance = sorted(candidates, key=lambda item: recommendation_score(item.row))[:3]

    aggressive_pool = sorted(
        candidates,
        key=lambda item: roi_pct(item.row.last_price, item.row.price) if roi_pct(item.row.last_price, item.row.price) is not None else -1.0,
        reverse=True,
    )
    aggressive: List[RecommendationRow] = []
    used = {(item.label, item.row.stock) for item in best_balance}
    for item in aggressive_pool:
        key = (item.label, item.row.stock)
        if key in used:
            continue
        aggressive.append(item)
        if len(aggressive) == 3:
            break
    return best_balance, aggressive


def recommendation_row_to_dict(item: RecommendationRow) -> Dict[str, object]:
    payload = option_row_to_dict(item.row)
    payload["label"] = item.label
    return payload


def render_recommendations(covered_calls: List[OptionRow], cash_secured_puts: List[OptionRow]) -> str:
    best_balance, aggressive = build_recommendation_groups(covered_calls, cash_secured_puts)

    lines = ["## Team Review", ""]
    lines.append("**Best Balance**")
    if best_balance:
        for item in best_balance:
            lines.append(format_recommendation_line(item.label, item.row))
        lines.append("")
        lines.append(
            "Why: these are the strongest remaining candidates after the earnings-week exclusions, biased toward moderate ROI rather than the most aggressive premium."
        )
    else:
        lines.append("- None today.")
        lines.append("")
        lines.append("Why: no filtered candidates remain above the current threshold.")

    lines.extend(["", "**Aggressive Premium**"])
    if aggressive:
        for item in aggressive:
            lines.append(format_recommendation_line(item.label, item.row))
    else:
        lines.append("- None beyond the best-balance group.")

    lines.append("")
    return "\n".join(lines)


def build_report_html(
    report_title: str,
    generated_at: str,
    expiration_text: str,
    included_count: int,
    requested_count: int,
    portfolio_rows: List[OptionRow],
    portfolio_expiration_label: str,
    covered_calls: List[OptionRow],
    covered_call_label: str,
    cash_secured_puts: List[OptionRow],
    cash_secured_put_label: str,
    pmcc_rows: List[PmccRow],
    skipped: List[str],
) -> str:
    covered_count = sum(1 for row in covered_calls if row.action == "Sell")
    put_count = sum(1 for row in cash_secured_puts if row.action == "Sell")
    skipped_items = "".join(
        f'<li style="margin:0 0 6px 0;color:#4b5563;">{escape(item)}</li>' for item in skipped
    )
    skipped_section = (
        '<section style="margin-top:28px;">'
        '<h2 style="margin:0 0 10px 0;font-size:20px;color:#111827;">Excluded Tickers</h2>'
        '<div style="border:1px solid #e5e7eb;border-radius:16px;padding:16px;background:#ffffff;">'
        f'<ul style="margin:0;padding-left:18px;">{skipped_items}</ul>'
        "</div>"
        "</section>"
    ) if skipped else ""

    return (
        "<!DOCTYPE html>"
        "<html><body style=\"margin:0;padding:24px;background:#f3f0ea;font-family:Arial,Helvetica,sans-serif;color:#111827;\">"
        '<div style="max-width:1120px;margin:0 auto;background:linear-gradient(180deg,#fffdfa 0%,#ffffff 100%);'
        'border:1px solid #e7dcc7;border-radius:24px;overflow:hidden;">'
        '<div style="padding:28px 32px;background:linear-gradient(135deg,#0f172a 0%,#1f2937 55%,#134e4a 100%);color:#f9fafb;">'
        '<div style="font-size:12px;letter-spacing:0.16em;text-transform:uppercase;color:#d1d5db;">Executive Options Brief</div>'
        f'<h1 style="margin:10px 0 0 0;font-size:34px;line-height:1.1;">{escape(report_title)}</h1>'
        f'<div style="margin-top:10px;font-size:14px;color:#d1d5db;">Generated {escape(generated_at)} | {escape(expiration_text)}</div>'
        "</div>"
        '<div style="padding:24px 32px 32px 32px;">'
        '<div style="display:flex;flex-wrap:wrap;gap:12px;margin-bottom:12px;">'
        f'{render_summary_card("Universe Included", str(included_count))}'
        f'{render_summary_card("Tickers Requested", str(requested_count))}'
        f'{render_summary_card("Covered Call Sells", str(covered_count))}'
        f'{render_summary_card("Cash-Secured Put Sells", str(put_count))}'
        "</div>"
        f'{render_portfolio_html_table(portfolio_rows, portfolio_expiration_label)}'
        f'{render_html_table("Covered Calls", covered_calls, covered_call_label)}'
        f'{render_html_table("Cash Secured Puts", cash_secured_puts, cash_secured_put_label)}'
        f'{render_pmcc_html_table(pmcc_rows)}'
        f"{skipped_section}"
        "</div>"
        "</div>"
        "</body></html>"
    )


def save_report_history(history_path: str, snapshot: Dict[str, object]) -> None:
    existing: List[Dict[str, object]] = []
    if os.path.exists(history_path):
        try:
            with open(history_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, list):
                existing = [item for item in payload if isinstance(item, dict)]
        except Exception:
            existing = []

    report_key = str(snapshot.get("reportDate"))
    expiration_key = str(snapshot.get("expiration"))
    filtered = [
        item for item in existing
        if not (str(item.get("reportDate")) == report_key and str(item.get("expiration")) == expiration_key)
    ]
    filtered.insert(0, snapshot)
    filtered.sort(
        key=lambda item: (
            str(item.get("reportDateIso") or ""),
            str(item.get("generatedAt") or ""),
            str(item.get("expiration") or ""),
        ),
        reverse=True,
    )

    with open(history_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(filtered, handle, indent=2)


def build_report(
    api_key: str,
    api_secret: str,
    symbols: List[str],
    report_title: str,
    expiration_override: Optional[date] = None,
    batch_size: int = 0,
    batch_pause_seconds: float = 0.0,
    enforce_min_price_filter: bool = False,
) -> Tuple[str, str, Dict[str, object]]:
    today = date.today()
    start = today - timedelta(days=400)
    report_expiration = expiration_override or fridays_from(today, 1)[0]
    report_start = monday_of_week(report_expiration)
    latest_prices = get_latest_prices(symbols, api_key, api_secret)
    earnings_cache = load_earnings_cache()
    earnings_cache_lock = threading.Lock()

    pct_otm_by_symbol = {}
    high_52w_by_symbol: Dict[str, float] = {}
    trend_by_symbol: Dict[str, str] = {}
    skipped: List[str] = []
    excluded_rows: List[ExcludedTickerRow] = []
    active_symbols: List[str] = []

    def batched_symbols(items: List[str]) -> List[List[str]]:
        if batch_size <= 0:
            return [items]
        return [items[i:i + batch_size] for i in range(0, len(items), batch_size)]

    def get_earnings_date_safe(symbol: str) -> Optional[date]:
        with earnings_cache_lock:
            cached = earnings_cache.get(symbol)
            if cached and cached.get("fetched_on") == today.isoformat():
                earnings_iso = cached.get("earnings_date")
                return date.fromisoformat(earnings_iso) if earnings_iso else None

        url = f"{STOCK_ANALYSIS_BASE_URL}/{symbol.lower()}/statistics/"
        html = http_get_text(url)
        earnings_date = parse_earnings_date_from_html(html)
        with earnings_cache_lock:
            earnings_cache[symbol] = {
                "fetched_on": today.isoformat(),
                "earnings_date": earnings_date.isoformat() if earnings_date else "",
            }
        return earnings_date

    def preprocess_symbol(symbol: str) -> Dict[str, object]:
        earnings_date: Optional[date] = None
        if symbol not in latest_prices:
            return {
                "status": "skip",
                "symbol": symbol,
                "skip_message": f"{symbol}: missing latest stock price",
                "excluded_row": ExcludedTickerRow(symbol, None, None, "N/A", None, None),
            }
        if enforce_min_price_filter and latest_prices[symbol] < 5:
            return {
                "status": "skip",
                "symbol": symbol,
                "skip_message": f"{symbol}: stock price below $5 threshold for Options Report",
                "excluded_row": ExcludedTickerRow(symbol, latest_prices[symbol], None, "N/A", None, None),
            }
        try:
            earnings_date = get_earnings_date_safe(symbol)
            bars = get_weekly_bars(symbol, start, today, api_key, api_secret)
            pct_otm = average_weekly_move_pct(bars)
            high_52w = max(float(bar["h"]) for bar in bars[-52:] if bar.get("h") is not None)
            trend = wavetrend_last_signal(bars)
            if earnings_in_report_week(earnings_date, report_start, report_expiration):
                best_options_label = "N/A"
                best_premium: Optional[float] = None
                best_roi: Optional[float] = None
                try:
                    excluded_call, _ = build_row(symbol, latest_prices[symbol], trend, pct_otm, "call", api_key, api_secret, expiration_override)
                    excluded_put, _ = build_row(symbol, latest_prices[symbol], trend, pct_otm, "put", api_key, api_secret, expiration_override)
                    call_roi = roi_pct(excluded_call.last_price, excluded_call.price)
                    put_roi = roi_pct(excluded_put.last_price, excluded_put.price)
                    if call_roi is not None or put_roi is not None:
                        if put_roi is None or (call_roi is not None and call_roi >= put_roi):
                            best_options_label = f"Covered Call (strike: {format_money(excluded_call.strike)})"
                            best_premium = excluded_call.last_price * 100.0 if excluded_call.last_price is not None else None
                            best_roi = call_roi
                        else:
                            best_options_label = f"Cash Secured Put (strike: {format_money(excluded_put.strike)})"
                            best_premium = excluded_put.last_price * 100.0 if excluded_put.last_price is not None else None
                            best_roi = put_roi
                except Exception:
                    pass
                return {
                    "status": "excluded",
                    "symbol": symbol,
                    "skip_message": f"{symbol}: earnings during report week ({earnings_date.isoformat()})",
                    "excluded_row": ExcludedTickerRow(symbol, latest_prices[symbol], earnings_date, best_options_label, best_premium, best_roi),
                }
            return {
                "status": "active",
                "symbol": symbol,
                "pct_otm": pct_otm,
                "high_52w": high_52w,
                "trend": trend,
            }
        except Exception as exc:
            return {
                "status": "skip",
                "symbol": symbol,
                "skip_message": f"{symbol}: {exc}",
                "excluded_row": ExcludedTickerRow(symbol, latest_prices.get(symbol), earnings_date, "N/A", None, None),
            }

    for batch_index, symbol_batch in enumerate(batched_symbols(symbols)):
        max_workers = min(len(symbol_batch), 8) or 1
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(preprocess_symbol, symbol) for symbol in symbol_batch]
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                symbol = str(result["symbol"])
                status = result["status"]
                if status == "active":
                    pct_otm_by_symbol[symbol] = float(result["pct_otm"])
                    high_52w_by_symbol[symbol] = float(result["high_52w"])
                    trend_by_symbol[symbol] = str(result["trend"])
                    active_symbols.append(symbol)
                else:
                    skipped.append(str(result["skip_message"]))
                    excluded_rows.append(result["excluded_row"])
        if batch_pause_seconds > 0 and batch_index < len(batched_symbols(symbols)) - 1:
            time.sleep(batch_pause_seconds)
    save_earnings_cache(earnings_cache)

    covered_calls = []
    cash_secured_puts = []
    portfolio_rows = []
    pmcc_rows: List[PmccRow] = []
    covered_call_expirations: Dict[str, date] = {}
    cash_secured_put_expirations: Dict[str, date] = {}
    portfolio_expirations: Dict[str, date] = {}
    final_symbols = []

    def build_active_symbol(symbol: str) -> Dict[str, object]:
        try:
            covered_call, covered_call_expiration = build_row(symbol, latest_prices[symbol], trend_by_symbol[symbol], pct_otm_by_symbol[symbol], "call", api_key, api_secret, expiration_override)
            cash_secured_put, cash_secured_put_expiration = build_row(symbol, latest_prices[symbol], trend_by_symbol[symbol], pct_otm_by_symbol[symbol], "put", api_key, api_secret, expiration_override)
            return {
                "status": "ok",
                "symbol": symbol,
                "covered_call": covered_call,
                "covered_call_expiration": covered_call_expiration,
                "cash_secured_put": cash_secured_put,
                "cash_secured_put_expiration": cash_secured_put_expiration,
            }
        except Exception as exc:
            return {
                "status": "skip",
                "symbol": symbol,
                "skip_message": f"{symbol}: {exc}",
                "excluded_row": ExcludedTickerRow(symbol, latest_prices.get(symbol), None, "N/A", None, None),
            }

    active_batches = batched_symbols(active_symbols)
    for batch_index, symbol_batch in enumerate(active_batches):
        max_workers = min(len(symbol_batch), 8) or 1
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(build_active_symbol, symbol) for symbol in symbol_batch]
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result["status"] == "ok":
                    symbol = str(result["symbol"])
                    covered_calls.append(result["covered_call"])
                    cash_secured_puts.append(result["cash_secured_put"])
                    covered_call_expirations[symbol] = result["covered_call_expiration"]
                    cash_secured_put_expirations[symbol] = result["cash_secured_put_expiration"]
                    final_symbols.append(symbol)
                else:
                    skipped.append(str(result["skip_message"]))
                    excluded_rows.append(result["excluded_row"])
        if batch_pause_seconds > 0 and batch_index < len(active_batches) - 1:
            time.sleep(batch_pause_seconds)

    pmcc_short_expiration = nearest_weekly_expiration(today, expiration_override)

    def build_pmcc_symbol(symbol: str) -> Optional[PmccRow]:
        price = latest_prices.get(symbol)
        pct_otm = pct_otm_by_symbol.get(symbol)
        high_52w = high_52w_by_symbol.get(symbol)
        if price is None or pct_otm is None or high_52w is None or high_52w <= 0:
            return None
        weekly_move_pct = pct_otm * 100.0
        below_52w_high_pct = ((price - high_52w) / high_52w) * 100.0
        if price < 50 or price > 700:
            return None
        if weekly_move_pct <= 0 or weekly_move_pct > 8:
            return None
        if below_52w_high_pct > 0 or below_52w_high_pct < -45:
            return None
        try:
            leaps_expiration, leaps_snapshot = choose_leaps_contract(symbol, price, today, api_key, api_secret)
            short_snapshot = choose_pmcc_short_call(
                symbol,
                price,
                pct_otm,
                pmcc_short_expiration,
                api_key,
                api_secret,
            )
            leaps_strike = option_snapshot_strike(leaps_snapshot)
            leaps_delta = option_snapshot_delta(leaps_snapshot)
            leaps_price = option_snapshot_price(leaps_snapshot)
            short_strike = option_snapshot_strike(short_snapshot)
            short_delta = option_snapshot_delta(short_snapshot)
            short_price = option_snapshot_price(short_snapshot)
            if (
                leaps_strike is None
                or leaps_delta is None
                or leaps_price is None
                or short_strike is None
            ):
                return None
            score = pmcc_score(price, pct_otm, below_52w_high_pct, short_price)
            return PmccRow(
                stock=symbol,
                price=price,
                pct_otm=pct_otm,
                below_52w_high_pct=below_52w_high_pct,
                leaps_expiration=leaps_expiration,
                leaps_strike=leaps_strike,
                leaps_delta=abs(leaps_delta),
                leaps_price=leaps_price,
                short_call_expiration=pmcc_short_expiration,
                short_call_strike=short_strike,
                short_call_delta=abs(short_delta) if short_delta is not None else None,
                short_call_price=short_price,
                score=score,
            )
        except Exception:
            return None

    preliminary_pmcc_symbols = sorted(
        active_symbols,
        key=lambda symbol: (
            -pmcc_score(
                latest_prices.get(symbol, 0.0),
                pct_otm_by_symbol.get(symbol, 0.0),
                ((latest_prices.get(symbol, 0.0) - high_52w_by_symbol.get(symbol, 1.0)) / high_52w_by_symbol.get(symbol, 1.0)) * 100.0
                if high_52w_by_symbol.get(symbol, 0.0) > 0
                else -100.0,
                1.0,
            ),
            symbol,
        ),
    )[:20]

    pmcc_batches = batched_symbols(preliminary_pmcc_symbols)
    for batch_index, symbol_batch in enumerate(pmcc_batches):
        max_workers = min(len(symbol_batch), 4) or 1
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(build_pmcc_symbol, symbol) for symbol in symbol_batch]
            for future in concurrent.futures.as_completed(futures):
                row = future.result()
                if row is not None:
                    pmcc_rows.append(row)
        if batch_pause_seconds > 0 and batch_index < len(pmcc_batches) - 1:
            time.sleep(batch_pause_seconds)

    pmcc_rows.sort(key=lambda row: (row.score, row.short_call_price or 0.0), reverse=True)
    pmcc_rows = pmcc_rows[:10]

    portfolio_tickers = load_my_portfolio_tickers()
    def build_portfolio_symbol(symbol: str) -> Optional[Tuple[str, OptionRow, date]]:
        if symbol not in latest_prices:
            return None
        try:
            pct_otm = pct_otm_by_symbol.get(symbol)
            if pct_otm is None:
                bars = get_weekly_bars(symbol, start, today, api_key, api_secret)
                pct_otm = average_weekly_move_pct(bars)
            if symbol not in trend_by_symbol:
                bars = get_weekly_bars(symbol, start, today, api_key, api_secret)
                trend_by_symbol[symbol] = wavetrend_last_signal(bars)
            portfolio_row, portfolio_expiration = build_row(symbol, latest_prices[symbol], trend_by_symbol[symbol], pct_otm, "call", api_key, api_secret, expiration_override)
            return symbol, portfolio_row, portfolio_expiration
        except Exception:
            return None

    max_workers = min(len(portfolio_tickers), 4) or 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(build_portfolio_symbol, symbol) for symbol in portfolio_tickers]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result is None:
                continue
            symbol, portfolio_row, portfolio_expiration = result
            portfolio_rows.append(portfolio_row)
            portfolio_expirations[symbol] = portfolio_expiration

    portfolio_rows.sort(key=lambda row: row.last_price or 0.0, reverse=True)

    covered_call_label = display_expiration(expiration_override or min(covered_call_expirations.values())) if covered_call_expirations else "N/A"
    cash_secured_put_label = display_expiration(expiration_override or min(cash_secured_put_expirations.values())) if cash_secured_put_expirations else "N/A"
    portfolio_label = display_expiration(expiration_override or min(portfolio_expirations.values())) if portfolio_expirations else "N/A"

    generated_dt = datetime.now()
    generated_at = generated_dt.strftime("%Y-%m-%d %H:%M:%S")
    report_date_label = f"{generated_dt.month}/{generated_dt.day}"
    report_date_iso = generated_dt.date().isoformat()
    parts = [
        f"# {report_title} - {report_date_label}",
        "",
        render_portfolio_table(portfolio_rows, portfolio_label),
        render_table("Covered Calls", covered_calls, covered_call_label),
        render_table("Cash Secured Puts", cash_secured_puts, cash_secured_put_label),
        render_pmcc_table(pmcc_rows),
    ]
    if excluded_rows:
        parts.extend(["", render_excluded_table(excluded_rows)])
    parts.extend(["", render_recommendations(covered_calls, cash_secured_puts)])
    parts.append("")
    markdown_report = "\n".join(parts)
    best_balance, aggressive = build_recommendation_groups(covered_calls, cash_secured_puts)
    filtered_excluded_rows = sorted(
        [row for row in excluded_rows if row.roi is not None and row.roi >= 2.0],
        key=lambda row: (row.roi if row.roi is not None else -1.0, row.stock),
        reverse=True,
    )
    snapshot = {
        "reportTitle": report_title,
        "reportDate": report_date_label,
        "reportDateIso": report_date_iso,
        "generatedAt": generated_at,
        "expiration": covered_call_label if covered_call_label != "N/A" else cash_secured_put_label,
        "includedCount": len(final_symbols),
        "requestedCount": len(symbols),
        "myPortfolio": {
            "title": "My Portfolio Report",
            "expiration": portfolio_label,
            "rows": [option_row_to_dict(row) for row in portfolio_rows],
            "totalPremium": sum((row.last_price or 0.0) * 100.0 for row in portfolio_rows),
            "totalPremiumText": format_money(sum((row.last_price or 0.0) * 100.0 for row in portfolio_rows)),
        },
        "coveredCalls": {
            "title": "Covered Calls",
            "expiration": covered_call_label,
            "rows": [option_row_to_dict(row) for row in sorted_sell_rows(covered_calls)],
        },
        "cashSecuredPuts": {
            "title": "Cash Secured Puts",
            "expiration": cash_secured_put_label,
            "rows": [option_row_to_dict(row) for row in sorted_sell_rows(cash_secured_puts)],
        },
        "pmccCandidates": {
            "title": "PMCC Candidates",
            "weeklyExpiration": display_expiration(pmcc_short_expiration),
            "rows": [pmcc_row_to_dict(row) for row in pmcc_rows],
        },
        "earningsThisWeek": {
            "title": "Earnings this Week",
            "rows": [excluded_row_to_dict(row) for row in filtered_excluded_rows],
        },
        "teamReview": {
            "title": "Team Review",
            "bestBalance": [recommendation_row_to_dict(item) for item in best_balance],
            "bestBalanceWhy": "these are the strongest remaining candidates after the earnings-week exclusions, biased toward moderate ROI rather than the most aggressive premium.",
            "aggressivePremium": [recommendation_row_to_dict(item) for item in aggressive],
        },
    }
    html_report = build_report_html(
        report_title=report_title,
        generated_at=generated_at,
        expiration_text=(expiration_override.isoformat() if expiration_override else "Closest Friday with listed contracts"),
        included_count=len(final_symbols),
        requested_count=len(symbols),
        portfolio_rows=portfolio_rows,
        portfolio_expiration_label=portfolio_label,
        covered_calls=covered_calls,
        covered_call_label=covered_call_label,
        cash_secured_puts=cash_secured_puts,
        cash_secured_put_label=cash_secured_put_label,
        pmcc_rows=pmcc_rows,
        skipped=skipped,
    )
    return markdown_report, html_report, snapshot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate covered call and cash secured put report from Alpaca data.")
    parser.add_argument("--output", default="options_report.md", help="Markdown report output path")
    parser.add_argument("--html-output", help="Optional HTML report output path")
    parser.add_argument("--json-output", help="Optional JSON snapshot output path")
    parser.add_argument("--history-output", help="Optional report history JSON output path")
    parser.add_argument("--expiration", help="Specific Friday expiration date in YYYY-MM-DD format")
    parser.add_argument("--preset", choices=["report", "report2", "current"], default="current", help="Ticker universe preset")
    parser.add_argument("--title", help="Report title override")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("APCA_API_KEY_ID")
    api_secret = os.environ.get("APCA_API_SECRET_KEY")
    if not api_key or not api_secret:
        print("Set APCA_API_KEY_ID and APCA_API_SECRET_KEY in the environment.", file=sys.stderr)
        return 1

    expiration_override = date.fromisoformat(args.expiration) if args.expiration else None
    if args.preset in {"report", "report2", "current"}:
        symbols = OPTIONS_REPORT_STOCKS
        default_title = "Options Report"
        batch_size = 10
        batch_pause_seconds = 2.0
        enforce_min_price_filter = True

    report, html_report, snapshot = build_report(
        api_key,
        api_secret,
        symbols,
        args.title or default_title,
        expiration_override,
        batch_size,
        batch_pause_seconds,
        enforce_min_price_filter,
    )
    with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(report)
    if args.html_output:
        with open(args.html_output, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(html_report)
    if args.json_output:
        with open(args.json_output, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(snapshot, handle, indent=2)
    if args.history_output:
        save_report_history(args.history_output, snapshot)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
