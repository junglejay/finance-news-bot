# 财务造假、监管与上市公司审计深度阅读机器人

这是一个由 GitHub Actions 驱动的中文研究机器人。它每天北京时间 07:30 运行，专注以下四类材料：

1. 财务造假与证券监管执法；
2. 上市公司审计、审计机构处罚与检查发现；
3. 财务报告、内部控制和非标准审计意见；
4. 与上述主题直接相关的专业研究。

系统优先采集监管机构和审计准则制定机构的一手公开材料，读取可公开访问的正文，经确定性规则筛选后，再使用 OpenAI 兼容的 AI 网关生成逐篇中文深度解读并推送到钉钉。

每次运行不保存原文或 AI 报告。抓取窗口默认为最近 24 小时，并通过 GitHub
Actions 缓存仅保存“已成功推送的 URL 和时间”，避免手动重跑或来源时间重叠导致重复推送。
URL 历史默认保留 14 天。

## 与旧版的主要区别

- 商品、宏观、央行动态、AI 政策和泛资本市场新闻不再是主动分类，也没有候选配额。
- “处罚”“调查”“fraud”等单个宽泛词不能单独入选；分类要求财务报告、上市公司或审计语境同时成立。
- SEC、证监会等综合监管源也必须通过主题门槛，内幕交易、操纵市场、加密资产等内容不会因为来源权威而自动入选。
- 候选池不再强行凑到最低数量；没有高相关材料时发送“暂无高相关更新”，而不是推送无关新闻或故障通知。
- 单一来源最多进入 4 条候选，避免监管机构集中发布时完全挤占其他地区和主题。

## 默认信息源

核心一手来源：

- SEC Accounting and Auditing Enforcement Releases（AAER）专门 RSS；
- SEC Press Releases（严格二次筛选）；
- SEC Litigation Releases；
- SEC Administrative Proceedings；
- PCAOB 新闻稿；
- Thomson Reuters Tax & Accounting 的公开 PCAOB 专题报道，用于补足只发布执法令、未发新闻稿的 PCAOB 案件；
- 英国 Financial Reporting Council（FRC）完整新闻栏目，经正文筛选审计质量、执法和财报内容；
- International Auditing and Assurance Standards Board（IAASB）新闻；
- 中国证监会行政处罚公开接口；
- 中国证监会要闻公开接口（严格二次筛选）；
- 巨潮资讯上市公司年报监管问询回复、会计师专项说明、财报更正及非标审计意见；
- 财政部行政处罚结果；
- 澳大利亚 ASIC 官方媒体发布公开 JSON；
- 香港会计及财务汇报局（AFRC）新闻。

可选补充来源：

- 已授权的 Financial Times RSS 或官方邮件通讯；
- Google Scholar Alert 邮件（仅保留财务舞弊、审计质量和内控研究）；
- 通过 `EXTRA_RSS_FEEDS` 明确配置的审计监管机构或专业媒体。

The Guardian Business、Wall Street Journal US Business、Yahoo Finance 等泛商业
RSS 不再默认抓取。需要其中某个来源时可以显式加入 `EXTRA_RSS_FEEDS`，但其内容
仍须通过相同的复合主题门槛。

Financial Times 和 Scholar 链接只保留订阅提供的标题、摘要和链接，不抓取受保护正文，也不会绕过付费墙或登录限制。其他正文仅从代码白名单中的公开域名读取。

## 筛选与输出

规则层先将条目映射为以下活动分类：

| 分类 | 进入条件示例 | 候选配额 |
| --- | --- | ---: |
| `fraud_enforcement` | 造假手法 + 财报/上市公司/审计语境，或 SEC AAER 专门栏目 | 5 |
| `public_company_audit` | 审计主题 + 审计质量/上市公司/财报语境，或审计监管专门栏目 | 5 |
| `reporting_controls` | 财务报告/内控问题 + 上市公司语境 | 3 |
| `research` | 与造假、审计质量或内控直接相关的研究 | 2 |

单次最多选择 12 条候选，最多向 AI 提供 10 篇已成功读取正文的材料，最终输出
1 至 6 篇；有 6 篇以内合格正文时全部输出，超过 6 篇时选择其中 4 至 6 篇。
每篇包括：

