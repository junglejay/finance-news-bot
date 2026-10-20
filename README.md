# 财务造假、监管与上市公司审计深度阅读机器人

这是一个由 GitHub Actions 驱动的中文研究机器人。它每天北京时间 07:30 运行，专注以下四类材料：

1. 财务造假与证券监管执法；
2. 上市公司审计、审计机构处罚与检查发现；
3. 财务报告、内部控制和非标准审计意见；
4. 与上述主题直接相关的专业研究。

系统优先采集监管机构和审计准则制定机构的一手公开材料，读取可公开访问的正文，经确定性规则筛选后，再使用 DeepSeek Flash（Anthropic 兼容 Messages API，1M 上下文）生成逐篇中文深度解读和独立的钉钉短报，向钉钉推送短报。

每次运行不保存原文或 AI 报告。国外来源默认抓取最近七天，香港及内地来源保持最近 24 小时，并通过 GitHub
Actions 缓存仅保存“已成功推送的 URL 和时间”及内地新闻当日配额标记，避免手动重跑或来源时间重叠导致重复推送，并确保同一天重跑也不会发送第二篇内地新闻。
URL 历史默认保留 14 天。

## 与旧版的主要区别

- 商品、宏观、央行动态、AI 政策和泛资本市场新闻不再是主动分类，也没有候选配额。
- “处罚”“调查”“fraud”等单个宽泛词不能单独入选；分类要求财务报告、上市公司或审计语境同时成立。
- SEC、证监会等综合监管源也必须通过主题门槛，内幕交易、操纵市场、加密资产等内容不会因为来源权威而自动入选。
- 候选池不再强行凑到最低数量；没有高相关材料时发送“暂无高相关更新”，而不是推送无关新闻或故障通知。
- 单一来源最多进入 4 条候选，中国内地来源每天最多进入 1 条最终候选。候选池和 AI 的最多 10 条输入均优先保留国外材料，香港及内地作为补充。
- 最终报告有足够国外候选时须由国外文章占多数；不足时须用完可用国外候选。返回结果经过数量校验，国外文章排在推送前面。

## 默认信息源

核心一手来源：

- SEC Accounting and Auditing Enforcement Releases（AAER）专门 RSS；
- SEC Press Releases（严格二次筛选）；
- SEC Litigation Releases；
- SEC Administrative Proceedings；
- SEC EDGAR 8-K：逐页覆盖抓取窗口，只抓 Item 4.01（审计师变更）和 Item 4.02（财报不再可信、重述），不再局限于最新 100 条；
- PCAOB 新闻稿：读取官方新闻页面当前使用的公开检索配置及 JSON，包含公开正文；页面不可用时使用已验证的公共检索配置直接读取同一官方 API；
- Journal of Accountancy 新闻：读取公开正文及首次发布日期，更新日期不会把旧稿变成新稿；
- 美国 DOJ 全辖区新闻稿：使用官方 JSON API 按发布日期倒序分页，含公开正文，仍须通过财务舞弊、审计及财报主题门槛；
- 英国 FCA 官方新闻 RSS（当前本地实测返回 403，明确报告来源失败，不当作无更新）；
- 加拿大 CPAB：读取执法报告 PDF 的 Published 日期及 CPAB Express 的有日期公告；检查年度、执法生效日及只有月份的报告日期不作为新的精确发布日期；
- Thomson Reuters Tax & Accounting 的公开 PCAOB 专题报道，用于补足只发布执法令、未发新闻稿的 PCAOB 案件；
- 英国 Financial Reporting Council（FRC）完整新闻栏目，经正文筛选审计质量、执法和财报内容；
- International Auditing and Assurance Standards Board（IAASB）新闻；
- 中国证监会行政处罚公开接口；
- 中国证监会要闻公开接口（严格二次筛选）；
- 巨潮资讯上市公司年报监管问询回复、会计师专项说明、财报更正及非标审计意见；
- 财政部行政处罚结果；
- 澳大利亚 ASIC 官网当前使用的媒体发布 JSON，主入口失败时自动切换到官方 Newsroom 备用 JSON；
- 日本 TDnet 上市公司披露：只抓会计师变更、审计意见、财报更正、内控缺陷和会计调查；
- 日本公认会计士・监查审查会（CPAAOB）的检查建议及监测报告；
- 香港交易所 HKEXnews：只抓审计师变更、非标意见、财报重述、内控缺陷及会计调查；
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

