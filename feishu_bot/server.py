"""FastAPI server for Feishu (Lark) Bot webhook — Vibe-Trading edition."""

from __future__ import annotations

import hashlib
import base64
import json
import os
import re
import sys
import threading
import traceback
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

# Ensure project root is on path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Load .env from project root (so FEISHU_APP_ID/SECRET are available)
load_dotenv(_PROJECT_ROOT / ".env", override=True)

from feishu_bot.client import FeishuClient
from feishu_bot.parser import parse_command
from feishu_bot.runner import run_analysis_async

app = FastAPI(title="Vibe-Trading Feishu Bot")

# ── Configuration from env ──────────────────────────────────────────────────

FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "").strip()
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "").strip()
FEISHU_ENCRYPT_KEY = os.environ.get("FEISHU_ENCRYPT_KEY", "").strip()
FEISHU_VERIFICATION_TOKEN = os.environ.get("FEISHU_VERIFICATION_TOKEN", "").strip()

if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
    print("[WARNING] FEISHU_APP_ID or FEISHU_APP_SECRET not set. Bot will not be able to send messages.")
else:
    print(f"[INFO] Feishu bot configured. App ID: {FEISHU_APP_ID[:8]}...")

feishu_client = FeishuClient(FEISHU_APP_ID, FEISHU_APP_SECRET)

# Simple in-memory session: sender_open_id -> last_stock_name
_user_last_stock: dict[str, str] = {}

# Active analysis tasks: chat_id -> {"stop_event": threading.Event(), "prompt": str}
_active_tasks: dict[str, dict] = {}


def _run_analysis_background(chat_id: str, prompt: str, max_iter: int = 50):
    """Run analysis in a background thread and send result to Feishu."""
    stop_event = threading.Event()
    _active_tasks[chat_id] = {"stop_event": stop_event, "prompt": prompt}

    def background_task():
        try:
            result = run_analysis_async(prompt=prompt, max_iter=max_iter, stop_event=stop_event)
            _send_analysis_result(chat_id, result)
        except Exception as e:
            traceback.print_exc()
            feishu_client.send_text_card(
                chat_id=chat_id,
                text=f"❌ 分析过程中出现错误：\n```\n{str(e)}\n```",
            )
        finally:
            _active_tasks.pop(chat_id, None)

    threading.Thread(target=background_task, daemon=True).start()


# ── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


def _run_analysis_background(chat_id: str, prompt: str, max_iter: int = 50):
    """Run analysis in a background thread and send result to Feishu."""
    def background_task():
        try:
            result = run_analysis_async(prompt=prompt, max_iter=max_iter)
            _send_analysis_result(chat_id, result)
        except Exception as e:
            traceback.print_exc()
            feishu_client.send_text_card(
                chat_id=chat_id,
                text=f"❌ 分析过程中出现错误：\n```\n{str(e)}\n```",
            )
    threading.Thread(target=background_task, daemon=True).start()


def _analysis_prompt(stock: str, analysis_type: str) -> str:
    """Build analysis prompt from menu selection."""
    prompts = {
        "comprehensive": f"综合投研分析 {stock}",
        "fundamentals": f"分析 {stock} 的基本面，包括财务状况、行业地位、估值水平",
        "technical": f"预测 {stock} 的技术面走势，包括趋势、支撑阻力位、买卖点",
        "news": f"分析 {stock} 的近期新闻舆情和市场情绪",
        "backtest": f"回测 {stock} 的常见交易策略",
    }
    return prompts.get(analysis_type, f"分析 {stock}")


