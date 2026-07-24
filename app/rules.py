"""Centralized, editable matching rules for the morning brief.

All tunable rule constants — ingestion source URLs, keyword dictionaries, source
authority identifiers, candidate quotas, time windows, article-extraction
thresholds, and AI generation parameters/prompt fragments — live here so the
matching behaviour can be maintained in one place.  The module logic (scoring,
candidate selection, prompt assembly, article extraction, source construction)
remains in its own module and imports from here.
"""

from __future__ import annotations

from .models import ItemCategory


# --- 1. Ingestion sources ------------------------------------------------------

FORBES_BUSINESS_RSS = "https://www.forbes.com/business/feed/"
EIA_TODAY_IN_ENERGY_RSS = "https://www.eia.gov/rss/todayinenergy.xml"
EIA_PRESS_RELEASES_RSS = "https://www.eia.gov/rss/press_rss.xml"
FEDERAL_RESERVE_PRESS_RSS = "https://www.federalreserve.gov/feeds/press_all.xml"
BOJ_WHATS_NEW_RSS = "https://www.boj.or.jp/en/rss/whatsnew.xml"
BOK_PRESS_RELEASES_RSS = "https://www.bok.or.kr/eng/bbs/E0000634/news.rss?menuNo=400069"
BIS_PRESS_RELEASES_RSS = "https://www.bis.org/doclist/all_pressrels.rss"
BIS_STATISTICAL_RELEASES_RSS = "https://www.bis.org/doclist/all_statistics.rss"
ECB_NEWS_RSS = "https://www.ecb.europa.eu/rss/press.html"
ECB_STATISTICAL_RELEASES_RSS = "https://www.ecb.europa.eu/rss/statpress.html"
EBA_NEWS_RSS = "https://www.eba.europa.eu/news-press/news/rss.xml"
CFTC_PRESS_URL = "https://www.cftc.gov/PressRoom/PressReleases?TB_iframe=true&page=0"
SEC_AAER_URL = (
    "https://www.sec.gov/enforcement-litigation/accounting-auditing-enforcement-releases"
    "?month=All&order=field_publish_date&sort=desc&year=All"
)
SEC_PRESS_URL = "https://www.sec.gov/newsroom/press-releases?month=All&year=All"
PCAOB_NEWS_URL = "https://pcaobus.org/news-events/news-releases"
GUARDIAN_BUSINESS_RSS = "https://www.theguardian.com/business/rss"
WSJ_US_BUSINESS_RSS = "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml"
YAHOO_FINANCE_RSS = "https://finance.yahoo.com/news/rssindex"

# Full text is read only from public pages operated by these sources.  In
# particular, FT and Scholar links are never fetched, even if a feed provides
# their headline, so the workflow does not attempt to bypass subscriptions.
PUBLIC_ARTICLE_DOMAINS = {
    "bis.org",
    "ecb.europa.eu",
    "ec.europa.eu",
    "eba.europa.eu",
    "forbes.com",
    "eia.gov",
    "boj.or.jp",
    "bok.or.kr",
    "sec.gov",
    "pcaobus.org",
    "cftc.gov",
    "federalreserve.gov",
    "theguardian.com",
    "finance.yahoo.com",
}
FULL_TEXT_BLOCKED_SOURCES = {"Financial Times", "Google Scholar Alert"}
HTTPS_UPGRADE_HOSTS = {"www.boj.or.jp"}


# --- 2. Keyword dictionaries ---------------------------------------------------


