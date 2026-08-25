"""UI 设计令牌与组件：深色专业金融风格。

层级原则：
- 一级信息（现价/涨跌幅/AI结论）：大字号、粗字重、语义色、首屏顶部
- 二级信息（OHLC/换手/市值等）：小字号、次级灰、等宽数字
配色遵循A股惯例：红涨绿跌；中性色为主基调，语义色仅用于数据本身。

本模块只产出 HTML 字符串，不依赖 streamlit/pandas，便于离线单测。
"""
import html as _html_mod

# ---- 设计令牌 ------------------------------------------------------------
BG = "#0C1117"
PANEL = "#12171E"
PANEL_2 = "#181F27"
BORDER = "#242D37"
BORDER_HOVER = "#35424F"

TEXT = "#E8EEF4"
TEXT_DIM = "#93A1AF"
TEXT_FAINT = "#5F6E7B"

UP = "#FF5D5D"      # 涨(红)
DOWN = "#26C281"    # 跌(绿)
FLAT = "#93A1AF"
ACCENT = "#58A6FF"
WARN = "#F0B429"

TONE_COLOR = {"bull": UP, "bear": DOWN, None: FLAT}

# signals 模块使用 "bullish"/"bearish"，组件内部用 "bull"/"bear"，此处归一化
_TONE_ALIAS = {"bull": "bull", "bullish": "bull",
               "bear": "bear", "bearish": "bear"}


def canon_tone(tone):
    """归一化语气值；未知值一律视为中性(None)。"""
    if tone is None:
        return None
    return _TONE_ALIAS.get(str(tone))

FONT_SANS = ('-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif')
FONT_NUM = ('"JetBrains Mono","SF Mono",Consolas,"Courier New",monospace')


