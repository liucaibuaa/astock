"""Parse natural language commands from Feishu messages for Vibe-Trading."""

from __future__ import annotations

import re
from typing import Optional


# Common Chinese stock names that might be mentioned in normal chat
NON_STOCK_PHRASES = {
    "你好", "在吗", "谢谢", "哈喽", "hi", "hello", "test", "测试",
    "好的", "收到", "嗯", "哦", "啊", "好", "行", "ok", "yes", "no",
    "拜拜", "再见", "稍等", "等一下", "来了", "在的",
    # Common words that should NOT be treated as stock names
    "状态", "情况", "结果", "问题", "内容", "数据", "信息",
    "报告", "分析", "预测", "建议", "结论", "原因", "方法",
    "今天", "明天", "昨天", "现在", "最近", "目前", "当前",
    "上涨", "下跌", "涨跌", "涨", "跌", "买", "卖",
    "买入", "卖出", "持有", "观望", "清仓", "减仓", "加仓",
}

# Prefixes users naturally add before a stock name.
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

# Analysis-intent words: if these appear after a stock name, user wants direct analysis
ANALYSIS_INTENT_WORDS = [
    "是否", "可以", "能不能", "能", "适合", "值得", "建议", "应该",
    "怎么", "如何", "为什么", "什么", "多少", "好吗", "行吗", "对吗",
    "短线", "长线", "波段", "持股", "买入", "卖出", "持有", "建仓",
    "加仓", "减仓", "清仓", "止盈", "止损", "解套",
    "涨跌", "上涨", "下跌", "涨吗", "跌吗", "会涨", "会跌",
    "看好", "看空", "观望", "注意", "风险", "机会",
]


def _extract_stock_code(text: str) -> Optional[str]:
    """Extract 6-digit stock code (with optional suffix)."""
    m = re.search(r"\b(\d{6}(?:\.SZ|\.SH|\.HK|\.BJ)?)\b", text)
    if m:
        return m.group(1)
    return None


def _extract_stock_name(text: str) -> Optional[str]:
    """Extract Chinese stock name from text.

    Strategy:
      1. Strip common prefixes.
      2. If the remainder is a clean 2-6 Chinese char name, return it.
      3. Try to match the LEADING 2-6 Chinese chars (stock names usually
         appear at the beginning after prefixes).
      4. Fall back to matching trailing Chinese chars.
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

    # PRIORITY: match the LEADING 2-6 Chinese chars.
    # This handles "京泉华是否可以做超短线" -> "京泉华"
    m = re.match(r"([一-龥]{2,6})", temp)
    if m:
        candidate = m.group(1)
        if candidate not in NON_STOCK_PHRASES:
            return candidate

    # Fallback: look for the last 2-6 Chinese chars
    m = re.search(r"([一-龥]{2,6})(?:\s*$|\s+的|\s+怎么样)", temp)
    if m:
        candidate = m.group(1)
        if candidate not in NON_STOCK_PHRASES:
            return candidate

    return None


def _has_specific_params(text: str) -> bool:
    """Check if the text contains specific analysis parameters or intent."""
    lower = text.lower()
    for marker in SPECIFIC_PARAM_MARKERS:
        if marker.lower() in lower:
            return True
    # Analysis-intent words also count as specific params
    for word in ANALYSIS_INTENT_WORDS:
        if word in lower:
            return True
    return False


def _is_general_chat(text: str) -> bool:
    """Detect general chit-chat messages that are not stock queries."""
    cleaned = text.strip()
    lower = cleaned.lower()

    # Exact match common greetings
    if lower in NON_STOCK_PHRASES:
        return True

    # Very short messages (1-2 chars) that are not stock codes
    if len(cleaned) <= 2 and not re.fullmatch(r"\d{6}", cleaned):
        return True

    # Greeting patterns
    greeting_patterns = [
        r"^(你好|在吗|哈喽|hi|hello|hey|早上好|中午好|晚上好|谢谢|感谢|辛苦了)",
        r"^(拜拜|再见|拜|回见|下次见)",
    ]
    for pat in greeting_patterns:
        if re.search(pat, lower):
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

    # Clear history command
    if lower in ("清空", "重置", "清除", "clear", "reset"):
        return {"type": "clear", "args": {}}

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

    # Live trading commands (must be checked BEFORE stock extraction)
    # Support both "live status" and "/live status"
    live_text = re.sub(r"^/", "", lower)
    live_match = re.match(r"live(?:\s+(.+))?", live_text)
    if live_match and (live_match.group(1) or live_text == "live"):
        sub = live_match.group(1).strip()
        sub_map = {
            "状态": "status", "status": "status",
            "启动": "start", "start": "start",
            "停止": "stop", "stop": "stop",
            "紧急停止": "halt", "halt": "halt", "急停": "halt",
            "恢复": "resume", "resume": "resume",
        }
        for key, action in sub_map.items():
            if sub == key or sub.startswith(key):
                return {"type": "live", "args": {"action": action}}

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

    # General chit-chat
    if _is_general_chat(cleaned):
        return {"type": "chat", "args": {"text": cleaned}}

    # No stock detected -> treat as free-form prompt
    return {
        "type": "run",
        "args": {
            "prompt": cleaned,
            "max_iter": 50,
        },
    }
