import logging
import os
import time
from datetime import datetime

import numpy as np
import pandas as pd
import requests

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

_SINA_HQ_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0",
}

MIN_DAILY_BARS = 80
MIN_DAILY_BARS_WEEKLY = 300
CN_TZ = "Asia/Shanghai"


def normalize_symbol(code: str) -> str:
    code = code.strip().lower()
    if code[:2] in ("sh", "sz", "bj"):
        return code
    if not (code.isdigit() and len(code) == 6):
        raise ValueError("股票代码须为6位数字，如 600519")
    if code.startswith(("60", "68")):
        return "sh" + code
    if code.startswith(("00", "30")):
        return "sz" + code
    if code.startswith(("43", "83", "87", "88", "92")):
        return "bj" + code
    raise ValueError(f"无法识别的市场代码: {code}")


def is_st_name(name: str) -> bool:
    return "ST" in str(name or "").upper()


def limit_ratio(symbol: str, is_st: bool = False) -> float:
    """板块涨跌幅限制。

    创业板(300/301)与科创板(688)：20%（ST 股同样适用，不降档）；
    北交所：30%；主板：10%——按现行规则主板 ST 已放宽至 10%，
    is_st 参数仅为兼容旧调用而保留。
    """
    code = symbol[2:] if symbol[:2] in ("sh", "sz", "bj") else symbol
    if code.startswith(("300", "301", "688")):
        return 0.20
    if symbol.startswith("bj") or code.startswith(("43", "83", "87", "88", "92")):
        return 0.30
    return 0.10


def round_cent(values) -> pd.Series:
    """A 股涨跌停价常用四舍五入到分：floor(x*100+0.5)/100。"""
    series = values if isinstance(values, pd.Series) else pd.Series(values)
    x = series.to_numpy(dtype=float)
    out = np.floor(x * 100.0 + 0.5 + 1e-8) / 100.0
    out[~np.isfinite(x)] = np.nan
    return pd.Series(out, index=series.index)


def fetch_realtime(symbol: str) -> dict:
    url = f"https://hq.sinajs.cn/list={symbol}"
    last_err: Exception = None
    for _ in range(3):
        try:
            r = requests.get(url, headers=_SINA_HQ_HEADERS, timeout=10)
            text = r.content.decode("gbk", errors="ignore")
            payload = text.split('"')[1]
            fields = payload.split(",")
            if len(fields) > 32:
                return {
                    "name": fields[0],
                    "open": float(fields[1]),
                    "prev_close": float(fields[2]),
                    "price": float(fields[3]),
                    "high": float(fields[4]),
                    "low": float(fields[5]),
                    "volume": float(fields[8]),
                    "amount": float(fields[9]),
                    "date": fields[30],
                    "time": fields[31],
                }
        except Exception as e:
            last_err = e
        time.sleep(1)
    logger.warning("实时行情获取失败 %s：%s", symbol, last_err)
    return {}


def _fetch_daily_sina(symbol: str, adjust: str) -> pd.DataFrame:
    import akshare as ak
    df = ak.stock_zh_a_daily(symbol=symbol, adjust=adjust)
    df = df.reset_index()
    cols = {"date": "date", "open": "open", "high": "high", "low": "low",
            "close": "close", "volume": "volume", "amount": "amount",
            "turnover": "turnover", "outstanding_share": "outstanding_share"}
    df = df[[c for c in cols if c in df.columns]].rename(columns=cols)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def _fetch_daily_tx(symbol: str, adjust: str) -> pd.DataFrame:
    import akshare as ak
    code, num = symbol[:2], symbol[2:]
    df = ak.stock_zh_a_hist_tx(symbol=code + num, adjust=adjust)
    df = df.rename(columns={"turnover": "turnover"})
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def _sanitize_bars(df: pd.DataFrame) -> pd.DataFrame:
    need = {"open", "high", "low", "close"}
    if not need.issubset(df.columns):
        return df
    ok = (df["close"] > 0) & (df["open"] > 0) & (df["high"] >= df["low"]) & (df["low"] > 0)
    return df.loc[ok].reset_index(drop=True)


def _turnover_series_to_percent(series: pd.Series) -> pd.Series:
    """把换手率统一成百分数。新浪为成交量/流通股本(0.13=13%)，部分源已是百分数。"""
    s = pd.to_numeric(series, errors="coerce")
    sample = s.dropna().tail(250)
    if sample.empty:
        return s
    if sample.median() < 0.3 and (sample > 1).mean() < 0.02:
        return s * 100.0
    return s


