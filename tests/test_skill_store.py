import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skill_store import (delete_skill, list_skills, load_skill,
                         new_skill_content, save_skill)


class TestSkillStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_save_load_roundtrip(self):
        save_skill("综合复盘", "内容A", base_dir=self.base)
        name = list_skills(self.base)[0]
        self.assertEqual(load_skill(name, base_dir=self.base), "内容A")
        # 中文与空格等不安全字符被替换，且可按原名再次读写
        save_skill("综合复盘", "内容B", base_dir=self.base)
        self.assertEqual(load_skill("综合复盘", base_dir=self.base), "内容B")

    def test_unsafe_names_sanitized(self):
        p = save_skill("../evil", "x", base_dir=self.base)
        self.assertEqual(p.parent, self.base)
        self.assertEqual(p.name, "evil.md")
        p2 = save_skill("README", "x", base_dir=self.base)
        self.assertNotEqual(p2.name.lower(), "readme.md")

    def test_list_excludes_readme_and_sorted(self):
        save_skill("b技能", "x", base_dir=self.base)
        save_skill("a技能", "y", base_dir=self.base)
        (self.base / "README.md").write_text("doc", encoding="utf-8")
        names = list_skills(self.base)
        self.assertEqual(names, ["a技能.md", "b技能.md"])

    def test_delete(self):
        save_skill("tmp技能", "x", base_dir=self.base)
        name = list_skills(self.base)[0]
        self.assertTrue(delete_skill(name, base_dir=self.base))
        self.assertFalse(delete_skill(name, base_dir=self.base))
        self.assertIsNone(load_skill(name, base_dir=self.base))

    def test_template_has_placeholders(self):
        content = new_skill_content("打板复盘")
        for token in ("{report}", "{plan}", "{bars}", "{snapshot}"):
            self.assertIn(token, content)
        self.assertIn("name: 打板复盘", content)

    def test_empty_name_fallback(self):
        p = save_skill("///", "x", base_dir=self.base)
        self.assertTrue(p.exists())
        self.assertNotEqual(p.name, ".md")


if __name__ == "__main__":
    unittest.main()
