# A股个股技术分析工具

基于 Streamlit 的 A股个股技术分析与 AI 解读工具：行情获取、指标计算（通达信口径）、
多空信号评分、关键价位推荐一站式完成，并可接入主流大模型进行多轮对话式解读。

> ⚠️ 本项目输出仅为基于历史数据的技术指标计算与推演，**不构成任何投资建议**。
> 股市有风险，入市需谨慎。

## ✨ 功能特性

### 技术分析
- **五联图**：K线 + 均线/布林带 · 成交量 · MACD · KDJ · RSI，休市日无空隙
- **通达信口径指标**：MACD 柱状 ×2、RSI 采用 SMA(X,N,1) 平滑、KDJ 以 50 为种子的
  通达信平滑、BOLL 总体标准差；量比基准剔除当期成交量
- **多空信号系统**：均线 / MACD / KDJ / RSI / 布林五大组别各投一票，避免同源信号
  重复计票；死叉文案区分"放量下杀"与"缩量阴跌"
- **A股规则适配**
  - T+1：信号仅基于已收盘K线，未收盘K线自动剔除（日/周线）
  - 涨跌停：按不复权价四舍五入到分判断；涨停拦截买入提示、跌停提示卖出风险
  - 板块差异化涨跌幅：主板 10%（含 ST 新规）、创业板/科创板 20%（ST 不降档）、
    北交所 30%
  - 空头结论一律表述为"减仓/回避"，不假设可做空
- **价位参考**：近端支撑压力（均线/布林轨/摆动高低点）+ ATR(14) 缓冲，
  输出低吸区 / 突破确认价 / 第一·第二目标 / 止损参考，全部四舍五入到分

### 🤖 AI 智能解读
- **多厂商接入**（OpenAI 兼容协议 + Anthropic 协议双支持）：

  | 预设 | 说明 |
  |---|---|
  | DeepSeek | V4 系列，思考模式开关与强度可控 |
  | Kimi / 通义千问 / 智谱 GLM / OpenAI | 开箱即用预设 |
  | Claude | Anthropic 原生 messages 协议 |
  | OpenRouter | 如 `stealth/ox-alpha` 等 |
  | 自定义 | Ollama / SiliconFlow / 各类中转站 |

- **多轮对话追问**：首次发送自动注入信号报告、价位参考与尾部K线压缩表
  （约 2.5k tokens），后续轮次只追加增量消息
- **思考链可视化**：实时流式展示模型推理过程，可折叠；侧边栏一键控制
  思考模式开关（on/off/auto）、思考强度（low~max）与是否显示
- **健壮性**：读超时 300s、零产出断流自动重试、正文被截自动"断点续写"、
  思考耗尽预算自动翻倍重试（上限 65536），确保拿到完整正文
- **Skills 技能系统**：分析提示词 = `skills/*.md` 文件，UI 内直接编辑保存；
  frontmatter 可配置温度 / max_tokens / K线注入条数 / 思考控制等
- **Key 安全**：环境变量 > 本机 secrets.toml > 手动输入三级优先，密码框输入不落日志

## 🚀 快速开始

```bash
# 1. 安装依赖（Anaconda base 环境即可满足）
pip install -r requirements.txt

# 2. 启动
streamlit run app.py
```

Windows 用户也可直接双击 `启动分析工具.bat`（需按本机 Python 路径自行修改）。

无需任何 Key 即可使用全部技术分析功能；AI 解读需在左侧边栏选择厂商并填入 API Key。

## 🔑 AI 配置

| 参数 | 说明 |
|---|---|
| API Key 来源优先级 | 环境变量 `ASTOCK_AI_KEY` → `.streamlit/secrets.toml` → 侧边栏手动输入 |
| 本机记住 | 勾选后写入 `.streamlit/secrets.toml`（明文，勿提交仓库/分享） |
| DeepSeek | base_url `https://api.deepseek.com`，模型如 `deepseek-v4-pro`；思考模式服务端默认开启且强度 high |
| OpenRouter | base_url `https://openrouter.ai/api/v1`，模型填页面上的模型 ID |

技能 frontmatter 支持字段：

```yaml
---
name: 技能名              # 可选
temperature: 0.3          # 采样温度（思考模式下部分厂商忽略）
max_tokens: 8192          # 输出预算：思考链与正文共享，思考型模型建议 ≥8192
bars_tail: 60             # 注入K线条数，调小省 token
thinking: auto            # on/off/auto：显式控制厂商思考模式开关
reasoning_effort: low     # low/medium/high/xhigh/max 思考强度
---
```

## 🧩 项目结构

```
astock_analyzer/
├── app.py            # Streamlit 主界面（图表/报告/AI对话/技能编辑器）
├── data_fetcher.py   # 行情获取(新浪/腾讯·akshare)、复权合并、涨跌停标志
├── indicators.py     # MA/BOLL/MACD/KDJ/RSI/ATR/量比（通达信口径）
├── signals.py        # 五组信号生成与分组投票评分
├── trade_plan.py     # 价位参考（支撑压力+ATR缓冲）
├── report.py         # 综合报告组装
├── ai_client.py      # 双协议 LLM 客户端（流式/重试/续写/预算自适应）
├── ai_context.py     # 提示词渲染与上下文压缩
├── skill_store.py    # skills 目录管理
├── skills/           # 预置技能：综合解读 / 趋势波段 / 风控审查
└── tests/            # 单元测试（65+ 用例，无需网络）
```

## 🧪 运行测试

```bash
python -m unittest discover -s tests
```

所有外部请求均被 mock，可离线运行。

## ⚠️ 已知局限

- 除权除息日的涨跌停基准价未做除权调整（该日标志可能偏差），精确修复需数据源提供"前收"字段
- 新股上市初期的特殊涨跌幅规则（首日 44% / 注册制前5日无限制等）未单独处理
- 新浪 qfq 数据在极端分红情形可能出现负价格，触发数据清洗删行
- 不同数据源成交量单位可能不一致（股 vs 手），比值类指标不受影响

## 📄 License

[MIT](LICENSE) © 2026 yjunmo

---

**免责声明**：本项目仅供学习与研究，所输出的指标、信号、价位及 AI 解读内容均基于
公开历史数据的技术性推演，不构成任何证券投资建议。据此操作，风险自担。
