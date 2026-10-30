"""图表构建层：五联技术图、回测净值图、基本面趋势图。

只产出 plotly Figure，不依赖 streamlit，便于复用与测试。
配色沿用 ui_theme 令牌；A股惯例红涨绿跌。
"""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import ui_theme as ut

UP = ut.UP
DOWN = ut.DOWN

_MA_COLORS = {
    "ma5": "#f39c12",
    "ma10": "#3498db",
    "ma20": "#9b59b6",
    "ma60": "#16a085",
    "ma120": "#e67e22",
    "ma250": "#7f8c8d",
}

_GRID = "#20262E"
_GRID_LINE = "#2C3640"
_HLINE = "#bdc3c7"


def _base_layout(fig: go.Figure, height: int = 1080):
    fig.update_layout(
        height=height,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=ut.FONT_SANS.replace("'", '"'), size=13,
                  color=ut.TEXT_DIM),
        hovermode="x unified",
        margin=dict(l=8, r=14, t=34, b=6),
    )
    fig.update_xaxes(gridcolor=_GRID, linecolor=_GRID_LINE,
                     tickfont=dict(size=12))
    fig.update_yaxes(gridcolor=_GRID, zerolinecolor=_GRID_LINE,
                     tickfont=dict(size=12), fixedrange=False)
    return fig


def make_figure(df: pd.DataFrame) -> go.Figure:
    """五联图：K线/均线/布林 · 成交量 · MACD · KDJ · RSI。"""
    x = df["date"].astype(str)
    fig = make_subplots(
        rows=5, cols=1, shared_xaxes=True,
        row_heights=[0.42, 0.12, 0.16, 0.15, 0.15],
        vertical_spacing=0.03,
        subplot_titles=("K线 / 均线 / 布林带", "成交量", "MACD（DIF橙 / DEA蓝）",
                        "KDJ（K橙 / D蓝 / J紫）", "RSI（6橙 / 12蓝 / 24绿）"),
    )
    fig.add_trace(go.Candlestick(
        x=x, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        increasing_line_color=UP, decreasing_line_color=DOWN,
        increasing_fillcolor=UP, decreasing_fillcolor=DOWN,
        name="K线", showlegend=False,
    ), row=1, col=1)
    for col, c in _MA_COLORS.items():
        if col not in df.columns:
            continue
        fig.add_trace(go.Scatter(
            x=x, y=df[col], name=col.upper(),
            showlegend=col in ("ma5", "ma20", "ma60", "ma250"),
            line=dict(width=1.2, color=c)), row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["boll_up"], name="BOLL上/下轨",
                             line=dict(width=1, dash="dot", color="#95a5a6")), row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["boll_low"], name="BOLL带",
                             line=dict(width=1, dash="dot", color="#95a5a6"),
                             fill="tonexty", fillcolor="rgba(149,165,166,0.08)",
                             showlegend=False), row=1, col=1)

    vol_colors = [UP if c >= o else DOWN for c, o in zip(df["close"], df["open"])]
    fig.add_trace(go.Bar(x=x, y=df["volume"], marker_color=vol_colors,
                         name="成交量", showlegend=False), row=2, col=1)
    vol_ma = df["vol_ma5"] if "vol_ma5" in df.columns else df["volume"].rolling(5).mean()
    fig.add_trace(go.Scatter(x=x, y=vol_ma, name="VOL-MA5",
                             line=dict(width=1, color="#f39c12"), showlegend=False), row=2, col=1)

    macd_colors = [UP if v >= 0 else DOWN for v in df["macd"]]
    fig.add_trace(go.Bar(x=x, y=df["macd"], marker_color=macd_colors,
                         name="MACD柱", showlegend=False), row=3, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["dif"], name="DIF",
                             line=dict(width=1.2, color="#f39c12"),
                             showlegend=False), row=3, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["dea"], name="DEA",
                             line=dict(width=1.2, color="#3498db"),
                             showlegend=False), row=3, col=1)
    fig.add_hline(y=0, line=dict(width=0.8, color=_HLINE), row=3, col=1)

    fig.add_trace(go.Scatter(x=x, y=df["kdj_k"], name="K",
                             line=dict(width=1.2, color="#f39c12"),
                             showlegend=False), row=4, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["kdj_d"], name="D",
                             line=dict(width=1.2, color="#3498db"),
                             showlegend=False), row=4, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["kdj_j"], name="J",
                             line=dict(width=1, color="#9b59b6"),
                             showlegend=False), row=4, col=1)
    for level in (80, 20):
        fig.add_hline(y=level, line=dict(width=0.8, color=_HLINE, dash="dash"), row=4, col=1)

    fig.add_trace(go.Scatter(x=x, y=df["rsi6"], name="RSI6",
                             line=dict(width=1.2, color="#f39c12"),
                             showlegend=False), row=5, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["rsi12"], name="RSI12",
                             line=dict(width=1, color="#3498db"),
                             showlegend=False), row=5, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["rsi24"], name="RSI24",
                             line=dict(width=1, color="#16a085"),
                             showlegend=False), row=5, col=1)
    for level in (80, 50, 20):
        fig.add_hline(y=level, line=dict(width=0.8, color=_HLINE, dash="dash"), row=5, col=1)

    fig.update_layout(
        legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0,
                    bgcolor="rgba(0,0,0,0)", font=dict(size=12.5)),
    )
    fig.update_xaxes(type="category", rangeslider_visible=False)
    fig.update_yaxes(title_text="价格 (元)", row=1, col=1)
    fig.update_yaxes(title_text="成交量", row=2, col=1)
    return _base_layout(fig, height=880)