- 核心命题及执法程序阶段；
- 可追溯的事实链；
- 造假/错报机制、关键审计程序和监管逻辑；
- 对财报使用者、审计委员会、审计机构和监管实践的观察；
- 尚未终局、证据缺口和后续核验事项。

AI 返回必须通过 Pydantic Schema、来源链接、标题、日期和正文可用性校验。调查、指控、拟处罚、和解和最终处罚必须区分，不能把指控写成既定事实。

## GitHub Actions 配置

工作流文件是 [`.github/workflows/morning-brief.yml`](.github/workflows/morning-brief.yml)。其中 `30 23 * * *` 为 UTC 时间，对应北京时间每天 07:30；也可以在 GitHub Actions 页面手动触发。

在仓库的 **Settings → Secrets and variables → Actions** 中添加：

| Secret | 必填 | 用途 |
| --- | --- | --- |
| `AI_API_KEY` | 是 | OpenAI 兼容 AI 网关密钥 |
| `DINGTALK_WEBHOOK` | 否 | 钉钉机器人 Webhook；留空时打印到 Actions 日志 |
| `DINGTALK_SECRET` | 否 | 钉钉机器人启用“加签”时需要 |
| `SEC_USER_AGENT` | 建议 | 含联系邮箱的 SEC 请求标识，例如 `audit-bot/2.0 contact=you@example.com` |
| `DELIVERY_HISTORY_FILE` | 否 | URL 去重文件，默认 `.cache/delivered_urls.json` |
| `SCHOLAR_IMAP_HOST`、`SCHOLAR_IMAP_USERNAME`、`SCHOLAR_IMAP_PASSWORD` | 否 | 接收 Scholar Alert 的专用邮箱 |
| `SCHOLAR_IMAP_PORT` | 否 | 默认 `993` |
| `FT_FEED_URL` | 否 | 合法授权的 FT RSS 地址 |
| `FT_EMAIL_SENDER` | 否 | 同一 IMAP 邮箱中 FT 官方通讯的发件人 |

`AI_BASE_URL` 和 `AI_MODEL` 可通过 `.env` 或 Actions secrets/variables 覆盖。

## 自定义公开来源

设置 `EXTRA_RSS_FEEDS`，每行一个 `来源名称|https://rss-url`。如果希望读取这些来源的公开正文，再在 `EXTRA_ARTICLE_DOMAINS` 中列出域名：

```text
EXTRA_RSS_FEEDS=Example Audit Regulator|https://example.org/news/rss.xml
EXTRA_ARTICLE_DOMAINS=example.org
```

额外来源不会因配置而自动获得高相关分类，仍需通过相同的复合主题门槛。只应加入无需登录、没有付费墙且你有权访问的公开来源。

建议的 Scholar Alerts：

```text
financial statement fraud
audit quality
auditor independence
financial restatement
internal control weakness
forensic accounting
```

## 本地验证

复制环境变量模板并安装依赖：

```bash
cp .env.example .env
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

查看真实抓取和筛选漏斗，但不推送钉钉：

```bash
python -m app.cli simulate --no-ai
python -m app.cli simulate --hours 168 --no-ai
python inspect_fetch.py --source SEC
python inspect_fetch.py --source 证监会
```

执行完整生产任务：

```bash
python -m app.cli run-once
```

`simulate` 不会调用钉钉 Webhook。`run-once` 在未配置 Webhook 时会把完整报告打印到日志。

## 失败处理

- 每个来源独立超时并重试三次，来源之间并发抓取，单源失败不影响其他来源。
- 对允许公开读取的官方页面先读取正文，再进行分类评分，避免笼统标题造成漏选。
- 正文只从白名单公开页面读取；单篇不可用时跳过，不伪装成“全文解读”。
- 已推送 URL 会被过滤；缓存不包含新闻正文或生成报告。
- 没有通过主题门槛的材料时正常结束并发送“暂无高相关更新”。
- 有候选但正文读取或 AI 校验连续失败时发送故障通知。
- 所有输出均标注“仅供研究参考，不构成投资建议”。
