"""Client for Vibe-Trading live-trading API server (R6 surface endpoints).

Talks to the local API server (default http://127.0.0.1:8000) that is started
with ``vibe-trading serve``.  All endpoints return JSON.
"""

from __future__ import annotations

import os
import requests
from typing import Optional


class LiveClient:
    """Minimal client for live-trading control endpoints."""

    def __init__(self, api_base: Optional[str] = None):
        self.api_base = (api_base or os.environ.get("VIBE_TRADING_API_URL", "http://127.0.0.1:8000")).rstrip("/")

    def _get(self, path: str) -> dict:
        resp = requests.get(f"{self.api_base}{path}", timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: Optional[dict] = None) -> dict:
        resp = requests.post(f"{self.api_base}{path}", json=body or {}, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_status(self) -> dict:
        """GET /live/status – returns broker auth/mandate/runner/halt state."""
        return self._get("/live/status")

    def start_runner(self, broker: str = "robinhood") -> dict:
        """POST /live/runner/start – start the persistent runner."""
        return self._post("/live/runner/start", {"broker": broker, "foreground": False})

    def stop_runner(self, broker: str = "robinhood") -> dict:
        """POST /live/runner/stop – stop the persistent runner."""
        return self._post("/live/runner/stop", {"broker": broker})

    def halt(self) -> dict:
        """POST /live/halt – trip the kill switch (global)."""
        return self._post("/live/halt")

    def resume(self) -> dict:
        """POST /live/resume – clear the kill switch (global)."""
        return self._post("/live/resume")

    @staticmethod
    def format_status(data: dict) -> str:
        """Format LiveStatusResponse JSON into a Feishu-friendly markdown string."""
        lines = ["**📈 Live 实盘交易状态**\n"]

        global_halted = data.get("global_halted", False)
        lines.append(f"**全局紧急停止**: {'🛑 已触发' if global_halted else '✅ 正常'}\n")

        brokers = data.get("brokers", [])
        if not brokers:
            lines.append("⚠️ 未配置任何券商通道。")
            return "\n".join(lines)

        for b in brokers:
            auth = b.get("auth", {})
            mandate = b.get("mandate")
            runner = b.get("runner", {})
            halted = b.get("halted", False)

            broker_name = auth.get("broker", "unknown")
            lines.append(f"\n---\n**券商**: `{broker_name}`")

            # Auth
            if auth.get("is_live_broker"):
                token_ok = "✅ 已授权" if auth.get("oauth_token_present") else "❌ 未授权"
            else:
                token_ok = "⚠️ 未配置"
            lines.append(f"**授权状态**: {token_ok}")

            # Runner
            alive = runner.get("alive", False)
            lines.append(f"**Runner**: {'🟢 运行中' if alive else '🔴 停止'}")
            last_age = runner.get("last_tick_age_seconds")
            if last_age is not None:
                lines.append(f"**最后心跳**: {last_age:.1f} 秒前")

            # Halt
            lines.append(f"**紧急停止**: {'🛑 已触发' if halted else '✅ 正常'}")

            # Mandate
            if mandate:
                lines.append(f"**授权书**: ✅ 有效")
                limits = mandate.get("limits", {})
                lines.append(f"  - 单笔限额: ${limits.get('max_order_notional_usd', 0):,.0f}")
                lines.append(f"  - 总敞口限额: ${limits.get('max_total_exposure_usd', 0):,.0f}")
                lines.append(f"  - 最大杠杆: {limits.get('max_leverage', 0)}x")
                lines.append(f"  - 日交易次数: {limits.get('max_trades_per_day', 0)}")
                exp = mandate.get("expires_in_seconds")
                if exp is not None:
                    lines.append(f"  - 剩余有效: {exp // 3600}h {(exp % 3600) // 60}m")
            else:
                lines.append(f"**授权书**: ⚠️ 未提交（只读模式）")

        return "\n".join(lines)