def ensure_turnover_percent(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    vol = pd.to_numeric(out["volume"], errors="coerce") if "volume" in out.columns else None
    if vol is not None and "outstanding_share" in out.columns:
        shares = pd.to_numeric(out["outstanding_share"], errors="coerce")
        computed = vol / shares.replace(0, np.nan) * 100.0
        if computed.notna().sum() >= min(10, max(1, len(out) // 2)):
            out["turnover"] = computed
            return out.drop(columns=["outstanding_share"], errors="ignore")
    if "turnover" in out.columns:
        out["turnover"] = _turnover_series_to_percent(out["turnover"])
    return out.drop(columns=["outstanding_share"], errors="ignore")


def fetch_daily(symbol: str, adjust: str = "qfq", min_rows: int = MIN_DAILY_BARS) -> pd.DataFrame:
    errors = []
    for fetcher in (_fetch_daily_sina, _fetch_daily_tx):
        for attempt in range(2):
            try:
                df = ensure_turnover_percent(_sanitize_bars(fetcher(symbol, adjust)))
                if len(df) >= min_rows:
                    return df
                errors.append(f"{fetcher.__name__}: 数据不足({len(df)}行，需要≥{min_rows})")
                break
            except Exception as e:
                errors.append(f"{fetcher.__name__}第{attempt + 1}次: {type(e).__name__}")
                time.sleep(1.5)
    raise RuntimeError("行情获取失败：" + "；".join(errors))


def merge_raw_ohlc(df: pd.DataFrame, raw: pd.DataFrame) -> pd.DataFrame:
    cols = raw[["date", "open", "high", "low", "close"]].rename(
        columns={c: f"raw_{c}" for c in ("open", "high", "low", "close")}
    )
    return df.merge(cols, on="date", how="left")


def add_limit_flags(df: pd.DataFrame, symbol: str, is_st: bool = False) -> pd.DataFrame:
    out = df.copy()
    if "raw_close" not in out.columns:
        out["is_limit_up"] = False
        out["is_limit_down"] = False
        return out
    # 已知局限：除权除息日交易所以"除权后前收盘价"为涨跌停基准，
    # 此处用 raw_close.shift(1) 未做除权调整，该日标志可能漏判/误判；
    # 精确修复需数据源提供"前收"字段或按复权因子跳变还原基准价。
    ratio = limit_ratio(symbol, is_st=is_st)
    prev = out["raw_close"].shift(1)
    out["limit_up_px"] = round_cent(prev * (1.0 + ratio))
    out["limit_down_px"] = round_cent(prev * (1.0 - ratio))
    close = out["raw_close"]
    out["is_limit_up"] = close.notna() & out["limit_up_px"].notna() & (close + 1e-9 >= out["limit_up_px"])
    out["is_limit_down"] = close.notna() & out["limit_down_px"].notna() & (close - 1e-9 <= out["limit_down_px"])
    return out


def _naive_cn(ts) -> pd.Timestamp:
    """转为上海墙钟的无时区时间，避免 tz 感知时间在 pandas 2 上去掉时区失败。"""
    ts = pd.Timestamp(ts)
    if ts.tzinfo is not None:
        ts = ts.tz_convert(CN_TZ)
        ts = pd.Timestamp(
            year=ts.year, month=ts.month, day=ts.day,
            hour=ts.hour, minute=ts.minute, second=ts.second,
            microsecond=ts.microsecond,
        )
    return ts


def drop_unclosed_bar(df: pd.DataFrame, period: str, now=None) -> pd.DataFrame:
    """丢弃未收盘的日K / 当周未结束的周K，避免把残段当完成信号。"""
    if df is None or df.empty or "date" not in df.columns:
        return df
    if now is None:
        if ZoneInfo is not None:
            now = pd.Timestamp(datetime.now(ZoneInfo(CN_TZ)).replace(tzinfo=None))
        else:
            now = pd.Timestamp.now()
    else:
        now = _naive_cn(now)

    last = _naive_cn(df["date"].iloc[-1]).normalize()
    before_close = (now.hour, now.minute) < (15, 5)

    if period == "daily":
        if last == now.normalize() and before_close:
            return df.iloc[:-1].reset_index(drop=True)
        return df

    last_iso, now_iso = last.isocalendar(), now.isocalendar()
    same_week = (last_iso.year, last_iso.week) == (now_iso.year, now_iso.week)
    week_open = now.dayofweek < 4 or (now.dayofweek == 4 and before_close)
    if same_week and week_open:
        return df.iloc[:-1].reset_index(drop=True)
    return df


def to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    indexed = df.set_index("date")
    g = indexed.resample("W-FRI")
    agg_map = {
        "open": ("open", "first"),
        "high": ("high", "max"),
        "low": ("low", "min"),
        "close": ("close", "last"),
        "volume": ("volume", "sum"),
    }
    if "amount" in indexed.columns:
        agg_map["amount"] = ("amount", "sum")
    if "turnover" in indexed.columns:
        agg_map["turnover"] = ("turnover", "sum")
    extras = (
        ("raw_open", "first"),
        ("raw_high", "max"),
        ("raw_low", "min"),
        ("raw_close", "last"),
        ("is_limit_up", "last"),
        ("is_limit_down", "last"),
        ("limit_up_px", "last"),
        ("limit_down_px", "last"),
    )
    for col, how in extras:
        if col in indexed.columns:
            agg_map[col] = (col, how)
    weekly = g.agg(**agg_map).dropna(subset=["close"])
    return weekly.reset_index()


def get_history(symbol: str, period: str = "daily", adjust: str = "qfq",
                is_st: bool = False) -> pd.DataFrame:
    min_rows = MIN_DAILY_BARS_WEEKLY if period == "weekly" else MIN_DAILY_BARS
    df = fetch_daily(symbol, adjust, min_rows=min_rows)
    if adjust:
        try:
            raw = fetch_daily(symbol, "", min_rows=min_rows)
            df = merge_raw_ohlc(df, raw)
        except RuntimeError:
            pass
    else:
        df = df.copy()
        df["raw_open"] = df["open"]
        df["raw_high"] = df["high"]
        df["raw_low"] = df["low"]
        df["raw_close"] = df["close"]
    df = drop_unclosed_bar(df, "daily")
    df = add_limit_flags(df, symbol, is_st=is_st)
    if period == "weekly":
        df = to_weekly(df)
        df = drop_unclosed_bar(df, "weekly")
    return df
