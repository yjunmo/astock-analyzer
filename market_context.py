"""市场环境上下文采集：为 AI 提供"技术面之外"的客观数据快照。

覆盖五个维度所需的原始数据（均来自公开接口，逐项容错，失败不致命）：
1. 市场整体情绪：涨跌家数、涨停/跌停家数与连板高度
2. 板块情况：个股所属行业板块当日涨跌幅、近5日趋势、板块内涨跌居前个股
3. 消息面：个股最新新闻列表 + 尽力而为的网页检索（DuckDuckGo/Bing）
4. 机构属性：龙虎榜上榜统计；成交额全市场排名
5. 北向资金：沪深港通资金流向摘要

输出为压缩文本占位符 {market_ctx}，由 ai_context 注入提示词。
数据仅代表抓取时点快照，AI 需基于此分析而非编造。
"""
from __future__ import annotations

import os

# 国内数据源直连：避免系统代理拦截东财/新浪请求（ddgs 已显式传代理，不受影响）
os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

from datetime import datetime, timedelta
from typing import Callable, Optional

import pandas as pd

NEWS_LIMIT = 8          # 注入的新闻条数
SEARCH_LIMIT = 5        # 网页检索结果条数
SEARCH_TIMEOUT = 6      # 单次网页检索超时(秒)
BING_URL = "https://cn.bing.com/search?q={q}&count=10"


def _today_str(fmt: str = "%Y%m%d") -> str:
    return datetime.now().strftime(fmt)


def _num(v, nd: int = 0) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    if f != f:  # NaN
        return "-"
    return f"{f:.{nd}f}"


def _safe(section: str, fn: Callable[[], list]) -> str:
    """执行采集函数；瞬时网络错误自动重试一次，仍失败降级为该节'获取失败'。"""
    last_err = None
    for attempt in range(2):
        try:
            lines = fn() or []
            if not lines:
                return f"### {section}\n（接口未返回数据）"
            return f"### {section}\n" + "\n".join(lines)
        except Exception as e:  # noqa: BLE001 —— 单节失败不影响整体
            last_err = e
            if attempt == 0:
                import time
                time.sleep(0.8)  # 东财接口偶发抖动，稍候重试
    return f"### {section}\n（获取失败：{type(last_err).__name__}）"


def _col(row, key, default="-"):
    v = row.get(key)
    return default if v is None or (isinstance(v, float) and v != v) else v


# ---------------------------------------------------------------- 市场情绪

def _breadth_and_rank(code6: str) -> list:
    """全市场快照 → 涨跌家数分布 + 个股成交额排名。"""
    import akshare as ak
    df = ak.stock_zh_a_spot_em()
    pct = pd.to_numeric(df["涨跌幅"], errors="coerce")
    up = int((pct > 0).sum())
    down = int((pct < 0).sum())
    flat = int(pct.notna().sum()) - up - down
    amt = pd.to_numeric(df["成交额"], errors="coerce")
    total_amt_yi = amt.sum() / 1e8

    rows = [f"- 沪深A股上涨 {up} 家 / 下跌 {down} 家 / 平盘 {flat} 家",
            f"- 两市总成交额约 {_num(total_amt_yi, 0)} 亿元"]

    hit = df[df["代码"].astype(str).str.zfill(6) == code6]
    if not hit.empty:
        r = hit.iloc[0]
        rank = int(amt.rank(ascending=False, na_option="bottom")[hit.index[0]])
        rows.append(f"- 本股成交额全市场排名 第{rank} 位"
                    f"（{_num(float(r['成交额']) / 1e8, 2)}亿元，"
                    f"现价{_num(r['最新价'], 2)} 涨跌幅{_num(r['涨跌幅'], 2)}%）")
    return rows


