"""面向 A 股的查询规划：实体别名、事件模板与一手来源路由。"""
from __future__ import annotations

import re


ALIASES = {
    "贵州茅台": ["贵州茅台酒股份有限公司", "Kweichow Moutai", "Moutai"],
    "宁德时代": ["宁德时代新能源科技股份有限公司", "CATL"],
    "比亚迪": ["比亚迪股份有限公司", "BYD"],
    "中芯国际": ["中芯国际集成电路制造有限公司", "SMIC"],
    "工业富联": ["富士康工业互联网股份有限公司", "Foxconn Industrial Internet", "FII"],
    "中际旭创": ["中际旭创股份有限公司", "Zhongji Innolight", "InnoLight"],
    "天孚通信": ["苏州天孚光通信股份有限公司", "TFC"],
    "新易盛": ["成都新易盛通信技术股份有限公司", "Eoptolink"],
}


def entity_aliases(code6: str, name: str, extra=None) -> list[str]:
    """返回适合分条检索的实体形式，避免把很多 OR 塞进一条查询。"""
    out: list[str] = []
    persisted = []
    try:
        from astock.research import research_store
        persisted = [r["alias"] for r in research_store.list_entity_aliases(code6)]
    except Exception:
        pass
    for value in [name, code6, *(ALIASES.get((name or "").strip(), [])),
                  *persisted, *(extra or [])]:
        value = str(value or "").strip()
        if value and value.lower() not in {x.lower() for x in out}:
            out.append(value)
    return out


def exchange_host(code6: str) -> str:
    code = str(code6 or "")
    if code.startswith(("4", "8", "92")):
        return "bse.cn"
    if code.startswith(("0", "2", "3")):
        return "szse.cn"
    return "sse.com.cn"


def plan_stock_queries(code6: str, name: str, extra_aliases=None) -> list[tuple[str, str]]:
    """按“官方→重大事件→主流媒体→国际来源→社交线索”生成查询计划。"""
    aliases = entity_aliases(code6, name, extra_aliases)
    primary = f'"{name}" {code6}'.strip()
    host = exchange_host(code6)
    jobs = [
        ("cninfo", f"{primary} site:cninfo.com.cn"),
        ("cninfo", f"{primary} site:{host}"),
        ("web", f"{primary} 业绩预告 财报 订单 回购 减持 并购 处罚 诉讼"),
        ("web", f"{primary} 产品 价格 产能 供应链 海外 政策"),
        ("em_news", f"{primary} site:eastmoney.com"),
        ("research", f"{primary} 研报 盈利预测 评级 site:eastmoney.com"),
        ("cls", f"{primary} site:cls.cn"),
        ("stcn", f"{primary} site:stcn.com"),
        ("yicai", f"{primary} site:yicai.com"),
        ("caixin", f"{primary} site:caixin.com"),
        ("xueqiu", f"site:xueqiu.com {primary}"),
    ]
    english = [a for a in aliases if re.search(r"[A-Za-z]{3,}", a)]
    foreign = english[:2] or ([name] if name else [])
    for term in foreign:
        jobs.extend([
            ("reuters", f'"{term}" site:reuters.com'),
            ("bloomberg", f'"{term}" site:bloomberg.com'),
            ("intl", f'"{term}" site:ft.com'),
            ("intl", f'"{term}" site:cnbc.com'),
        ])
    if foreign:
        jobs.append(("x", f'site:x.com "{foreign[0]}"'))
    seen, out = set(), []
    for channel, query in jobs:
        key = query.lower()
        if key not in seen:
            seen.add(key)
            out.append((channel, query))
    return out
