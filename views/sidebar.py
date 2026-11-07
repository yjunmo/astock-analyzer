"""侧边栏：分析设置 + AI 配置 + 技能编辑器。"""
import os

import streamlit as st

from astock.ai import skill_store
from astock.ai.ai_client import PROVIDERS, clear_local_config, load_local_config, save_local_config


def render_sidebar():
    with st.sidebar:
        st.markdown('<div class="cloud-eyebrow">YOUR WORKSPACE</div>', unsafe_allow_html=True)
        st.markdown("### 个股研究")
        st.caption("从市场全景，深入一家公司")
        samples = [("600519", "茅台"), ("600036", "招商银行"),
                   ("300750", "宁德时代"), ("601318", "中国平安")]
        q1, q2 = st.columns(2)
        for i, (code_, label) in enumerate(samples):
            box = q1 if i % 2 == 0 else q2
            if box.button(f"{label}", width="stretch", key=f"q{i}"):
                st.session_state["code_field"] = code_
                st.session_state["_pending_code"] = code_
                st.session_state["_pending_run"] = True
        with st.form("stock_query", border=False):
            code = st.text_input("股票代码", key="code_field",
                                 placeholder="如 600519 / 000001",
                                 help="支持沪深、创业板、科创板和北交所 6 位代码")
            period = st.radio(
                "研究周期", ["daily", "weekly", "monthly"],
                format_func=lambda p: {"daily": "日线", "weekly": "周线",
                                       "monthly": "月线"}[p],
                horizontal=True, key="period_radio")
            with st.expander("高级参数", expanded=False):
                adjust = st.selectbox(
                    "复权方式", ["qfq", "", "hfq"],
                    format_func=lambda a: {"qfq": "前复权", "hfq": "后复权",
                                           "": "不复权"}[a],
                    help="趋势研究通常使用前复权；核对历史成交价可选不复权")
                n_bars = st.slider("图表区间", 60, 300, 150, step=10,
                                   help="只影响图表展示，不改变指标计算历史")
            run = st.form_submit_button("开始分析", type="primary",
                                        width="stretch", icon=":material/search:")
        st.caption("基础分析无需 API Key · 数据异常会自动降级")
    return code, period, adjust, n_bars, run


def render_ai_settings() -> dict:
    """侧边栏 AI 设置：厂商/BaseURL/模型/Key/本机记忆 + 技能选择。"""
    with st.sidebar:
        st.divider()
        with st.expander("AI 研究助手（可选）", expanded=False,
                         icon=":material/auto_awesome:"):
            return _render_ai_settings_body()


