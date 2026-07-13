import base64
import json
import os
import re
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from html import unescape
from http.server import BaseHTTPRequestHandler
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import alpaca_options_report as report_module
from alpaca_options_report import OPTIONS_REPORT_STOCKS, build_report


GITHUB_API_BASE = "https://api.github.com"
DEFAULT_REPOSITORY = "streamboxestv/options-report"
DEFAULT_BRANCH = "main"
LATEST_REPORT_PATH = "latest_report.json"
REPORT_HISTORY_PATH = "report_history.json"
MARKDOWN_REPORT_PATH = "options_report.md"
CHICAGO_TZ = ZoneInfo("America/Chicago")


def json_response(request: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    request.send_response(status)
    request.send_header("Content-Type", "application/json; charset=utf-8")
    request.send_header("Cache-Control", "no-store")
    request.end_headers()
    request.wfile.write(json.dumps(payload).encode("utf-8"))


def unauthorized(request: BaseHTTPRequestHandler) -> None:
    json_response(request, 401, {"error": "Unauthorized"})


def github_request(path: str, token: str, method: str = "GET", payload: Optional[dict] = None) -> dict:
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        GITHUB_API_BASE + path,
        method=method,
        data=body,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "options-report-vercel-cron",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload_text = response.read().decode("utf-8")
        return json.loads(payload_text) if payload_text else {}


def fetch_repo_file(repository: str, path: str, branch: str, token: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        payload = github_request(f"/repos/{repository}/contents/{path}?ref={branch}", token)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, None
        raise
    content = payload.get("content")
    encoding = payload.get("encoding")
    if content and encoding == "base64":
        decoded = base64.b64decode(content).decode("utf-8")
    else:
        decoded = None
    return payload.get("sha"), decoded


def update_repo_file(
    repository: str,
    branch: str,
    path: str,
    content: str,
    sha: Optional[str],
    token: str,
    message: str,
) -> None:
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha
    github_request(f"/repos/{repository}/contents/{path}", token, method="PUT", payload=payload)


def merge_history(existing_history: List[Dict[str, object]], snapshot: Dict[str, object]) -> List[Dict[str, object]]:
    report_date_iso = str(snapshot.get("reportDateIso") or "")
    expiration = str(snapshot.get("expiration") or "")
    filtered = [
        item for item in existing_history
        if not (
            str(item.get("reportDateIso") or "") == report_date_iso
            and str(item.get("expiration") or "") == expiration
        )
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
    return filtered


def skipped_tickers_from_html(html_report: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for raw_item in re.findall(r"<li[^>]*>(.*?)</li>", html_report, flags=re.IGNORECASE | re.DOTALL):
        reason = unescape(re.sub(r"<[^>]+>", "", raw_item)).strip()
        if not reason:
            continue
        ticker = reason.split(":", 1)[0].strip()
        if ticker:
            rows.append({"ticker": ticker, "reason": reason})
    return rows


def earnings_date_text(earnings_iso: str) -> str:
    try:
        earnings_date = date.fromisoformat(earnings_iso)
    except ValueError:
        return "N/A"
    return f"{earnings_date.month}/{earnings_date.day}"


def include_all_earnings_skips(snapshot: Dict[str, object]) -> None:
    earnings = snapshot.get("earningsThisWeek") if isinstance(snapshot.get("earningsThisWeek"), dict) else {}
    existing_rows = earnings.get("rows") if isinstance(earnings.get("rows"), list) else []
    rows_by_ticker = {
        str(row.get("ticker")): row
        for row in existing_rows
        if isinstance(row, dict) and row.get("ticker")
    }

    skipped = snapshot.get("skippedTickers") if isinstance(snapshot.get("skippedTickers"), list) else []
    for item in skipped:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker") or "").strip()
        reason = str(item.get("reason") or "")
        match = re.search(r"earnings during report week \((\d{4}-\d{2}-\d{2})\)", reason)
        if not ticker or not match or ticker in rows_by_ticker:
            continue
        earnings_iso = match.group(1)
        rows_by_ticker[ticker] = {
            "ticker": ticker,
            "price": None,
            "priceText": "N/A",
            "earningsDate": earnings_iso,
            "earningsDateText": earnings_date_text(earnings_iso),
            "action": "Earnings this week",
            "premium": None,
            "premiumText": "N/A",
            "roiPct": None,
            "roiPctText": "N/A",
        }

    earnings["title"] = str(earnings.get("title") or "Earnings this Week")
    earnings["rows"] = sorted(
        rows_by_ticker.values(),
        key=lambda row: (
            str(row.get("earningsDate") or "9999-12-31"),
            str(row.get("ticker") or ""),
        ),
    )
    snapshot["earningsThisWeek"] = earnings


def should_run_refresh(now_utc: datetime) -> bool:
    chicago_now = now_utc.astimezone(CHICAGO_TZ)
    return chicago_now.weekday() < 5 and chicago_now.hour == 9


def options_report_stocks() -> List[str]:
    replacements = {
        "ALB": "ALAB",
        "CELH": "KLAC",
        "EOSE": "TXN",
        "LYFT": "LITE",
        "PINS": "GNRC",
        "SATS": "ECHO",
        "SMR": "SMTC",
    }
    return [replacements.get(symbol, symbol) for symbol in OPTIONS_REPORT_STOCKS]


def nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    current = date(year, month, 1)
    current += timedelta(days=(weekday - current.weekday()) % 7)
    return current + timedelta(days=7 * (occurrence - 1))


def last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        current = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        current = date(year, month + 1, 1) - timedelta(days=1)
    return current - timedelta(days=(current.weekday() - weekday) % 7)


def observed_fixed_holiday(year: int, month: int, day: int) -> date:
    holiday = date(year, month, day)
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


def easter_date(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def market_holidays(year: int) -> set[date]:
    return {
        observed_fixed_holiday(year, 1, 1),
        nth_weekday(year, 1, 0, 3),
        nth_weekday(year, 2, 0, 3),
        easter_date(year) - timedelta(days=2),
        last_weekday(year, 5, 0),
        observed_fixed_holiday(year, 6, 19),
        observed_fixed_holiday(year, 7, 4),
        nth_weekday(year, 9, 0, 1),
        nth_weekday(year, 11, 3, 4),
        observed_fixed_holiday(year, 12, 25),
    }


def expiration_override_for_today(today: date) -> Optional[date]:
    tomorrow = today + timedelta(days=1)
    if today.weekday() == 3 and tomorrow.weekday() == 4 and tomorrow in market_holidays(tomorrow.year):
        return today
    return None


def snapshot_option_price(snapshot: Dict) -> Optional[float]:
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


def choose_priced_option_contract(contracts, price: float, target_strike: float, option_type: str) -> Tuple[float, Optional[float]]:
    parsed = []
    for snapshot in contracts:
        greeks = snapshot.get("greeks") or {}
        contract = snapshot.get("option_contract") or snapshot.get("contract") or {}
        strike_value = contract.get("strike_price")
        strike = float(strike_value) if strike_value is not None else report_module.strike_from_contract_symbol(snapshot.get("contract_symbol", ""))
        if strike is None:
            continue
        if option_type == "call" and strike < price:
            continue
        if option_type == "put" and strike > price:
            continue
        option_price = snapshot_option_price(snapshot)
        parsed.append(
            {
                "strike": strike,
                "last": option_price,
                "has_price": option_price is not None,
                "delta_target_distance": abs(strike - target_strike),
                "open_interest": float(contract.get("open_interest") or 0),
                "delta": abs(float(greeks.get("delta"))) if greeks.get("delta") is not None else float("inf"),
            }
        )

    if not parsed:
        raise RuntimeError(f"No OTM {option_type} contracts available")

    parsed.sort(
        key=lambda item: (
            0 if item["has_price"] else 1,
            item["delta_target_distance"],
            -item["open_interest"],
            item["delta"],
        )
    )
    best = parsed[0]
    return best["strike"], best["last"]


report_module.choose_option_contract = choose_priced_option_contract


def portfolio_position_value(rows: List[Dict[str, object]]) -> float:
    return sum(float(row.get("price") or 0.0) * 100.0 for row in rows)


def add_my_portfolio_puts(
    snapshot: Dict[str, object],
    api_key: str,
    api_secret: str,
    expiration_override: Optional[date],
) -> None:
    portfolio = snapshot.get("myPortfolio") if isinstance(snapshot.get("myPortfolio"), dict) else {}
    source_rows = portfolio.get("rows") if isinstance(portfolio.get("rows"), list) else []
    put_rows: List[Dict[str, object]] = []
    expirations: List[date] = []

    for row in source_rows:
        try:
            symbol = str(row.get("ticker") or "")
            price = float(row.get("price") or 0.0)
            pct_otm = float(row.get("avgWeeklyMovePct") or 0.0) / 100.0
            trend = str(row.get("trend") or "N/A")
            if not symbol or price <= 0:
                continue
            put_row, put_expiration = report_module.build_row(
                symbol,
                price,
                trend,
                pct_otm,
                "put",
                api_key,
                api_secret,
                expiration_override,
            )
            put_rows.append(report_module.option_row_to_dict(put_row))
            expirations.append(put_expiration)
        except Exception:
            continue

    position_value = portfolio_position_value(source_rows)
    total_premium = sum(float(row.get("premium") or 0.0) for row in put_rows)
    expiration_label = report_module.display_expiration(expiration_override or min(expirations)) if expirations else str(portfolio.get("expiration") or "N/A")

    portfolio["totalPositionValue"] = position_value
    portfolio["totalPositionValueText"] = report_module.format_money(position_value)
    snapshot["myPortfolio"] = portfolio
    snapshot["myPortfolioPuts"] = {
        "title": "My Portfolio Cash Secured Puts",
        "expiration": expiration_label,
        "rows": put_rows,
        "totalPositionValue": position_value,
        "totalPositionValueText": report_module.format_money(position_value),
        "totalPremium": total_premium,
        "totalPremiumText": report_module.format_money(total_premium),
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        cron_secret = os.environ.get("CRON_SECRET")
        auth_header = self.headers.get("authorization")
        if cron_secret and auth_header != f"Bearer {cron_secret}":
            unauthorized(self)
            return

        now_utc = datetime.now(tz=ZoneInfo("UTC"))
        force_refresh = "force=1" in (self.path or "")
        if not force_refresh and not should_run_refresh(now_utc):
            chicago_now = now_utc.astimezone(CHICAGO_TZ)
            json_response(
                self,
                200,
                {
                    "ok": True,
                    "skipped": True,
                    "reason": "Outside 9 AM America/Chicago refresh window.",
                    "currentChicagoTime": chicago_now.isoformat(),
                },
            )
            return

        api_key = os.environ.get("APCA_API_KEY_ID")
        api_secret = os.environ.get("APCA_API_SECRET_KEY")
        github_token = os.environ.get("GITHUB_TOKEN")
        repository = os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPOSITORY)
        branch = os.environ.get("GITHUB_BRANCH", DEFAULT_BRANCH)

        missing = [
            name
            for name, value in (
                ("APCA_API_KEY_ID", api_key),
                ("APCA_API_SECRET_KEY", api_secret),
                ("GITHUB_TOKEN", github_token),
            )
            if not value
        ]
        if missing:
            json_response(self, 500, {"error": f"Missing environment variables: {', '.join(missing)}"})
            return

        try:
            today = now_utc.astimezone(CHICAGO_TZ).date()
            expiration_override = expiration_override_for_today(today)
            markdown_report, html_report, snapshot = build_report(
                api_key=api_key,
                api_secret=api_secret,
                symbols=options_report_stocks(),
                report_title="Options Report",
                expiration_override=expiration_override,
                batch_size=10,
                batch_pause_seconds=0.5,
                enforce_min_price_filter=True,
            )
            snapshot["skippedTickers"] = skipped_tickers_from_html(html_report)
            include_all_earnings_skips(snapshot)
            add_my_portfolio_puts(snapshot, api_key, api_secret, expiration_override)

            latest_sha, _ = fetch_repo_file(repository, LATEST_REPORT_PATH, branch, github_token)
            history_sha, history_text = fetch_repo_file(repository, REPORT_HISTORY_PATH, branch, github_token)
            markdown_sha, _ = fetch_repo_file(repository, MARKDOWN_REPORT_PATH, branch, github_token)

            existing_history: List[Dict[str, object]] = []
            if history_text:
                payload = json.loads(history_text)
                if isinstance(payload, list):
                    existing_history = [item for item in payload if isinstance(item, dict)]

            merged_history = merge_history(existing_history, snapshot)
            latest_json = json.dumps(snapshot, indent=2) + "\n"
            history_json = json.dumps(merged_history, indent=2) + "\n"

            report_date = str(snapshot.get("reportDate") or "")
            expiration = str(snapshot.get("expiration") or "")
            update_repo_file(
                repository,
                branch,
                LATEST_REPORT_PATH,
                latest_json,
                latest_sha,
                github_token,
                f"Refresh options report snapshot for {report_date}",
            )
            update_repo_file(
                repository,
                branch,
                REPORT_HISTORY_PATH,
                history_json,
                history_sha,
                github_token,
                f"Update options report history for {report_date}",
            )
            update_repo_file(
                repository,
                branch,
                MARKDOWN_REPORT_PATH,
                markdown_report,
                markdown_sha,
                github_token,
                f"Refresh options report markdown for {report_date}",
            )
        except Exception as exc:
            json_response(self, 500, {"error": str(exc)})
            return

        json_response(
            self,
            200,
            {
                "ok": True,
                "forced": force_refresh,
                "reportDate": snapshot.get("reportDate"),
                "reportDateIso": snapshot.get("reportDateIso"),
                "expiration": expiration,
                "repository": repository,
                "branch": branch,
            },
        )
