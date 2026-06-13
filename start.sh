#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Vibe-Trading 一键启动脚本
# 同时启动后端 API (port 8899) 和前端 dev server (port 5899)
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_PORT=8899
FRONTEND_PORT=5899

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log() {
    echo -e "${GREEN}[Vibe-Trading]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[Vibe-Trading]${NC} $1"
}

err() {
    echo -e "${RED}[Vibe-Trading]${NC} $1"
}

# 清理函数：脚本退出时杀死后台进程
cleanup() {
    echo
    log "正在停止服务..."
    if [[ -n "${BACKEND_PID:-}" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
        kill "$BACKEND_PID" 2>/dev/null || true
        wait "$BACKEND_PID" 2>/dev/null || true
        log "后端已停止 (PID: $BACKEND_PID)"
    fi
    log "所有服务已停止"
}
trap cleanup EXIT INT TERM

# 探测 conda 初始化脚本
CONDA_SH=""
for path in "$HOME/miniconda3/etc/profile.d/conda.sh" \
            "$HOME/anaconda3/etc/profile.d/conda.sh" \
            "/home/liucai/miniconda3/etc/profile.d/conda.sh" \
            "/home/liucai/anaconda3/etc/profile.d/conda.sh"; do
    if [[ -f "$path" ]]; then
        CONDA_SH="$path"
        break
    fi
done

if [[ -z "$CONDA_SH" ]]; then
    err "未找到 conda 初始化脚本，请确认 conda 已安装"
    err "常见路径: $HOME/miniconda3/etc/profile.d/conda.sh"
    exit 1
fi

source "$CONDA_SH"

if ! conda activate vibe_trading 2>/dev/null; then
    err "无法激活 conda 环境 'vibe_trading'"
    exit 1
fi
log "Conda 环境已激活: vibe_trading"

# 检查端口占用
if command -v ss >/dev/null 2>&1; then
    if ss -tln | grep -q ":$BACKEND_PORT "; then
        warn "端口 $BACKEND_PORT 已被占用，后端可能已在运行"
    fi
    if ss -tln | grep -q ":$FRONTEND_PORT "; then
        warn "端口 $FRONTEND_PORT 已被占用，前端可能已在运行"
    fi
fi

# 启动后端（后台）
cd "$SCRIPT_DIR"
log "正在启动后端 API (port $BACKEND_PORT)..."
vibe-trading serve --port "$BACKEND_PORT" > /tmp/vibe-trading-backend.log 2>&1 &
BACKEND_PID=$!

# 等待后端启动
for i in {1..30}; do
    if grep -q "Application startup complete" /tmp/vibe-trading-backend.log 2>/dev/null; then
        break
    fi
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        err "后端启动失败，请查看日志: /tmp/vibe-trading-backend.log"
        exit 1
    fi
    sleep 1
done

if kill -0 "$BACKEND_PID" 2>/dev/null; then
    log "后端已启动 → http://localhost:$BACKEND_PORT (PID: $BACKEND_PID)"
else
    err "后端启动超时或失败，请查看日志: /tmp/vibe-trading-backend.log"
    exit 1
fi

# 启动前端（前台，Ctrl+C 会停掉它，然后 cleanup 停后端）
cd "$SCRIPT_DIR/frontend"
log "正在启动前端 dev server (port $FRONTEND_PORT)..."
warn "前端日志会直接输出到终端，按 Ctrl+C 可同时停止前后端"
echo -e "${BLUE}============================================================${NC}"
echo -e "${GREEN}  Vibe-Trading 启动完成${NC}"
echo -e "${GREEN}  打开浏览器访问: http://localhost:$FRONTEND_PORT${NC}"
echo -e "${BLUE}============================================================${NC}"
npm run dev -- --host --port "$FRONTEND_PORT"