def render_search_settings() -> None:
    """可选搜索 API：仅写入当前进程环境，不在界面或日志中回显。"""
    with st.sidebar:
        with st.expander("搜索增强（可选）", expanded=False,
                         icon=":material/travel_explore:"):
            from astock.search.agent_reach_backend import backend_installed
            installed = backend_installed()
            st.caption("优先使用 Agent-Reach 全网搜索，无需填写搜索 Key；网页正文由 Jina Reader 提取。"
                       "搜索服务失败时自动切换备用来源。Agent 规划、证据核验和补搜使用当前 AI 模型；"
                       "无模型时只执行基础检索，不标记为完成核验。")
            if installed:
                st.caption("Agent-Reach 搜索依赖已安装；实际连通状态见情报页的渠道健康。")
            else:
                st.caption("Agent-Reach 搜索依赖未安装，当前使用备用搜索。安装方法见 README 的搜索增强章节。")
            st.caption("Twitter/X 可配置独立只读通道：使用 Cookie-Editor 手动导出并在本地终端隐藏输入，"
                       "不自动读取浏览器 Cookie。配置命令见 README；仅配置完成不代表已联网验证。")
            if "search_brave_key" not in st.session_state:
                st.session_state["search_brave_key"] = os.environ.get(
                    "ASTOCK_BRAVE_API_KEY", "")
            if "search_tavily_key" not in st.session_state:
                st.session_state["search_tavily_key"] = os.environ.get(
                    "ASTOCK_TAVILY_API_KEY", "")
            if "search_google_key" not in st.session_state:
                st.session_state["search_google_key"] = os.environ.get(
                    "ASTOCK_GOOGLE_CSE_KEY", "")
            if "search_google_cx" not in st.session_state:
                st.session_state["search_google_cx"] = os.environ.get(
                    "ASTOCK_GOOGLE_CSE_CX", "")
            brave = st.text_input("Brave Search API Key", type="password",
                                  key="search_brave_key")
            tavily = st.text_input("Tavily API Key", type="password",
                                   key="search_tavily_key")
            google = st.text_input("Google CSE API Key", type="password",
                                   key="search_google_key")
            google_cx = st.text_input("Google CSE CX", type="password",
                                      key="search_google_cx")
            if brave.strip():
                os.environ["ASTOCK_BRAVE_API_KEY"] = brave.strip()
            if tavily.strip():
                os.environ["ASTOCK_TAVILY_API_KEY"] = tavily.strip()
            if google.strip():
                os.environ["ASTOCK_GOOGLE_CSE_KEY"] = google.strip()
            if google_cx.strip():
                os.environ["ASTOCK_GOOGLE_CSE_CX"] = google_cx.strip()
            enabled = [name for name, key in (("Brave", brave), ("Tavily", tavily),
                                               ("Google CSE", google and google_cx))
                       if key.strip()]
            st.caption("备用搜索：" + ("、".join(enabled) if enabled else "免费聚合搜索"))
            st.divider()
            st.caption("预警通知 Webhook（留空则只保存站内通知）")
            webhook = st.text_input("Webhook URL", value=os.environ.get("ASTOCK_WEBHOOK_URL", ""),
                                    key="alert_webhook_url")
            token = st.text_input("Webhook Token", value=os.environ.get("ASTOCK_WEBHOOK_TOKEN", ""),
                                  type="password", key="alert_webhook_token")
            if webhook.strip():
                os.environ["ASTOCK_WEBHOOK_URL"] = webhook.strip()
            if token.strip():
                os.environ["ASTOCK_WEBHOOK_TOKEN"] = token.strip()