CAPITAL_MARKETS_ANCHORS = {
    "capital market",
    "capital markets",
    "equity market",
    "equity markets",
    "stock market",
    "stock markets",
    "securities market",
    "securities markets",
    "ipo",
    "initial public offering",
    "listing",
    "delisting",
    "bond market",
    "bond markets",
    "corporate bond",
    "credit spread",
    "资本市场",
    "股票市场",
    "证券市场",
    "首次公开募股",
    "上市",
    "退市",
    "债券市场",
}
GOVERNANCE_AUDIT_ANCHORS = {
    # 公司治理
    "corporate governance",
    "board governance",
    "board of directors",
    "audit committee",
    # 审计师与审计程序
    "audit",
    "auditing",
    "auditor",
    "auditor independence",
    "auditor rotation",
    "auditor report",
    "audit failure",
    "independent auditor",
    "annual audit",
    "certified public accountant",
    "certified public accountants",
    "cpa",
    # 审计意见
    "going concern opinion",
    "qualified opinion",
    "adverse opinion",
    "disclaimer of opinion",
    "key audit matter",
    # 准则与财务报告
    "financial risk",
    "financial reporting",
    "internal control",
    "internal controls",
    "professional standards",
    "auditing standards",
    "accounting standards",
    "gaap",
    "ifrs",
    "sarbanes-oxley",
    "sox",
    "10-k",
    "10-q",
    # 中文
    "公司治理",
    "董事会",
    "审计",
    "年报审计",
    "审计意见",
    "非标意见",
    "保留意见",
    "无法表示意见",
    "审计委员会",
    "注册会计师",
    "会计师事务所",
    "独立性",
    "轮换",
    "内部控制",
    "信息披露",
    "信披",
    "财务报告",
    "财务风险",
    "执业准则",
    "会计准则",
    "审计准则",
}
POLICY_AI_ANCHORS = {
    "ai",
    "artificial intelligence",
    "generative ai",
    "machine learning",
    "large language model",
    "ai governance",
    "ai regulation",
    "ai policy",
    "legal policy",
    "public policy",
    "legislation",
    "legislative",
    "rulemaking",
    "regulatory framework",
    "legal framework",
    "legal reform",
    "人工智能",
    "生成式人工智能",
    "机器学习",
    "大语言模型",
    "ai治理",
    "ai监管",
    "ai政策",
    "法律政策",
    "立法",
    "政策法规",
}

MACRO_CONTEXT_TERMS = {
    "economic growth",
    "employment",
    "exchange rate",
    "exports",
    "gdp",
    "imports",
    "payrolls",
    "recession",
    "trade",
    "yield",
    "yields",
}
RISK_ANCHORS = {
    # 财务造假手法
    "big bath",
    "bribery",
    "channel stuffing",
    "compliance",
    "control deficiency",
    "controls deficiency",
    "cookie jar",
    "corruption",
    "earnings management",
    "earnings manipulation",
    "embezzlement",
    "fictitious revenue",
    "fictitious sales",
    "fcpa",
    "financial statement",
    "fraud",
    "going concern",
    "kickback",
    "material weakness",
    "misstatement",
    "off-balance-sheet",
    "ponzi",
    "related party transaction",
    "related-party transaction",
    "restatement",
    "revenue recognition",
    "round-tripping",
    "securities violation",
    "self-dealing",
    "shell company",
    "tunneling",
    "whistleblower",
    # 监管执法动作
    "cease-and-desist",
    "charges",
    "consent decree",
    "deferred prosecution agreement",
    "disgorgement",
    "fine",
    "fines",
    "indictment",
    "non-prosecution agreement",
    "officer-and-director bar",
    "penalty",
    "penalties",
    "sanction",
    "sanctions",
    "settlement",
    "subpoena",
    "wells notice",
    # 中文
    "财务造假",
    "财务舞弊",
    "会计造假",
    "虚增收入",
    "虚增利润",
    "虚构业务",
    "虚开发票",
    "关联交易",
    "资金占用",
    "违规担保",
    "盈余管理",
    "商誉减值",
    "大洗澡",
    "利益输送",
    "立案调查",
    "行政处罚",
    "警示函",
    "问询函",
    "关注函",
    "监管措施",
    "市场禁入",
    "罚款",
    "罚单",
    "传票",
    "退市风险警示",
}
RISK_CONTEXT_TERMS = {
    "disclosure",
    "investigation",
    "lawsuit",
    "violation",
    "probe",
    "inquiry",
    "litigation",
    "criminal",
    "civil action",
    "调查",
    "诉讼",
    "违规",
    "处罚",
    "整改",
}
RESEARCH_TERMS = {
    "commodity futures",
    "crude oil",
    "gold",
    "macroeconomics",
    "monetary policy",
    "financial statement fraud",
    "internal control",
    "forensic accounting",
    "asset pricing",
    "quantitative finance",
}


# --- 3. Source authority identifiers ------------------------------------------

AUTHORITATIVE_SOURCE_PREFIXES = (
    "BIS",
    "Bank of Japan",
    "Bank of Korea",
    "CFTC",
    "ECB",
    "European Banking Authority",
    "European Central Bank",
    "European Commission",
    "Federal Reserve",
    "IMF",
    "OECD",
    "PCAOB",
    "SEC",
    "U.S. EIA",
    "World Bank",
)
AUTHORITATIVE_DOMAINS = {
    "bis.org",
    "bls.gov",
    "boj.or.jp",
    "bok.or.kr",
    "cftc.gov",
    "ecb.europa.eu",
    "ec.europa.eu",
    "eba.europa.eu",
    "eia.gov",
    "federalreserve.gov",
    "imf.org",
    "oecd.org",
    "pcaobus.org",
    "sec.gov",
    "worldbank.org",
}
REGULATORY_SOURCE_PREFIXES = ("CFTC", "PCAOB", "SEC")


