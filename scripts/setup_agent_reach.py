"""安装项目独立的 Agent-Reach 与 mcporter，不修改全局工具或 Agent 技能。"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--twitter", action="store_true", help="同时安装可选 Twitter/X 只读后端")
    args = parser.parse_args()
    node, npm = shutil.which("node"), shutil.which("npm")
    if not node or not npm:
        raise SystemExit("请先安装 Node.js 20.11 或更高版本（含 npm）。")
    version = subprocess.check_output([node, "--version"], text=True).strip().lstrip("v")
    if tuple(int(x) for x in version.split(".")[:2]) < (20, 11):
        raise SystemExit("需要 Node.js >= 20.11。")
    env_dir = ROOT / ".astock" / "agent-reach-venv"
    python = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    uv = shutil.which("uv")
    if not python.is_file():
        if uv:
            subprocess.run([uv, "venv", "--python", sys.executable, str(env_dir)], check=True)
        else:
            venv.EnvBuilder(with_pip=True).create(env_dir)
    requirements = str(ROOT / "requirements-agent-reach.txt")
    install = ([uv, "pip", "install", "--python", str(python), "-r", requirements] if uv else
               [str(python), "-m", "pip", "install", "-r", requirements])
    if args.twitter:
        install += ["-r", str(ROOT / "requirements-twitter.txt")]
    subprocess.run(install, check=True)
    # Windows 的 npm 入口是 .cmd，直接使用 Node 执行 npm-cli.js。
    npm_command = [npm]
    if os.name == "nt":
        npm_cli = Path(npm).parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
        npm_command = [node, str(npm_cli)]
    subprocess.run([*npm_command, "install", "--prefix", str(ROOT / ".astock" / "agent-reach-tools"),
                    "mcporter@0.7.3", "--no-audit", "--no-fund"], check=True)
    print("Agent-Reach 搜索依赖已安装。项目使用 config/mcporter.json 中的 Exa 配置。")
    print("重启研究台后生效；情报页的渠道健康显示实际搜索结果与错误。")
    if args.twitter:
        print(f"Twitter 后端已安装；请在本地终端配置 Cookie：{python} scripts/agent_reach_twitter.py configure")


if __name__ == "__main__":
    main()
