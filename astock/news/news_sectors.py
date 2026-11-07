"""全球要闻板块配置：查询与分类对全行业一视同仁，不绑定单一公司。

抓取标准：事件具备全球性影响力，或可能显著影响主要金融资产价格。
板块 id 稳定，供筛选与展示；查询词只用行业/宏观用语，不用某一家公司当检索主词。
"""
from __future__ import annotations

import re

def _rb(en: str) -> tuple:
    """每个板块路透、彭博、金融时报分站检索；词少、不合在一条 OR 里。"""
    en = (en or "").strip()
    return (
        f"{en} site:reuters.com",
        f"{en} site:bloomberg.com",
        f"{en} site:ft.com",
    )


# 跨资产、全球宏观（不指向具体公司）
GLOBAL_QUERIES = (
    ("macro", "全球重大财经 央行 利率 股市 原油 黄金"),
    ("macro", "breaking financial news central bank oil gold stocks site:reuters.com"),
    ("macro", "global markets news site:bloomberg.com"),
    ("macro", "site:cls.cn 全球 要闻 央行 原油"),
)

# 可能推动股、债、汇、商品价格的通用信号
IMPACT_KW = (
    "美联储", "欧央行", "日央行", "央行", "加息", "降息", "利率决议",
    "非农", "cpi", "ppi", "pce", "通胀", "衰退", "制裁", "关税", "战争",
    "停火", "违约", "熔断", "暴涨", "暴跌", "崩盘", "油价", "opec",
    "国债收益率", "美元指数", "federal reserve", "interest rate",
    "inflation", "recession", "sanctions", "tariff", "default",
    "selloff", "rally", "crude",
)

# id -> 展示名、说明、行业检索、分类关键词（可含行业内常见主体，仅用于归类）
SECTORS = {
    "tech": {
        "label": "科技股",
        "hint": "半导体、消费电子、云计算、软件与硬件设备",
        "queries": (
            "科技股 半导体 云计算 消费电子",
        ) + _rb("semiconductor technology stocks"),
        "keywords": (
            "科技股", "科技板块", "半导体", "芯片", "晶圆", "消费电子", "云计算",
            "软件", "手机", "foundry", "semiconductor", "chipmaker", "chips",
            "gpu", "cloud computing", "big tech", "hardware", "智能手机",
            "晶圆代工", "代工", "wafer",
        ),
    },
    "ai": {
        "label": "AI应用股",
        "hint": "大模型、生成式应用、AI 软件与落地场景",
        "queries": (
            "人工智能 应用 大模型 生成式 软件",
        ) + _rb("artificial intelligence generative AI software"),
        "keywords": (
            "人工智能", "大模型", "生成式", "智能体", "llm",
            "generative ai", "ai application", "机器学习", "自动驾驶软件",
        ),
    },
    "finance": {
        "label": "大金融",
        "hint": "银行、保险、券商、交易所与支付",
        "queries": (
            "银行 保险 券商 金融股 央行",
        ) + _rb("banks insurers financials stocks"),
        "keywords": (
            "银行", "保险", "券商", "投行", "支付", "金融股", "美联储",
            "bank", "insurer", "brokerage", "wall street", "credit",
        ),
    },
    "energy": {
        "label": "能源股",
        "hint": "原油、天然气、炼化、传统能源与公用事业",
        "queries": (
            "原油 天然气 能源股 油价 OPEC",
        ) + _rb("oil energy stocks crude OPEC"),
        "keywords": (
            "原油", "石油", "天然气", "油价", "opec", "炼化", "能源股",
            "crude", "brent", "wti", "lng", "refinery", "oil price",
            "energy",
        ),
    },
    "pharma": {
        "label": "医药股",
        "hint": "创新药、医疗器械、生物科技与医保政策",
        "queries": (
            "医药股 生物科技 创新药 FDA",
        ) + _rb("pharma biotech drug FDA"),
        "keywords": (
            "医药", "生物科技", "创新药", "疫苗", "fda", "临床", "医保",
            "pharma", "biotech", "drug", "vaccine", "clinical trial",
        ),
    },
    "precious": {
        "label": "贵金属",
        "hint": "黄金、白银、铂族及金银矿",
        "queries": (
            "黄金 白银 贵金属 金价 避险",
        ) + _rb("gold silver precious metals bullion"),
        "keywords": (
            "黄金", "白银", "金价", "银价", "铂金", "贵金属", "避险",
            "gold", "silver", "bullion", "xau", "precious metal",
        ),
    },
    "metals": {
        "label": "有色金属",
        "hint": "铜、铝、锂、稀土等工业金属",
        "queries": (
            "有色金属 铜 铝 锂 稀土 期货",
        ) + _rb("copper aluminum lithium rare earth"),
        "keywords": (
            "有色", "铜", "铝", "锌", "锂", "稀土", "镍", "钴",
            "copper", "aluminum", "lithium", "nickel", "rare earth",
        ),
    },
    "macro": {
        "label": "宏观利率",
        "hint": "央行、通胀、就业、国债与汇率",
        "queries": (
            "美联储 欧央行 非农 通胀 国债收益率",
        ) + _rb("Federal Reserve ECB CPI Treasury yields"),
        "keywords": (
            "美联储", "欧央行", "非农", "通胀", "cpi", "pce", "国债",
            "收益率", "美元指数", "federal reserve", "ecb", "treasury",
            "inflation", "payrolls", "interest rate",
        ),
    },
    "consumer": {
        "label": "消费",
        "hint": "零售、餐饮、白酒、电商与可选消费",
        "queries": (
            "消费股 零售 电商 白酒",
        ) + _rb("consumer retail e-commerce stocks"),
        "keywords": (
            "消费", "零售", "电商", "白酒", "餐饮", "旅游",
            "retail", "e-commerce", "consumer staple", "consumer discretionary",
        ),
    },
    "auto": {
        "label": "汽车出行",
        "hint": "整车、新能源车、智能驾驶与出行",
        "queries": (
            "汽车 新能源车 电动车 销量",
        ) + _rb("auto EV electric vehicle carmakers"),
        "keywords": (
            "汽车", "新能源车", "电动车", "整车", "智能驾驶",
            "ev", "electric vehicle", "carmaker", "automaker",
        ),
    },
    "defense": {
        "label": "军工国防",
        "hint": "国防订单、地缘冲突相关军工资产",
        "queries": (
            "军工 国防 导弹 地缘",
        ) + _rb("defense military aerospace stocks"),
        "keywords": (
            "军工", "国防", "导弹", "战机", "北约",
            "defense", "military", "pentagon", "nato", "aerospace",
        ),
    },
    "realty": {
        "label": "地产基建",
        "hint": "房地产、基建、水泥与工程承包",
        "queries": (
            "房地产 基建 住房 利率",
        ) + _rb("real estate housing infrastructure"),
        "keywords": (
            "房地产", "楼市", "住房", "基建", "水泥",
            "real estate", "housing", "mortgage", "infrastructure",
        ),
    },
    "agri": {
        "label": "农产品",
        "hint": "大豆、玉米、小麦与软商品",
        "queries": (
            "农产品 大豆 玉米 小麦 期货",
        ) + _rb("soy corn wheat agriculture"),
        "keywords": (
            "大豆", "玉米", "小麦", "农产品", "糖", "棉花",
            "soy", "corn", "wheat", "agriculture", "grain",
        ),
    },
}

