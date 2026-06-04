"""Parse natural language commands from Feishu messages for Vibe-Trading."""

from __future__ import annotations

import re
from typing import Optional


# Common Chinese stock names that might be mentioned in normal chat
# In production, this should be replaced with a real stock database lookup
NON_STOCK_PHRASES = {"你好", "在吗", "谢谢", "哈喽", "hi", "hello", "test", "测试", "好的", "收到"}

# Prefixes users naturally add before a stock name.
# Sorted by length DESC so longer prefixes match first.
STOCK_PREFIXES = [
    "请你帮我分析", "请帮我分析", "请帮我查", "请帮我预测",
    "帮我分析", "帮我看看", "帮我查", "帮我预测", "帮我",
    "给我", "分析一下", "看一下", "分析", "看看", "预测", "评估",
    "请分析", "请预测", "请评估", "请查看", "请查询", "请",
    "能分析", "能帮我", "能不能帮我", "能不能",
    "我想分析", "我想预测", "我想看", "我想要", "我想",
    "如何", "怎么样", "怎么",
]

# Signals that the user has given a SPECIFIC analysis request (not just a stock name)
SPECIFIC_PARAM_MARKERS = [
    "--", "深度", "分析师", "语言", "日期", "策略", "指标",
    "基本面", "技术面", "消息面", "资金面", "政策面",
    "财务", "估值", "K线", "均线", "MACD", "RSI", "布林带",
    "夏普比率", "最大回撤", "波动率", "贝塔", "alpha",
    "202", "20", "-", "到", "从", "区间",
    "多因子", "因子", "量化", "策略", "模型",
    " bullish", "bearish", "long", "short", "buy", "sell",
]


def _extract_stock_code(text: str) -> Optional[str]:
    """Extract 6-digit stock code (with optional suffix)."""
    m = re.search(r"\b(\d{6}(?:\.SZ|\.SH|\.HK|\.BJ)?)\b", text)
    if m:
        return m.group(1)
    return None


def _extract_stock_name(text: str) -> Optional[str]:
    """Extract Chinese stock name from text.

    Looks for 2-6 Chinese characters that are likely a stock name.
    """
    # Remove prefixes to find the core stock name
    temp = text
    for prefix in STOCK_PREFIXES:
        if temp.startswith(prefix):
            temp = temp[len(prefix):].strip()
            break

    # If after stripping prefix we get a clean 2-6 Chinese char name
    if re.fullmatch(r"[一-龥]{2,6}", temp) and temp not in NON_STOCK_PHRASES:
        return temp

    # Also try: look for the last 2-6 Chinese chars in the stripped sentence
    # This handles "看一下贵州茅台" -> "贵州茅台"
    m = re.search(r"([一-龥]{2,6})(?:\s*$|\s+的|\s+怎么样)", temp)
    if m:
        candidate = m.group(1)
        if candidate not in NON_STOCK_PHRASES:
            return candidate

    return None


def _has_specific_params(text: str) -> bool:
    """Check if the text contains specific analysis parameters."""
    lower = text.lower()
    for marker in SPECIFIC_PARAM_MARKERS:
        if marker in lower:
            return True
    return False


MENU_OPTIONS = [
    ("comprehensive", "综合投研分析", ["综合投研", "综合分析", "投研", "全面分析"]),
    ("fundamentals", "基本面分析", ["基本面", "财务", "估值", "财报"]),
    ("technical", "技术面预测", ["技术面", "技术", "趋势", "预测", "k线", "走势"]),
    ("news", "新闻舆情分析", ["新闻", "舆情", "情绪", "消息", "媒体"]),
    ("backtest", "策略回测", ["回测", "策略", "测试", "模拟"]),
]


def parse_menu_selection(text: str) -> Optional[dict]:
    """Parse a menu selection reply (e.g. '1', '2', '基本面').

    Returns {"type": "menu_select", "args": {"analysis_type": "xxx"}} or None.
    """
    text = text.strip()
    if not text:
        return None

    # Number selection: 1-5
    number_map = {"1": "comprehensive", "2": "fundamentals", "3": "technical",
                  "4": "news", "5": "backtest", "6": "help"}
    if text in number_map:
        return {"type": "menu_select", "args": {"analysis_type": number_map[text]}}

    # Keyword selection
    lower = text.lower()
    for analysis_type, label, keywords in MENU_OPTIONS:
        for kw in keywords:
            if kw in lower:
                return {"type": "menu_select", "args": {"analysis_type": analysis_type}}

    # Help keywords
    if lower in ("帮助", "help", "?", "怎么用", "指令", "菜单"):
        return {"type": "menu_select", "args": {"analysis_type": "help"}}

    return None


def parse_command(text: str) -> Optional[dict]:
    """Parse a Feishu message text into a command dict.

    Logic:
      1. Help commands -> help
      2. Extract stock name/code from text
         - If no specific params found -> stock_menu (show options)
         - If specific params found -> run (direct analysis)
      3. Menu selection (1-5 or keywords) -> menu_select
      4. Everything else -> run

    Examples:
      - "贵州茅台"               -> stock_menu
      - "帮我分析天齐锂业"        -> stock_menu
      - "看一下宁德时代"          -> stock_menu
      - "分析天齐锂业基本面"      -> run
      - "回测茅台 2024-01-01"   -> run
      - "计算000001.SZ夏普比率" -> run
      - "1"                     -> menu_select
      - "基本面"                 -> menu_select
    """
    text = text.strip()
    if not text:
        return None

    lower = text.lower()

    # Stop / cancel commands
    if lower in ("终止", "停止", "取消", "中止", "结束", "stop", "cancel", "quit"):
        return {"type": "stop", "args": {}}

    # Help commands (direct)
    if lower in ("帮助", "help", "?", "怎么用", "指令"):
        return {"type": "help", "args": {}}

    # Strip @mentions (e.g. "@_user_1 分析茅台" -> "分析茅台")
    cleaned = re.sub(r"@\S+", "", text).strip()
    if not cleaned:
        return None

    # Check if it's a menu selection first (numbers or keywords)
    menu = parse_menu_selection(cleaned)
    if menu:
        return menu

    # Try to extract a stock code or name
    stock = _extract_stock_code(cleaned) or _extract_stock_name(cleaned)

    if stock:
        # If user gave specific analysis params, treat as direct run
        if _has_specific_params(cleaned):
            return {
                "type": "run",
                "args": {
                    "prompt": cleaned,
                    "max_iter": 50,
                },
            }
        # Otherwise, show the interactive menu
        return {"type": "stock_menu", "args": {"stock": stock}}

    # No stock detected -> treat as free-form prompt
    return {
        "type": "run",
        "args": {
            "prompt": cleaned,
            "max_iter": 50,
        },
    }
