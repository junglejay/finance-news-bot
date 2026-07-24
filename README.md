# 商品与风控情报深度阅读机器人

这是一个由 GitHub Actions 驱动的无状态中文研究机器人。它每天北京时间 07:30 运行，收集能源、金属、农产品、宏观市场驱动、财务舞弊和内部控制相关的一手公告与权威公开订阅源；读取可公开访问的全文后，使用 OpenAI 兼容的 AI 网关逐篇生成中文深度阅读，再投递到钉钉机器人。

每次任务只在 GitHub 托管运行器的内存中处理数据：不部署服务、不创建 SQLite 文件、不保存原文、报告或投递记录。运行时会在内存内按 URL/内容哈希去重，并抓取最近 24 小时的信息（周一覆盖最近 72 小时）。

## 输出方式

输出不再是“几条判断 + 卡片式简报”。每次成功运行会选择 1 至 4 篇公开可读且相关性最高的文章，逐篇输出：

- 文章的核心命题；
- 可追溯的事实链；
- 多段中文深度解读；
- 商品、期货或公司风险的条件式传导观察；
- 反证、局限与下一步核验事项。

AI 只能使用已收集的标题、摘要和本次临时读取的正文；每一篇分析的来源、日期与原文链接都必须与输入条目一致。正文一律转述，不会大段引用来源内容。

程序会按商品、宏观驱动、财务风险和研究四类建立候选池，分类配额分别为 6、4、4、2，单次最多 16 条。候选不足 12 条时，会优先保留央行、监管机构、能源统计机构等第一方来源；普通来源中没有明确相关性的文章不会被用于强行凑数。

## 默认信息源

- Forbes 公开 RSS；
- 美国能源信息署（EIA）`Today in Energy` 与新闻稿 RSS；
- 美联储新闻稿 RSS；
- 国际清算银行（BIS）新闻稿与统计发布 RSS；
- 欧洲中央银行（ECB）新闻、讲话与统计发布 RSS；
- 美国商品期货交易委员会（CFTC）新闻稿；
- SEC 会计与审计执法公告、SEC 新闻稿；
- PCAOB 新闻稿；
- 已授权的 Financial Times RSS 或官方邮件通讯（可选）；
- Google Scholar Alert 邮件（可选）。

Financial Times 与 Google Scholar 链接仅保留已授权订阅提供的标题、摘要和链接；程序不会抓取其受保护正文，也不会尝试绕过付费墙或登录限制。其他来源的正文也只会从来源官网公开页面读取，且仅在本次任务的内存中保留。

BIS 与 ECB 已作为默认来源启用，`bis.org` 和 `ecb.europa.eu` 也已加入公开正文域名白名单，无需再配置 Actions variables。

若要加入自己的其他权威公开订阅源，可设置 `EXTRA_RSS_FEEDS`：每行一个 `来源名称|https://rss-url`。若希望这些额外来源也参与“深度阅读”，再在 `EXTRA_ARTICLE_DOMAINS` 中用逗号列出其公开正文域名。只应加入无需登录、没有付费墙且你有权访问的域名。在 GitHub 上建议把这两个值设为仓库的 **Actions variables**，而不是 Secret。

```text
EXTRA_RSS_FEEDS=International Energy Agency|https://example.org/news/rss.xml
EXTRA_ARTICLE_DOMAINS=example.org
```

## GitHub Actions 配置

工作流文件是 [.github/workflows/morning-brief.yml](.github/workflows/morning-brief.yml)。其中的 `30 23 * * *` 是 UTC 时间，对应北京时间每天 07:30；也可以在 GitHub 的 Actions 页面使用 **Run workflow** 手动触发。

在仓库的 **Settings → Secrets and variables → Actions** 中添加：

| Secret | 必填 | 用途 |
| --- | --- | --- |
| `AI_API_KEY` | 是 | OpenAI 兼容 AI 网关的密钥 |
| `DINGTALK_WEBHOOK` | 否 | 钉钉机器人 Webhook；留空时把完整深度阅读打印到 Actions 日志 |
| `DINGTALK_SECRET` | 否 | 仅在钉钉机器人启用“加签”安全设置时需要 |
| `SEC_USER_AGENT` | 是 | 含联系邮箱的 SEC/CFTC 请求标识，例如 `newsbot/1.0 contact=you@example.com` |
| `SCHOLAR_IMAP_HOST`、`SCHOLAR_IMAP_USERNAME`、`SCHOLAR_IMAP_PASSWORD` | 否 | 接收 Scholar Alert 的专用邮箱 IMAP 参数 |
| `SCHOLAR_IMAP_PORT` | 否 | 默认为 `993` |
| `FT_FEED_URL` | 否 | 合法授权的 FT RSS 地址 |
| `FT_EMAIL_SENDER` | 否 | 同一 IMAP 邮箱中 FT 官方通讯的发件人地址 |

`AI_BASE_URL` 和 `AI_MODEL` 在代码中分别默认使用 `https://minitoken.top/v1` 与 `deepseek-v4-flash`；本地运行时可通过 `.env` 覆盖。额外 RSS 与其公开正文域名应分别写入 Actions variables `EXTRA_RSS_FEEDS`、`EXTRA_ARTICLE_DOMAINS`，格式见上节。

若使用 Scholar，建议建立以下提醒：

```text
commodity futures
crude oil price
gold safe haven
financial statement fraud
internal control
forensic accounting
```

## 本地验证

复制环境变量模板并填写测试凭据：

```bash
cp .env.example .env
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
python -m app.cli run-once
```

最后一条命令会调用真实来源与 AI 网关；设置 `DINGTALK_WEBHOOK` 时发送钉钉消息，未设置时直接打印完整深度阅读。测试本身不会调用外部服务。

如果想先在不推送钉钉的情况下查看每一步（抓取 / 筛选 / 读正文 / AI 生成）的中间结果，可以用模拟命令：

```bash
python -m app.cli simulate              # 生产窗口（24h，周一 72h）
python -m app.cli simulate --hours 168  # 指定抓取窗口
python -m app.cli simulate --no-ai      # 跳过 AI，只看抓取/筛选/读正文
```

`simulate` 不会真正调用钉钉 Webhook，只打印“将会推送”的报告内容；需要真实推送仍用 `run-once`。

## 失败处理

- 每个订阅源独立超时并重试三次；单一来源失败不影响其余来源。
- 每篇公开正文独立读取；正文不可用时只跳过该篇，不会回退为对受限文章的“全文阅读”。
- AI 返回必须通过 Pydantic Schema、来源链接、标题、日期和“已读取公开正文”校验；连续失败三次时不发送未经验证的内容，而是发送故障通知（或打印到日志）。
- 所有输出均标注“仅供研究参考，不构成投资建议”。
