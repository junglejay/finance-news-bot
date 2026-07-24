"""Topic, source, ranking, and generation rules for the focused audit brief."""

from __future__ import annotations

from .models import ItemCategory


# --- 1. First-party specialist sources ---------------------------------------

# SEC's dedicated Accounting and Auditing Enforcement Releases feed.
SEC_AAER_RSS = "https://www.sec.gov/rss/divisions/enforce/friactions.xml"
SEC_PRESS_RSS = "https://www.sec.gov/news/pressreleases.rss"
SEC_LITIGATION_RSS = "https://www.sec.gov/enforcement-litigation/litigation-releases/rss"
SEC_ADMIN_PROCEEDINGS_RSS = (
    "https://www.sec.gov/enforcement-litigation/administrative-proceedings/rss"
)
PCAOB_NEWS_URL = "https://pcaobus.org/news-events"
# The unfiltered listing is intentional: FRC publishes audit inspection and
# enforcement reviews under "Publications", not only under its investigation
# tag. Compound topic scoring still rejects actuarial/stewardship news.
FRC_AUDIT_ENFORCEMENT_URL = "https://www.frc.org.uk/news-and-events/news/"
IAASB_NEWS_URL = "https://www.iaasb.org/news"
ASIC_MEDIA_RELEASES_API = "https://www.asic.gov.au/_data/mr2023/"
AFRC_PRESS_RELEASES_URL = "https://www.afrc.org.hk/en-hk/news-centre/press-releases/"
THOMSON_REUTERS_PCAOB_URL = "https://tax.thomsonreuters.com/news/topic/pcaob/"
CNINFO_ANNOUNCEMENTS_API = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_PDF_BASE_URL = "https://static.cninfo.com.cn/"

# CSRC publishes complete searchable JSON records, including the public body
# text. Separate channels cover policy/news and formal administrative penalties.
CSRC_NEWS_API = (
    "https://www.csrc.gov.cn/searchList/a1a078ee0bc54721ab6b148884c784a8"
    "?_isAgg=true&_isJson=true&_pageSize=30&_template=index"
    "&_rangeTimeGte=&_channelName=&page=1"
)
CSRC_PENALTIES_API = (
    "https://www.csrc.gov.cn/searchList/28de6b87eda140cb93de4dd10d11867d"
    "?_isAgg=true&_isJson=true&_pageSize=30&_template=index"
    "&_rangeTimeGte=&_channelName=&page=1"
)
MOF_SANCTIONS_URL = "https://www.mof.gov.cn/gp/xxgkml/jdjcj/index.htm"

PUBLIC_ARTICLE_DOMAINS = {
    "afrc.org.hk",
    "asic.gov.au",
    "csrc.gov.cn",
    "cninfo.com.cn",
    "frc.org.uk",
    "iaasb.org",
    "mof.gov.cn",
    "pcaobus.org",
    "sec.gov",
    "thomsonreuters.com",
}
FULL_TEXT_BLOCKED_SOURCES = {"Financial Times", "Google Scholar Alert"}


# --- 2. Compound topic dictionaries ------------------------------------------

# Fraud terms deliberately describe financial reporting conduct rather than
# generic scams, cybercrime, market manipulation, or investment fraud.
FRAUD_TERMS = {
    "accounting fraud",
    "accounting irregularities",
    "books and records violation",
    "channel stuffing",
    "cookie jar reserve",
    "earnings manipulation",
    "falsified financial statements",
    "false accounting",
    "fictitious revenue",
    "fictitious sales",
    "financial reporting fraud",
    "financial statement fraud",
    "fraudulent financial reporting",
    "improper capitalization",
    "improper revenue recognition",
    "inflated assets",
    "inflated earnings",
    "inflated profit",
    "misappropriation of assets",
    "off-balance-sheet",
    "round-tripping",
    "虚假财务报告",
    "虚假记载",
    "虚假采购",
    "虚假贸易",
    "虚构业务",
    "虚构交易",
    "虚构收入",
    "虚增存货",
    "虚增收入",
    "虚增利润",
    "虚增资产",
    "财务舞弊",
    "财务造假",
    "会计造假",
    "伪造审计证据",
    "伪造财务资料",
    "提前确认收入",
    "少计成本",
    "少计费用",
    "隐瞒负债",
    "资金占用",
    "违规担保",
    "利益输送",
}

ENFORCEMENT_ACTION_TERMS = {
    "administrative order",
    "administrative proceeding",
    "barred",
    "cease-and-desist",
    "charges",
    "civil penalty",
    "disciplinary action",
    "disciplinary order",
    "disgorgement",
    "enforcement action",
    "fine",
    "fines",
    "investigation",
    "penalty",
    "sanction",
    "sanctions",
    "settlement",
    "suspension",
    "立案",
    "立案调查",
    "行政处罚",
    "处罚决定",
    "监管措施",
    "纪律处分",
    "公开谴责",
    "警示函",
    "责令改正",
    "市场禁入",
    "罚款",
    "罚没",
    "暂停执业",
    "吊销",
    "调查",
}

