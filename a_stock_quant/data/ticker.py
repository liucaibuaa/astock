"""A-stock ticker resolution — ported from tradingagents-astock.

Supports: 6-digit codes, SH/SZ/BJ prefix/suffix variants, Chinese stock names.
"""


import logging
import re as _re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Market prefix
# ---------------------------------------------------------------------------


def get_prefix(code: str) -> str:
    """6-digit A-stock code -> market prefix for Tencent API."""
    if code.startswith(("6", "9")):
        return "sh"
    elif code.startswith("8"):
        return "bj"
    return "sz"


def _normalize_ticker(symbol: str) -> str:
    """Strip exchange prefix/suffix, return pure 6-digit code.

    Handles: '688017', 'SH688017', '688017.SH', 'sh688017'
    """
    s = symbol.strip().upper()
    for suffix in (".SH", ".SZ", ".BJ"):
        if s.endswith(suffix):
            s = s[:-len(suffix)]
            break
    for prefix in ("SH", "SZ", "BJ"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    # Validate: only allow alphanumeric, dot, dash, underscore, caret
    cleaned = s.strip()
    if not cleaned or not _re.match(r"^[A-Za-z0-9._\-\^]+$", cleaned):
        raise ValueError(f"Invalid ticker characters: {s!r}")
    if cleaned.replace(".", "") == "":
        raise ValueError(f"Ticker is all dots: {s!r}")
    if len(cleaned) > 32:
        raise ValueError(f"Ticker too long: {s!r}")
    return cleaned


# ---------------------------------------------------------------------------
# Stock name <-> code mapping (cached, lazy-init via mootdx)
# ---------------------------------------------------------------------------

_name_to_code: dict[str, str] | None = None
_code_to_name: dict[str, str] | None = None


def _build_name_code_map() -> tuple[dict[str, str], dict[str, str]]:
    """Build name->code and code->name maps via mootdx (both SH & SZ markets)."""
    global _name_to_code, _code_to_name
    if _name_to_code is not None:
        return _name_to_code, _code_to_name

    try:
        from mootdx.quotes import Quotes

        client = Quotes.factory(market="std")
        n2c: dict[str, str] = {}
        c2n: dict[str, str] = {}

        for market in (0, 1):  # 0=SZ, 1=SH
            try:
                stocks = client.stocks(market=market)
                if stocks is None or stocks.empty:
                    continue
                for _, row in stocks.iterrows():
                    code = str(row["code"]).strip()
                    name = str(row["name"]).strip()
                    if not _re.match(r"^[036]\d{5}$", code):
                        continue
                    clean_name = name.replace(" ", "").replace("　", "")
                    n2c[clean_name] = code
                    c2n[code] = clean_name
            except Exception:
                logger.warning("Failed to fetch stocks for market %d", market)

        _name_to_code = n2c
        _code_to_name = c2n
        logger.info("Built stock name-code map: %d entries", len(n2c))
    except ImportError:
        logger.warning("mootdx not available; Chinese name resolution disabled")
        _name_to_code = {}
        _code_to_name = {}
    except Exception:
        logger.exception("Failed to build name-code map")
        _name_to_code = {}
        _code_to_name = {}

    return _name_to_code, _code_to_name


def resolve_ticker(user_input: str) -> str:
    """Resolve user input (code or Chinese name) to a 6-digit A-stock code.

    Accepts: '600379', 'SH600379', '600379.SH', '宝光股份'
    Returns: '600379'
    Raises: ValueError if not resolvable.
    """
    s = user_input.strip()
    if not s:
        raise ValueError("Input cannot be empty")

    has_chinese = any("一" <= ch <= "鿿" for ch in s)

    if not has_chinese:
        return _normalize_ticker(s)

    clean = s.replace(" ", "").replace("　", "")
    n2c, _ = _build_name_code_map()

    if not n2c:
        raise ValueError(
            f"Cannot resolve Chinese stock name '{s}': "
            "mootdx is not available for name-to-code mapping. "
            "Please use a 6-digit stock code instead."
        )

    if clean in n2c:
        return n2c[clean]

    matches = {name: code for name, code in n2c.items() if clean in name}
    if len(matches) == 1:
        return next(iter(matches.values()))
    if len(matches) > 1:
        examples = ", ".join(f"{n}({c})" for n, c in list(matches.items())[:5])
        raise ValueError(
            f"'{s}' matches multiple stocks: {examples}. "
            "Please enter the full name or code."
        )

    raise ValueError(f"Cannot find stock '{s}'. Please check the name.")


def resolve_watchlist(items: list[str]) -> list[str]:
    """Batch resolve a list of user inputs to 6-digit codes."""
    return [resolve_ticker(item) for item in items]