def apply_global_css() -> str:
    """全局样式注入：令牌、排版、面板、徽章、悬停态。随页面渲染一次。"""
    return f"""
<style>
:root {{
  --bg:{BG}; --panel:{PANEL}; --panel2:{PANEL_2};
  --border:{BORDER}; --border-h:{BORDER_HOVER};
  --text:{TEXT}; --dim:{TEXT_DIM}; --faint:{TEXT_FAINT};
  --up:{UP}; --down:{DOWN}; --flat:{FLAT};
  --accent:{ACCENT}; --warn:{WARN};
}}
.stApp {{ background:{BG}; }}
section[data-testid="stSidebar"] {{
  background:#10151C; border-right:1px solid var(--border);
}}
.block-container {{ padding:2.4rem 1.4rem 2rem; max-width:1440px; }}
/* Streamlit 固定头部为半透明，避免遮挡首行内容 */
header[data-testid="stHeader"] {{
  background:rgba(12,17,23,.55); backdrop-filter:blur(6px);
}}
h1,h2,h3,.stMarkdown,.stText,.stDataFrame {{ color:var(--text); }}
#MainMenu,footer,[data-testid="stStatusWidget"] {{ visibility:hidden; }}

.num, .stMetric, [data-testid="stMetricValue"] {{
  font-variant-numeric:tabular-nums;
}}

/* ---- 面板卡片 ---- */
.card {{
  background:linear-gradient(180deg,var(--panel) 0%,var(--panel2) 100%);
  border:1px solid var(--border); border-radius:12px;
  padding:12px 14px; height:100%;
  transition:border-color .15s ease, transform .15s ease;
}}
.card:hover {{ border-color:var(--border-h); transform:translateY(-1px); }}

/* ---- 顶部行情概览 ---- */
.hero {{ display:flex; gap:18px; align-items:center; flex-wrap:wrap;
  background:linear-gradient(180deg,var(--panel) 0%,var(--panel2) 100%);
  border:1px solid var(--border); border-radius:14px; padding:14px 20px; }}
.hero .idbox .name {{ font-size:20px; font-weight:700; color:var(--text);
  line-height:1.2; white-space:nowrap; }}
.hero .idbox .code {{ font-size:13px; color:var(--dim);
  font-family:{FONT_NUM}; margin-top:2px; white-space:nowrap; }}
.hero .price {{ font-size:clamp(30px,3.2vw,42px); font-weight:700; line-height:1;
  font-family:{FONT_NUM}; font-variant-numeric:tabular-nums; white-space:nowrap; }}
.chg {{ display:inline-flex; align-items:baseline; gap:8px;
  font-family:{FONT_NUM}; font-size:15px; font-weight:600;
  padding:3px 10px; border-radius:8px; margin-top:4px; }}
.chg.up   {{ color:var(--up);   background:rgba(255,93,93,.10); }}
.chg.down {{ color:var(--down); background:rgba(38,194,129,.10); }}
.chg.flat {{ color:var(--flat); background:rgba(147,161,175,.10); }}
.ohlc {{ display:flex; gap:16px; flex-wrap:wrap; }}
.ohlc .it {{ min-width:64px; }}
.lbl {{ font-size:11px; color:var(--faint); letter-spacing:.06em; }}
.val {{ font-size:14px; color:var(--text); font-family:{FONT_NUM};
  font-variant-numeric:tabular-nums; }}
.badge {{ font-size:11px; color:var(--dim); border:1px solid var(--border);
  border-radius:999px; padding:2px 9px; white-space:nowrap; }}
.ts {{ font-size:11px; color:var(--faint); font-family:{FONT_NUM}; }}

/* ---- 结论横幅 ---- */
.banner {{ border:1px solid var(--border); border-left-width:4px;
  border-radius:12px; padding:12px 16px;
  background:linear-gradient(90deg,var(--tint) 0%,transparent 55%),
             var(--panel); }}
.banner .head {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; }}
.banner .verdict {{ font-size:17px; font-weight:700; color:var(--tone); }}
.banner .score {{ font-family:{FONT_NUM}; font-size:13px; color:var(--dim); }}
.banner .ops  {{ font-size:12.5px; color:var(--dim); margin-top:6px; }}

/* ---- KPI 卡片 ---- */
.kpi .lbl {{ margin-bottom:4px; }}
.kpi .val {{ font-size:19px; font-weight:600; }}
.kpi .sub {{ font-size:11px; color:var(--faint); margin-top:3px;
  font-family:{FONT_NUM}; }}
.kpi.pos .val {{ color:var(--up); }}
.kpi.neg .val {{ color:var(--down); }}

/* ---- 信号条目 ---- */
.sig {{ display:flex; gap:10px; align-items:flex-start;
  padding:7px 10px; border-radius:8px; border:1px solid transparent; }}
.sig:hover {{ background:var(--panel2); border-color:var(--border); }}
.sig .mk {{ font-weight:700; font-family:{FONT_NUM}; width:16px;
  text-align:center; flex:none; }}
.sig.bull .mk, .sig.bull .tag {{ color:var(--up); }}
.sig.bear .mk, .sig.bear .tag {{ color:var(--down); }}
.sig.flat .mk, .sig.flat .tag {{ color:var(--flat); }}
.sig .tx {{ font-size:13px; color:var(--text); line-height:1.45; }}
.sig .grp {{ font-size:12px; font-weight:700; color:var(--dim);
  margin:10px 0 2px; letter-spacing:.03em; }}
.grp-chip {{ display:inline-flex; gap:6px; align-items:center;
  font-size:12px; padding:3px 10px; border-radius:999px;
  border:1px solid var(--border); background:var(--panel); color:var(--dim); }}
.dot {{ width:8px; height:8px; border-radius:50%; }}

/* ---- 风险面板 ---- */
.risk {{ border:1px solid var(--border); border-radius:12px;
  background:var(--panel); overflow:hidden; }}
.risk .hd {{ padding:9px 14px; font-size:13px; font-weight:700;
  color:var(--text); background:var(--panel2);
  border-bottom:1px solid var(--border);
  display:flex; justify-content:space-between; align-items:center; }}
.risk .lv {{ font-size:12px; font-weight:700; padding:2px 10px;
  border-radius:999px; }}
.risk.lv-high {{ color:#FF8A8A; background:rgba(255,93,93,.12); }}
.risk.lv-mid  {{ color:var(--warn); background:rgba(240,180,41,.12); }}
.risk.lv-low  {{ color:var(--accent); background:rgba(88,166,255,.12); }}
.risk ul {{ margin:0; padding:10px 14px 12px 30px; }}
.risk li {{ font-size:12.5px; color:var(--dim); line-height:1.7; }}

/* ---- 区块标题 ---- */
.sec {{ display:flex; align-items:center; gap:8px; margin:18px 0 8px; }}
.sec .bar {{ width:3px; height:15px; border-radius:2px; background:var(--accent); }}
.sec .t {{ font-size:14.5px; font-weight:700; color:var(--text); }}
.sec .hint {{ font-size:11.5px; color:var(--faint); }}

/* ---- 价位参考 ---- */
.pxcard {{ text-align:left; }}
.pxcard .val {{ font-size:16px; font-weight:600; }}

/* ---- 窄屏适配 ---- */
@media (max-width: 860px) {{
  .hero {{ gap:12px; padding:12px 14px; }}
  .ohlc {{ gap:10px; }}
  .hero .idbox .name {{ font-size:17px; }}
}}
</style>
"""


def esc(v) -> str:
    """HTML 转义：信号文本含 '<' '>'（如 MA5<MA20）时防止被当作标签解析。"""
    return _html_mod.escape(str(v), quote=False)


def _tone_cls(tone) -> str:
    c = canon_tone(tone)
    return {None: "flat", "bull": "bull", "bear": "bear"}[c]


def tone_color(tone) -> str:
    return TONE_COLOR.get(canon_tone(tone), FLAT)