AUDIT_TERMS = {
    "annual audit",
    "audit",
    "auditing",
    "audit committee",
    "audit engagement",
    "audit failure",
    "audit firm",
    "audit opinion",
    "audit quality",
    "audit report",
    "auditor",
    "auditor independence",
    "auditor rotation",
    "external audit",
    "independent auditor",
    "public company audit",
    "statutory audit",
    "注册会计师",
    "会计师事务所",
    "上市公司审计",
    "年报审计",
    "年度审计",
    "审计",
    "审计委员会",
    "审计失败",
    "审计意见",
    "审计报告",
    "签字会计师",
    "签字注册会计师",
}

AUDIT_QUALITY_TERMS = {
    "audit documentation",
    "audit evidence",
    "audit inspection",
    "auditing standard",
    "due professional care",
    "engagement quality review",
    "independence violation",
    "inspection finding",
    "inspection report",
    "inspection reports",
    "professional skepticism",
    "quality control",
    "reasonable assurance",
    "sufficient appropriate audit evidence",
    "working papers",
    "函证",
    "审计程序",
    "审计底稿",
    "审计证据",
    "审计准则",
    "未勤勉尽责",
    "独立性",
    "监盘",
    "职业怀疑",
    "质量控制",
    "执业质量",
    "风险评估程序",
    "实质性程序",
}

REPORTING_CONTROL_TERMS = {
    "accounting estimate",
    "accounting standard",
    "adverse opinion",
    "disclaimer of opinion",
    "financial reporting",
    "financial statement",
    "going concern",
    "going concern opinion",
    "internal control",
    "internal control over financial reporting",
    "key audit matter",
    "material misstatement",
    "material weakness",
    "non-gaap",
    "qualified opinion",
    "restatement",
    "restate",
    "revenue recognition",
    "significant deficiency",
    "会计差错",
    "会计差错更正",
    "会计估计",
    "会计准则",
    "信息披露",
    "内部控制",
    "内控缺陷",
    "内控审计",
    "关键审计事项",
    "持续经营",
    "更正公告",
    "财务报告",
    "财务报表",
    "重大错报",
    "重大缺陷",
    "收入确认",
    "审计意见",
    "保留意见",
    "否定意见",
    "无法表示意见",
    "非标准审计意见",
    "非标意见",
    "监管问询函",
    "年报问询函",
    "年度报告问询函",
    "问询函回复",
    "专项说明",
}

PUBLIC_COMPANY_CONTEXT_TERMS = {
    "10-k",
    "10-q",
    "annual report",
    "audit client",
    "exchange act reporting",
    "issuer",
    "listed company",
    "listed companies",
    "public company",
    "public companies",
    "registrant",
    "securities filing",
    "上市公司",
    "上市企业",
    "公众公司",
    "发行人",
    "证券发行",
    "股票发行",
    "年度报告",
    "年报",
}

RESEARCH_TERMS = {
    "audit failure",
    "audit quality",
    "auditor independence",
    "earnings management",
    "financial restatement",
    "financial statement fraud",
    "forensic accounting",
    "fraud detection",
    "internal control weakness",
    "professional skepticism",
    "审计质量",
    "审计失败",
    "审计师独立性",
    "财务舞弊",
    "财务造假",
    "法务会计",
    "内部控制缺陷",
}

# General enforcement topics that must not enter merely because the source is a
# securities regulator. They only reduce a score after a positive topic match.
OFF_TOPIC_TERMS = {
    "crypto",
    "cryptocurrency",
    "cybersecurity",
    "insider trading",
    "investment adviser",
    "market manipulation",
    "municipal advisor",
    "offering fraud",
    "trading platform",
    "内幕交易",
    "操纵市场",
    "操纵股票",
    "操纵期货",
    "非法荐股",
    "从业人员买卖股票",
}

# Audit regulators also oversee engagements outside the listed-company market.
# Those records must not qualify solely because a regulator and the word
# "auditor" appear together.
NON_PUBLIC_COMPANY_AUDIT_TERMS = {
    "smsf",
    "smsf auditor",
    "smsf auditors",
    "self-managed super fund",
    "self-managed super funds",
}

# Routine meetings and speeches often repeat broad anti-fraud policy language.
# They are useful background, but are not article-level candidate material
# unless their own headline names a focused fraud, audit, or reporting issue.
GENERIC_REGULATORY_EVENT_TERMS = {
    "座谈会",
    "工作会议",
    "工作座谈会",
    "会见",
    "meeting with",
    "roundtable",
}

# AAER occasionally publishes scheduling or reinstatement paperwork that is
# technically in scope but has little news or research value.
LOW_VALUE_PROCEDURAL_TERMS = {
    "appointment",
    "appointments",
    "application for reinstatement",
    "extension of time",
    "memorandum of understanding",
    "order dismissing proceedings",
    "order granting extension",
    "order postponing",
    "order regarding hearing",
    "order terminating",
    "re-appointment",
    "reinstatement",
    "sign mou",
    "signs mou",
}


# --- 3. Source authority and scope --------------------------------------------

