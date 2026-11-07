"""UI 设计令牌与组件：深色专业金融风格。

层级原则：
- 一级信息（现价/涨跌幅/AI结论）：大字号、粗字重、语义色、首屏顶部
- 二级信息（OHLC/换手/市值等）：小字号、次级灰、等宽数字
配色遵循A股惯例：红涨绿跌；中性色为主基调，语义色仅用于数据本身。

本模块只产出 HTML 字符串，不依赖 streamlit/pandas，便于离线单测。
"""
import html as _html_mod

from astock.version import RELEASE_LABEL

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
ACCENT = "#B4CDA6"
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
.stApp {{ background:{BG}; font-size:16px; }}
section[data-testid="stSidebar"] {{
  background:#10151C; border-right:1px solid var(--border);
}}
.block-container {{ padding:1.15rem 1.7rem 2rem; max-width:1920px; }}
section[data-testid="stSidebar"] > div {{ padding-top:1.5rem; }}
[data-testid="stVerticalBlock"] {{ gap:.8rem; }}
[data-testid="stTextInput"] input, [data-baseweb="select"] {{ font-size:13px; }}
[data-testid="stButton"] button, [data-testid="stDownloadButton"] button {{
  border-radius:7px; font-size:13px; min-height:38px;
}}
[data-testid="stVerticalBlockBorderWrapper"] > div {{ border-color:var(--border); }}
/* 保留侧边栏折叠入口，移除覆盖产品头部的框架工具栏。 */
header[data-testid="stHeader"] {{
  height:0; min-height:0; background:transparent; pointer-events:none;
}}
[data-testid="stToolbar"] {{ pointer-events:none; }}
[data-testid="stToolbarActions"], [data-testid="stAppDeployButton"],
[data-testid="stMainMenu"] {{ display:none; }}
[data-testid="stExpandSidebarButton"] {{
  pointer-events:auto; position:fixed; left:8px; top:17px;
}}
body:has([data-testid="stExpandSidebarButton"]) .brand-shell {{ padding-left:28px; }}
h1,h2,h3 {{ color:var(--text); }}
h3 {{ font-size:1.35rem !important; }}
.stMarkdown, .stText, p, li, [data-testid="stMarkdownContainer"] p {{
  color:var(--text); font-size:1.02rem;
}}
#MainMenu,footer,[data-testid="stStatusWidget"] {{ visibility:hidden; }}
.stTabs [data-baseweb="tab-list"] {{ gap:6px; }}
.stTabs [data-baseweb="tab"] {{ font-size:15px; font-weight:600; }}
section[data-testid="stSidebar"] .stHeading h2 {{ font-size:1.05rem; }}
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {{
  font-size:.88rem;
}}
div[data-testid="stSegmentedControl"] {{ margin:.15rem 0 .8rem; }}
div[data-testid="stSegmentedControl"] button {{
  min-height:36px; font-weight:600; border-radius:6px; font-size:13px;
}}
.st-key-main_nav {{ border-bottom:1px solid var(--border); padding-bottom:12px; }}
.st-key-main_nav [data-testid="stButtonGroup"] {{ gap:5px; }}
.st-key-main_nav button {{ border:1px solid transparent; background:transparent; }}
.st-key-main_nav button[aria-pressed="true"], .st-key-main_nav button[aria-checked="true"] {{
  background:#243329; color:#d7e7ce; border-color:#475b42;
}}