def make_backtest_figure(bt: dict) -> go.Figure:
    """回测净值图：策略净值 vs 买入持有 + 回撤副图。"""
    df = bt.get("equity_df")
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.7, 0.3], vertical_spacing=0.06,
        subplot_titles=("策略净值 vs 买入持有（起点=1.0）", "回撤"),
    )
    if df is not None and not df.empty:
        x = df["date"].astype(str)
        fig.add_trace(go.Scatter(
            x=x, y=df["strategy"], name="策略净值",
            line=dict(width=1.6, color=ut.ACCENT)), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=x, y=df["benchmark"], name="买入持有",
            line=dict(width=1.1, color="#93A1AF", dash="dot")), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=x, y=df["drawdown"] * 100, name="回撤%",
            line=dict(width=1, color=ut.WARN),
            fill="tozeroy", fillcolor="rgba(240,180,41,0.10)"), row=2, col=1)
    fig.update_layout(
        legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0,
                    bgcolor="rgba(0,0,0,0)"),
    )
    return _base_layout(fig, height=520)


def make_fundamental_figure(snap: dict) -> go.Figure:
    """基本面趋势图：盈利能力/负债率折线 + 营收/净利柱状（按报告期）。"""
    from fundamentals import revenue_profit_series, trend_series

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=False,
        row_heights=[0.55, 0.45], vertical_spacing=0.12,
        subplot_titles=("盈利能力与负债率（单季，%）", "营业总收入 / 净利润（按报告期）"),
    )
    line_specs = (
        ("roe", "ROE%", "#f39c12"),
        ("gross_margin", "毛利率%", "#3498db"),
        ("net_margin", "净利率%", "#9b59b6"),
        ("debt_ratio", "资产负债率%", "#7f8c8d"),
    )
    for key, label, color in line_specs:
        ps, vals = trend_series(snap, key)
        if not ps or not vals:
            continue
        fig.add_trace(go.Scatter(x=ps, y=vals, name=label,
                                 line=dict(width=1.3, color=color),
                                 mode="lines+markers", marker=dict(size=3)),
                      row=1, col=1)
    rp, rev, prof = revenue_profit_series(snap)
    if rp:
        fig.add_trace(go.Bar(x=rp, y=rev, name="营业总收入",
                             marker_color=ut.ACCENT), row=2, col=1)
        fig.add_trace(go.Bar(x=rp, y=prof, name="净利润",
                             marker_color=UP), row=2, col=1)
    fig.update_layout(barmode="group",
                      legend=dict(orientation="h", yanchor="bottom", y=1.01,
                                  x=0, bgcolor="rgba(0,0,0,0)"),
                      hovermode="x unified")
    return _base_layout(fig, height=560)


def make_shortterm_figure(df: pd.DataFrame, tail: int = 120) -> go.Figure:
    """短线图：收盘价走势 + 涨停/炸板标记 + 量能柱。"""
    d = df.tail(tail).reset_index(drop=True)
    x = d["date"].astype(str)
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.62, 0.38], vertical_spacing=0.06,
        subplot_titles=("收盘价走势（▲涨停 · ✕炸板 · ◆连板）", "成交量"),
    )
    fig.add_trace(go.Scatter(x=x, y=d["close"], name="收盘价",
                             line=dict(width=1.4, color=ut.ACCENT)),
                  row=1, col=1)
    if "is_limit_up" in d.columns:
        up = d["is_limit_up"].to_numpy(dtype=bool)
        fig.add_trace(go.Scatter(
            x=x, y=[float(c) if v else None for c, v in zip(d["close"], up)],
            name="涨停", mode="markers",
            marker=dict(symbol="triangle-up", size=9, color=UP)), row=1, col=1)
        if "raw_high" in d.columns and "limit_up_px" in d.columns:
            touch = ((d["raw_high"] + 1e-9 >= d["limit_up_px"])
                     & ~up & d["limit_up_px"].notna()).to_numpy()
            fig.add_trace(go.Scatter(
                x=x, y=[float(c) if v else None
                        for c, v in zip(d["close"], touch)],
                name="炸板", mode="markers",
                marker=dict(symbol="x", size=8, color=ut.WARN)), row=1, col=1)
    vol_colors = [UP if c >= o else DOWN
                  for c, o in zip(d["close"], d["open"])]
    fig.add_trace(go.Bar(x=x, y=d["volume"], marker_color=vol_colors,
                         name="成交量", showlegend=False), row=2, col=1)
    fig.update_layout(
        legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0,
                    bgcolor="rgba(0,0,0,0)"),
        hovermode="x unified",
    )
    return _base_layout(fig, height=560)


def make_limit_hist(bt: dict) -> go.Figure:
    """打板回测图：单笔收益直方图 + 累计净值曲线。"""
    trades = bt.get("trades") or []
    equity = bt.get("equity") or []
    fig = make_subplots(
        rows=1, cols=2, column_widths=[0.42, 0.58],
        horizontal_spacing=0.10,
        subplot_titles=("单笔收益分布（%）", "累计净值（起点=1）"),
    )
    if trades:
        rets = [t["return_pct"] for t in trades]
        fig.add_trace(go.Histogram(x=rets, nbinsx=24,
                                   marker_color=ut.ACCENT,
                                   name="单笔收益"), row=1, col=1)
    if equity:
        x = list(range(1, len(equity) + 1))
        colors = [UP if v >= 1 else DOWN for v in equity]
        fig.add_trace(go.Scatter(
            x=x, y=equity, name="累计净值",
            line=dict(width=1.5, color=ut.ACCENT),
            mode="lines+markers", marker=dict(size=3, color=colors)),
            row=1, col=2)
        fig.add_hline(y=1.0, line=dict(width=0.8, color=_HLINE, dash="dash"),
                      row=1, col=2)
    fig.update_layout(showlegend=False, bargap=0.05)
    return _base_layout(fig, height=380)