def sec_header(title: str, hint: str = "") -> str:
    hint_html = f'<span class="hint">{esc(hint)}</span>' if hint else ""
    return (f'<div class="sec"><div class="bar"></div>'
            f'<div class="t">{esc(title)}</div>{hint_html}</div>')


def hero(name: str, symbol_disp: str, badges: list,
         price: float, chg_pct: float, prev_price: float,
         ohlc: dict, ts: str, closed_only: bool) -> str:
    """顶部行情概览栏。一级信息=现价+涨跌幅；二级=OHLC/量额。"""
    direction = "up" if chg_pct > 0 else ("down" if chg_pct < 0 else "flat")
    sign = "+" if chg_pct > 0 else ""
    chg_abs = price - prev_price
    src = "实时快照" if not closed_only else "收盘价"
    badges_html = "".join(f'<span class="badge">{esc(b)}</span>' for b in badges)

    def it(label, value):
        return (f'<div class="it"><div class="lbl">{esc(label)}</div>'
                f'<div class="val">{esc(value)}</div></div>')

    ohlc_html = "".join(it(k, v) for k, v in ohlc.items())
    arrow = "▲" if chg_pct > 0 else ("▼" if chg_pct < 0 else "—")
    return f"""
<div class="hero">
  <div class="idbox">
    <div class="name">{esc(name)}</div>
    <div class="code">{esc(symbol_disp)}</div>
    <div style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap">{badges_html}</div>
  </div>
  <div style="flex:none">
    <div class="lbl">{src} · 元</div>
    <div class="price" style="color:var(--{direction})">{price:.2f}</div>
  </div>
  <div style="flex:none;padding-top:14px">
    <span class="chg {direction}">{arrow} {sign}{chg_abs:.2f} ({sign}{chg_pct:.2f}%)</span>
  </div>
  <div style="margin-left:auto"><div class="ohlc">{ohlc_html}
    <div class="it"><div class="lbl">数据时点</div><div class="ts">{esc(ts)}</div></div>
  </div></div>
</div>"""


def verdict_banner(verdict: str, tone, bull: int, bear: int, ops: str) -> str:
    """AI 综合结论横幅：一级信息。tone 决定左侧色条与文字色。"""
    c = tone_color(tone)
    tint = {"bull": "rgba(255,93,93,.08)", "bear": "rgba(38,194,129,.08)",
            None: "rgba(147,161,175,.06)"}[canon_tone(tone)]
    conf = max(bull, bear) / max(bull + bear, 1)
    return f"""
<div class="banner" style="--tone:{c};--tint:{tint}">
  <div class="head">
    <span class="badge">AI 综合评估</span>
    <span class="verdict">{esc(verdict)}</span>
    <span class="score">多空组别比 {bull}:{bear} · 置信参考 {conf * 100:.0f}%</span>
  </div>
  <div class="ops">{esc(ops)}</div>
</div>"""


def kpi(label: str, value: str, sub: str = "", cls: str = "") -> str:
    """二级信息 KPI 卡片：label 小字灰、value 等宽大数、sub 辅助说明。"""
    return (f'<div class="card kpi {cls}"><div class="lbl">{esc(label)}</div>'
            f'<div class="val num">{esc(value)}</div>'
            + (f'<div class="sub">{esc(sub)}</div>' if sub else "")
            + "</div>")


def grp_chip(title: str, bull: int, bear: int) -> str:
    if bull > bear:
        c, dot = UP, UP
    elif bear > bull:
        c, dot = DOWN, DOWN
    else:
        c, dot = FLAT, FLAT
    return (f'<span class="grp-chip"><span class="dot" '
            f'style="background:{dot}"></span>{esc(title)}'
            f'&nbsp;<span class="num" style="color:{c}">{bull}:{bear}</span></span>')


def sig_group(title: str, items: list) -> str:
    """一组信号条目：▲偏多 ▼偏空 — 中性，悬停高亮。"""
    out = [f'<div class="grp">{esc(title)}</div>']
    for text, status in items:
        mk = {"bull": "▲", "bear": "▼"}.get(status if status in ("bull", "bear")
                                             else _tone_cls(status), "—")
        out.append(f'<div class="sig {_tone_cls(status)}">'
                   f'<span class="mk">{mk}</span><span class="tx">{esc(text)}</span></div>')
    return "".join(out)


def risk_panel(level: str, reasons: list) -> str:
    """风险提示面板：level ∈ 高/中/低。"""
    lv_cls = {"高": "lv-high", "中": "lv-mid", "低": "lv-low"}.get(level, "lv-mid")
    lis = "".join(f"<li>{esc(r)}</li>" for r in reasons)
    return (f'<div class="risk"><div class="hd"><span>⚠️ 风险提示</span>'
            f'<span class="lv {lv_cls}">{esc(level)}风险</span></div>'
            f"<ul>{lis}</ul></div>")