单次最多选择 12 条候选，其中中国内地来源最多 1 条；最多向 AI 提供 10 篇已成功读取正文的材料，最终输出
1 至 6 篇；有 6 篇以内合格正文时全部输出，超过 6 篇时选择其中 4 至 6 篇。
每篇包括：

- 核心命题及执法程序阶段；
- 可追溯的事实链；
- 造假/错报机制、关键审计程序和监管逻辑；
- 对财报使用者、审计委员会、审计机构和监管实践的观察；
- 尚未终局、证据缺口和后续核验事项。

AI 返回必须通过 Pydantic Schema、来源链接、标题、日期和正文可用性校验。调查、指控、拟处罚、和解和最终处罚必须区分，不能把指控写成既定事实。

钉钉每篇发送一条短报，通知标题直接显示中文事件。消息依次包括：

- 中文事件标题（最多 36 字）；
- 一句话结论（最多 70 字，保留当前程序阶段）；
- 1 至 3 条关键事实（每条最多 65 字，保留关键数字和单位）；
- 一段解读（最多 100 字）；
- 后续核验（最多 70 字，聚焦 1 至 2 个事项）；
- 原始来源、发布日期、当批序号、原文链接及研究免责声明。

短报由同一次模型调用在 `dingtalk_brief` 中独立撰写，不截断长文。完整解读仍保留在报告对象及其 Markdown 导出中；系统没有完整报告网页，消息链接指向新闻原文。短报缺失或超长会触发生成校验与重试，不会回退发送长篇报告。`simulate_run.py` 通过与实际推送相同的渲染函数展示通知标题和正文，预览本身不发送 Webhook。

## GitHub Actions 配置

工作流文件是 [`.github/workflows/morning-brief.yml`](.github/workflows/morning-brief.yml)。其中 `30 23 * * *` 为 UTC 时间，对应北京时间每天 07:30；也可以在 GitHub Actions 页面手动触发。

在仓库的 **Settings → Secrets and variables → Actions** 中添加：

| Secret | 必填 | 用途 |
| --- | --- | --- |
| `ANTHROPIC_AUTH_TOKEN` | 是 | DeepSeek API 密钥，仅保存在本地 `.env` 或 Actions Secret 中 |
| `DINGTALK_WEBHOOK` | 否 | 钉钉机器人 Webhook；留空时打印到 Actions 日志 |
| `DINGTALK_SECRET` | 否 | 钉钉机器人启用“加签”时需要 |
| `SEC_USER_AGENT` | 建议 | 含联系邮箱的 SEC 请求标识，例如 `audit-bot/2.0 contact=you@example.com` |
| `DELIVERY_HISTORY_FILE` | 否 | URL 去重文件，默认 `.cache/delivered_urls.json` |
| `SCHOLAR_IMAP_HOST`、`SCHOLAR_IMAP_USERNAME`、`SCHOLAR_IMAP_PASSWORD` | 否 | 接收 Scholar Alert 的专用邮箱 |
| `SCHOLAR_IMAP_PORT` | 否 | 默认 `993` |
| `FT_FEED_URL` | 否 | 合法授权的 FT RSS 地址 |
| `FT_EMAIL_SENDER` | 否 | 同一 IMAP 邮箱中 FT 官方通讯的发件人 |

`ANTHROPIC_BASE_URL` 默认为 `https://api.deepseek.com/anthropic`，
`ANTHROPIC_MODEL` 默认为 `deepseek-flash[1m]`。本地模拟通过 `.env` 配置，Actions 通过同名
repository variables 覆盖地址和模型，通过 Secret `ANTHROPIC_AUTH_TOKEN` 提供密钥。
修改本地 `.env` 不会自动更新 GitHub Secrets。