def _zt_dt_pools() -> list:
    """涨停池（含连板高度）与跌停池统计。"""
    import akshare as ak
    d = _today_str()
    out = []
    zt = ak.stock_zt_pool_em(date=d)
    if zt is not None and len(zt):
        lb = pd.to_numeric(zt.get("连板数"), errors="coerce")
        out.append(f"- 今日涨停 {len(zt)} 家，最高连板 {_num(lb.max())} 板")
        top = zt.assign(_lb=lb).sort_values("_lb", ascending=False).head(3)
        names = "、".join(f"{r['名称']}({int(r['_lb'])}板)" for _, r in top.iterrows())
        out.append(f"- 连板高标：{names}")
    else:
        out.append("- 今日涨停池为空或非交易日")
    dt = ak.stock_zt_pool_dtgc_em(date=d)
    n = 0 if dt is None else len(dt)
    out.append(f"- 今日跌停 {n} 家")
    return out


def _northbound() -> list:
    """北向资金摘要（字段随接口版本浮动，防御式解析）。"""
    import akshare as ak
    df = ak.stock_hsgt_fund_flow_summary_em()
    recs = df.to_dict("records")
    # 优先精确匹配"北向"行，避免误取沪股通/深股通单边数据
    best = None
    for row in recs:
        cells = [str(v).strip() for v in row.values()]
        if any(c == "北向" for c in cells):
            best = row
            break
    if best is None:
        for row in recs:
            if "北向" in " ".join(str(v) for v in row.values()):
                best = row
                break
    if best is not None:
        kv = "，".join(f"{k}:{v}" for k, v in list(best.items())[:6])
        return [f"- {kv}"]
    return ["- 未识别到北向汇总行"]


# ---------------------------------------------------------------- 板块

