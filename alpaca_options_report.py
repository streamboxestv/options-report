import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple


REPORT2_INPUT_STOCKS = [
    "AA", "ACHR", "BABA", "AFRM", "AG", "AI", "ALB", "AAPL", "AMAT", "AMPX",
    "AMD", "ANET", "APLD", "APO", "APP", "ASTS", "AMZN", "AVGO", "AXTI", "BE",
    "BROS", "BULL", "CAVA", "CCJ", "CCL", "CDE", "CEG", "CELH", "CIFR", "CLSK",
    "CMG", "COIN", "CPNG", "CORZ", "CRM", "CRCL", "CRWD", "CRWV", "CVNA", "DAL",
    "DASH", "DDOG", "FSLY", "DELL", "DKNG", "DUOL", "ELF", "EL", "EOSE", "ETSY",
    "EXPE", "FCX", "FIGR", "FISV", "FLY", "NFLX", "FSLR", "FTNT", "SPOT", "GLW",
    "GLXY", "HIMS", "HL", "HOOD", "HPE", "AAOI", "HUT", "HPQ", "IONQ", "INTC",
    "IOT", "IREN", "JOBY", "KKR", "LUMN", "LUNR", "LRCX", "LUV", "LYFT", "MBLY",
    "MCHP", "MP", "MRVL", "MSTR", "MSFT", "MU", "NBIS", "NET", "NOW", "NVDA",
    "NU", "NVTS", "OKLO", "OKTA", "ONDS", "ONON", "OPEN", "ORCL", "OSCR", "KTOS",
    "PANW", "PATH", "PINS", "PL", "PLTR", "POET", "PYPL", "QBTS", "QCOM", "QS",
    "QUBT", "RCAT", "RBLX", "RDDT", "RDW", "RGTI", "RIVN", "RKLB", "RKT", "ROKU",
    "RCL", "SATS", "SHOP", "SMCI", "SMR", "AMD", "SNOW", "SOFI", "SOUN", "TE",
    "TEM", "TSM", "TSLA", "TOST", "TTD", "U", "UAL", "UEC", "UBER", "UPST",
    "USAR", "UUUU", "VRT", "VST", "W", "WULF", "XYZ", "GOOGL", "META", "ZETA", "ZS",
]


def dedupe_keep_order(symbols: List[str]) -> List[str]:
    return list(dict.fromkeys(symbols))


OPTIONS_REPORT_STOCKS = dedupe_keep_order(REPORT2_INPUT_STOCKS)
MY_PORTFOLIO_REPORT_FILE = "my_portfolio_report.txt"
EARNINGS_CACHE_FILE = "earnings_calendar_cache.json"
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


