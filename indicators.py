"""技术指标计算。

约定：
- add_* 直接在传入的 DataFrame 上原地写入指标列并返回同一对象；
  compute_all 内部先 copy，避免污染调用方数据。
- MACD/RSI/KDJ 采用通达信口径；BOLL 标准差为总体口径(ddof=0，同 TradingView)。
- 量比(vol_ratio)基准为截至上一周期的均量(shift 1)，避免当日本身稀释倍数。
"""
import numpy as np
import pandas as pd


DAILY_MA_PERIODS = (5, 10, 20, 60, 120, 250)
WEEKLY_MA_PERIODS = (5, 10, 20, 60)


def add_ma(df: pd.DataFrame, periods=DAILY_MA_PERIODS) -> pd.DataFrame:
    for p in periods:
        df[f"ma{p}"] = df["close"].rolling(p).mean()
    return df


def add_macd(df: pd.DataFrame, fast=12, slow=26, signal=9) -> pd.DataFrame:
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    df["dif"] = ema_fast - ema_slow
    df["dea"] = df["dif"].ewm(span=signal, adjust=False).mean()
    df["macd"] = 2 * (df["dif"] - df["dea"])
    return df


def _sma_cn(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(alpha=1.0 / n, adjust=False).mean()


def _sma_cn_seed(series: pd.Series, n: int, seed: float = 50.0) -> pd.Series:
    """通达信 SMA：Y = (X + (N-1)*Y')/N，无效值不更新，起始种子默认 50。"""
    alpha = 1.0 / n
    x = series.to_numpy(dtype=float, copy=False)
    out = np.empty(len(x), dtype=float)
    prev = float(seed)
    for i, v in enumerate(x):
        if not np.isfinite(v):
            out[i] = np.nan
            continue
        prev = (1.0 - alpha) * prev + alpha * v
        out[i] = prev
    return pd.Series(out, index=series.index)


def add_rsi(df: pd.DataFrame, periods=(6, 12, 24)) -> pd.DataFrame:
    delta = df["close"].diff()
    up = delta.clip(lower=0).fillna(0.0)
    down = (-delta).clip(lower=0).fillna(0.0)
    for p in periods:
        avg_up = _sma_cn(up, p).to_numpy(dtype=float)
        avg_down = _sma_cn(down, p).to_numpy(dtype=float)
        safe_down = np.where(avg_down == 0, np.nan, avg_down)
        with np.errstate(divide="ignore", invalid="ignore"):
            rs = avg_up / safe_down
            rsi = np.where(
                avg_down == 0,
                np.where(avg_up == 0, 50.0, 100.0),
                100.0 - 100.0 / (1.0 + rs),
            )
        df[f"rsi{p}"] = pd.Series(rsi, index=df.index)
    return df


def add_kdj(df: pd.DataFrame, n=9, m1=3, m2=3) -> pd.DataFrame:
    low_n = df["low"].rolling(n).min()
    high_n = df["high"].rolling(n).max()
    span = (high_n - low_n).replace(0, np.nan)
    rsv = (df["close"] - low_n) / span * 100
    k = _sma_cn_seed(rsv, m1)
    d = _sma_cn_seed(k, m2)
    df["kdj_k"] = k
    df["kdj_d"] = d
    df["kdj_j"] = 3 * k - 2 * d
    return df


def add_boll(df: pd.DataFrame, n=20, k=2) -> pd.DataFrame:
    mid = df["close"].rolling(n).mean()
    std = df["close"].rolling(n).std(ddof=0)
    df["boll_mid"] = mid
    df["boll_up"] = mid + k * std
    df["boll_low"] = mid - k * std
    return df


def add_volume(df: pd.DataFrame, n=5) -> pd.DataFrame:
    """均量线 + 量比。量比 = 当期成交量 / 截至上一周期的 n 期均量。"""
    df["vol_ma5"] = df["volume"].rolling(n).mean()
    base = df["vol_ma5"].shift(1)
    df["vol_ratio"] = df["volume"] / base.replace(0, np.nan)
    return df


def add_atr(df: pd.DataFrame, n=14) -> pd.DataFrame:
    """Wilder ATR：TR 的 ewm(alpha=1/n) 平滑，供价位参考模块使用。"""
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"],
         (df["high"] - prev_close).abs(),
         (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    df["atr"] = tr.ewm(alpha=1.0 / n, adjust=False).mean()
    return df


def compute_all(df: pd.DataFrame, period: str = "daily") -> pd.DataFrame:
    out = df.copy()
    ma_periods = WEEKLY_MA_PERIODS if period == "weekly" else DAILY_MA_PERIODS
    out = add_ma(out, ma_periods)
    out = add_boll(out)
    out = add_macd(out)
    out = add_kdj(out)
    out = add_rsi(out)
    out = add_atr(out)
    if "volume" in out.columns:
        out = add_volume(out)
    return out
