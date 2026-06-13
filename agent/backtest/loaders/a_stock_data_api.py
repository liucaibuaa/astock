"""Adapter for the open-source a-stock-data toolkit.

This module exposes a curated subset of functions from
``/home/liucai/a-stock-data/SKILL.md`` so that Vibe-Trading can fetch
A-share market data, research reports, news, fundamentals, and announcements.

The original skill is self-contained; the functions below are copied from its
public code blocks with minimal changes (type hints, docstrings, error handling).
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Market / ticker helpers
# ---------------------------------------------------------------------------

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def normalize_code(code: str) -> str:
    """Normalize any supported A-share ticker format to a 6-digit code."""
    c = code.strip().upper()
    c = c.replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    c = c.replace("SH", "").replace("SZ", "").replace("BJ", "")
    return c[-6:] if len(c) >= 6 else c


def get_prefix(code: str) -> str:
    """6-digit code -> exchange prefix used by Tencent/Sina URLs."""
    if code.startswith(("6", "9")):
        return "sh"
    elif code.startswith("8"):
        return "bj"
    return "sz"


# ---------------------------------------------------------------------------
# Eastmoney shared helpers (rate-limited)
# ---------------------------------------------------------------------------

DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

EM_SESSION = requests.Session()
EM_SESSION.headers.update({"User-Agent": UA})
EM_MIN_INTERVAL = 1.0
_em_last_call = [0.0]


def em_get(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 15,
    **kwargs: Any,
) -> requests.Response:
    """Eastmoney request gateway with serial rate limiting and session reuse."""
    wait = EM_MIN_INTERVAL - (time.time() - _em_last_call[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.5))
    try:
        return EM_SESSION.get(url, params=params, headers=headers, timeout=timeout, **kwargs)
    finally:
        _em_last_call[0] = time.time()


def eastmoney_datacenter(
    report_name: str,
    columns: str = "ALL",
    filter_str: str = "",
    page_size: int = 50,
    sort_columns: str = "",
    sort_types: str = "-1",
) -> list[dict]:
    """Unified Eastmoney datacenter query."""
    params = {
        "reportName": report_name,
        "columns": columns,
        "filter": filter_str,
        "pageNumber": "1",
        "pageSize": str(page_size),
        "sortColumns": sort_columns,
        "sortTypes": sort_types,
        "source": "WEB",
        "client": "WEB",
    }
    r = em_get(DATACENTER_URL, params=params, timeout=15)
    d = r.json()
    if d.get("result") and d["result"].get("data"):
        return d["result"]["data"]
    return []


# ---------------------------------------------------------------------------
# Layer 1: Market data (quotes + k-lines)
# ---------------------------------------------------------------------------

def tencent_quote(codes: list[str]) -> dict[str, dict]:
    """Batch real-time quotes from Tencent Finance.

    Returns:
        Mapping of normalized 6-digit code to fields such as
        name, price, open, high, low, pe_ttm, pb, mcap_yi, limit_up, limit_down.
    """
    normalized = [normalize_code(c) for c in codes]
    prefixed = [f"{get_prefix(c)}{c}" for c in normalized]

    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
    req = requests.Request("GET", url, headers={"User-Agent": "Mozilla/5.0"})
    resp = requests.Session().send(req.prepare(), timeout=10)
    resp.encoding = "gbk"
    data = resp.text

    result: dict[str, dict] = {}
    for line in data.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 53:
            continue
        code = key[2:]
        result[code] = {
            "name": vals[1],
            "price": float(vals[3]) if vals[3] else 0.0,
            "last_close": float(vals[4]) if vals[4] else 0.0,
            "open": float(vals[5]) if vals[5] else 0.0,
            "change_pct": float(vals[32]) if vals[32] else 0.0,
            "high": float(vals[33]) if vals[33] else 0.0,
            "low": float(vals[34]) if vals[34] else 0.0,
            "turnover_pct": float(vals[38]) if vals[38] else 0.0,
            "pe_ttm": float(vals[39]) if vals[39] else 0.0,
            "mcap_yi": float(vals[44]) if vals[44] else 0.0,
            "float_mcap_yi": float(vals[45]) if vals[45] else 0.0,
            "pb": float(vals[46]) if vals[46] else 0.0,
            "limit_up": float(vals[47]) if vals[47] else 0.0,
            "limit_down": float(vals[48]) if vals[48] else 0.0,
            "pe_static": float(vals[52]) if vals[52] else 0.0,
        }
    return result


def baidu_kline_with_ma(code: str, start_time: str = "") -> dict[str, Any]:
    """Baidu K-line data with built-in MA5/MA10/MA20.

    Returns:
        {"keys": [...], "rows": [...]} where each row is a semicolon-delimited
        string aligned to ``keys``.
    """
    url = "https://finance.pae.baidu.com/selfselect/getstockquotation"
    params = {
        "all": "1",
        "isIndex": "false",
        "isBk": "false",
        "isBlock": "false",
        "isFutures": "false",
        "isStock": "true",
        "newFormat": "1",
        "group": "quotation_kline_ab",
        "finClientType": "pc",
        "code": code,
        "start_time": start_time,
        "ktype": "1",
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/vnd.finance-web.v1+json",
        "Origin": "https://gushitong.baidu.com",
        "Referer": "https://gushitong.baidu.com/",
    }
    r = requests.get(url, params=params, headers=headers, timeout=10)
    d = r.json()
    result = d.get("Result", {})
    md = result.get("newMarketData", {})
    return {"keys": md.get("keys", []), "rows": md.get("marketData", "").split(";")}


def baidu_kline_to_ohlcv(code: str, start_date: str, end_date: str) -> pd.DataFrame | None:
    """Fetch Baidu K-line and normalize to Vibe-Trading OHLCV schema."""
    start_time = pd.Timestamp(start_date).strftime("%Y%m%d%H%M%S")
    raw = baidu_kline_with_ma(normalize_code(code), start_time=start_time)
    if not raw.get("rows"):
        return None

    keys = raw["keys"]
    col_index = {k: i for i, k in enumerate(keys)}
    required = {"time", "open", "close", "high", "low", "volume"}
    if not required.issubset(set(keys)):
        return None

    rows: list[dict] = []
    for row in raw["rows"]:
        parts = row.split(",")
        if len(parts) < len(keys):
            continue
        time_str = parts[col_index["time"]]
        # Baidu time format: YYYYMMDDHHMMSS for daily bars (00:00:00)
        date_str = time_str[:8]
        rows.append(
            {
                "trade_date": pd.Timestamp(date_str),
                "open": float(parts[col_index["open"]]),
                "high": float(parts[col_index["high"]]),
                "low": float(parts[col_index["low"]]),
                "close": float(parts[col_index["close"]]),
                "volume": float(parts[col_index["volume"]]),
            }
        )

    if not rows:
        return None
    df = pd.DataFrame(rows).set_index("trade_date").sort_index()
    df = df.loc[start_date:end_date]
    df = df.dropna(subset=["open", "high", "low", "close"])
    if "volume" not in df.columns:
        df["volume"] = 0.0
    return df


def mootdx_daily_ohlcv(code: str, start_date: str, end_date: str) -> pd.DataFrame | None:
    """Fetch daily OHLCV via mootdx (TDX TCP protocol) and normalize it.

    mootdx is the preferred A-share quote source in a-stock-data because it
    does not ban IPs.  We request slightly more bars than calendar days in the
    range and then filter by date.
    """
    try:
        from mootdx.quotes import Quotes
    except ImportError as exc:
        raise ImportError("mootdx is required for a_stock_data quotes") from exc

    code = normalize_code(code)
    start = pd.Timestamp(start_date)
    today = pd.Timestamp.now().normalize()
    # A-shares trade ~242 days/year; request enough bars from *today* back to
    # cover the requested start_date, then filter by date.
    calendar_days = max((today - start).days + 1, 30)
    offset = max(int(calendar_days * 1.5), 30)

    client = Quotes.factory(market="std")
    df = client.bars(symbol=code, category=4, offset=offset)
    if df is None or df.empty:
        return None

    # mootdx returns a DatetimeIndex with intraday timestamps; keep only the date part.
    df.index = pd.to_datetime(df.index.date)
    df.index.name = "trade_date"
    df = df[~df.index.duplicated(keep="last")]
    df = df.sort_index()
    df = df.loc[start_date:end_date]

    # mootdx already duplicates `vol` as `volume`; drop the duplicate before renaming.
    if "volume" in df.columns:
        df = df.drop(columns=["volume"])
    df = df.rename(columns={"vol": "volume"})
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    ohlcv_cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[ohlcv_cols].dropna(subset=["open", "high", "low", "close"])
    if "volume" not in df.columns:
        df["volume"] = 0.0
    return df


def fetch_a_share_ohlcv(code: str, start_date: str, end_date: str) -> pd.DataFrame | None:
    """Fetch A-share OHLCV, preferring mootdx and falling back to Baidu."""
    try:
        df = mootdx_daily_ohlcv(code, start_date, end_date)
        if df is not None and not df.empty:
            return df
    except Exception as exc:
        logger = logging.getLogger(__name__)
        logger.debug("mootdx fetch failed for %s: %s", code, exc)

    return baidu_kline_to_ohlcv(code, start_date, end_date)


# ---------------------------------------------------------------------------
# Layer 2: Research reports
# ---------------------------------------------------------------------------

REPORT_API = "https://reportapi.eastmoney.com/report/list"
PDF_TPL = "https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf"


def eastmoney_reports(code: str, max_pages: int = 5) -> list[dict]:
    """Fetch Eastmoney research-report list for a single stock."""
    code = normalize_code(code)
    all_records: list[dict] = []
    for page in range(1, max_pages + 1):
        params = {
            "industryCode": "*",
            "pageSize": "100",
            "industry": "*",
            "rating": "*",
            "ratingChange": "*",
            "beginTime": "2000-01-01",
            "endTime": "2030-01-01",
            "pageNo": str(page),
            "fields": "",
            "qType": "0",
            "orgCode": "",
            "code": code,
            "rcode": "",
            "p": str(page),
            "pageNum": str(page),
            "pageNumber": str(page),
        }
        r = em_get(
            REPORT_API,
            params=params,
            headers={"Referer": "https://data.eastmoney.com/"},
            timeout=30,
        )
        d = r.json()
        rows = d.get("data") or []
        if not rows:
            break
        all_records.extend(rows)
        if page >= (d.get("TotalPage", 1) or 1):
            break
    return all_records


def download_report_pdf(record: dict, target_dir: str = "./reports") -> str | None:
    """Download a single research-report PDF; return saved path or None."""
    info_code = record.get("infoCode", "")
    if not info_code:
        return None
    date = (record.get("publishDate") or "")[:10]
    org = record.get("orgSName") or "未知"
    title = re.sub(r'[\\/:*?"<>|]', "_", record.get("title", ""))[:80]
    fname = f"{date}_{org}_{title}.pdf"
    target = Path(target_dir) / fname
    if target.exists():
        return str(target)
    url = PDF_TPL.format(info_code=info_code)
    r = em_get(url, headers={"Referer": "https://data.eastmoney.com/"}, timeout=60)
    if r.status_code == 200 and len(r.content) >= 1024:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(r.content)
        return str(target)
    return None


# ---------------------------------------------------------------------------
# Layer 5: News
# ---------------------------------------------------------------------------

def eastmoney_stock_news(code: str, page_size: int = 20) -> list[dict]:
    """Per-stock news from Eastmoney search API."""
    code = normalize_code(code)
    cb = "jQuery_news"
    url = "https://search-api-web.eastmoney.com/search/jsonp"
    inner_params = json.dumps(
        {
            "uid": "",
            "keyword": code,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": 1,
                    "pageSize": page_size,
                    "preTag": "",
                    "postTag": "",
                }
            },
        },
        separators=(",", ":"),
    )
    params = {"cb": cb, "param": inner_params}
    headers = {"User-Agent": UA, "Referer": "https://so.eastmoney.com/"}
    r = em_get(url, params=params, headers=headers, timeout=15)

    text = r.text
    try:
        json_str = text[text.index("(") + 1 : text.rindex(")")]
        d = json.loads(json_str)
    except Exception:
        return []

    rows: list[dict] = []
    articles = d.get("result", {}).get("cmsArticleWebOld", []) or []
    for a in articles:
        rows.append(
            {
                "title": re.sub(r"<[^>]+>", "", a.get("title", "")),
                "content": re.sub(r"<[^>]+>", "", a.get("content", ""))[:200],
                "time": a.get("date", ""),
                "source": a.get("mediaName", ""),
                "url": a.get("url", ""),
            }
        )
    return rows


def eastmoney_global_news(page_size: int = 50) -> list[dict]:
    """7x24 global finance news from Eastmoney."""
    url = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
    params = {
        "client": "web",
        "biz": "web_724",
        "fastColumn": "102",
        "sortEnd": "",
        "pageSize": str(page_size),
        "req_trace": str(uuid.uuid4()),
    }
    headers = {"User-Agent": UA, "Referer": "https://kuaixun.eastmoney.com/"}
    r = em_get(url, params=params, headers=headers, timeout=10)
    d = r.json()

    rows: list[dict] = []
    for item in d.get("data", {}).get("fastNewsList", []):
        rows.append(
            {
                "title": item.get("title", ""),
                "summary": item.get("summary", ""),
                "time": item.get("showTime", ""),
                "url": item.get("url", ""),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Layer 6: Fundamentals
# ---------------------------------------------------------------------------

def eastmoney_stock_info(code: str) -> dict:
    """Basic stock profile from Eastmoney push2 API."""
    code = normalize_code(code)
    market_code = 1 if code.startswith("6") else 0
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "fltt": "2",
        "invt": "2",
        "fields": "f57,f58,f84,f85,f127,f116,f117,f189,f43",
        "secid": f"{market_code}.{code}",
    }
    headers = {"User-Agent": UA}
    r = em_get(url, params=params, headers=headers, timeout=10)
    d = r.json().get("data", {})
    return {
        "code": d.get("f57", ""),
        "name": d.get("f58", ""),
        "industry": d.get("f127", ""),
        "total_shares": d.get("f84", 0),
        "float_shares": d.get("f85", 0),
        "mcap": d.get("f116", 0),
        "float_mcap": d.get("f117", 0),
        "list_date": str(d.get("f189", "")),
        "price": d.get("f43", 0),
    }


def sina_financial_report(code: str, report_type: str = "lrb", num: int = 8) -> list[dict]:
    """Sina financial statements (balance/income/cashflow).

    Args:
        report_type: "fzb" (balance), "lrb" (income), "llb" (cash flow).
        num: number of recent reports.
    """
    code = normalize_code(code)
    prefix = "sh" if code.startswith("6") else "sz"
    paper_code = f"{prefix}{code}"
    url = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
    params = {
        "paperCode": paper_code,
        "source": report_type,
        "type": "0",
        "page": "1",
        "num": str(num),
    }
    headers = {"User-Agent": UA}
    r = requests.get(url, params=params, headers=headers, timeout=15)
    report_list = r.json().get("result", {}).get("data", {}).get("report_list", {}) or {}

    rows: list[dict] = []
    for period in sorted(report_list.keys(), reverse=True)[:num]:
        obj = report_list[period]
        rec: dict[str, Any] = {"报告期": f"{period[:4]}-{period[4:6]}-{period[6:8]}"}
        for it in obj.get("data", []) or []:
            title = it.get("item_title", "")
            if not title or it.get("item_value") is None:
                continue
            rec[title] = it.get("item_value")
            tongbi = it.get("item_tongbi")
            if tongbi not in (None, ""):
                rec[f"{title}_同比"] = tongbi
        rows.append(rec)
    return rows


# ---------------------------------------------------------------------------
# Layer 7: Announcements
# ---------------------------------------------------------------------------

def _cninfo_ts_to_date(ts: Any) -> str:
    """Convert cninfo Unix-ms timestamp to date string."""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
    return str(ts)[:10] if ts else ""


_CNINFO_ORGID_MAP: dict[str, str] = {}


def _cninfo_orgid(code: str) -> str:
    """Resolve the real orgId used by cninfo for a given stock."""
    global _CNINFO_ORGID_MAP
    if not _CNINFO_ORGID_MAP:
        try:
            r = requests.get(
                "http://www.cninfo.com.cn/new/data/szse_stock.json",
                headers={"User-Agent": UA},
                timeout=15,
            )
            _CNINFO_ORGID_MAP = {s["code"]: s["orgId"] for s in r.json().get("stockList", [])}
        except Exception as exc:
            print(f"[WARN] cninfo orgId mapping fetch failed, using fallback rules: {exc}")
    org = _CNINFO_ORGID_MAP.get(code)
    if org:
        return org
    if code.startswith("6"):
        return f"gssh0{code}"
    elif code.startswith(("8", "4")):
        return f"gsbj0{code}"
    return f"gssz0{code}"


def cninfo_announcements(code: str, page_size: int = 30) -> list[dict]:
    """Fetch official announcements from cninfo.com.cn."""
    code = normalize_code(code)
    url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    org_id = _cninfo_orgid(code)
    payload = {
        "stock": f"{code},{org_id}",
        "tabName": "fulltext",
        "pageSize": str(page_size),
        "pageNum": "1",
        "column": "",
        "category": "",
        "plate": "",
        "seDate": "",
        "searchkey": "",
        "secid": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    headers = {
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://www.cninfo.com.cn/new/disclosure",
        "Origin": "https://www.cninfo.com.cn",
    }
    r = requests.post(url, data=payload, headers=headers, timeout=15)
    d = r.json()

    rows: list[dict] = []
    for item in d.get("announcements", []) or []:
        rows.append(
            {
                "title": item.get("announcementTitle", ""),
                "type": item.get("announcementTypeName", ""),
                "date": _cninfo_ts_to_date(item.get("announcementTime")),
                "url": (
                    f"https://www.cninfo.com.cn/new/disclosure/detail?"
                    f"annoId={item.get('announcementId', '')}"
                ),
            }
        )
    return rows