def api_get(path: str, api_key: str, api_secret: str, params: Optional[Dict[str, str]] = None) -> Dict:
    query = "?" + urllib.parse.urlencode(params) if params else ""
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
        "Accept": "application/json",
    }
    last_error: Optional[Exception] = None
    for attempt in range(5):
        request = urllib.request.Request(DATA_BASE_URL + path + query, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 429 and attempt < 4:
                retry_after = exc.headers.get("Retry-After")
                time.sleep(float(retry_after) if retry_after else (2 + attempt * 2))
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
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
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
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def save_earnings_cache(cache: Dict[str, Dict[str, str]]) -> None:
    try:
        with open(EARNINGS_CACHE_FILE, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(cache, handle, indent=2, sort_keys=True)
    except OSError:
        return


def parse_earnings_date_from_html(html: str) -> Optional[date]:
    match = re.search(r"Earnings Date<!----></span>.*?<td[^>]*title=\"([A-Za-z]+ \d{1,2}, \d{4})\"", html, re.I | re.S)
    if not match:
        match = re.search(r"The last earnings date was .*?([A-Za-z]+ \d{1,2}, \d{4})", html, re.I | re.S)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%B %d, %Y").date()
    except ValueError:
        return None


def get_earnings_date(symbol: str, cache: Dict[str, Dict[str, str]], today: date) -> Optional[date]:
    cached = cache.get(symbol)
    if cached and cached.get("fetched_on") == today.isoformat():
        value = cached.get("earnings_date")
        return date.fromisoformat(value) if value else None
    html = http_get_text(f"{STOCK_ANALYSIS_BASE_URL}/{symbol.lower()}/statistics/")
    earnings_date = parse_earnings_date_from_html(html)
    cache[symbol] = {"fetched_on": today.isoformat(), "earnings_date": earnings_date.isoformat() if earnings_date else ""}
    return earnings_date


def monday_of_week(day_value: date) -> date:
    return day_value - timedelta(days=day_value.weekday())


def earnings_in_report_week(earnings_date: Optional[date], report_start: date, expiration_date: date) -> bool:
    return earnings_date is not None and report_start <= earnings_date <= expiration_date


def isoformat_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_latest_prices(symbols: List[str], api_key: str, api_secret: str) -> Dict[str, float]:
    payload = api_get("/v2/stocks/trades/latest", api_key, api_secret, {"symbols": ",".join(symbols), "feed": STOCK_FEED})
    trades = payload.get("trades", {})
    return {symbol: float(trades[symbol]["p"]) for symbol in symbols if trades.get(symbol) and trades[symbol].get("p") is not None}


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
    moves = [((float(bar["h"]) - float(bar["l"])) / float(bar["c"])) for bar in bars if float(bar["c"]) > 0]
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
        results.append(window_sum / min(index + 1, length))
    return results


def wavetrend_last_signal(daily_bars: List[Dict]) -> str:
    closes = [float(bar["c"]) for bar in daily_bars if bar.get("c") is not None]
    if len(closes) < 20:
        return "N/A"
    esa = ema_series(closes, 6)
    deviations = [abs(close - esa_value) for close, esa_value in zip(closes, esa)]
    d_values = ema_series(deviations, 6)
    ci_values = [(close - esa_value) / (0.015 * d_value) if d_value != 0 else 0.0 for close, esa_value, d_value in zip(closes, esa, d_values)]
    wt1 = ema_series(ci_values, 14)
    wt2 = sma_series(wt1, 2)
    for index in range(len(wt1) - 1, 0, -1):
        if wt1[index - 1] <= wt2[index - 1] and wt1[index] > wt2[index]:
            return "Buy"
        if wt1[index - 1] >= wt2[index - 1] and wt1[index] < wt2[index]:
            return "Sell"
    return "N/A"


def fridays_from(start_on_or_after: date, count: int) -> List[date]:
    first = start_on_or_after + timedelta(days=(4 - start_on_or_after.weekday()) % 7)
    return [first + timedelta(days=7 * i) for i in range(count)]


def display_expiration(expiration_date: date) -> str:
    return f"{expiration_date.month}/{expiration_date.day}"


def paged_option_chain(symbol: str, expiration_date: date, option_type: str, api_key: str, api_secret: str) -> List[Dict]:
    rows: List[Dict] = []
    page_token: Optional[str] = None
    while True:
        params = {"expiration_date": expiration_date.isoformat(), "type": option_type, "feed": "indicative", "limit": "1000"}
        if page_token:
            params["page_token"] = page_token
        payload = api_get(f"/v1beta1/options/snapshots/{symbol}", api_key, api_secret, params)
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
    strike_digits = contract_symbol[-8:]
    return int(strike_digits) / 1000.0 if len(contract_symbol) >= 8 and strike_digits.isdigit() else None


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
        parsed.append({
            "strike": strike,
            "last": float(last) if last is not None else None,
            "delta_target_distance": abs(strike - target_strike),
            "has_last": last is not None,
            "open_interest": float(contract.get("open_interest") or 0),
            "delta": abs(float(greeks.get("delta"))) if greeks.get("delta") is not None else math.inf,
        })
    if not parsed:
        raise RuntimeError(f"No OTM {option_type} contracts available")
    parsed.sort(key=lambda item: (item["delta_target_distance"], 0 if item["has_last"] else 1, -item["open_interest"], item["delta"]))
    best = parsed[0]
    return best["strike"], best["last"]


def format_money(value: Optional[float]) -> str:
    return "N/A" if value is None else f"${value:,.2f}"


def roi_pct(last_price: Optional[float], stock_price: float) -> Optional[float]:
    return None if last_price is None or stock_price <= 0 else (last_price / stock_price) * 100.0


def action_for_premium(last_price: Optional[float], stock_price: float) -> str:
    current_roi = roi_pct(last_price, stock_price)
    return "Sell" if current_roi is not None and current_roi >= 1.0 else "Wait"


def option_row_to_dict(row: OptionRow) -> Dict[str, object]:
    premium = row.last_price * 100.0 if row.last_price is not None else None
    roi = roi_pct(row.last_price, row.price)
    return {
        "ticker": row.stock,
        "price": row.price,
        "priceText": format_money(row.price),
        "trend": row.trend,
        "avgWeeklyMovePct": row.pct_otm * 100.0,
        "avgWeeklyMovePctText": f"{row.pct_otm * 100:.2f}%",
        "strike": row.strike,
        "strikeText": format_money(row.strike),
        "premium": premium,
        "premiumText": format_money(premium) if premium is not None else "N/A",
        "roiPct": roi,
        "roiPctText": f"{roi:.2f}%" if roi is not None else "N/A",
        "action": row.action,
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
        return text.rjust(width) if align == "right" else text.center(width) if align == "center" else text.ljust(width)
    header_line = "| " + " | ".join(pad_cell(header, widths[index], "left") for index, header in enumerate(headers)) + " |"
    separator_cells = []
    for index, align in enumerate(aligns):
        width = max(widths[index], 3)
        separator_cells.append("-" * (width - 1) + ":" if align == "right" else ":" + "-" * (width - 2) + ":" if align == "center" else "-" * width)
    separator_line = "| " + " | ".join(separator_cells) + " |"
    body_lines = ["| " + " | ".join(pad_cell(cell, widths[index], aligns[index]) for index, cell in enumerate(row)) + " |" for row in rows]
    return "\n".join([header_line, separator_line, *body_lines])


def build_row(symbol: str, price: float, trend: str, pct_otm: float, option_type: str, api_key: str, api_secret: str, expiration_override: Optional[date] = None) -> Tuple[OptionRow, date]:
    target_strike = price * (1 + pct_otm) if option_type == "call" else price * (1 - pct_otm)
    contracts: List[Dict] = []
    expiration_used = expiration_override
    fridays_to_check = [expiration_override] if expiration_override else fridays_from(date.today(), 8)
    for friday in fridays_to_check:
        found = paged_option_chain(symbol, friday, option_type, api_key, api_secret)
        if found:
            contracts = found
            expiration_used = friday
            break
    if not contracts or expiration_used is None:
        raise RuntimeError(f"No Friday {option_type} contracts found for {symbol}")
    strike, last_price = choose_option_contract(contracts, price, target_strike, option_type)
    return OptionRow(symbol, price, trend, pct_otm, strike, last_price, action_for_premium(last_price, price)), expiration_used


def render_table(title: str, rows: List[OptionRow], expiration_label: str) -> str:
    sorted_rows = sorted([row for row in rows if row.action == "Sell"], key=lambda row: roi_pct(row.last_price, row.price) or -1.0, reverse=True)
    table_rows = [[row.stock, format_money(row.price), row.trend, f"{row.pct_otm * 100:.2f}%", format_money(row.strike), format_money(row.last_price * 100.0) if row.last_price is not None else "N/A", f"{(roi_pct(row.last_price, row.price) or 0):.2f}%"] for row in sorted_rows] or [["None", "N/A", "N/A", "N/A", "N/A", "N/A", "N/A"]]
    return "\n".join([f"## {title} - Expiration {expiration_label}", "", render_markdown_table(["Ticker", "Price", "Trend", "Avg Weekly Move %", "OTM Strike", "Premium", "ROI %"], table_rows, ["left", "right", "left", "right", "right", "right", "right"]), ""])


def load_my_portfolio_tickers() -> List[str]:
    if not os.path.exists(MY_PORTFOLIO_REPORT_FILE):
        return []
    with open(MY_PORTFOLIO_REPORT_FILE, "r", encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]


def render_portfolio_table(rows: List[OptionRow], expiration_label: str) -> str:
    total_premium = 0.0
    table_rows: List[List[str]] = []
    for row in rows:
        premium_value = row.last_price * 100.0 if row.last_price is not None else 0.0
        total_premium += premium_value
        table_rows.append([row.stock, format_money(row.price), f"{row.pct_otm * 100:.2f}%", format_money(row.strike), format_money(premium_value) if row.last_price is not None else "N/A"])
    table_rows.append(["**Total**", "", "", "", f"**{format_money(total_premium)}**"])
    return "\n".join([f"## My Portfolio Report - Expiration {expiration_label}", "", render_markdown_table(["Ticker", "Price", "Avg Weekly Move %", "Covered Call Strike", "Premium"], table_rows, ["left", "right", "right", "right", "right"]), ""])


def render_excluded_table(rows: List[ExcludedTickerRow]) -> str:
    filtered_rows = sorted([row for row in rows if row.roi is not None and row.roi >= 2.0], key=lambda row: (row.roi if row.roi is not None else -1.0, row.stock), reverse=True)
    table_rows = [[row.stock, format_money(row.price) if row.price is not None else "N/A", f"{row.earnings_date.month}/{row.earnings_date.day}" if row.earnings_date else "N/A", row.options_label, format_money(row.premium) if row.premium is not None else "N/A", f"{row.roi:.2f}%" if row.roi is not None else "N/A"] for row in filtered_rows] or [["None", "N/A", "N/A", "N/A", "N/A", "N/A"]]
    return "\n".join([ "## Earnings this Week", "", render_markdown_table(["Ticker", "Price", "Earnings Date", "Action", "Premium", "ROI %"], table_rows, ["left", "right", "left", "left", "right", "right"]), ""])


def sorted_sell_rows(rows: List[OptionRow]) -> List[OptionRow]:
    return sorted([row for row in rows if row.action == "Sell"], key=lambda row: roi_pct(row.last_price, row.price) or -1.0, reverse=True)


def recommendation_score(row: OptionRow) -> float:
    roi = roi_pct(row.last_price, row.price)
    return math.inf if roi is None else abs(roi - 1.5) + (row.pct_otm * 100.0) / 25.0


def format_recommendation_line(label: str, row: OptionRow) -> str:
    roi = roi_pct(row.last_price, row.price)
    return f"- {label}: `{row.stock}` | price `{format_money(row.price)}` | avg weekly move `{row.pct_otm * 100:.2f}%` | OTM strike `{format_money(row.strike)}` | premium `{format_money(row.last_price * 100.0) if row.last_price is not None else 'N/A'}` | ROI `{f'{roi:.2f}%' if roi is not None else 'N/A'}`"


def build_recommendation_groups(covered_calls: List[OptionRow], cash_secured_puts: List[OptionRow]) -> Tuple[List[RecommendationRow], List[RecommendationRow]]:
    candidates = [RecommendationRow("Covered Call", row) for row in sorted_sell_rows(covered_calls)] + [RecommendationRow("Cash Secured Put", row) for row in sorted_sell_rows(cash_secured_puts)]
    best_balance = sorted(candidates, key=lambda item: recommendation_score(item.row))[:3]
    aggressive: List[RecommendationRow] = []
    used = {(item.label, item.row.stock) for item in best_balance}
    for item in sorted(candidates, key=lambda item: roi_pct(item.row.last_price, item.row.price) or -1.0, reverse=True):
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
    lines = ["## Team Review", "", "**Best Balance**"]
    if best_balance:
        lines.extend(format_recommendation_line(item.label, item.row) for item in best_balance)
        lines.extend(["", "Why: these are the strongest remaining candidates after the earnings-week exclusions, biased toward moderate ROI rather than the most aggressive premium."])
    else:
        lines.extend(["- None today.", "", "Why: no filtered candidates remain above the current threshold."])
    lines.extend(["", "**Aggressive Premium**"])
    lines.extend(format_recommendation_line(item.label, item.row) for item in aggressive) if aggressive else lines.append("- None beyond the best-balance group.")
    lines.append("")
    return "\n".join(lines)


def build_report(api_key: str, api_secret: str, symbols: List[str], report_title: str, expiration_override: Optional[date] = None, batch_size: int = 0, batch_pause_seconds: float = 0.0, enforce_min_price_filter: bool = False) -> Tuple[str, str, Dict[str, object]]:
    today = date.today()
    start = today - timedelta(days=400)
    report_expiration = expiration_override or fridays_from(today, 1)[0]
    report_start = monday_of_week(report_expiration)
    latest_prices = get_latest_prices(symbols, api_key, api_secret)
    earnings_cache = load_earnings_cache()

    pct_otm_by_symbol: Dict[str, float] = {}
    trend_by_symbol: Dict[str, str] = {}
    excluded_rows: List[ExcludedTickerRow] = []
    active_symbols: List[str] = []

    for symbol in symbols:
        earnings_date: Optional[date] = None
        if symbol not in latest_prices:
            excluded_rows.append(ExcludedTickerRow(symbol, None, None, "N/A", None, None))
            continue
        if enforce_min_price_filter and latest_prices[symbol] < 5:
            excluded_rows.append(ExcludedTickerRow(symbol, latest_prices[symbol], None, "N/A", None, None))
            continue
        try:
            earnings_date = get_earnings_date(symbol, earnings_cache, today)
            bars = get_weekly_bars(symbol, start, today, api_key, api_secret)
            daily_bars = get_daily_bars(symbol, start, today, api_key, api_secret)
            pct_otm = average_weekly_move_pct(bars)
            pct_otm_by_symbol[symbol] = pct_otm
            trend_by_symbol[symbol] = wavetrend_last_signal(daily_bars)
            if earnings_in_report_week(earnings_date, report_start, report_expiration):
                best_options_label = "N/A"
                best_premium: Optional[float] = None
                best_roi: Optional[float] = None
                try:
                    excluded_call, _ = build_row(symbol, latest_prices[symbol], trend_by_symbol[symbol], pct_otm, "call", api_key, api_secret, expiration_override)
                    excluded_put, _ = build_row(symbol, latest_prices[symbol], trend_by_symbol[symbol], pct_otm, "put", api_key, api_secret, expiration_override)
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
                excluded_rows.append(ExcludedTickerRow(symbol, latest_prices[symbol], earnings_date, best_options_label, best_premium, best_roi))
                continue
            active_symbols.append(symbol)
        except Exception:
            excluded_rows.append(ExcludedTickerRow(symbol, latest_prices.get(symbol), earnings_date, "N/A", None, None))

    save_earnings_cache(earnings_cache)

    covered_calls: List[OptionRow] = []
    cash_secured_puts: List[OptionRow] = []
    portfolio_rows: List[OptionRow] = []
    covered_call_expirations: Dict[str, date] = {}
    cash_secured_put_expirations: Dict[str, date] = {}
    portfolio_expirations: Dict[str, date] = {}
    final_symbols: List[str] = []

    batches = [active_symbols] if batch_size <= 0 else [active_symbols[index:index + batch_size] for index in range(0, len(active_symbols), batch_size)]
    for batch_index, batch in enumerate(batches):
        for symbol in batch:
            try:
                covered_call, cc_exp = build_row(symbol, latest_prices[symbol], trend_by_symbol[symbol], pct_otm_by_symbol[symbol], "call", api_key, api_secret, expiration_override)
                cash_secured_put, csp_exp = build_row(symbol, latest_prices[symbol], trend_by_symbol[symbol], pct_otm_by_symbol[symbol], "put", api_key, api_secret, expiration_override)
                covered_calls.append(covered_call)
                cash_secured_puts.append(cash_secured_put)
                covered_call_expirations[symbol] = cc_exp
                cash_secured_put_expirations[symbol] = csp_exp
                final_symbols.append(symbol)
            except Exception:
                excluded_rows.append(ExcludedTickerRow(symbol, latest_prices.get(symbol), None, "N/A", None, None))
        if batch_pause_seconds > 0 and batch_index < len(batches) - 1:
            time.sleep(batch_pause_seconds)

    for symbol in load_my_portfolio_tickers():
        if symbol not in latest_prices:
            continue
        try:
            pct_otm = pct_otm_by_symbol.get(symbol)
            if pct_otm is None:
                pct_otm = average_weekly_move_pct(get_weekly_bars(symbol, start, today, api_key, api_secret))
            trend = trend_by_symbol.get(symbol)
            if trend is None:
                trend = wavetrend_last_signal(get_daily_bars(symbol, start, today, api_key, api_secret))
            row, exp = build_row(symbol, latest_prices[symbol], trend, pct_otm, "call", api_key, api_secret, expiration_override)
            portfolio_rows.append(row)
            portfolio_expirations[symbol] = exp
        except Exception:
            continue

    covered_call_label = display_expiration(expiration_override or min(covered_call_expirations.values())) if covered_call_expirations else "N/A"
    cash_secured_put_label = display_expiration(expiration_override or min(cash_secured_put_expirations.values())) if cash_secured_put_expirations else "N/A"
    portfolio_label = display_expiration(expiration_override or min(portfolio_expirations.values())) if portfolio_expirations else "N/A"

    generated_dt = datetime.now()
    report_date_label = f"{generated_dt.month}/{generated_dt.day}"
    report_date_iso = generated_dt.date().isoformat()
    generated_at = generated_dt.strftime("%Y-%m-%d %H:%M:%S")

    markdown_report = "\n".join([
        f"# {report_title} - {report_date_label}",
        "",
        render_portfolio_table(portfolio_rows, portfolio_label),
        render_table("Covered Calls", covered_calls, covered_call_label),
        render_table("Cash Secured Puts", cash_secured_puts, cash_secured_put_label),
        "",
        render_excluded_table(excluded_rows),
        "",
        render_recommendations(covered_calls, cash_secured_puts),
        "",
    ])

    best_balance, aggressive = build_recommendation_groups(covered_calls, cash_secured_puts)
    filtered_excluded_rows = sorted([row for row in excluded_rows if row.roi is not None and row.roi >= 2.0], key=lambda row: (row.roi if row.roi is not None else -1.0, row.stock), reverse=True)
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
    return markdown_report, "", snapshot
