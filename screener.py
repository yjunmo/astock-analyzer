"""短线候选扫描：全市场按短线活跃度规则初筛 + 评分排序。

规则口径（规则化初筛，非投资建议）：
- 剔除 ST/退市、北交所（流动性差）、新股前缀 N/C；
- 价格区间 2~200 元；
- 涨跌幅 +1%~+9.5%（已启动但未封板/未跌停，次日可介入）；
- 换手率 3%~25%（活跃但非过度换手）、量比 ≥ 1.2（放量）；
- 成交额 ≥ 2 亿（流动性充足）；
- 评分 = 涨幅/量比/换手/成交额 的分位排名加权，输出前 N 名。

数据源：东财全市场快照（含量比/换手率）优先；失败降级新浪全市场快照
（仅涨跌幅/成交额口径，评分自动切换权重）。
纯计算与解析函数不依赖网络，可离线测试。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import numpy as np
import pandas as pd

from fundamentals import _TENCENT_HEADERS, _default_get, _to_num

PRICE_MIN = 2.0
PRICE_MAX = 200.0
CHG_MIN = 1.0
CHG_MAX = 9.5
CHG_MAX_GEX = 19.5    # 创业板/科创板涨跌停20%：未封板口径上限
TURNOVER_MIN = 3.0
TURNOVER_MAX = 25.0
VR_MIN = 1.2
AMT_MIN = 2e8
TOP_N = 30

BOARD_MAIN = "主板"
BOARD_GEX = "创业·科创"

_BOARD_CAPS = ((BOARD_MAIN, CHG_MAX), (BOARD_GEX, CHG_MAX_GEX))

_EM_TIMEOUT = 25.0     # 东财全市场分页抓取超时（秒；健康时也偏慢，超时走腾讯）
_SINA_TIMEOUT = 20.0   # 新浪全市场列表超时（秒）
_TX_CHUNK = 60         # 腾讯批量行情每请求条数
_TX_WORKERS = 8
_TX_BATCH_TIMEOUT = 45.0
_CODES_TIMEOUT = 20.0  # 代码列表抓取超时

_WEIGHTS_FULL = {"chg": 0.30, "vr": 0.25, "turnover": 0.25, "amount": 0.20}
_WEIGHTS_TX = {"chg": 0.35, "turnover": 0.35, "amount": 0.30}
_WEIGHTS_DEGRADED = {"chg": 0.55, "amount": 0.45}

_WEIGHT_COLS = {"chg": "涨跌幅", "vr": "量比", "turnover": "换手率",
                "amount": "成交额"}

_EM_COLS = {"代码", "名称", "最新价", "涨跌幅", "换手率", "量比", "成交额"}
_SINA_COLS = {"代码", "名称", "最新价", "涨跌幅", "成交额"}


def _call_with_timeout(fn, timeout: float):
    """在后台线程执行 fn，超时返回 None（线程随后自行结束）。

    不用 with 块：其 __exit__ 会 shutdown(wait=True)，若底层 akshare 调用
    没有内部超时，超时后整页仍会被卡住等线程结束；此处 wait=False 立即
    返回，由线程在后台自行退出。
    """
    ex = ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(fn)
    try:
        return fut.result(timeout=timeout)
    except Exception:  # noqa: BLE001
        return None
    finally:
        ex.shutdown(wait=False)


def _fetch_em(ak) -> Optional[pd.DataFrame]:
    fn = getattr(ak, "stock_zh_a_spot_em", None)
    if fn is None:
        return None
    return _call_with_timeout(fn, _EM_TIMEOUT)


def _fetch_sina(ak) -> Optional[pd.DataFrame]:
    fn = getattr(ak, "stock_zh_a_spot", None)
    if fn is None:
        return None
    return _call_with_timeout(fn, _SINA_TIMEOUT)


def _tencent_batch(codes, http_get=None, workers: int = _TX_WORKERS) -> pd.DataFrame:
    """腾讯批量行情：换手率/涨跌幅/成交额齐全，仅无量比。

    容错：腾讯在高并发下偶发 SSL 断连，单块重试一次；整批覆盖率不足
    60% 时对缺失代码补拉一轮，仍缺失则该批按实际返回处理。
    """
    import time

    import data_fetcher as dtf

    symbols = []
    for c in codes:
        try:
            symbols.append(dtf.normalize_symbol(str(c)))
        except ValueError:
            continue
    if not symbols:
        return pd.DataFrame()

    get = http_get or _default_get

    def one(chunk):
        url = "https://qt.gtimg.cn/q=" + ",".join(chunk)
        for attempt in range(2):
            try:
                r = get(url, timeout=10, headers=_TENCENT_HEADERS)
                text = r.content.decode("gbk", errors="ignore")
                out = {}
                for seg in text.split(";"):
                    if '"' not in seg:
                        continue
                    f = seg.split('"')[1].split("~")
                    if len(f) < 47 or not f[2].isdigit():
                        continue
                    amt = _to_num(f[37])
                    out[f[2]] = {
                        "名称": f[1],
                        "最新价": _to_num(f[3]),
                        "涨跌幅": _to_num(f[32]),
                        "换手率": _to_num(f[38]),
                        "成交额": amt * 1e4 if amt is not None else None,
                    }
                return out
            except Exception:  # noqa: BLE001 —— 偶发 SSL 断连，重试一次
                if attempt == 0:
                    time.sleep(0.5)
        return {}

    chunks = [symbols[i:i + _TX_CHUNK] for i in range(0, len(symbols), _TX_CHUNK)]
    merged = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for part in ex.map(one, chunks):
            merged.update(part)

    # 覆盖率补拉：缺失超过 40% 时对缺的代码再跑一轮
    missing = [s for s in symbols if s[2:] not in merged]
    if len(missing) > 0.4 * len(symbols):
        chunks2 = [missing[i:i + _TX_CHUNK]
                   for i in range(0, len(missing), _TX_CHUNK)]
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for part in ex.map(one, chunks2):
                merged.update(part)

    rows = [{"代码": code, **d} for code, d in merged.items()]
    return pd.DataFrame(rows)


def _code_col(df: pd.DataFrame) -> str:
    """代码列定位：优先列名含"代码/code"，否则取第一列。"""
    for c in df.columns:
        if "代码" in str(c) or "code" in str(c).lower():
            return c
    return df.columns[0]


def fetch_codes(ak_mod=None) -> list:
    """A股代码全列表。优先沪深两市独立接口并行抓取（快且稳，约数秒）；
    全部失败才回退 stock_info_a_code_name（爬交易所官网，约90秒且含北交所，
    而北交所本就不参与短线扫描）。代码列表变化极慢，调用方应按天缓存。"""
    ak = ak_mod
    if ak is None:
        import akshare as ak

    parts: dict = {}

    def grab(key, fn_name, *args):
        f = getattr(ak, fn_name, None)
        if f is None:
            return
        try:
            d = f(*args)
            if d is not None and not d.empty:
                parts[key] = [str(c) for c in d[_code_col(d)].tolist()]
        except Exception:  # noqa: BLE001
            pass

    specs = [("sh_main", "stock_info_sh_name_code", "主板A股"),
             ("sh_star", "stock_info_sh_name_code", "科创板"),
             ("sz", "stock_info_sz_name_code", "A股列表")]
    with ThreadPoolExecutor(max_workers=3) as ex:
        list(ex.map(lambda s: grab(*s), specs))
    codes, seen = [], set()
    for key in ("sh_main", "sh_star", "sz"):
        for c in parts.get(key, []):
            if c not in seen:
                seen.add(c)
                codes.append(c)
    if codes:
        return codes

    fn = getattr(ak, "stock_info_a_code_name", None)
    if fn is not None:
        try:
            df = _call_with_timeout(fn, _CODES_TIMEOUT)
            if df is not None and not df.empty:
                return [str(c) for c in df[_code_col(df)].tolist()]
        except Exception:  # noqa: BLE001
            pass
    return []


def fetch_spot_market(ak_mod=None, http_get=None, codes: Optional[list] = None) -> tuple:
    """全市场快照。三级兜底：腾讯批量（换手率齐全、无量比，快且稳）
    → 东财(全字段含量比，但接口慢/易抖动) → 新浪(仅涨幅/成交额)。

    codes 可传入缓存好的A股代码列表（省去列表抓取）；缺省时内部抓取。
    返回 (df, source)；source ∈ 'tx'/'em'/'sina'/None（全部失败）。
    """
    ak = ak_mod
    if ak is None:
        import akshare as ak

    def clean(df):
        if df is None or df.empty:
            return None
        out = df.copy()
        out["代码"] = out["代码"].astype(str).str.zfill(6)
        for c in ("最新价", "涨跌幅", "换手率", "量比", "成交额"):
            if c in out.columns:
                out[c] = pd.to_numeric(out[c], errors="coerce")
        return out if ("代码" in out.columns and "涨跌幅" in out.columns) else None

    if codes is None:
        codes = fetch_codes(ak_mod=ak)
    if codes:
        tx = clean(_call_with_timeout(
            lambda: _tencent_batch(codes, http_get=http_get),
            _TX_BATCH_TIMEOUT))
        if tx is not None:
            return tx, "tx"

    em = clean(_call_with_timeout(
        lambda: _fetch_em(ak), _EM_TIMEOUT))
    if em is not None:
        return em, "em"

    sina = clean(_call_with_timeout(
        lambda: _fetch_sina(ak), _SINA_TIMEOUT))
    if sina is not None:
        return sina, "sina"
    return pd.DataFrame(), None


def _excluded_name(name: str) -> bool:
    n = str(name or "").upper()
    if "ST" in n or "退" in n:
        return True
    return n.startswith(("N", "C"))


def _excluded_code(code6: str) -> bool:
    return code6.startswith(("43", "83", "87", "88", "92"))


def _board_of(code6: str) -> str:
    """板块归属：300/301/688/689 → 创业·科创；其余 → 主板。"""
    c = str(code6).zfill(6)
    if c.startswith(("300", "301", "688", "689")):
        return BOARD_GEX
    return BOARD_MAIN


def filter_codes_for_scope(codes, include_gex: bool = True) -> list:
    """拉取前过滤代码列表：关闭创科时仅保留沪深主板，减少约1/3批量请求。"""
    if include_gex:
        return list(codes)
    return [c for c in codes if _board_of(c) == BOARD_MAIN]


def screen(df: pd.DataFrame, source: str = "em",
           limit: int = TOP_N) -> dict:
    """按短线活跃度规则筛选并评分。返回 {ok, rows, boards, note, n_total, degraded}。

    板块分层：主板涨幅≤9.5%、创业/科创≤19.5%（涨跌停口径不同），
    各自在组内做分位排名打分；rows 为两组合并（含「板块」列，按得分降序），
    boards = {"主板": {"rows", "n"}, "创业·科创": {"rows", "n"}}。
    source='em' 全字段（量比+换手）；'tx' 缺量比（按换手+涨幅+成交额）；
    'sina' 仅涨幅/成交额。缺失字段自动降级权重与过滤条件。
    """
    if df is None or df.empty or "代码" not in df.columns:
        return {"ok": False, "reason": "全市场快照获取失败"}
    d = df.copy()
    d = d[~d["名称"].map(_excluded_name) & ~d["代码"].map(_excluded_code)]
    n_total = len(d)

    d = d[(d["最新价"] >= PRICE_MIN) & (d["最新价"] <= PRICE_MAX)]
    d = d[d["成交额"] >= AMT_MIN]

    has_to = "换手率" in d.columns
    has_vr = "量比" in d.columns
    if has_to:
        d = d[(d["换手率"] >= TURNOVER_MIN) & (d["换手率"] <= TURNOVER_MAX)]
    if has_vr:
        d = d[d["量比"] >= VR_MIN]

    if source == "em" and has_to and has_vr:
        weights, degraded, note = _WEIGHTS_FULL, False, \
            "东财全市场快照（涨跌幅/量比/换手/成交额）"
    elif has_to:
        weights, degraded, note = _WEIGHTS_TX, True, \
            "腾讯行情口径：换手/涨幅/成交额齐全，但无量比过滤"
    else:
        weights, degraded, note = _WEIGHTS_DEGRADED, True, \
            "数据源降级：仅含涨跌幅/成交额，未过滤量比与换手"
    note += "；涨幅上限分板块：主板≤9.5%、创业/科创≤19.5%"

    need = [_WEIGHT_COLS[c] for c in weights if _WEIGHT_COLS[c] in d.columns]
    d = d.dropna(subset=need)

    if d.empty:
        return {"ok": True, "rows": pd.DataFrame(), "boards": {},
                "n_total": n_total, "degraded": degraded,
                "note": note + "；无满足条件的候选（今日短线活跃度不足）"}

    d["板块"] = d["代码"].map(_board_of)

    def pct_rank(s: pd.Series) -> pd.Series:
        return s.rank(pct=True) * 100.0

    def score_one(sub: pd.DataFrame) -> pd.DataFrame:
        """组内分位排名评分并截断输出。"""
        if sub.empty:
            return sub
        score_v = np.zeros(len(sub))
        for key, w in weights.items():
            col = _WEIGHT_COLS[key]
            if col in sub.columns:
                score_v += w * pct_rank(sub[col]).to_numpy()
        out = sub.assign(得分=score_v.round(1)).sort_values("得分", ascending=False)
        cols = ["代码", "名称", "板块", "最新价", "涨跌幅"]
        if has_to:
            cols.append("换手率")
        if has_vr:
            cols.append("量比")
        cols += ["成交额", "得分"]
        rows = out.head(limit)[cols].reset_index(drop=True)
        rows["成交额"] = (rows["成交额"] / 1e8).round(1)  # 亿
        return rows

    boards = {}
    for board, cap in _BOARD_CAPS:
        sub = d[(d["板块"] == board)
                & (d["涨跌幅"] >= CHG_MIN) & (d["涨跌幅"] <= cap)]
        rows_b = score_one(sub)
        boards[board] = {"rows": rows_b, "n": len(rows_b)}

    parts = [b["rows"] for b in boards.values() if b["rows"] is not None
             and not b["rows"].empty]
    combined = (pd.concat(parts, ignore_index=True)
                if parts else pd.DataFrame())
    if not combined.empty:
        combined = combined.sort_values("得分", ascending=False).reset_index(drop=True)
    if combined.empty:
        return {"ok": True, "rows": combined, "boards": boards,
                "n_total": n_total, "degraded": degraded,
                "note": note + "；无满足条件的候选（今日短线活跃度不足）"}
    return {"ok": True, "rows": combined, "boards": boards,
            "n_total": n_total, "degraded": degraded, "note": note}


def candidates_text(res: dict, limit: int = 40) -> str:
    """候选表 → 注入 AI 的紧凑文本。"""
    if not res.get("ok") or res.get("rows") is None or res["rows"].empty:
        return "（暂无符合短线规则的候选股）"
    rows = res["rows"].head(limit)
    cols = ["代码", "名称", "最新价", "涨跌幅"]
    if "板块" in rows.columns:
        cols.append("板块")
    if "换手率" in rows.columns:
        cols.append("换手率")
    if "量比" in rows.columns:
        cols.append("量比")
    cols += ["成交额", "得分"]
    lines = ["|".join(cols)]
    for _, r in rows.iterrows():
        lines.append("|".join(
            f"{r[c]:.2f}" if isinstance(r[c], float) and c != "成交额"
            else f"{r[c]}" for c in cols))
    lines.append(f"（共扫描 {res['n_total']} 只非ST个股，" + res["note"] + "）")
    return "\n".join(lines)