REQUIRED_SECTOR_IDS = (
    "tech", "ai", "finance", "energy", "pharma", "precious", "metals",
)


def sector_ids() -> list:
    return list(SECTORS.keys())


def sector_label(sid: str) -> str:
    spec = SECTORS.get(sid) or {}
    return str(spec.get("label") or sid)


def labels_of(ids) -> list:
    out = []
    for sid in ids or []:
        lab = sector_label(sid)
        if lab not in out:
            out.append(lab)
    return out


def build_queries(selected=None) -> list:
    """(seed_sector, query) 列表。

    全市场模式先覆盖全球宏观，再为每个板块保留一条宽口径查询；不再把每个
    板块的 Reuters/Bloomberg/FT 三组站内检索全部铺开，否则一次刷新会产生
    近百个外部请求，既慢又更容易触发搜索引擎限流。定向板块仍保留完整查询。
    """
    want = [s for s in (selected or []) if s in SECTORS]
    jobs = [] if want else list(GLOBAL_QUERIES)
    for sid in (want or sector_ids()):
        queries = list(SECTORS[sid].get("queries") or ())
        if not want:
            queries = queries[:1]
        for q in queries:
            jobs.append((sid, q))
    return _uniq_jobs(jobs)


GLOBAL_SOCIAL = (
    ("macro", "site:x.com NVIDIA earnings"),
    ("macro", "site:x.com Tesla earnings"),
    ("macro", "site:x.com Fed Powell"),
    ("macro", "site:x.com oil OPEC"),
    ("macro", "site:x.com gold price"),
    ("macro", "site:x.com Bitcoin price"),
    ("macro", "site:x.com China rare earth"),
    ("macro", "site:x.com 美联储 降息"),
    ("macro", "site:x.com 复盘 收评 大A"),
    ("macro", "site:x.com 新闻联播 A股"),
    ("macro", "site:weibo.com 全球财经 热点"),
    ("macro", "site:weibo.com 财经博主 利好 利空"),
)