/* ---- 产品头部 / 空状态 ---- */
.brand-shell {{ display:flex; align-items:center; gap:11px; margin:0 0 5px; }}
.brand-mark {{ width:34px; height:34px; border-radius:8px; display:grid;
  place-items:center; font-size:24px; color:#c5ddba;
  background:#24352d; border:1px solid #3c5343; }}
.brand-title {{ color:var(--text); font-size:18px; font-weight:700; line-height:1.15; }}
.brand-sub {{ color:var(--dim); font-size:11px; margin-top:4px; }}
.brand-tag {{ margin-left:auto; color:#a0b296; border:1px solid #354837;
  font-size:10px; letter-spacing:.08em; padding:5px 9px; border-radius:5px; }}

/* ---- 市场云图 ---- */
.cloud-heading {{ display:flex; align-items:center; justify-content:space-between; gap:16px; margin:2px 0 8px; }}
.cloud-eyebrow {{ font-size:10px; color:#9eb293; letter-spacing:.17em; font-weight:650; }}
.cloud-title {{ font-size:27px; font-weight:680; padding:6px 0 0; margin:0; line-height:1.35; }}
.cloud-title > span {{ font-size:12px; font-weight:400; color:var(--dim); margin-left:16px; }}
.cloud-session {{ font-size:11px; color:var(--dim); white-space:nowrap; }}
.cloud-session i {{ display:inline-block; width:6px; height:6px; border-radius:50%; background:#a4bc98; margin-right:7px; }}
.st-key-cloud_filters {{ background:var(--panel); border-radius:9px; }}
.st-key-cloud_filters [data-testid="stWidgetLabel"] p {{ font-size:11px; color:var(--dim); }}
.st-key-cloud_filters [data-testid="stCheckbox"] {{ padding-top:24px; }}
.cloud-summary {{ display:flex; align-items:center; gap:32px; padding:18px 23px;
  background:linear-gradient(105deg,#19231e,#141d22 65%); border:1px solid #2e3d36;
  border-radius:10px; margin:2px 0 0; }}
.cloud-overview {{ display:flex; flex-direction:column; gap:5px; min-width:126px; }}
.cloud-overview strong {{ font-size:33px; line-height:1; font-weight:650; font-variant-numeric:tabular-nums; }}
.cloud-overview small {{ font-size:17px; font-weight:450; margin-left:3px; }}
.cloud-overview > span:last-child {{ font-size:11px; color:var(--dim); }}
.cloud-breadth {{ flex:1; min-width:80px; }}
.cloud-bar {{ display:flex; overflow:hidden; height:9px; border-radius:2px; gap:2px; }}
.cloud-bar i {{ display:block; min-width:0; }}
.cloud-counts {{ display:flex; gap:20px; margin-top:10px; color:var(--dim); font-size:11px; }}
.cloud-stat {{ display:flex; flex-direction:column; gap:10px; padding-left:24px; border-left:1px solid #2b373b; }}
.cloud-stat > span {{ font-size:11px; color:var(--dim); }}
.cloud-stat strong {{ font-size:20px; font-weight:600; font-variant-numeric:tabular-nums; white-space:nowrap; }}
.pos {{ color:var(--up); }} .neg {{ color:var(--down); }}
.cloud-meta {{ display:flex; justify-content:space-between; flex-wrap:wrap; gap:8px;
  color:var(--dim); font-size:11px; margin:9px 0 0; }}
.cloud-meta b {{ color:var(--text); font-weight:600; }}
.cloud-meta em {{ color:var(--faint); font-style:normal; margin-left:12px; }}
.cloud-legend {{ display:flex; justify-content:space-between; flex-wrap:wrap; gap:10px; color:var(--faint); font-size:10px; margin-top:-3px; }}
.cloud-legend > div {{ display:flex; gap:10px; flex-wrap:wrap; }}
.cloud-legend i {{ display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:4px; }}
.cloud-pick {{ display:flex; flex-direction:column; gap:4px; padding-left:8px; }}
.cloud-pick b {{ color:var(--dim); font-size:12px; font-weight:500; }}
.cloud-pick strong {{ font-size:22px; font-weight:650; }}
.cloud-pick small {{ font-size:14px; margin-left:12px; }}
.cloud-pick > span {{ color:var(--dim); font-size:11px; }}
/* 云图使用紧凑的行情终端布局，减少顶部占用；仅作用于云图页。 */
.block-container:has(.cloud-heading) {{ padding:.45rem 1rem 1rem; max-width:none; }}
.block-container:has(.cloud-heading) [data-testid="stVerticalBlock"] {{ gap:.35rem; }}
.block-container:has(.cloud-heading) [data-testid="stMarkdownContainer"]:has(> .brand-shell, > [class^="cloud-"]) {{
  margin-bottom:0;
}}
.block-container:has(.cloud-heading) .brand-shell {{ gap:8px; margin:0; min-height:28px; }}
.block-container:has(.cloud-heading) .brand-mark {{ width:28px; height:28px; font-size:20px; border-radius:6px; }}
.block-container:has(.cloud-heading) .brand-title {{ font-size:16px; }}
.block-container:has(.cloud-heading) .brand-sub {{ display:none; }}
.block-container:has(.cloud-heading) .brand-tag {{ padding:3px 7px; font-size:9px; }}
.block-container:has(.cloud-heading) .st-key-main_nav {{ padding-bottom:5px; }}
.cloud-heading {{ margin:0; gap:12px; }}
.cloud-heading .cloud-eyebrow {{ display:none; }}
.cloud-title {{ padding:0; font-size:18px; line-height:1.4; }}
.cloud-title > span {{ font-size:11px; margin-left:12px; }}
.st-key-cloud_actions [data-testid="stButton"] button,
.st-key-cloud_actions [data-testid="stPopover"] button {{ min-height:32px; padding-top:3px; padding-bottom:3px; }}
.st-key-cloud_search input {{ padding-top:5px; padding-bottom:5px; }}
.block-container:has(.cloud-heading) .st-key-cloud_filters {{ padding:6px 10px; border-radius:6px; }}
.st-key-cloud_filters [data-testid="stSelectbox"],
.st-key-cloud_filters [data-testid="stMultiSelect"] {{ display:flex; align-items:center; gap:8px; }}
.st-key-cloud_filters [data-testid="stWidgetLabel"] {{ margin:0; flex-shrink:0; min-height:0; }}
.st-key-cloud_filters [data-testid="stSelectbox"] > div,
.st-key-cloud_filters [data-testid="stMultiSelect"] > div {{ flex:1; min-width:0; }}
.st-key-cloud_filters [data-testid="stWidgetLabel"] p {{ white-space:nowrap; }}
.st-key-cloud_filters [data-testid="stCheckbox"] {{ padding-top:0; min-height:32px; display:flex; align-items:center; }}
.block-container:has(.cloud-heading) .cloud-summary {{ gap:20px; padding:8px 14px; border-radius:7px; margin:0; }}
.cloud-overview .cloud-eyebrow {{ display:none; }}
.cloud-overview {{ gap:3px; min-width:108px; }}
.cloud-overview strong {{ font-size:26px; }}
.cloud-overview small {{ font-size:14px; }}
.cloud-overview > span:last-child {{ font-size:10px; line-height:1.3; }}
.cloud-stat {{ gap:4px; padding-left:18px; }}
.cloud-stat strong {{ font-size:17px; }}
.cloud-stat > span {{ font-size:10px; }}
.cloud-bar {{ height:7px; }}
.cloud-counts {{ margin-top:6px; gap:14px; font-size:10px; }}
.cloud-meta {{ font-size:10px; line-height:1.4; margin:0; gap:4px; }}
.empty-hero {{ border:1px solid var(--border); border-radius:18px; overflow:hidden;
  background:radial-gradient(circle at 85% 15%,rgba(88,166,255,.16),transparent 32%),
    linear-gradient(145deg,#111923 0%,#0F141B 70%); padding:34px 34px 28px; }}
.empty-eyebrow {{ color:var(--accent); font-size:12px; font-weight:750;
  letter-spacing:.12em; text-transform:uppercase; }}
.empty-title {{ color:var(--text); font-size:clamp(27px,3vw,42px); font-weight:780;
  line-height:1.22; margin:9px 0 10px; max-width:760px; }}
.empty-copy {{ color:var(--dim); font-size:15px; line-height:1.8; max-width:760px; }}
.feature-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr));
  gap:10px; margin-top:22px; }}
.feature-card {{ border:1px solid var(--border); border-radius:12px; padding:13px;
  background:rgba(18,23,30,.72); }}
.feature-card .ic {{ font-size:18px; }}
.feature-card .ft {{ color:var(--text); font-size:13.5px; font-weight:700; margin-top:7px; }}
.feature-card .fd {{ color:var(--faint); font-size:12px; line-height:1.55; margin-top:3px; }}
.quality-strip {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:16px; }}
.quality-strip span {{ color:var(--dim); font-size:12px; padding:4px 9px;
  border-radius:999px; border:1px solid var(--border); background:rgba(12,17,23,.7); }}

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
.hero .idbox .name {{ font-size:23px; font-weight:700; color:var(--text);
  line-height:1.2; white-space:nowrap; }}
.hero .idbox .code {{ font-size:14.5px; color:var(--dim);
  font-family:{FONT_NUM}; margin-top:2px; white-space:nowrap; }}
.hero .price {{ font-size:clamp(34px,3.8vw,50px); font-weight:700; line-height:1;
  font-family:{FONT_NUM}; font-variant-numeric:tabular-nums; white-space:nowrap; }}
.chg {{ display:inline-flex; align-items:baseline; gap:8px;
  font-family:{FONT_NUM}; font-size:17px; font-weight:600;
  padding:4px 12px; border-radius:8px; margin-top:4px; }}
.chg.up   {{ color:var(--up);   background:rgba(255,93,93,.10); }}
.chg.down {{ color:var(--down); background:rgba(38,194,129,.10); }}
.chg.flat {{ color:var(--flat); background:rgba(147,161,175,.10); }}
.ohlc {{ display:flex; gap:18px; flex-wrap:wrap; }}
.ohlc .it {{ min-width:68px; }}
.lbl {{ font-size:12.5px; color:var(--faint); letter-spacing:.05em; }}
.val {{ font-size:16px; color:var(--text); font-family:{FONT_NUM};
  font-variant-numeric:tabular-nums; }}
.badge {{ font-size:12px; color:var(--dim); border:1px solid var(--border);
  border-radius:999px; padding:2.5px 10px; white-space:nowrap; }}
.ts {{ font-size:12.5px; color:var(--faint); font-family:{FONT_NUM}; }}

/* ---- 结论横幅 ---- */
.banner {{ border:1px solid var(--border); border-left-width:4px;
  border-radius:12px; padding:12px 16px;
  background:linear-gradient(90deg,var(--tint) 0%,transparent 55%),
             var(--panel); }}
.banner .head {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; }}
.banner .verdict {{ font-size:20px; font-weight:700; color:var(--tone); }}
.banner .score {{ font-family:{FONT_NUM}; font-size:14.5px; color:var(--dim); }}
.banner .ops  {{ font-size:14px; color:var(--dim); margin-top:6px; }}

/* ---- KPI 卡片 ---- */
.kpi .lbl {{ margin-bottom:4px; }}
.kpi .val {{ font-size:22px; font-weight:600; }}
.kpi .sub {{ font-size:12.5px; color:var(--faint); margin-top:3px;
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
.sig .tx {{ font-size:14.5px; color:var(--text); line-height:1.5; }}
.sig .grp {{ font-size:13.5px; font-weight:700; color:var(--dim);
  margin:10px 0 2px; letter-spacing:.03em; }}
.grp-chip {{ display:inline-flex; gap:6px; align-items:center;
  font-size:13px; padding:3.5px 11px; border-radius:999px;
  border:1px solid var(--border); background:var(--panel); color:var(--dim); }}
.dot {{ width:8px; height:8px; border-radius:50%; }}

/* ---- 风险面板 ---- */
.risk {{ border:1px solid var(--border); border-radius:12px;
  background:var(--panel); overflow:hidden; }}
.risk .hd {{ padding:10px 14px; font-size:14.5px; font-weight:700;
  color:var(--text); background:var(--panel2);
  border-bottom:1px solid var(--border);
  display:flex; justify-content:space-between; align-items:center; }}
.risk .lv {{ font-size:13px; font-weight:700; padding:2.5px 11px;
  border-radius:999px; }}
.risk.lv-high {{ color:#FF8A8A; background:rgba(255,93,93,.12); }}
.risk.lv-mid  {{ color:var(--warn); background:rgba(240,180,41,.12); }}
.risk.lv-low  {{ color:var(--accent); background:rgba(88,166,255,.12); }}
.risk ul {{ margin:0; padding:10px 14px 12px 30px; }}
.risk li {{ font-size:14px; color:var(--dim); line-height:1.75; }}

/* ---- 区块标题 ---- */
.sec {{ display:flex; align-items:center; gap:8px; margin:20px 0 9px; }}
.sec .bar {{ width:3.5px; height:17px; border-radius:2px; background:var(--accent); }}
.sec .t {{ font-size:16.5px; font-weight:700; color:var(--text); }}
.sec .hint {{ font-size:13px; color:var(--faint); }}

/* ---- 价位参考 ---- */
.pxcard {{ text-align:left; }}
.pxcard .val {{ font-size:18px; font-weight:600; }}

/* ---- 参考来源 ---- */
.srcbox {{ border:1px solid var(--border); border-radius:10px;
  background:var(--panel); padding:9px 13px; margin-top:10px; }}
.srcbox .hd {{ font-size:12.5px; font-weight:700; color:var(--dim);
  margin-bottom:5px; letter-spacing:.04em; }}
.srcbox ol {{ margin:0; padding-left:22px; }}
.srcbox li {{ font-size:12.5px; line-height:1.75; color:var(--dim);
  word-break:break-all; }}
.srcbox a {{ color:var(--accent); text-decoration:none; }}
.srcbox a:hover {{ text-decoration:underline; }}

/* ---- 消息情报 ---- */
.nitem {{ border:1px solid var(--border); border-radius:10px; padding:10px 12px;
  background:var(--panel); margin-bottom:8px; border-left:3px solid var(--flat); }}
.nitem.bull {{ border-left-color:var(--up); }}
.nitem.bear {{ border-left-color:var(--down); }}
.nmeta {{ display:flex; gap:6px; flex-wrap:wrap; align-items:center; margin-bottom:5px; }}
.nchip {{ font-size:11.5px; border:1px solid var(--border); border-radius:999px;
  padding:1px 8px; color:var(--dim); }}
.nchip.off {{ color:var(--accent); border-color:rgba(88,166,255,.4); }}
.nchip.soc {{ color:var(--warn); border-color:rgba(240,180,41,.4); }}
.nchip.med {{ color:var(--text); }}
.nstance {{ font-size:12px; font-weight:700; padding:1px 8px; border-radius:6px; }}
.nstance.bull {{ color:var(--up); background:rgba(255,93,93,.12); }}
.nstance.bear {{ color:var(--down); background:rgba(38,194,129,.12); }}
.nstance.neu {{ color:var(--flat); background:rgba(147,161,175,.12); }}
.ntime {{ font-size:12px; color:var(--faint); font-family:{FONT_NUM}; }}
.ntitle {{ font-size:14.5px; font-weight:600; line-height:1.45; }}
.ntitle a {{ color:var(--text); text-decoration:none; }}
.ntitle a:hover {{ color:var(--accent); }}
.nsnip {{ font-size:13px; color:var(--dim); margin-top:4px; line-height:1.55; }}
.nimg {{ margin-top:8px; max-width:min(100%,360px); max-height:180px;
  border-radius:8px; border:1px solid var(--border); object-fit:contain;
  background:var(--panel2); }}
.ncap {{ font-size:12.5px; color:var(--dim); margin-top:4px; line-height:1.5; }}
.nwhy {{ font-size:12.5px; color:var(--faint); margin-top:5px; }}
.nmeter {{ display:flex; height:10px; border-radius:99px; overflow:hidden;
  background:var(--panel2); border:1px solid var(--border); }}
.nmeter i {{ display:block; height:100%; }}
.nmeter .b {{ background:var(--up); }}
.nmeter .e {{ background:var(--down); }}
.nmeter .n {{ background:#3d4a57; }}
.nstat {{ display:flex; gap:8px; flex-wrap:wrap; margin:6px 0 10px; }}
.nstat span {{ font-size:12.5px; color:var(--dim); border:1px solid var(--border);
  border-radius:999px; padding:2px 10px; }}
.nstat .ok {{ color:var(--accent); }}
.nstat .bad {{ color:var(--warn); }}
.nnote {{ font-size:13px; color:var(--dim); line-height:1.65; padding:10px 12px;
  border:1px dashed var(--border); border-radius:10px; background:var(--panel2); }}
.evcols {{ display:flex; gap:18px; flex-wrap:wrap; margin-top:8px; }}
.evcols .lbl {{ font-size:12px; color:var(--faint); margin-bottom:4px; }}
.evcols .nstance {{ margin:0 6px 6px 0; display:inline-block; }}
.stable {{ width:100%; border-collapse:collapse; margin-top:8px; font-size:13px; }}
.stable th, .stable td {{ border:1px solid var(--border); padding:6px 8px;
  text-align:left; color:var(--dim); }}
.stable th {{ color:var(--text); background:var(--panel2); font-weight:600; }}
.stable td {{ color:var(--text); }}
.tpills {{ display:flex; gap:6px; flex-wrap:wrap; margin:4px 0 10px; }}
.scat {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(158px,1fr));
  gap:8px; margin:8px 0 12px; }}
.scard {{ border:1px solid var(--border); border-radius:10px; padding:10px 12px;
  background:var(--panel); }}
.scard.on {{ border-color:var(--accent); }}
.scard .l {{ font-size:13.5px; font-weight:600; color:var(--text); }}
.scard .h {{ font-size:12px; color:var(--dim); margin-top:4px; line-height:1.45; }}
.scard .n {{ font-size:12px; color:var(--accent); margin-top:6px; }}

/* ---- 多周期对比 ---- */
.mpgrid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr));
  gap:10px; margin:6px 0 10px; }}
.mpcard {{ border:1px solid var(--border); border-radius:12px; padding:12px 14px;
  background:var(--panel); border-left:3.5px solid var(--flat); }}
.mpcard.bull {{ border-left-color:var(--up); }}
.mpcard.bear {{ border-left-color:var(--down); }}
.mpcard.off {{ opacity:.62; }}
.mpcard .pl {{ font-size:13px; font-weight:700; color:var(--dim);
  letter-spacing:.04em; display:flex; justify-content:space-between; }}
.mpcard .v {{ font-size:15px; font-weight:700; margin-top:7px; line-height:1.4; }}
.mpcard .s {{ font-size:12px; color:var(--dim); margin-top:6px;
  font-family:{FONT_NUM}; }}
.mpcard .c {{ font-size:12px; color:var(--faint); margin-top:4px; }}

/* ---- 基本面分组 ---- */
.fgroup {{ border:1px solid var(--border); border-radius:12px; background:var(--panel);
  padding:10px 13px 12px; }}
.fgroup .hd {{ font-size:13px; font-weight:700; color:var(--dim);
  letter-spacing:.05em; padding-bottom:7px; margin-bottom:7px;
  border-bottom:1px dashed var(--border); }}
.frow {{ display:flex; justify-content:space-between; gap:8px; padding:3.5px 0; }}
.frow .fl {{ font-size:13px; color:var(--dim); }}
.frow .fv {{ font-size:13.5px; color:var(--text); font-family:{FONT_NUM};
  font-weight:600; }}
.frow .fv.pos {{ color:var(--up); }}
.frow .fv.neg {{ color:var(--down); }}
.fgap {{ font-size:12.5px; color:var(--warn); border:1px dashed rgba(240,180,41,.45);
  border-radius:8px; padding:7px 10px; margin-top:10px; }}

/* ---- 窄屏适配 ---- */
@media (max-width: 860px) {{
  .block-container {{ padding:1rem .8rem 1.5rem; }}
  .hero {{ gap:12px; padding:12px 14px; }}
  .ohlc {{ gap:10px; }}
  .hero .idbox .name {{ font-size:17px; }}
  .feature-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
  .empty-hero {{ padding:24px 20px; }}
  .cloud-title > span, .cloud-session, .brand-tag {{ display:none; }}
  .cloud-summary {{ flex-wrap:wrap; gap:18px; padding:16px; }}
  .cloud-breadth {{ min-width:150px; }}
  .cloud-stat {{ padding-left:0; border:0; flex:1; }}
  .cloud-counts {{ gap:10px; }}
  .cloud-meta em {{ display:block; margin:5px 0 0; }}
  .block-container:has(.cloud-heading) {{ padding:.65rem .8rem 1rem; }}
  .block-container:has(.cloud-heading) .cloud-summary {{ gap:12px; }}
  .cloud-stat {{ padding-left:0; }}
}}
@media (max-width: 640px) {{
  .st-key-cloud_market [role="radiogroup"] {{ flex-wrap:wrap; overflow:visible; gap:3px; }}
  .st-key-cloud_market [role="radiogroup"] button {{ border-radius:5px; }}
  .st-key-cloud_actions [data-testid="stHorizontalBlock"] {{ flex-wrap:nowrap; gap:10px; }}
  .st-key-cloud_actions [data-testid="stColumn"] {{ width:calc(50% - 5px) !important;
    min-width:0 !important; flex:1 !important; }}
  .st-key-cloud_filters [data-testid="stHorizontalBlock"] {{ flex-wrap:wrap; gap:12px; }}
  .st-key-cloud_filters [data-testid="stColumn"] {{ width:calc(50% - 6px) !important;
    min-width:calc(50% - 6px) !important; flex:1 1 calc(50% - 6px) !important; }}
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


def brand_header() -> str:
    """紧凑的产品识别头部。"""
    return f"""
<div class="brand-shell">
  <div class="brand-mark">⌁</div>
  <div><div class="brand-title">A股研究台</div>
  <div class="brand-sub">{RELEASE_LABEL} · 全市场洞察与个股研究</div></div>
  <span class="brand-tag">RESEARCH WORKSPACE</span>
</div>"""


def empty_state() -> str:
    """未选择个股时也提供明确价值与下一步，不再展示空白画布。"""
    features = (
        ("⌁", "技术结构", "五组独立投票，多周期共振与关键价位"),
        ("◫", "基本面", "盈利、成长、偿债、现金流和估值快照"),
        ("↺", "策略回测", "T+1 执行约束、涨跌停过滤与交易明细"),
        ("✦", "事件情报", "个股消息、板块要闻与可选 AI 研判"),
    )
    cards = "".join(
        f'<div class="feature-card"><div class="ic">{icon}</div>'
        f'<div class="ft">{title}</div><div class="fd">{desc}</div></div>'
        for icon, title, desc in features)
    return f"""
<div class="empty-hero">
  <div class="empty-eyebrow">Research workspace</div>
  <div class="empty-title">把行情变成可验证的研究结论</div>
  <div class="empty-copy">在左侧输入 6 位股票代码并开始分析。工具会先给出趋势、动量、
  波动和位置，再展开技术信号、财务质量、历史回测与消息催化。每个结论都展示依据，
  AI 是可选增强，不是基础分析的前置条件。</div>
  <div class="feature-grid">{cards}</div>
  <div class="quality-strip"><span>已收盘 K 线</span><span>A股 T+1</span>
  <span>涨跌停约束</span><span>数据源自动降级</span><span>非投资建议</span></div>
</div>"""


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


def verdict_banner(verdict: str, tone, bull: int, bear: int, ops: str,
                   badge: str = "规则信号评估") -> str:
    """综合结论横幅：一级信息。tone 决定左侧色条与文字色，badge 可自定义标签。"""
    c = tone_color(tone)
    tint = {"bull": "rgba(255,93,93,.08)", "bear": "rgba(38,194,129,.08)",
            None: "rgba(147,161,175,.06)"}[canon_tone(tone)]
    conf = max(bull, bear) / max(bull + bear, 1) if bull + bear else 0
    return f"""
<div class="banner" style="--tone:{c};--tint:{tint}">
  <div class="head">
    <span class="badge">{esc(badge)}</span>
    <span class="verdict">{esc(verdict)}</span>
    <span class="score">多空组别比 {bull}:{bear} · 同向占比 {conf * 100:.0f}%</span>
  </div>
  <div class="ops">{esc(ops)}</div>
</div>"""


def kpi(label: str, value: str, sub: str = "", cls: str = "") -> str:
    """二级信息 KPI 卡片：label 小字灰、value 等宽大数、sub 辅助说明。"""
    return (f'<div class="card kpi {cls}"><div class="lbl">{esc(label)}</div>'
            f'<div class="val num">{esc(value)}</div>'
            + (f'<div class="sub">{esc(sub)}</div>' if sub else "")
            + "</div>")


def multi_period_cards(entries: dict, verdict: str) -> str:
    """多周期共振卡片：entries={period: {label, ok, tone, verdict, bull, bear,
    n_bars, close, reason}}，顶部横幅为共振结论。"""
    cards = []
    for period in ("daily", "weekly", "monthly"):
        e = entries.get(period) or {}
        tone = canon_tone(e.get("tone"))
        if not e.get("ok"):
            cards.append(
                f'<div class="mpcard off"><div class="pl">{esc(e.get("label", period))}'
                f'<span>不可用</span></div>'
                f'<div class="v" style="color:var(--faint)">'
                f'{esc(e.get("reason", "数据不足"))}</div></div>')
            continue
        cls = {None: "", "bull": "bull", "bear": "bear"}[tone]
        color = tone_color(tone)
        mark = {None: "—", "bull": "▲", "bear": "▼"}[tone]
        cards.append(
            f'<div class="mpcard {cls}">'
            f'<div class="pl">{esc(e["label"])}<span style="color:{color}">{mark}</span></div>'
            f'<div class="v">{esc(e.get("verdict", ""))}</div>'
            f'<div class="s">多 {e.get("bull", 0)} : 空 {e.get("bear", 0)} · '
            f'{e.get("n_bars", 0)}根</div>'
            f'<div class="c">最新收盘 {e.get("close", 0):.2f}</div></div>')
    return (f'<div class="banner" style="--tone:var(--accent);'
            f'--tint:rgba(88,166,255,.07)">'
            f'<div class="head"><span class="badge">多周期共振</span>'
            f'<span class="verdict">{esc(verdict)}</span></div></div>'
            f'<div class="mpgrid">{"".join(cards)}</div>')


def fund_group(title: str, rows: list) -> str:
    """基本面分组面板。rows=[(label, value, cls)]，cls ∈ ''/pos/neg。"""
    body = "".join(
        f'<div class="frow"><span class="fl">{esc(lab)}</span>'
        f'<span class="fv {esc(cls)}">{esc(val)}</span></div>'
        for lab, val, cls in rows)
    return (f'<div class="fgroup"><div class="hd">{esc(title)}</div>'
            f"{body}</div>")


def fund_gap_note(gaps: list) -> str:
    """基本面数据缺口提示；无缺口返回空串。"""
    if not gaps:
        return ""
    return (f'<div class="fgap">⚠️ 数据缺口：{esc("、".join(gaps))}'
            f"——以上缺失维度如实声明，不作推测填充。</div>")


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


def sources_block(items: list) -> str:
    """联网搜索的参考来源列表：items=[(标题,url)]。"""
    if not items:
        return ""
    lis = "".join(
        f'<li><a href="{_html_mod.escape(str(u), quote=True)}" '
        f'target="_blank" rel="noreferrer">{esc(t)}</a></li>'
        for t, u in items)
    return (f'<div class="srcbox"><div class="hd">🔗 参考来源</div>'
            f"<ol>{lis}</ol></div>")


def news_verdict_banner(verdict: str, tone, bull: int, bear: int,
                        neu: int, hint: str) -> str:
    """消息面结论横幅。tone ∈ bull/bear/mixed/neutral。"""
    t = canon_tone(tone)
    if t is None and str(tone) == "mixed":
        t = None
    c = tone_color(t)
    tint = {"bull": "rgba(255,93,93,.08)", "bear": "rgba(38,194,129,.08)",
            None: "rgba(147,161,175,.06)"}[t]
    return f"""
<div class="banner" style="--tone:{c};--tint:{tint}">
  <div class="head">
    <span class="badge">消息面研判</span>
    <span class="verdict">{esc(verdict)}</span>
    <span class="score">利好 {bull} · 利空 {bear} · 中性 {neu}</span>
  </div>
  <div class="ops">{esc(hint)}</div>
</div>"""


def news_meter(bull: int, bear: int, neu: int) -> str:
    total = max(bull + bear + neu, 1)
    def w(n):
        return max(0.0, n / total * 100)
    return (
        f'<div class="nmeter" title="利好{bull} 利空{bear} 中性{neu}">'
        f'<i class="b" style="width:{w(bull):.1f}%"></i>'
        f'<i class="e" style="width:{w(bear):.1f}%"></i>'
        f'<i class="n" style="width:{w(neu):.1f}%"></i></div>'
    )


def news_channel_bar(entries: list) -> str:
    """entries=[(展示名, 状态)]，状态 ok / empty / error:Xxx。"""
    chips = []
    for name, st in entries:
        st = str(st or "")
        if st == "ok":
            cls, mark = "ok", "✓"
        elif st.startswith("error"):
            cls, mark = "bad", "✗"
        else:
            cls, mark = "", "·"
        chips.append(f'<span class="{cls}">{esc(name)} {mark}</span>')
    return f'<div class="nstat">{"".join(chips)}</div>'


def news_item_card(d: dict) -> str:
    """单条消息卡片。d 由 news_intel.item_view 产出。"""
    stance = d.get("stance") or "neutral"
    cls = {"bull": "bull", "bear": "bear"}.get(stance, "neu")
    tier = d.get("tier") or "web"
    chip_cls = {"official": "off", "social": "soc", "media": "med"}.get(tier, "")
    title = d.get("title") or "(无标题)"
    url = d.get("url") or ""
    if url.startswith("http"):
        href = _html_mod.escape(url, quote=True)
        title_html = f'<a href="{href}" target="_blank" rel="noreferrer">{esc(title)}</a>'
    else:
        title_html = esc(title)
    bits = [
        f'<span class="nchip {chip_cls}">{esc(d.get("tier_label") or "")}</span>',
        f'<span class="nchip">{esc(d.get("channel_label") or "")}</span>',
        f'<span class="nstance {cls}">{esc(d.get("stance_label") or "未标注")}</span>',
    ]
    if d.get("quality_label"):
        qcls = "off" if d.get("quality_label") == "重点" else ""
        bits.insert(0, f'<span class="nchip {qcls}">{esc(d["quality_label"])}'
                       f' · {int(d.get("quality_score") or 0)}</span>')
    if int(d.get("cluster_size") or 1) > 1:
        bits.append(f'<span class="nchip med">同事件 {int(d["cluster_size"])} 个来源</span>')
    if d.get("ai_label"):
        bits.append(f'<span class="nchip">{esc(d["ai_label"])}</span>')
    if d.get("verified_label"):
        bits.append(f'<span class="nchip">{esc(d["verified_label"])}</span>')
    if d.get("msg_type"):
        bits.append(f'<span class="nchip">{esc(d["msg_type"])}</span>')
    if d.get("impact"):
        bits.append(f'<span class="nchip">影响{esc(d["impact"])}</span>')
    if d.get("has_image"):
        bits.append('<span class="nchip med">含图片</span>')
    if d.get("published"):
        bits.append(f'<span class="ntime">{esc(d["published"])}</span>')
    for lab in d.get("sector_labels") or []:
        bits.append(f'<span class="nchip med">{esc(lab)}</span>')
    snip = d.get("snippet") or ""
    why = d.get("reason") or ""
    src = d.get("source_name") or ""
    related = "、".join(d.get("related_sources") or [])
    source_text = src + (("；关联来源 " + related) if related else "")
    extra = f" · {esc(source_text)}" if source_text else ""
    img = next((u for u in (d.get("image_urls") or [])
                if str(u).startswith("http")), "")
    img_html = ""
    if img:
        href = _html_mod.escape(img, quote=True)
        img_html = f'<img class="nimg" src="{href}" alt="来源配图" loading="lazy">'
    cap = d.get("image_caption") or ""
    cap_html = (f'<div class="ncap">图片识读 {esc(cap)}</div>' if cap else "")
    return (
        f'<div class="nitem {cls}"><div class="nmeta">{"".join(bits)}</div>'
        f'<div class="ntitle">{title_html}</div>'
        + (f'<div class="nsnip">{esc(snip)}</div>' if snip else "")
        + img_html + cap_html
        + (f'<div class="nwhy">{esc(why)}{extra}</div>' if why or src else "")
        + "</div>"
    )


def sector_catalog(rows: list, active: list = None) -> str:
    """板块目录：先浏览分类。rows 为 {id,label,hint,count}。"""
    active = set(active or [])
    cards = []
    for row in rows or []:
        sid = (row or {}).get("id") or ""
        on = " on" if sid in active else ""
        cnt = (row or {}).get("count")
        nhtml = (f'<div class="n">{int(cnt)} 条</div>'
                 if cnt is not None else "")
        cards.append(
            f'<div class="scard{on}"><div class="l">{esc((row or {}).get("label") or sid)}</div>'
            f'<div class="h">{esc((row or {}).get("hint") or "")}</div>{nhtml}</div>')
    return f'<div class="scat">{"".join(cards)}</div>'


def theme_pills(themes: list) -> str:
    chips = "".join(f'<span class="nchip med">{esc(t)}</span>' for t in themes or [])
    return f'<div class="tpills">{chips}</div>'


def supply_table(rows: list) -> str:
    if not rows:
        return ""
    has_score = any((r or {}).get("score") not in (None, "", "—") for r in rows)
    body = []
    for r in rows:
        extra = (f"<td>{esc((r or {}).get('score') or '—')}</td>"
                 if has_score else "")
        body.append(
            "<tr>"
            f"<td>{esc((r or {}).get('company') or '—')}</td>"
            f"<td>{esc((r or {}).get('code') or '—')}</td>"
            f"<td>{esc((r or {}).get('product') or '—')}</td>"
            f"<td>{esc((r or {}).get('role') or '—')}</td>"
            f"{extra}</tr>")
    head = "<th>环节/标的</th><th>代码</th><th>材料/产品</th><th>角色/依据</th>"
    if has_score:
        head += "<th>利好度</th>"
    return (
        f'<table class="stable"><thead><tr>{head}'
        f"</tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


def event_card(d: dict) -> str:
    stance = (d or {}).get("stance") or "mixed"
    cls = {"bull": "bull", "bear": "bear"}.get(stance, "neu")
    st_label = {"bull": "偏利好", "bear": "偏利空",
                "mixed": "多空交织"}.get(stance, "中性")
    st_cls = {"bull": "bull", "bear": "bear"}.get(stance, "neu")
    title = (d or {}).get("title") or "(无标题)"
    why = (d or {}).get("why") or ""

    def chips(items, kind):
        if not items:
            return '<span class="nchip">暂无</span>'
        return "".join(f'<span class="nstance {kind}">{esc(x)}</span>' for x in items)

    cols = (
        f'<div class="evcols"><div><div class="lbl">利好谁</div>'
        f'{chips((d or {}).get("bull") or [], "bull")}</div>'
        f'<div><div class="lbl">利空谁</div>'
        f'{chips((d or {}).get("bear") or [], "bear")}</div></div>'
    )
    table = supply_table((d or {}).get("supply_chain") or [])
    extra = ""
    demands = (d or {}).get("tech_demands") or []
    mats = (d or {}).get("materials") or []
    if demands or mats:
        extra = (
            '<div class="evcols">'
            + (f'<div><div class="lbl">技术需求</div>{chips(demands, "neu")}</div>'
               if demands else "")
            + (f'<div><div class="lbl">关键材料</div>{chips(mats, "bull")}</div>'
               if mats else "")
            + "</div>"
        )
    return (
        f'<div class="nitem {cls}"><div class="nmeta">'
        f'<span class="nchip med">重大事件</span>'
        f'<span class="nstance {st_cls}">{esc(st_label)}</span></div>'
        f'<div class="ntitle">{esc(title)}</div>'
        + (f'<div class="nwhy">{esc(why)}</div>' if why else "")
        + cols + extra + table + "</div>"
    )


def tech_chain_card(d: dict) -> str:
    """相关标的卡片：传导逻辑 + 多只映射样本。"""
    d = d or {}
    stance = d.get("stance") or "bull"
    cls = {"bull": "bull", "bear": "bear"}.get(stance, "neu")
    mats = d.get("materials") or []
    mat_bits = []
    segs = list(d.get("segments") or [])
    for m in mats:
        name = (m or {}).get("name") or ""
        if name:
            mat_bits.append(f'<span class="nchip med">{esc(name)}</span>')
        for s in (m or {}).get("segments") or []:
            if s and s not in segs:
                segs.append(s)
    demands = d.get("tech_demands") or []
    dem_html = "".join(f'<span class="nchip">{esc(x)}</span>' for x in demands)
    seg_html = "".join(f'<span class="nchip">{esc(x)}</span>' for x in segs)
    rows = []
    cands = list(d.get("candidates") or [])
    cands.sort(key=lambda c: (
        -int((c or {}).get("bull_score") or 0),
        {"bull": 0, "neutral": 1, "mixed": 2, "bear": 3}.get(
            (c or {}).get("stance") or "", 1),
        (c or {}).get("name") or "",
    ))
    for c in cands:
        name = (c or {}).get("name") or ""
        role = (c or {}).get("role") or ""
        basis = (c or {}).get("basis") or ""
        score = (c or {}).get("bull_score")
        c_stance = (c or {}).get("stance") or ""
        st_zh = {"bull": "偏利好", "bear": "偏利空",
                 "neutral": "中性", "mixed": "交织"}.get(c_stance, "")
        score_lab = ""
        if score not in (None, ""):
            score_lab = f"{score}/10" + (f" {st_zh}" if st_zh else "")
        rows.append({
            "company": name or role or "相关标的",
            "code": (c or {}).get("code") or "—",
            "product": (c or {}).get("material") or "—",
            "role": (basis if not name else (
                role + (" · " + basis if basis else "")).strip(" ·")),
            "score": score_lab,
        })
    table = supply_table(rows)
    return (
        f'<div class="nitem {cls}"><div class="nmeta">'
        f'<span class="nchip med">相关标的</span>'
        f'<span class="nchip">模型归纳</span></div>'
        f'<div class="ntitle">{esc(d.get("event") or "(无标题)")}</div>'
        + (f'<div class="nwhy">传导逻辑 {dem_html}</div>' if dem_html else "")
        + (f'<div class="nwhy">受益环节 {"".join(mat_bits) or seg_html}</div>'
           if (mat_bits or seg_html) else "")
        + table + "</div>"
    )
