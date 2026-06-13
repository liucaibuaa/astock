"""Smoke test for the a-stock-data integration.

Verifies that Vibe-Trading can fetch A-share OHLCV, research reports,
news, fundamentals, and announcements through the new a_stock_data loader.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the agent package importable when running from the repo root.
AGENT_ROOT = Path(__file__).resolve().parent / "agent"
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from backtest.loaders.a_stock_data_loader import DataLoader

TEST_CODE = "600519"  # Kweichow Moutai — liquid, well-covered A-share benchmark


def _section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(title)
    print("=" * 60)


def main() -> int:
    loader = DataLoader()

    _section("1. Availability check")
    if not loader.is_available():
        print("FAIL: a_stock_data loader reports unavailable (SKILL.md not found)")
        return 1
    print(f"OK: loader={loader.name}, markets={loader.markets}, requires_auth={loader.requires_auth}")

    _section("2. OHLCV market data (Baidu K-line)")
    data_map = loader.fetch(
        codes=[f"{TEST_CODE}.SH"],
        start_date="2024-01-01",
        end_date="2024-01-31",
        interval="1D",
    )
    if not data_map:
        print("FAIL: no OHLCV data returned")
        return 1
    df = data_map[f"{TEST_CODE}.SH"]
    print(f"OK: fetched {len(df)} bars")
    print(df.head(3).to_string())

    _section("3. Research reports")
    reports = loader.fetch_reports(TEST_CODE, max_pages=1)
    print(f"OK: fetched {len(reports)} reports")
    for r in reports[:3]:
        print(f"  {r.get('publishDate', '')[:10]} | {r.get('orgSName', '')} | {r.get('title', '')[:60]}")

    _section("4. News")
    news = loader.fetch_news(TEST_CODE, page_size=10)
    print(f"OK: fetched {len(news)} news items")
    for n in news[:3]:
        print(f"  {n.get('time', '')} | {n.get('source', '')} | {n.get('title', '')[:60]}")

    _section("5. Fundamentals")
    fundamentals = loader.fetch_fundamentals(TEST_CODE)
    print("OK: fetched fundamentals")
    for k, v in fundamentals.items():
        print(f"  {k}: {v}")

    _section("6. Announcements")
    announcements = loader.fetch_announcements(TEST_CODE, page_size=10)
    print(f"OK: fetched {len(announcements)} announcements")
    for a in announcements[:3]:
        print(f"  {a.get('date', '')} | {a.get('type', '')} | {a.get('title', '')[:60]}")

    _section("All checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