async def _handle_feishu_event(request: Request) -> JSONResponse:
    """Core handler for Feishu events."""
    body = await request.body()
    try:
        payload: dict = json.loads(body)
    except json.JSONDecodeError:
        return JSONResponse({"code": 0, "msg": "ignored"})

    # 1. URL verification (support both 1.0 and 2.0 formats)
    if payload.get("type") == "url_verification":
        challenge = payload.get("challenge", "")
        return JSONResponse({"challenge": challenge})

    # 2.0 format URL verification
    event = payload.get("event", {})
    if event.get("type") == "url_verification":
        challenge = event.get("challenge", "")
        return JSONResponse({"challenge": challenge})

    # 2. Parse event
    header = payload.get("header", {})
    event_type = header.get("event_type", "")
    event = payload.get("event", {})

    # ── Handle card button clicks ─────────────────────────────────────────────
    # Support both event subscription (2.0) and message card callback (1.0) formats
    is_card_action = False
    action_value = {}
    card_chat_id = ""

    # Format 1: Event subscription 2.0 (card.action.trigger)
    if event_type == "card.action.trigger":
        is_card_action = True
        action_value = event.get("action", {}).get("value", {})
        card_chat_id = action_value.get("chat_id", "") or event.get("context", {}).get("open_chat_id", "")

    # Format 2: Message card callback (direct payload with action.value)
    elif "action" in payload and "value" in payload.get("action", {}):
        is_card_action = True
        action_value = payload.get("action", {}).get("value", {})
        card_chat_id = action_value.get("chat_id", "") or payload.get("open_chat_id", "")

    if is_card_action:
        stock = action_value.get("stock", "")
        analysis_type = action_value.get("analysis_type", "")
        chat_id = card_chat_id

        print(f"[DEBUG] Card action: stock={stock}, type={analysis_type}, chat={chat_id}")

        if not chat_id:
            return JSONResponse({"code": 0, "msg": "no chat_id"})

        if analysis_type == "help":
            try:
                feishu_client.send_text_card(chat_id=chat_id, text=_help_text())
            except Exception as e:
                print(f"[ERROR] Failed to send help: {e}")
            return JSONResponse({"code": 0, "msg": "ok"})

        # IMPORTANT: Must return within 3s for Feishu webhook.
        def _handle_card_action():
            prompt = _analysis_prompt(stock, analysis_type)
            try:
                feishu_client.send_text_card(
                    chat_id=chat_id,
                    text=f"📊 收到请求：\n"
                         f"**股票**: {stock}\n"
                         f"**类型**: {analysis_type}\n"
                         f"⏳ 正在启动 Vibe-Trading 分析，请稍候...",
                )
            except Exception as e:
                print(f"[ERROR] Failed to send message to Feishu: {e}")
                traceback.print_exc()
            _run_analysis_background(chat_id, prompt)

        threading.Thread(target=_handle_card_action, daemon=True).start()
        return JSONResponse({"code": 0, "msg": "ok"})

    # ── Handle text messages ──────────────────────────────────────────────────
    if event_type != "im.message.receive_v1":
        return JSONResponse({"code": 0, "msg": "ignored"})

    message = event.get("message", {})
    sender = event.get("sender", {})

    msg_type = message.get("message_type", "")
    content_raw = message.get("content", "{}")
    chat_id = message.get("chat_id", "")
    sender_id = sender.get("sender_id", {}).get("open_id", "")

    if msg_type != "text":
        return JSONResponse({"code": 0, "msg": "ignored"})

    try:
        content: dict = json.loads(content_raw)
    except json.JSONDecodeError:
        return JSONResponse({"code": 0, "msg": "ignored"})

    text = content.get("text", "").strip()
    print(f"[DEBUG] Received message from {sender_id}: {text!r}")
    if not text:
        return JSONResponse({"code": 0, "msg": "ignored"})

    # Filter out bot self messages
    if sender.get("sender_type") == "app":
        return JSONResponse({"code": 0, "msg": "ignored"})

    # Parse command
    parsed = parse_command(text)
    print(f"[DEBUG] Parsed command: {parsed}")
    if not parsed:
        return JSONResponse({"code": 0, "msg": "ignored"})

    command_type = parsed.get("type")
    args = parsed.get("args", {})

    if command_type == "stock_menu":
        stock = args.get("stock", "")
        # Record last stock for this user
        _user_last_stock[sender_id] = stock
        try:
            feishu_client.send_text_card(
                chat_id=chat_id,
                text=_stock_menu_text(stock),
            )
        except Exception as e:
            print(f"[ERROR] Failed to send menu text: {e}")
            traceback.print_exc()
        return JSONResponse({"code": 0, "msg": "ok"})

    if command_type == "menu_select":
        analysis_type = args.get("analysis_type", "")
        stock = _user_last_stock.get(sender_id, "")

        if not stock:
            try:
                feishu_client.send_text_card(
                    chat_id=chat_id,
                    text="⚠️ 请先输入股票名称（如：贵州茅台），再选择分析类型。",
                )
            except Exception as e:
                print(f"[ERROR] Failed to send message: {e}")
            return JSONResponse({"code": 0, "msg": "ok"})

        if analysis_type == "help":
            feishu_client.send_text_card(chat_id=chat_id, text=_help_text())
            return JSONResponse({"code": 0, "msg": "ok"})

        prompt = _analysis_prompt(stock, analysis_type)
        try:
            feishu_client.send_text_card(
                chat_id=chat_id,
                text=f"📊 收到请求：\n"
                     f"**股票**: {stock}\n"
                     f"**类型**: {analysis_type}\n"
                     f"⏳ 正在启动 Vibe-Trading 分析，请稍候...\n"
                     f"💡 如需终止，请回复 **终止**",
            )
        except Exception as e:
            print(f"[ERROR] Failed to send message to Feishu: {e}")
            traceback.print_exc()

        _run_analysis_background(chat_id, prompt)
        return JSONResponse({"code": 0, "msg": "ok"})

    if command_type == "run":
        prompt = args.get("prompt", "")
        try:
            feishu_client.send_text_card(
                chat_id=chat_id,
                text=f"📊 收到请求：\n"
                     f"**内容**: {prompt[:200]}{'...' if len(prompt) > 200 else ''}\n"
                     f"⏳ 正在启动 Vibe-Trading 分析，请稍候...\n"
                     f"💡 如需终止，请回复 **终止**",
            )
        except Exception as e:
            print(f"[ERROR] Failed to send message to Feishu: {e}")
            traceback.print_exc()

        _run_analysis_background(chat_id, prompt, args.get("max_iter", 50))
        return JSONResponse({"code": 0, "msg": "ok"})

    if command_type == "stop":
        task = _active_tasks.get(chat_id)
        if task:
            task["stop_event"].set()
            try:
                feishu_client.send_text_card(
                    chat_id=chat_id,
                    text="🛑 已发送终止信号，正在停止分析...",
                )
            except Exception as e:
                print(f"[ERROR] Failed to send stop confirmation: {e}")
        else:
            try:
                feishu_client.send_text_card(
                    chat_id=chat_id,
                    text="⚠️ 当前没有正在运行的分析任务。",
                )
            except Exception as e:
                print(f"[ERROR] Failed to send message: {e}")
        return JSONResponse({"code": 0, "msg": "ok"})

    if command_type == "help":
        feishu_client.send_text_card(chat_id=chat_id, text=_help_text())
        return JSONResponse({"code": 0, "msg": "ok"})

    return JSONResponse({"code": 0, "msg": "ok"})


