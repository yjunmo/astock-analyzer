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
temperature: 0.3
max_tokens: 8192
bars_tail: 60
---
你是一名严谨的A股技术分析助理。请基于下方工具已计算好的客观数据进行分析，
不得虚构数值；区分指标事实与概率推断；偏空结论表述为减仓/回避（A股无做空工具）。

## 输出结构
1. 结论摘要
2. 依据逐条分析
3. 操作思路（结合价位参考）
4. 风险与失效条件

## 信号报告
{report}

## 价位参考
{plan}

## 实时快照
{snapshot}

## 近期行情（管道分隔，尾部K线）
{bars}
"""


def new_skill_content(title: str = "") -> str:
    content = TEMPLATE
    if title:
        content = content.replace("name: 新技能", f"name: {title}", 1)
    return content
