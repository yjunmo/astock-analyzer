"""skills 目录管理：技能文件的列表/读取/保存/删除与新建模板。

文件名做安全过滤：仅保留字母数字下划线连字符与中文，防止路径穿越。
"""
from __future__ import annotations

import re
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent / "skills"

_UNSAFE = re.compile(r"[^0-9A-Za-z_\u4e00-\u9fff-]+")


def _safe_name(name: str) -> str:
    stem = Path(str(name)).stem
    cleaned = _UNSAFE.sub("_", stem).strip("._-") or "skill"
    if cleaned.lower() == "readme":
        cleaned = "my_skill"
    return cleaned + ".md"


def list_skills(base_dir: Path = SKILLS_DIR) -> list:
    if not base_dir.exists():
        return []
    return sorted(p.name for p in base_dir.glob("*.md")
                  if p.name.lower() != "readme.md")


def load_skill(name: str, base_dir: Path = SKILLS_DIR):
    p = base_dir / _safe_name(name)
    if not p.exists():
        return None
    # utf-8-sig 兼容带 BOM 的文件（如 Windows 记事本保存）
    return p.read_text(encoding="utf-8-sig")


def save_skill(name: str, content: str, base_dir: Path = SKILLS_DIR) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    p = base_dir / _safe_name(name)
    p.write_text(content, encoding="utf-8")
    return p


def delete_skill(name: str, base_dir: Path = SKILLS_DIR) -> bool:
    p = base_dir / _safe_name(name)
    if p.exists():
        p.unlink()
        return True
    return False


TEMPLATE = """---
name: 新技能
temperature: 0.2
max_tokens: 6144
bars_tail: 60
reasoning_effort: low
---
<role>
你是严谨的 A 股研究分析师。基于用户消息中的 <research_context> 回答问题。
</role>

<analysis_protocol>
1. 事实引用证据编号；推断写置信度、关键假设和失效条件。
2. 主动检查反证和数据缺口，不把回测表现写成未来概率。
3. 新闻区分公告、媒体报道和未证实社交传闻。
</analysis_protocol>

<response_contract>
按“核心结论 → 证据与反证 → 情景/执行条件 → 风险提示”输出。保持简洁，不展示隐藏思维链，不构成投资建议。
</response_contract>
"""


def new_skill_content(title: str = "") -> str:
    content = TEMPLATE
    if title:
        content = content.replace("name: 新技能", f"name: {title}", 1)
    return content