@app.get("/")
@app.post("/")
async def feishu_root(request: Request):
    """Fallback endpoint for Feishu events."""
    if request.method == "GET":
        return JSONResponse({"code": 0, "msg": "ok"})
    return await _handle_feishu_event(request)


@app.get("/feishu/webhook")
@app.post("/feishu/webhook")
async def feishu_webhook(request: Request):
    """Main webhook entry for Feishu events."""
    if request.method == "GET":
        return JSONResponse({"code": 0, "msg": "ok"})
    return await _handle_feishu_event(request)


# ── Result formatting ───────────────────────────────────────────────────────

def _send_analysis_result(chat_id: str, result: dict):
    """Format and send Vibe-Trading analysis result to Feishu chat."""
    import datetime

    status = result.get("status", "unknown")
    prompt = result.get("prompt", "")
    content = result.get("content", "")
    reason = result.get("reason", "")
    run_id = result.get("run_id", "")
    elapsed = result.get("elapsed_seconds", 0)

    status_emoji = {
        "completed": "✅",
        "failed": "❌",
        "timeout": "⏱️",
        "cancelled": "🛑",
        "error": "⚠️",
    }.get(status, "⚪")

    header = f"## {status_emoji} Vibe-Trading 分析结果\n\n"
    header += f"**状态**: {status.upper()}\n"
    header += f"**耗时**: {int(elapsed // 60)}分{int(elapsed % 60)}秒\n"

    if run_id:
        header += f"**Run ID**: `{run_id}`\n"

    header += "\n---\n\n"

    if content:
        text = header + content
    elif reason:
        text = header + f"**原因**: {reason}"
    else:
        text = header + "*无详细输出*"

    # 1. Save full report as local .md file
    report_dir = _PROJECT_ROOT / "reports"
    report_dir.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_prompt = re.sub(r'[\\/:*?"<>|]', "_", prompt)[:50]
    md_path = report_dir / f"{ts}_{safe_prompt}.md"
    try:
        md_path.write_text(text, encoding="utf-8")
        print(f"[INFO] Report saved to {md_path}")
    except Exception as e:
        print(f"[ERROR] Failed to save report: {e}")
        md_path = None

    # 2. Send text summary (paged)
    try:
        feishu_client.send_long_text_card(
            chat_id=chat_id,
            title="TradingAgents 投研报告",
            text=text,
        )
    except Exception as e:
        print(f"[ERROR] Failed to send result card: {e}")

    # 3. Try to upload and send the .md file
    if md_path and md_path.exists():
        try:
            file_key = feishu_client.upload_file(str(md_path))
            if file_key:
                feishu_client.send_file_message(chat_id, file_key)
                print(f"[INFO] File sent to Feishu: {md_path.name}")
        except Exception as e:
            print(f"[ERROR] Failed to send file: {e}")

        # 4. Always tell user the local path
        try:
            feishu_client.send_text_card(
                chat_id=chat_id,
                text=f"📎 **完整报告已保存**\n"
                     f"本地路径：`{md_path}`\n"
                     f"（如需查看完整内容，可直接在服务器上打开此文件）",
            )
        except Exception as e:
            print(f"[ERROR] Failed to send file path: {e}")


