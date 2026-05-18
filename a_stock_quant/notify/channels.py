"""Notification dispatch: console, sound, webhook channels."""


import asyncio
import logging

from a_stock_quant.storage.models import AlertEvent, AlertSeverity

logger = logging.getLogger(__name__)


class NotificationManager:
    """Routes AlertEvents to enabled notification channels."""

    def __init__(self, config: dict):
        self._cfg = config.get("notifications", {})
        self._console = self._cfg.get("console", {}).get("enabled", True)
        self._sound = self._cfg.get("sound", {}).get("enabled", False)
        self._sound_file = self._cfg.get("sound", {}).get("sound_file")
        self._webhook = self._cfg.get("webhook", {}).get("enabled", False)
        self._dingtalk_url = self._cfg.get("webhook", {}).get("dingtalk_url")
        self._wechat_url = self._cfg.get("webhook", {}).get("wechat_work_url")

    async def send(self, alert: AlertEvent) -> None:
        tasks = []
        if self._console:
            tasks.append(self._send_console(alert))
        if self._sound:
            tasks.append(self._send_sound(alert))
        if self._webhook:
            tasks.append(self._send_webhook(alert))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _send_console(self, alert: AlertEvent) -> None:
        emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(
            alert.severity.value, ""
        )
        print(
            f"{emoji} [{alert.triggered_at.strftime('%H:%M:%S')}] "
            f"{alert.severity.value.upper()} | {alert.name}({alert.code}) | "
            f"{alert.message}"
        )

    async def _send_sound(self, alert: AlertEvent) -> None:
        try:
            if self._sound_file:
                import subprocess
                subprocess.run(
                    ["aplay", self._sound_file],
                    capture_output=True, timeout=5,
                )
            else:
                print("\a")  # terminal bell
        except Exception as e:
            logger.debug("Sound notification failed: %s", e)

    async def _send_webhook(self, alert: AlertEvent) -> None:
        import aiohttp

        color = {"info": "#2196F3", "warning": "#FF9800", "critical": "#F44336"}.get(
            alert.severity.value, "#9E9E9E"
        )

        # DingTalk markdown format
        if self._dingtalk_url:
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "title": f"A-Stock Alert: {alert.name}",
                    "text": (
                        f"## {alert.severity.value.upper()}: {alert.name}({alert.code})\n\n"
                        f"- **Price**: {alert.price:.2f}\n"
                        f"- **Change**: {alert.change_pct:+.2f}%\n"
                        f"- **Message**: {alert.message}\n"
                        f"- **Time**: {alert.triggered_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    ),
                },
            }
            try:
                async with aiohttp.ClientSession() as session:
                    await session.post(
                        self._dingtalk_url, json=payload,
                        timeout=aiohttp.ClientTimeout(total=10),
                    )
            except Exception as e:
                logger.warning("DingTalk webhook failed: %s", e)

        # WeChat Work markdown format
        if self._wechat_url:
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "content": (
                        f"## <font color=\"{color}\">{alert.severity.value.upper()}</font>: "
                        f"{alert.name}({alert.code})\n"
                        f">Price: <font color=\"comment\">{alert.price:.2f}</font>\n"
                        f">Change: <font color=\"comment\">{alert.change_pct:+.2f}%</font>\n"
                        f">Message: {alert.message}\n"
                        f">Time: {alert.triggered_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    ),
                },
            }
            try:
                async with aiohttp.ClientSession() as session:
                    await session.post(
                        self._wechat_url, json=payload,
                        timeout=aiohttp.ClientTimeout(total=10),
                    )
            except Exception as e:
                logger.warning("WeChat Work webhook failed: %s", e)