def _sector(code6: str) -> list:
    import akshare as ak
    info = ak.stock_individual_info_em(symbol=code6)
    ind = ""
    for _, r in info.iterrows():
        if str(r.get("item")) == "行业":
            ind = str(r.get("value"))
            break
    if not ind:
        return ["- 未能识别所属行业"]
    boards = ak.stock_board_industry_name_em()
    row = boards[boards["板块名称"].astype(str) == ind]
    if row.empty:
        return [f"- 所属行业「{ind}」未在东财行业板块列表中找到"]
    r = row.iloc[0]
    out = [f"- 所属行业板块：{ind}，当日涨跌幅 {_num(r.get('涨跌幅'), 2)}%",
           f"  板块内上涨 {_num(r.get('上涨家数'))} 家 / 下跌 {_num(r.get('下跌家数'))} 家"]

    end = datetime.now()
    start = end - timedelta(days=10)
    hist = ak.stock_board_industry_hist_em(
        symbol=ind, start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"), period="daily", adjust="")
    if hist is not None and len(hist) >= 2:
        closes = pd.to_numeric(hist["收盘"], errors="coerce").dropna()
        if len(closes) >= 2:
            lookback = min(5, len(closes) - 1)
            chg = (closes.iloc[-1] / closes.iloc[-1 - lookback] - 1) * 100
            out.append(f"  近{lookback}个交易日板块累计 {_num(chg, 2)}%")

    cons = ak.stock_board_industry_cons_em(symbol=ind)
    cpct = pd.to_numeric(cons["涨跌幅"], errors="coerce")
    ranked = cons.assign(_p=cpct).sort_values("_p", ascending=False)
    top = ranked.head(3)
    out.append("  板块领涨：" + "、".join(
        f"{r['名称']}{_num(r['_p'], 2)}%" for _, r in top.iterrows()))
    lows = ranked.iloc[len(top):].dropna(subset=["_p"]).tail(2)
    if len(lows):
        out.append("  板块领跌：" + "、".join(
            f"{r['名称']}{_num(r['_p'], 2)}%" for _, r in lows.iterrows()))
    return out


# ---------------------------------------------------------------- 指数与公告

def _indices() -> list:
    """主要指数当日点位/涨跌幅/成交额——大盘量能的结构化来源。"""
    import akshare as ak
    df = ak.stock_zh_index_spot_em(symbol="沪深重要指数")
    want = ("上证指数", "深证成指", "创业板指", "科创50")
    out = []
    for _, r in df.iterrows():
        if r.get("名称") in want:
            chg = float(r.get("涨跌幅") or 0)
            amt = float(r.get("成交额") or 0) / 1e8
            out.append(f"- {r['名称']} {_num(r.get('最新价'), 2)}"
                       f"（{chg:+.2f}%）成交额 {_num(amt)} 亿")
    return out or ["- 未匹配到主要指数行"]


def _disclosures(code6: str) -> list:
    """近两周公告（巨潮资讯）——财报等重大事项的权威结构化来源。"""
    import akshare as ak
    end = datetime.now()
    start = end - timedelta(days=14)
    df = None
    for market in ("沪深京", "深市", "沪市"):
        try:
            df = ak.stock_zh_a_disclosure_report_cninfo(
                symbol=code6, market=market,
                start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"))
        except Exception:
            continue
        if df is not None and len(df):
            break
    if df is None or df.empty:
        return [f"- 近14日无公告（或晚间披露尚未同步至巨潮接口）"]
    title_col = next(c for c in df.columns if "标题" in str(c))
    date_col = next((c for c in df.columns if "日期" in str(c)), None)
    out = []
    for _, r in df.head(8).iterrows():
        d = str(r[date_col])[:10] if date_col else "-"
        out.append(f"- [{d}] {r[title_col]}")
    return out


# ---------------------------------------------------------------- 消息面

def _news(code6: str) -> list:
    import akshare as ak
    df = ak.stock_news_em(symbol=code6)
    if df is None or df.empty:
        return ["- 近期无个股新闻"]
    out = []
    for _, r in df.head(NEWS_LIMIT).iterrows():
        t = str(r.get("发布时间", ""))[:16].replace("-", "-")
        out.append(f"- [{t}] ({r.get('文章来源', '-')}) {r.get('新闻标题', '')}")
    return out


# ---------------------------------------------------------------- 龙虎榜

def _lhb(code6: str) -> list:
    import akshare as ak
    for span in ("近三月", "近一月"):
        try:
            stat = ak.stock_lhb_stock_statistic_em(symbol=span)
        except Exception:
            continue
        hit = stat[stat["代码"].astype(str).str.zfill(6) == code6]
        if hit.empty:
            continue
        r = hit.iloc[0].to_dict()
        parts = []
        for k, v in r.items():
            ks = str(k)
            if any(w in ks for w in ("上榜次数", "净买额", "机构")):
                parts.append(f"{ks}:{_num(v) if isinstance(v, (int, float)) else v}")
        return [f"- {span}龙虎榜：" + ("，".join(parts) if parts else str(list(r.values())[:4]))]
    return [f"- 近三个月无龙虎榜上榜记录"]


# ---------------------------------------------------------------- 组装

def build_market_context(code6: str, name: str = "",
                         fetcher: Optional[Callable[[str], str]] = None) -> str:
    """生成 {market_ctx} 文本。fetcher 参数仅供测试注入替代抓取逻辑。"""
    code6 = str(code6).zfill(6)
    sections: list[tuple[str, Callable[[], list]]] = [
        ("市场情绪（涨跌家数/成交额）", lambda: _breadth_and_rank(code6)),
        ("主要指数与大盘量能", _indices),
        ("涨停跌停与连板高度", _zt_dt_pools),
        ("所属行业板块", lambda: _sector(code6)),
        ("北向资金", _northbound),
        ("龙虎榜机构统计", lambda: _lhb(code6)),
        ("近两周公告（巨潮资讯·财报等权威来源）", lambda: _disclosures(code6)),
        ("个股最新新闻", lambda: _news(code6)),
    ]
    blocks = []
    # 各节并行抓取：总耗时≈最慢一节，而非逐节累加（网页检索单节可达数秒）
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(len(sections), 10)) as ex:
        for block in ex.map(lambda pair: _safe(*pair), sections):
            blocks.append(block)
    header = (f"数据时点：{datetime.now().strftime('%Y-%m-%d %H:%M')}（抓取时点快照，"
              "非实时行情；标注'获取失败/不可用'的维度请如实说明数据缺口，禁止编造）")
    return header + "\n\n" + "\n\n".join(blocks)