def _render_ai_settings_body() -> dict:
    """AI 设置主体放进折叠面板，避免基础研究被连接参数淹没。"""
    with st.container():
        saved = load_local_config()
        env_key = os.environ.get("ASTOCK_AI_KEY", "")
        prov_ids = list(PROVIDERS.keys())
        default_pid = saved.get("provider") if saved.get("provider") in PROVIDERS else "deepseek"
        pid = st.selectbox("厂商", prov_ids,
                           index=prov_ids.index(default_pid),
                           format_func=lambda x: PROVIDERS[x]["label"],
                           key="ai_provider")
        first = "_ai_prev_provider" not in st.session_state
        prev = st.session_state.get("_ai_prev_provider")
        if first or prev != pid:
            st.session_state["_ai_prev_provider"] = pid
            info = PROVIDERS[pid]
            if first and saved.get("base_url"):
                st.session_state.setdefault("ai_base_url", saved["base_url"])
                st.session_state.setdefault("ai_model", saved.get("model", ""))
            else:
                st.session_state["ai_base_url"] = info["base_url"]
                st.session_state["ai_model"] = info["models"][0] if info["models"] else ""
            if first and saved.get("api_key") and not env_key:
                pass  # 下方 value= 会带入 saved 的 Key

        base_url = st.text_input("Base URL", key="ai_base_url")
        hint_models = "，".join(PROVIDERS[pid]["models"]) or "如 http://localhost:11434/v1 + 模型名"
        model = st.text_input("模型", key="ai_model",
                              help=f"常见模型：{hint_models}")
        default_key = env_key or saved.get("api_key", "")
        if "ai_api_key_input" not in st.session_state:
            st.session_state["ai_api_key_input"] = default_key
        api_key = st.text_input("API Key", key="ai_api_key_input",
                                type="password",
                                help="优先级：环境变量 ASTOCK_AI_KEY > 本机保存 > 手动输入")
        remember = st.checkbox("记住到本机（.streamlit/secrets.toml，明文）",
                               value=bool(saved.get("api_key")) and not env_key,
                               disabled=bool(env_key))
        c1, c2 = st.columns(2)
        if c1.button("💾 保存本机", disabled=not (remember and api_key)):
            save_local_config({"provider": pid, "base_url": base_url,
                               "model": model, "api_key": api_key})
            st.success("已保存，下次启动免输入")
        if c2.button("🗑 清除本机", disabled=not saved):
            clear_local_config()
            st.rerun()

        skills = skill_store.list_skills()
        skill_name = st.selectbox("分析技能", skills or ["default"],
                                  index=(skills.index("default") if "default" in skills else 0),
                                  key="ai_skill_sel")
        st.caption("提示词可在下方「技能编辑器」中修改并保存。")

        st.markdown("---")
        think_mode = st.selectbox(
            "思考模式开关", ["auto", "on", "off"],
            format_func=lambda v: {"auto": "跟随技能/厂商默认",
                                   "on": "强制开启", "off": "关闭"}[v],
            key="ai_think_mode",
            help="DeepSeek V4 服务端默认开启思考且强度 high；"
                 "显式选择将覆盖技能 frontmatter 配置")
        effort = st.selectbox(
            "思考强度", ["auto", "low", "medium", "high", "xhigh", "max"],
            format_func=lambda v: "跟随技能/默认" if v == "auto" else v,
            key="ai_effort",
            help="DeepSeek V4 映射：medium→high；仅支持该参数的厂商生效")
        show_think = st.toggle("💭 显示模型推理", value=False, key="ai_show_think",
                               help="默认只显示研究结论；开启后展示模型返回的推理字段")

        st.markdown("---")
        web_opts = ["off", "on"]
        if st.session_state.get("ai_web_mode") == "native":
            st.session_state["ai_web_mode"] = "on"
        web_mode = st.selectbox(
            "🌐 联网搜索", web_opts,
            format_func=lambda v: {"off": "关闭",
                                   "on": "开启（Agent 研究搜索）"}.get(v, v),
            key="ai_web_mode",
            help="开启后先规划问题、搜索并阅读原文，再核验与补搜，回答保留来源和缺口。所有厂商使用同一研究流程")
        return {"provider_cfg": PROVIDERS[pid], "model": model.strip(),
                "api_key": api_key.strip(), "skill_name": skill_name,
                "think_mode": think_mode, "effort": effort,
                "show_think": show_think, "web_mode": web_mode}


def render_skill_editor():
    with st.expander("✏️ 技能编辑器（Markdown 提示词，保存后立即生效）"):
        skills = skill_store.list_skills()
        mode = st.radio("操作", ("编辑现有", "新建技能"), horizontal=True, key="ed_mode")
        if mode == "编辑现有":
            if not skills:
                st.info("skills 目录暂无技能文件")
                return
            name = st.selectbox("选择技能", skills, key="ed_sel")
            content = skill_store.load_skill(name) or ""
            edited = st.text_area(
                "内容（支持占位符 {report} {plan} {bars} {snapshot} {fundamentals} {backtest} {multi_period}）",
                value=content, height=440, key=f"ed_body_{name}")
            new_name = st.text_input("另存为（留空则覆盖原文件）", key="ed_as")
            col1, col2, _ = st.columns([1, 1.4, 2])
            if col1.button("💾 保存", key="ed_save"):
                target = new_name.strip() or name
                skill_store.save_skill(target, edited)
                st.success(f"已保存：{skill_store._safe_name(target)}")
                st.rerun()
            if col2.button("🗑 删除该技能", key="ed_del"):
                skill_store.delete_skill(name)
                st.rerun()
        else:
            title = st.text_input("新技能名称", placeholder="如：打板复盘", key="ed_new_name")
            if st.button("从模板创建", key="ed_create"):
                clean = title.strip()
                if not clean:
                    st.warning("请先输入名称")
                else:
                    skill_store.save_skill(clean, skill_store.new_skill_content(clean))
                    st.success(f"已创建 {skill_store._safe_name(clean)}，请切回「编辑现有」完善内容")
                    st.rerun()