DeepSeek 的 `[1m]` 后缀作为客户端上下文提示保存在配置中，直接请求 API 时使用模型 ID
`deepseek-flash`；该模型支持 1M 上下文。请求同时提供官方支持的 `x-api-key` 认证头。
DeepSeek 报告生成使用 32,768 token 的输出预算和 `output_config.effort=low`，
降低长报告因思考占用预算而截断的风险；截断错误会包含预算和实际输出 token 数。
切换服务商时必须同步更换 API 密钥；如果 Actions 中已有同名 repository variables，
也要将它们更新为上述新地址和模型。

接口说明：[DeepSeek Anthropic API](https://api-docs.deepseek.com/guides/anthropic_api/)。

代码仍兼容旧的 `AI_API_KEY`、`AI_BASE_URL`、`AI_MODEL`；使用 OpenAI 兼容网关时，
移除 `ANTHROPIC_*` 配置并显式设置对应 `AI_*` 值。存在 `ANTHROPIC_*` 配置时优先使用
Anthropic 协议，请求 `/v1/messages`，不会向该地址发送 Chat Completions 格式的请求。

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
python simulate_run.py              # 读取 .env，包含真实 AI 调用，不推送钉钉
python inspect_fetch.py --source SEC
python inspect_fetch.py --source 证监会
```

模拟与采集检查默认使用相同的分来源窗口；`--hours 168` 可将所有来源统一扩大到最近七天。
模拟默认只读本地推送历史、跳过已发送 URL 并遵守内地当日配额，不修改历史；诊断时可用 `--ignore-history` 忽略历史。
本地缺少 Actions 的历史缓存时，模拟不会知道线上已经推送了哪些 URL，候选数可能高于正式运行。
任一来源最终抓取失败时，检查命令返回退出码 `1`，仍会展示其余来源结果。
FRC 等列表优先读取独立发布日期字段；日本 CPAAOB 支持令和、平成、昭和日期，
不会借用相邻公告的日期。未来日期不作为当前新闻入选。
巨潮公告只有日期精度，窗口按上海时区的日历日判断，包含窗口起始日的整天，
避免把接口的零点时间戳当成精确发布时间而漏采；重叠条目仍由已推送 URL 历史去重。
PCAOB 的备用检索配置仅含公开页面的客户端标识与索引名称，无需新增 Secret。
若配置失效并返回空索引，采集会明确报错；有历史公告但窗口内无新稿则正常返回零条。
SEC 8-K 每页读取 100 条，以全部披露的日期判断是否覆盖窗口；没有会计相关条目的
页面也会继续翻页。重复页面、无效日期、请求失败或达到 100 页仍未覆盖窗口时明确报错，
不会把未完成的采集当作成功结果。
DOJ 使用官方 API 的 `sort=date&direction=DESC`，逐页覆盖窗口，并遵守每秒最多四次请求的限制。
CPAB 和 DOJ 的仅日期字段包含窗口起始日整天，之后由成功推送的 URL 去重；CPAB 的月份精度检查报告通过有明确发布日期的 Express 公告跟踪。

执行完整生产任务：

```bash
python -m app.cli run-once
```

`simulate` 不会调用钉钉 Webhook。`run-once` 从进程环境读取配置（本地运行前需导入 `.env`），
在未配置 Webhook 时会把完整报告打印到日志。

## 失败处理

- 每个来源独立超时并重试三次，来源之间并发抓取，单源失败不影响其他来源。
- 对允许公开读取的官方页面先读取正文，再进行分类评分，避免笼统标题造成漏选。
- 正文只从白名单公开页面读取；单篇不可用时跳过，不伪装成“全文解读”。
- 已推送 URL 会被过滤；缓存不包含新闻正文或生成报告。
- 没有通过主题门槛的材料时正常结束并发送“暂无高相关更新”。
- 有候选但正文读取或 AI 校验连续失败时发送故障通知。
- 所有输出均标注“仅供研究参考，不构成投资建议”。
