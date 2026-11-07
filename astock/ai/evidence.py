"""统一证据注册表与引用审计。引用有效性不等于语义或投资判断正确。"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

CITATION_RE = re.compile(r"\[((?:T|M|F|B|S|N|P|K|W)\d+)\]")
_MISSING = re.compile(r"^\s*[（(]?(?:数据缺失|数据不足|基本面数据缺失|未采集|未加载|未取得|未运行|多周期对比数据缺失|短线分析数据缺失|尚未建立|无实时快照|联网研究模式)")


def context_evidence(messages: list) -> tuple[dict, dict]:
    registry, scope = {}, {}
    for message in messages:
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "")
        if not content.lstrip().startswith("<research_context "):
            continue
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            continue
        instrument = root.attrib.get("instrument", "")
        scope["instrument"] = instrument
        match = re.search(r"^(.*?)\s*[（(](\d{6})[）)]", instrument)
        if match:
            scope.update(name=match[1].strip(), code=match[2])
        for node in root.findall("evidence"):
            sid, text = node.attrib.get("id", ""), "".join(node.itertext()).strip()
            if sid == "N":
                for part in re.split(r"(?=\[N\d+\])", text):
                    found = CITATION_RE.match(part)
                    if found:
                        registry[found[1]] = {"text": part, "available": "原文状态=read" in part,
                                               "kind": "news"}
            elif re.fullmatch(r"(?:T|M|F|B|S|P|K)\d+", sid):
                registry[sid] = {"text": text, "kind": "local",
                                 "available": bool(text) and not bool(_MISSING.match(text))}
    scope["fundamentals"] = registry.get("F1", {}).get("text", "")
    scope['business'] = registry.get('F2', {}).get('text', '')
    period = re.search(r"报告期[：:]\s*(\d{4}-\d{2}-\d{2})", scope['fundamentals'])
    if period:
        scope['report_period'] = period[1]
    return registry, scope


def with_web_evidence(registry: dict, report: dict | None) -> dict:
    result = dict(registry)
    if not report:
        return result
    supported = set()
    docs = {d["source_id"]: d for d in report.get("sources", []) if d.get("source_id")}
    for claim in report.get("claims", []):
        if claim.get("status") not in {"single_source", "multiple_sources"}:
            continue
        for ref in claim.get("evidence", []):
            doc = docs.get(ref.get("source_id"), {})
            quote = str(ref.get("quote") or "")
            if len(quote) >= 4 and quote in str(doc.get("body") or ""):
                supported.add(ref["source_id"])
    for sid, doc in docs.items():
        result[sid] = {"available": doc.get("read_status") == "read"
                      and doc.get("relevant") is not False and sid in supported,
                       "kind": "web", "url": doc.get("url", ""),
                       "read_status": doc.get("read_status", "not_read")}
    return result


def audit_citations(answer: str, registry: dict, report: dict | None = None) -> dict:
    cited = list(dict.fromkeys(CITATION_RE.findall(answer)))
    invalid = [sid for sid in cited if sid not in registry]
    unsupported = [sid for sid in cited if sid in registry and not registry[sid].get("available")]
    valid = [sid for sid in cited if registry.get(sid, {}).get("available")]
    # 百分比衡量已使用引用的可核验性，不以来源数量冒充问题覆盖或事实正确率。
    coverage = round(len(valid) / len(cited) * 100, 1) if cited else 0.0
    blocking = report.get('critical_gaps') if report else None
    gaps = list(blocking if isinstance(blocking, list) else report.get('gaps', [])) if report else []
    if report and report.get('status') != 'complete' and not gaps:
        gaps.append('联网研究仍未通过完成检查')
    if invalid:
        gaps.append("不存在的证据编号：" + "、".join(invalid))
    if unsupported:
        gaps.append("未获原文/数据支持的引用：" + "、".join(unsupported))
    if not valid:
        gaps.append("没有可核验引用")
    return {"valid_citations": valid, "invalid_citations": invalid,
            "unsupported_citations": unsupported, "coverage": coverage,
            "coverage_label": "引用可核验率（非问题覆盖率或事实正确率）",
            "uncertainties": list(report.get('gaps', [])) if report else [],
            "search_status": report.get("status") if report else "off",
            "gaps": gaps, "complete": bool(valid) and not invalid and not unsupported
            and not gaps and (not report or report.get("status") == "complete")}


def sanitize_citations(answer: str, registry: dict) -> str:
    def replace(match):
        sid = match[1]
        if sid not in registry:
            return "[来源待核验]"
        if not registry[sid].get("available"):
            return f"[线索{sid}，未核验]"
        return match[0]
    return CITATION_RE.sub(replace, answer)
