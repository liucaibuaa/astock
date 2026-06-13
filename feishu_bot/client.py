"""Feishu (Lark) Open API client."""

from __future__ import annotations

import time
import json
import requests
from pathlib import Path
from typing import Optional


class FeishuClient:
    """Minimal Feishu API client for sending messages."""

    BASE_URL = "https://open.feishu.cn/open-apis"

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._tenant_access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    def _get_tenant_access_token(self) -> str:
        """Get or refresh tenant access token."""
        if self._tenant_access_token and time.time() < self._token_expires_at - 60:
            return self._tenant_access_token

        url = f"{self.BASE_URL}/auth/v3/tenant_access_token/internal"
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json; charset=utf-8"},
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to get token: {data}")

        self._tenant_access_token = data["tenant_access_token"]
        self._token_expires_at = time.time() + data.get("expire", 7200)
        return self._tenant_access_token

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._get_tenant_access_token()}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def send_text_card(self, chat_id: str, text: str):
        """Send an interactive card message to a chat."""
        url = f"{self.BASE_URL}/im/v1/messages"
        payload = {
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": json.dumps(
                {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "title": {
                            "tag": "plain_text",
                            "content": "TradingAgents 投研报告",
                        },
                        "template": "orange",
                    },
                    "elements": [
                        {
                            "tag": "div",
                            "text": {
                                "tag": "lark_md",
                                "content": text,
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        }
        resp = requests.post(
            url,
            headers=self._headers(),
            params={"receive_id_type": "chat_id"},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != 0:
            print(f"[FeishuClient] send message failed: {result}")
        return result

    def send_text(self, chat_id: str, text: str):
        """Send a plain text message."""
        url = f"{self.BASE_URL}/im/v1/messages"
        payload = {
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        resp = requests.post(
            url,
            headers=self._headers(),
            params={"receive_id_type": "chat_id"},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def send_menu_card(self, chat_id: str, stock: str):
        """Send an interactive menu card for selecting analysis type."""
        url = f"{self.BASE_URL}/im/v1/messages"
        payload = {
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": json.dumps(
                {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "title": {
                            "tag": "plain_text",
                            "content": f"📈 {stock}",
                        },
                        "template": "blue",
                    },
                    "elements": [
                        {
                            "tag": "div",
                            "text": {
                                "tag": "lark_md",
                                "content": f"**请选择对 `{stock}` 的分析类型：**",
                            },
                        },
                        {"tag": "hr"},
                        {
                            "tag": "action",
                            "actions": [
                                {
                                    "tag": "button",
                                    "text": {
                                        "tag": "plain_text",
                                        "content": "📊 综合投研分析",
                                    },
                                    "type": "primary",
                                    "value": {
                                        "stock": stock,
                                        "analysis_type": "comprehensive",
                                        "chat_id": chat_id,
                                    },
                                },
                                {
                                    "tag": "button",
                                    "text": {
                                        "tag": "plain_text",
                                        "content": "📈 基本面分析",
                                    },
                                    "type": "default",
                                    "value": {
                                        "stock": stock,
                                        "analysis_type": "fundamentals",
                                        "chat_id": chat_id,
                                    },
                                },
                            ],
                        },
                        {
                            "tag": "action",
                            "actions": [
                                {
                                    "tag": "button",
                                    "text": {
                                        "tag": "plain_text",
                                        "content": "📉 技术面预测",
                                    },
                                    "type": "default",
                                    "value": {
                                        "stock": stock,
                                        "analysis_type": "technical",
                                        "chat_id": chat_id,
                                    },
                                },
                                {
                                    "tag": "button",
                                    "text": {
                                        "tag": "plain_text",
                                        "content": "📰 新闻舆情分析",
                                    },
                                    "type": "default",
                                    "value": {
                                        "stock": stock,
                                        "analysis_type": "news",
                                        "chat_id": chat_id,
                                    },
                                },
                            ],
                        },
                        {
                            "tag": "action",
                            "actions": [
                                {
                                    "tag": "button",
                                    "text": {
                                        "tag": "plain_text",
                                        "content": "🧪 策略回测",
                                    },
                                    "type": "default",
                                    "value": {
                                        "stock": stock,
                                        "analysis_type": "backtest",
                                        "chat_id": chat_id,
                                    },
                                },
                                {
                                    "tag": "button",
                                    "text": {
                                        "tag": "plain_text",
                                        "content": "💡 帮助",
                                    },
                                    "type": "default",
                                    "value": {
                                        "stock": stock,
                                        "analysis_type": "help",
                                        "chat_id": chat_id,
                                    },
                                },
                            ],
                        },
                    ],
                },
                ensure_ascii=False,
            ),
        }
        resp = requests.post(
            url,
            headers=self._headers(),
            params={"receive_id_type": "chat_id"},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != 0:
            print(f"[FeishuClient] send menu card failed: {result}")
        return result

    def _send_card_page(self, chat_id: str, title: str, text: str) -> dict:
        """Send a single interactive card page."""
        url = f"{self.BASE_URL}/im/v1/messages"
        payload = {
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": json.dumps(
                {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "title": {
                            "tag": "plain_text",
                            "content": title,
                        },
                        "template": "orange",
                    },
                    "elements": [
                        {
                            "tag": "div",
                            "text": {
                                "tag": "lark_md",
                                "content": text.strip(),
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        }
        resp = requests.post(
            url,
            headers=self._headers(),
            params={"receive_id_type": "chat_id"},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def upload_file(self, file_path: str) -> Optional[str]:
        """Upload a file to Feishu and return the file_key.

        Requires 'im:resource:upload' permission.
        Returns None if upload fails.
        """
        url = f"{self.BASE_URL}/im/v1/files"
        fp = Path(file_path)
        # Feishu does not recognise .md extension well; rename to .txt for upload
        upload_name = fp.name
        if upload_name.endswith(".md"):
            upload_name = upload_name[:-3] + ".txt"

        try:
            with open(file_path, "rb") as f:
                resp = requests.post(
                    url,
                    headers={"Authorization": f"Bearer {self._get_tenant_access_token()}"},
                    data={"file_type": "stream", "file_name": upload_name},
                    files={"file": (upload_name, f, "application/octet-stream")},
                    timeout=60,
                )
            resp.raise_for_status()
            result = resp.json()
            if result.get("code") == 0:
                file_key = result.get("data", {}).get("file_key")
                print(f"[FeishuClient] uploaded file: {file_key}")
                return file_key
            else:
                print(f"[FeishuClient] upload failed: {result}")
        except requests.exceptions.HTTPError as e:
            print(f"[FeishuClient] upload HTTP error: {e}")
            try:
                body = e.response.json()
                print(f"[FeishuClient] error response body: {json.dumps(body, ensure_ascii=False)}")
            except Exception:
                print(f"[FeishuClient] raw response: {e.response.text[:500]}")
        except Exception as e:
            print(f"[FeishuClient] upload exception: {e}")
        return None

    def send_file_message(self, chat_id: str, file_key: str) -> dict:
        """Send a file message to a chat."""
        url = f"{self.BASE_URL}/im/v1/messages"
        payload = {
            "receive_id": chat_id,
            "msg_type": "file",
            "content": json.dumps({"file_key": file_key}, ensure_ascii=False),
        }
        resp = requests.post(
            url,
            headers=self._headers(),
            params={"receive_id_type": "chat_id"},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def send_long_text_card(self, chat_id: str, title: str, text: str, max_len: int = 4000):
        """Send long text by splitting into multiple interactive cards.

        Default max_len=4000 is a safe limit for Feishu interactive cards.
        If a single page exceeds the limit and Feishu returns an error,
        we automatically halve the chunk size and retry.
        """
        # Try single message first
        if len(text) <= max_len:
            try:
                return self._send_card_page(chat_id, title, text)
            except Exception as e:
                # If single message fails (likely too long), fall through to paging
                print(f"[FeishuClient] single message failed, falling back to paging: {e}")

        # Split by paragraphs, respecting max_len
        chunks = []
        current = ""
        for para in text.split("\n"):
            if len(current) + len(para) + 1 > max_len and current:
                chunks.append(current)
                current = para
            else:
                current = current + "\n" + para if current else para
        if current:
            chunks.append(current)

        results = []
        total = len(chunks)
        for i, chunk in enumerate(chunks, 1):
            header_title = title if i == 1 else f"{title} (续 {i}/{total})"
            try:
                result = self._send_card_page(chat_id, header_title, chunk)
                results.append(result)
                if result.get("code") != 0:
                    print(f"[FeishuClient] page {i}/{total} failed: {result}")
            except Exception as e:
                print(f"[FeishuClient] page {i}/{total} exception: {e}")
                # If even a single paragraph is too long, force-split it roughly in half
                if len(chunk) > max_len // 2:
                    mid = len(chunk) // 2
                    # Try to split at a newline near the middle
                    split_at = chunk.rfind("\n", mid - 200, mid + 200)
                    if split_at == -1:
                        split_at = mid
                    first_half = chunk[:split_at]
                    second_half = chunk[split_at:]
                    try:
                        r1 = self._send_card_page(chat_id, f"{header_title} (上)", first_half)
                        results.append(r1)
                    except Exception as e2:
                        print(f"[FeishuClient] force-split first half failed: {e2}")
                    try:
                        r2 = self._send_card_page(chat_id, f"{header_title} (下)", second_half)
                        results.append(r2)
                    except Exception as e2:
                        print(f"[FeishuClient] force-split second half failed: {e2}")
                else:
                    raise
        return results