# --- 4. Candidate counts and per-category quotas ------------------------------

MIN_CANDIDATES = 12
MAX_CANDIDATES = 16
CATEGORY_LIMITS = {
    ItemCategory.COMMODITY: 6,
    ItemCategory.MACRO: 4,
    ItemCategory.CAPITAL_MARKETS: 4,
    ItemCategory.GOVERNANCE_AUDIT: 4,
    ItemCategory.RISK: 5,
    ItemCategory.POLICY_AI: 3,
    ItemCategory.RESEARCH: 2,
}


# --- 4b. Audit/fraud scoring weights -----------------------------------------
# Governance/audit and fraud/internal-control categories use higher weights than
# the other topic categories so audit- and fraud-relevant articles rank higher.
AUDIT_FRAUD_ANCHOR_WEIGHT = 15
AUDIT_FRAUD_ANCHOR_CAP = 60
AUDIT_FRAUD_CONTEXT_WEIGHT = 3
AUDIT_FRAUD_CONTEXT_CAP = 15
REGULATORY_SOURCE_BONUS = 30


# --- 5. Ingestion time window -------------------------------------------------

DEFAULT_WINDOW_HOURS = 24
WEEKEND_WINDOW_HOURS = 72


# --- 6. Public article extraction thresholds ----------------------------------

MAX_ARTICLE_CHARS = 12_000
MIN_ARTICLE_CHARS = 300
ARTICLE_READER_CONCURRENCY = 4


# --- 7. AI generation rules and prompt fragments ------------------------------

SYSTEM_PROMPT = """你是一名审慎的中文金融研究编辑。你要对输入中已公开读取到正文的原始文章做逐篇深度阅读，而不是写晨报、快讯或摘要卡片。

只能使用输入资料明确给出的事实；不得补充输入未支持的数字、指控、价格走势、公司事实或因果结论。必须用简体中文，保持客观，并清楚区分已披露事实、合理的研究推演和仍待核验的事项。正文内容必须用自己的话转述，绝不长句引用或大段复述原文。不要给出确定性的投资建议。

严格输出 JSON 对象，不使用 Markdown 代码块，也不输出任何额外文字。"""

MAX_AI_INPUT_CANDIDATES = 6
MAX_AI_ARTICLE_CHARS = 5_000
MIN_AI_OUTPUT_ARTICLES = 3
MAX_AI_OUTPUT_ARTICLES = 4

# Static schema template for one analysis entry (report_date is filled in by the
# prompt assembler; disclaimer is kept inline to match the original payload).
AI_OUTPUT_SCHEMA_TEMPLATE = {
    "title": "必须逐字取自输入",
    "source": "必须逐字取自输入",
    "url": "必须逐字取自输入",
    "published_at": "必须逐字取自输入",
    "core_thesis": "用一段说明文章的核心命题",
    "fact_chain": ["按文章内容列出 3 至 6 个可追溯事实"],
    "detailed_reading": "2 至 4 个自然段，解释事实之间的关系、文章的意义与边界；只做转述和明确标示的推演",
    "transmission_or_risk": ["给出 1 至 4 条市场传导、监管风险或研究观察"],
    "limits_and_next_checks": ["可选：指出事实缺口、反证或下一步核验项"],
}

AI_TASK_DESCRIPTION = "从输入候选中输出 1 至 4 篇最值得阅读的深度文章解读。"

AI_RULES = [
    "每一篇 analyses 只能选择 full_text_available=true 的输入条目；没有可读取正文的条目不能进入输出。",
    "每一篇的 title、source、url、published_at 必须与一个输入条目完全一致。",
    "优先选择事实密度、研究价值最高的文章；不要为了凑数量而选题。",
    "在事实密度相近时，优先关注财务造假、监管执法、上市公司审计相关的文章，但不为凑数而放宽事实核验标准。",
    "fact_chain 只能包含输入正文或来源摘要能够支持的事实。",
    "detailed_reading 必须充分解释文章逻辑，不能退化为条目式简报，也不能复制原文表达。",
    "transmission_or_risk 可以做条件式研究推演，但必须说明其为观察而不是既成事实。",
    "不要把不同文章的事实混在同一篇分析中。",
    "无法得到完整正文的 Google Scholar Alert 和 Financial Times 线索不能进入深度阅读输出。",
]
