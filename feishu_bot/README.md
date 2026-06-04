# TradingAgents-Astock 飞书机器人

将 TradingAgents-Astock 多 Agent 投研框架接入飞书（Lark）群聊/单聊对话窗口。

## 功能

- 在飞书群里 `@机器人 分析 贵州茅台`，自动启动多 Agent 投研流程
- 支持中文股票名、6 位代码、拼音
- 支持自定义分析日期、辩论深度、分析师组合
- 分析完成后以富文本卡片形式推送报告摘要
- 完整 Markdown 报告自动保存到服务器

## 快速开始

### 1. 安装依赖

```bash
cd /path/to/TradingAgents-astock

# 安装主项目（如果尚未安装）
pip install -e .

# 安装 Bot 额外依赖
pip install -r feishu_bot/requirements.txt
```

### 2. 配置飞书应用

1. 访问 [飞书开放平台](https://open.feishu.cn/)
2. 创建**企业自建应用**
3. **权限管理** → 申请以下权限：
   - `im:message:send_as_bot`
   - `im:message`
4. **应用功能** → **机器人** → 启用机器人
5. **开发配置** → **事件与回调**：
   - 添加事件：`im.message.receive_v1`
   - 请求地址：`https://your-domain.com/feishu/webhook`
   - 建议关闭"加密"（本 Bot 暂未实现 AES 解密，可通过 HTTPS 保障安全）
6. 记录 **App ID** 和 **App Secret**
7. **版本管理与发布** → 创建版本 → 申请发布 → 将机器人拉入群聊

### 3. 配置环境变量

创建 `.env` 文件（或直接在服务器环境变量中设置）：

```bash
# 飞书应用凭证（必填）
FEISHU_APP_ID=cli_xxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 可选：事件订阅加密/验证
# FEISHU_ENCRYPT_KEY=
# FEISHU_VERIFICATION_TOKEN=

# TradingAgents 原有配置也写在 .env 中
OPENAI_API_KEY=sk-...
# DEEPSEEK_API_KEY=...
# ANTHROPIC_API_KEY=...
```

### 4. 启动服务

```bash
# 开发模式
uvicorn feishu_bot.server:app --host 0.0.0.0 --port 8000 --reload

# 生产模式（建议配合 systemd / docker / pm2）
uvicorn feishu_bot.server:app --host 0.0.0.0 --port 8000 --workers 2
```

确保 `https://your-domain.com/feishu/webhook` 能从公网访问到该服务。

## 使用方式

在飞书群里 @机器人 或私聊机器人：

```
分析 贵州茅台
分析 000001 2025-06-01
分析 宁德时代 --深度=5
分析 五粮液 --分析师=市场,新闻,基本面 --深度=2
帮助
```

### 命令参数

| 参数 | 示例 | 说明 |
|------|------|------|
| 股票 | `贵州茅台`, `000001`, `宁德时代` | 中文名、6位代码均可 |
| 日期 | `2025-06-01` | 可选，默认今天 |
| `--深度` | `--深度=3` | 辩论轮数 1–5，默认 3 |
| `--分析师` | `--分析师=市场,情绪,新闻` | 指定参与的分析师 |
| `--语言` | `--语言=English` | 输出语言，默认中文 |

### 可用分析师

- `市场` / `market`
- `情绪` / `social`
- `新闻` / `news`
- `基本面` / `fundamentals`
- `政策` / `policy`（A 股特化）
- `游资` / `hot_money`（A 股特化）
- `解禁` / `lockup`（A 股特化）

## 架构说明

```
Feishu 消息 → FastAPI /feishu/webhook
                → 立即返回 200 + "分析中..."（满足 3 秒超时）
                → 后台线程启动 TradingAgentsGraph
                → 分析完成后调用飞书 API 推送卡片消息
```

## 部署建议

### Docker 部署

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . .
RUN pip install -e . && pip install -r feishu_bot/requirements.txt

EXPOSE 8000
CMD ["uvicorn", "feishu_bot.server:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Nginx 反向代理

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    location /feishu/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 生产环境注意事项

1. **HTTPS 必须**：飞书事件订阅要求公网 HTTPS 地址
2. **单实例限制**：当前使用后台 `threading.Thread`，同一进程内运行。如果并发量大，建议：
   - 引入 Redis + Celery / RQ 做异步任务队列
   - 或部署为单实例服务（分析本身是 CPU/IO 密集型，多实例同时跑意义不大）
3. **日志监控**：Bot 运行日志打印在 stdout，建议接入 `journalctl` 或 Docker logs
4. **报告持久化**：报告默认保存在 `./data/results/` 下，可挂载到 NAS/S3

## 进阶：接入 Redis 队列（可选）

当团队多人同时使用时，后台线程模型可能会阻塞。可以替换 `server.py` 中的 `threading.Thread` 为任务队列：

```python
# 使用 Celery
from celery import Celery
app = Celery('tasks', broker='redis://localhost:6379/0')

@app.task
def run_analysis_task(chat_id, ticker, date):
    ...

# server.py 中
run_analysis_task.delay(chat_id, ticker, date)
```

## 文件说明

| 文件 | 职责 |
|------|------|
| `server.py` | FastAPI 服务入口，接收飞书事件 |
| `client.py` | 飞书 Open API 封装（发消息、获取 Token） |
| `parser.py` | 解析用户自然语言命令 |
| `runner.py` | 调用 `TradingAgentsGraph` 执行分析 |
| `requirements.txt` | Bot 额外依赖 |
