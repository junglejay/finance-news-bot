# 商品与风控情报晨报机器人

这是一个由 GitHub Actions 驱动的无状态中文晨报机器人。它在每个工作日北京时间 07:30 运行一次，采集商品、期货、原油、黄金、财务舞弊和内部控制相关情报，经 OpenAI 兼容的 AI 网关提炼后推送至钉钉自定义机器人。

每次任务仅在 GitHub 托管运行器的内存中处理数据：不部署服务、不创建 SQLite 文件、不上传运行状态、不保存新闻正文或简报历史。任务会在本次运行内按 URL/内容哈希去重，并按最近 24 小时（周一覆盖最近 72 小时）抓取信息。

## 信息来源

- Forbes 公开 RSS；
- 已授权的 Financial Times RSS，或发送至专用邮箱的 FT 官方邮件通讯；
- Google Scholar Alert 邮件；
- SEC 会计与审计执法公告、SEC 新闻稿，以及 PCAOB 新闻公告。

FT 仅支持授权 RSS 或官方邮件标题/链接，不抓取或绕过付费墙。

## GitHub Actions 配置

工作流文件是 [morning-brief.yml](.github/workflows/morning-brief.yml)。其中的 `30 23 * * 0-4` 是 UTC 时间，对应北京时间周一至周五 07:30；也可以在 GitHub 的 Actions 页面使用 **Run workflow** 手动触发。

在 GitHub 仓库的 **Settings → Secrets and variables → Actions** 中添加以下 Secrets：

| Secret | 必填 | 用途 |
| --- | --- | --- |
| `AI_API_KEY` | 是 | OpenAI 兼容 AI 网关的密钥 |
| `DINGTALK_WEBHOOK` | 可选 | 钉钉机器人 Webhook；留空时在 Actions 日志中打印简报 |
| `DINGTALK_SECRET` | 可选 | 仅当机器人使用“加签”安全设置时需要；可作为本地 `.env` 配置 |
| `SEC_USER_AGENT` | 是 | 含联系方式的 SEC 请求标识，例如 `newsbot/1.0 contact=you@example.com` |
| `SCHOLAR_IMAP_HOST`、`SCHOLAR_IMAP_USERNAME`、`SCHOLAR_IMAP_PASSWORD` | 可选 | 接收 Scholar Alert 的专用邮箱 IMAP 参数 |
| `SCHOLAR_IMAP_PORT` | 可选 | 默认为 `993` |
| `FT_FEED_URL` | 可选 | 合法授权的 FT RSS 地址 |
| `FT_EMAIL_SENDER` | 可选 | 同一 IMAP 邮箱中 FT 官方通讯的发件人地址 |

`AI_BASE_URL` 与 `AI_MODEL` 由代码默认设为 `https://minitoken.top/v1` 和 `deepseek-v4-flash`；本地运行时可通过 `.env` 覆盖。

Scholar 邮箱启用后，建议创建以下 Google Scholar 提醒：

```text
commodity futures
crude oil price
gold safe haven
financial statement fraud
internal control
forensic accounting
```

## 本地验证

复制环境变量模板并填入测试凭据：

```bash
cp .env.example .env
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
python -m app.cli run-once
```

最后一条命令会调用真实来源与 AI 网关；设置 `DINGTALK_WEBHOOK` 时发送钉钉消息，未设置时直接打印简报。测试不会调用外部服务。

## 失败处理

- 每个来源独立超时并重试三次；单一来源失败不会阻止其他来源处理。
- AI 网关返回内容必须通过 JSON Schema 与来源链接校验；连续失败三次时，不发送未经验证的简报，而是在钉钉或标准输出中给出故障通知。
- 输出始终带有“非投资建议”声明。