def _stock_menu_text(stock: str) -> str:
    return (
        f"**📈 {stock}**\n\n"
        f"请选择分析类型（直接回复序号或关键词）：\n\n"
        f"1️⃣ **综合投研分析** — 全面多维度分析\n"
        f"2️⃣ **基本面分析** — 财务、行业、估值\n"
        f"3️⃣ **技术面预测** — 趋势、支撑阻力、买卖点\n"
        f"4️⃣ **新闻舆情分析** — 近期新闻与市场情绪\n"
        f"5️⃣ **策略回测** — 常见交易策略回测\n"
        f"6️⃣ **帮助** — 查看使用指南\n\n"
        f"💡 **提示**：也可以直接输入完整指令，如 `分析 {stock} 基本面`"
    )


def _help_text() -> str:
    return (
        "**📈 Vibe-Trading 飞书机器人使用指南**\n\n"
        "**发送股票名称**（如：贵州茅台、000001）：\n"
        "→ 弹出分析类型菜单，回复序号即可\n\n"
        "**直接发送完整指令**：\n"
        "`分析贵州茅台的基本面`\n"
        "`回测 茅台 2024-01-01 到 2024-12-31`\n"
        "`计算 000001.SZ 的夏普比率`\n"
        "`帮我写一个突破策略`\n\n"
        "**终止分析**：\n"
        "分析运行期间，回复 `终止` / `停止` / `取消` 即可强制结束\n\n"
        "**说明**：\n"
        "- Vibe-Trading 是通用金融研究 Agent\n"
        "- 支持股票分析、策略回测、代码生成等\n"
        "- 分析耗时 1–5 分钟不等\n\n"
        "**帮助**：发送 `帮助` 查看本消息"
    )