AUTHORITATIVE_SOURCE_PREFIXES = (
    "AFRC",
    "ASIC",
    "IAASB",
    "PCAOB",
    "SEC",
    "UK FRC",
    "中国证监会",
    "财政部",
)
AUTHORITATIVE_DOMAINS = {
    "afrc.org.hk",
    "asic.gov.au",
    "csrc.gov.cn",
    "frc.org.uk",
    "iaasb.org",
    "mof.gov.cn",
    "pcaobus.org",
    "sec.gov",
}
REGULATORY_SOURCE_PREFIXES = (
    "AFRC",
    "ASIC",
    "PCAOB",
    "SEC",
    "UK FRC",
    "中国证监会",
    "财政部",
)
DEDICATED_FRAUD_SOURCE_PREFIXES = ("SEC Accounting & Auditing Enforcement",)
DEDICATED_AUDIT_SOURCE_PREFIXES = ("AFRC", "ASIC", "UK FRC", "IAASB")


# --- 4. Candidate selection and scoring ---------------------------------------

MAX_CANDIDATES = 12
MAX_ITEMS_PER_SOURCE = 4
MIN_RELEVANCE_SCORE = 55
CATEGORY_LIMITS = {
    ItemCategory.FRAUD_ENFORCEMENT: 5,
    ItemCategory.PUBLIC_COMPANY_AUDIT: 5,
    ItemCategory.REPORTING_CONTROLS: 3,
    ItemCategory.RESEARCH: 2,
}

AUTHORITATIVE_SOURCE_BONUS = 15
DEDICATED_SOURCE_BONUS = 15
OFF_TOPIC_PENALTY = 20


# --- 5. Ingestion and public article reading ----------------------------------

DEFAULT_WINDOW_HOURS = 24
WEEKEND_WINDOW_HOURS = 24
MAX_ARTICLE_CHARS = 16_000
MIN_ARTICLE_CHARS = 300
ARTICLE_READER_CONCURRENCY = 4


# --- 6. AI generation ----------------------------------------------------------

SYSTEM_PROMPT = """你是一名专注财务舞弊、证券监管和上市公司审计的中文研究编辑。你要对已公开读取到正文的一手材料或高质量报道逐篇做可追溯的深度解读。

只能使用输入资料明确给出的事实，不得补充输入未支持的数字、指控、责任认定、公司事实或因果结论。对调查、指控、拟处罚、和解、最终处罚与法院裁判必须严格区分，不能把指控写成已认定事实。必须用简体中文，区分来源披露、研究推演和仍待核验事项。用自己的话转述，不大段引用原文，不给出确定性投资建议。

严格输出 JSON 对象，不使用 Markdown 代码块，也不输出任何额外文字。"""

MAX_AI_INPUT_CANDIDATES = 10
MAX_AI_ARTICLE_CHARS = 7_000
MIN_AI_OUTPUT_ARTICLES = 4
MAX_AI_OUTPUT_ARTICLES = 6

AI_OUTPUT_SCHEMA_TEMPLATE = {
    "title": "必须逐字取自输入",
    "source": "必须逐字取自输入",
    "url": "必须逐字取自输入",
    "published_at": "必须逐字取自输入",
    "core_thesis": "说明案件、监管规则或审计问题的核心命题，并标明程序阶段",
    "fact_chain": ["列出 3 至 6 个可由正文追溯的事实，覆盖主体、期间、行为、依据和结果（如有）"],
    "detailed_reading": "2 至 4 个自然段，解释造假或错报机制、关键审计程序、监管逻辑、责任边界及其意义",
    "transmission_or_risk": ["给出 1 至 4 条对财报使用者、审计委员会、审计机构或监管实践的观察"],
    "limits_and_next_checks": ["指出尚未终局、证据缺口、申辩情况或后续需要核验的文件"],
}

AI_TASK_DESCRIPTION = "从候选中选择最有事实密度和专业价值的财务造假、监管执法或上市公司审计材料，输出 1 至 6 篇深度解读。"

AI_RULES = [
    "analyses 只能选择 full_text_available=true 且 category 属于 fraud_enforcement、public_company_audit、reporting_controls 或 research 的输入条目。",
    "每篇的 title、source、url、published_at 必须与一个输入条目完全一致。",
    "优先顺序为：正式监管决定或纪律处分、包含具体审计缺陷的检查/执法材料、重大财务报告规则变化、事实充分的专业报道。",
    "同等条件下优先第一方监管来源，并避免多篇内容重复或全部来自同一机构。",
    "涉及执法时必须写明程序阶段；不得把调查、指控、拟处罚或和解中的陈述写成终局认定。",
    "涉及审计时优先识别被审计主体与期间、审计意见、关键错报、失效审计程序、审计师责任及处罚结果；正文没有的信息应明确说未披露。",
    "fact_chain 只能包含输入正文或来源摘要能够支持的事实，不得把不同文章的事实合并。",
    "detailed_reading 应解释会计处理或审计逻辑，不能退化为快讯、市场行情评论或泛泛风险提示。",
    "Financial Times 和 Google Scholar Alert 在未获得公开全文时不能进入输出。",
]