# 公司财报/大事件比泛行业词更可能命中 Bing 国际索引里的 X 帖子；
# Bing 对词数接近 AND 语义，锚点保持 1-2 个词，随事件热度自然命中
SOCIAL_ANCHORS = {
    "tech": "NVIDIA earnings",
    "ai": "OpenAI ChatGPT",
    "finance": "bank earnings",
    "energy": "oil OPEC",
    "pharma": "FDA approval",
    "precious": "gold price",
    "metals": "copper price",
    "macro": "Fed Powell",
    "consumer": "retail earnings",
    "auto": "Tesla earnings",
    "defense": "defense stocks",
    "realty": "housing market",
    "agri": "corn price",
}


def _pick_terms(spec: dict, want_en: bool, n: int = 4) -> str:
    out = []
    for k in spec.get("keywords") or ():
        s = str(k).strip()
        if not s:
            continue
        has_zh = bool(re.search(r"[\u4e00-\u9fff]", s))
        if want_en and not has_zh:
            out.append(s)
        elif (not want_en) and has_zh:
            out.append(s)
        if len(out) >= n:
            break
    return " ".join(out)


def social_queries(selected=None) -> list:
    """X / 微博：中文、英语分条检索博主评价与传闻，不先验真伪。

    英文查询优先用公司/事件锚点（Bing 国际索引里 X 帖子多随热门事件出现），
    辅以行业关键词；中文查询另走微博。
    """
    want = [s for s in (selected or []) if s in SECTORS]
    jobs = []
    if not want:
        # 全市场页以主要新闻为主，仅抽取少量社交温度；板块定向页再深挖。
        return _uniq_jobs(list(GLOBAL_SOCIAL)[:6])
    else:
        targets = want
    for sid in targets:
        spec = SECTORS.get(sid) or {}
        anchor = SOCIAL_ANCHORS.get(sid)
        if anchor:
            jobs.append((sid, f"site:x.com {anchor}"))
        en = _pick_terms(spec, True, n=2)
        zh = _pick_terms(spec, False, n=2)
        if en:
            jobs.append((sid, f"site:x.com {en}"))
        if zh:
            jobs.append((sid, f"site:x.com {zh}"))
            jobs.append((sid, f"site:weibo.com {zh}"))
    return _uniq_jobs(jobs)


def _uniq_jobs(jobs) -> list:
    seen = set()
    uniq = []
    for sid, q in jobs:
        key = (sid, q)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((sid, q))
    return uniq


def is_global_impact(text: str) -> bool:
    blob = (text or "").lower()
    return any(k.lower() in blob for k in IMPACT_KW)


def classify_sectors(text: str) -> list:
    """按关键词打板块标签，一条新闻可属于多个板块。"""
    blob = (text or "").lower()
    if not blob.strip():
        return []
    hit = []
    for sid, spec in SECTORS.items():
        if any(str(k).lower() in blob for k in spec.get("keywords") or ()):
            hit.append(sid)
    return hit


def tag_item_sectors(title: str, snippet: str = "") -> list:
    """按正文关键词打板块；对不上任何板块但含全球冲击词时归入宏观。"""
    blob = f"{title} {snippet}"
    tags = classify_sectors(blob)
    if not tags and is_global_impact(blob):
        tags.append("macro")
    return tags


def filter_by_sectors(items: list, selected: list) -> list:
    """只保留标签与所选板块有交集的条目；所选为空则原样返回。"""
    want = [s for s in (selected or []) if s in SECTORS]
    if not want:
        return list(items or [])
    want_set = set(want)
    out = []
    for it in items or []:
        tags = set(getattr(it, "sectors", None) or [])
        if tags & want_set:
            out.append(it)
    return out


def count_by_sector(items: list) -> dict:
    counts = {sid: 0 for sid in SECTORS}
    for it in items or []:
        for sid in set(getattr(it, "sectors", None) or []):
            if sid in counts:
                counts[sid] += 1
    return counts


def event_matches_sectors(event: dict, selected: list) -> bool:
    want = [s for s in (selected or []) if s in SECTORS]
    if not want:
        return True
    ev = event or {}
    parts = [ev.get("title") or "", ev.get("why") or ""]
    parts.extend(ev.get("bull") or [])
    parts.extend(ev.get("bear") or [])
    blob = " ".join(str(x) for x in parts)
    tags = set(classify_sectors(blob))
    return bool(tags & set(want))
