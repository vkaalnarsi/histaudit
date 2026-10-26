"""histaudit —— 给 shell 历史做体检。

设计原则：
1. 只读不写：任何时候都不执行历史里记录的命令。
2. 先列结论，再给证据，最后给处理范围。
3. 不制造恐慌——风险按具体程度描述，不喊「危险」。

仅依赖 Python 标准库。

用法:
    python histaudit.py --help
    python histaudit.py --file ~/.bash_history
    python histaudit.py --file ~/.bash_history --top 20
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from collections import Counter, defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# 风险规则：只标具体行为，不做价值判断
# ---------------------------------------------------------------------------

# 每条规则给出：匹配模式、风险描述、建议动作
RISK_RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"\brm\s+(-[a-zA-Z]*\s+)*(-rf|-fr|-r\s+-f)\s+/(\s|$)"),
     "递归删除根目录", "确认这条是否有意为之；`rm -rf /` 的写法通常来自复制粘贴"),
    (re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f?\s+~/?$"),
     "递归删除家目录", "检查路径是否完整"),
    (re.compile(r"\bgit\s+(reset\s+--hard|clean\s+-[a-zA-Z]*f)"),
     "丢弃未提交的改动", "确认工作区没有需要保留的内容"),
    (re.compile(r"\bgit\s+push\s+.*--force(?!-with-lease)"),
     "强制推送覆盖远端", "确认远端没有他人提交会被覆盖"),
    (re.compile(r"\bgit\s+branch\s+-D\b"),
     "强制删除分支", "确认该分支的提交已被合并或另存"),
    (re.compile(r"\bdd\s+.*of=/dev/"),
     "直接写块设备", "确认目标设备，误写会损坏磁盘"),
    (re.compile(r"\bmkfs(\.\w+)?\b"),
     "格式化文件系统", "确认目标分区"),
    (re.compile(r":\(\)\s*\{.*\}\s*;?\s*:"),
     "fork 炸弹写法", "确认不是误粘贴"),
    (re.compile(r"\bchmod\s+(-R\s+)?777\b"),
     "给所有文件开放全部权限", "确认是否真的需要"),
    (re.compile(r"\bsudo\s+rm\b"),
     "以管理员身份删除文件", "确认路径与权限需求"),
    (re.compile(r"\bcurl\b[^|;]*\|\s*(sudo\s+)?(ba)?sh\b"),
     "下载并直接执行远程脚本", "先下载查看内容再执行"),
    (re.compile(r"\bhistory\s+-c\b"),
     "清空命令历史", "确认是否真的要清除痕迹"),
    (re.compile(r"\bexport\s+[A-Z_]*(TOKEN|SECRET|PASSWORD|KEY)[A-Z_]*\s*="),
     "把敏感值写进环境变量", "注意它可能被后续命令记录下来"),
]

# 命令模式归一化：把参数抹掉，只留命令骨架
FLAG_RE = re.compile(r"^--?[A-Za-z0-9][\w-]*$")
LONG_FLAG_RE = re.compile(r"^--[a-z0-9][\w-]*$")
SUBCOMMAND_HINTS = {
    "git": {"subcommands"},
    "docker": {"run", "exec", "ps", "images", "build", "logs", "compose"},
    "kubectl": {"get", "describe", "logs", "apply", "delete", "rollout"},
    "npm": {"run", "install", "test", "build"},
    "pip": {"install", "uninstall", "list", "show"},
    "cargo": {"build", "run", "test", "install"},
    "go": {"build", "run", "test", "get"},
    "yarn": {"install", "run", "add"},
    "docker-compose": {"up", "down", "logs", "ps", "build"},
    "systemctl": {"status", "start", "stop", "restart", "enable", "disable"},
    "apt": {"install", "remove", "update", "upgrade"},
    "brew": {"install", "uninstall", "list"},
}


def normalize_command(command: str) -> str:
    """把一条命令归一化成模式：抹掉参数值，保留命令与子命令结构。

    `git log --oneline -n 20` → `git log --oneline`
    `ls -la /tmp` → `ls`
    """
    try:
        parts = shlex.split(command, comments=True)
    except ValueError:
        parts = command.split()
    if not parts:
        return ""

    tokens = [parts[0]]
    base = os.path.basename(parts[0])
    if base in SUBCOMMAND_HINTS and len(parts) > 1:
        candidate = parts[1]
        if not candidate.startswith("-"):
            tokens.append(candidate)

    # 只保留布尔型标志，抹掉带值的参数
    rest = parts[len(tokens):]
    i = 0
    while i < len(rest):
        token = rest[i]
        nxt = rest[i + 1] if i + 1 < len(rest) else None
        # 下一个 token 不以 "-" 开头 → 当前标志吃掉了它，两者一起丢掉
        flag_takes_value = nxt is not None and not nxt.startswith("-")
        if token.startswith("--"):
            if "=" in token:
                i += 1
                continue
            if LONG_FLAG_RE.match(token) and not flag_takes_value:
                tokens.append(token)
            i += 1
            continue
        if token.startswith("-") and len(token) > 1:
            if token[1:].isdigit():
                i += 1
                continue
            if not flag_takes_value:
                tokens.append(token)
            i += 1
            continue
        # 裸参数：之后的都是值，不再保留
        break
    return " ".join(tokens)


def parse_history(text: str) -> list[str]:
    """从历史文件内容里提取命令。

    bash 的历史里可能带 `#epoch` 时间戳行，这里跳过。
    """
    commands = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") and stripped[1:].strip().isdigit():
            continue
        commands.append(stripped)
    return commands


def find_risks(command: str) -> list[dict]:
    """检查一条命令命中的风险规则。"""
    hits = []
    for pattern, description, advice in RISK_RULES:
        if pattern.search(command):
            hits.append({"description": description, "advice": advice})
    return hits


# ---------------------------------------------------------------------------
# 分析
# ---------------------------------------------------------------------------

def analyze(commands: list[str]) -> dict:
    """统计命令模式，筛出值得复核的条目。"""
    pattern_counter: Counter[str] = Counter()
    pattern_examples: dict[str, str] = {}
    risky: list[dict] = []
    by_prefix: Counter[str] = Counter()

    for index, command in enumerate(commands):
        pattern = normalize_command(command)
        if pattern:
            pattern_counter[pattern] += 1
            pattern_examples.setdefault(pattern, command)
        base = command.split()[0] if command.split() else "?"
        by_prefix[os.path.basename(base)] += 1

        hits = find_risks(command)
        if hits:
            risky.append({
                "index": index,
                "command": command,
                "risks": hits,
            })

    total = len(commands)
    top = [{
        "pattern": pattern,
        "count": count,
        "share": round(count / total, 4) if total else 0,
        "example": pattern_examples.get(pattern, ""),
    } for pattern, count in pattern_counter.most_common()]

    # 只出现一次、且没命中风险的命令，视为低价值噪声
    noise = [t["pattern"] for t in top if t["count"] == 1]

    return {
        "total_commands": total,
        "unique_patterns": len(pattern_counter),
        "top_patterns": top,
        "risky": risky,
        "noise_patterns": noise,
        "prefix_counts": by_prefix.most_common(20),
        "concentration": {
            "top_10_share": round(sum(t["count"] for t in top[:10]) / total, 4)
            if total else 0,
            "top_1_percent": round(top[0]["share"] * 100, 2) if top else 0,
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

DEFAULT_PATHS = [
    "~/.bash_history",
    "~/.zsh_history",
    "~/.sh_history",
]


def locate_history(explicit: str | None) -> list[Path]:
    """定位历史文件。找不到时返回空列表。"""
    if explicit:
        return [Path(explicit).expanduser()]
    found = []
    for candidate in DEFAULT_PATHS:
        p = Path(candidate).expanduser()
        if p.is_file():
            found.append(p)
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="histaudit",
        description="只读分析 shell 历史：统计命令模式，筛出值得复核的条目（仅依赖标准库）",
        epilog=(
            "示例:\n"
            "  python histaudit.py --file ~/.bash_history\n"
            "  python histaudit.py --file ~/.bash_history --top 20\n"
            "  python histaudit.py --file ~/.bash_history --json\n"
            "\n"
            "本工具只读取历史文件，不会执行其中任何一条命令。\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--file", help="历史文件路径；不给则自动找常见位置")
    ap.add_argument("--top", type=int, default=10, help="列出前 N 个高频模式（默认 10）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--only-risky", action="store_true", help="只列出需要复核的条目")
    args = ap.parse_args(argv)

    paths = locate_history(args.file)
    if not paths:
        print("找不到历史文件。用 --file 指定路径。", file=sys.stderr)
        return 2
    if args.file and not paths[0].is_file():
        print(f"找不到历史文件：{paths[0]}", file=sys.stderr)
        return 2

    commands: list[str] = []
    used: list[str] = []
    for path in paths:
        if not path.is_file():
            print(f"跳过不存在的文件：{path}", file=sys.stderr)
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"读取失败 {path}：{e}", file=sys.stderr)
            continue
        commands.extend(parse_history(text))
        used.append(str(path))

    if not commands:
        print("历史文件里没有读到命令。", file=sys.stderr)
        return 1

    result = analyze(commands)
    result["files"] = used

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"分析文件：{'、'.join(used)}")
    print(f"命令总数：{result['total_commands']}")
    print(f"不同模式：{result['unique_patterns']}")
    print(f"前 10 个模式占全部命令的 {result['concentration']['top_10_share'] * 100:.1f}%"
          f"（最高一个占 {result['concentration']['top_1_percent']:.1f}%）")
    print()

    if not args.only_risky:
        print(f"高频命令模式（前 {min(args.top, len(result['top_patterns']))} 个）：")
        for row in result["top_patterns"][:args.top]:
            print(f"  {row['count']:>5} 次  {row['pattern']}")
        print()

    print(f"需要复核的条目：{len(result['risky'])} 条")
    if result["risky"]:
        for item in result["risky"]:
            print(f"\n  第 {item['index'] + 1} 条：{item['command']}")
            for risk in item["risks"]:
                print(f"    - {risk['description']}")
                print(f"      建议：{risk['advice']}")
    else:
        print("  （未命中任何具体风险规则。注意这不等于安全，规则只看写法不看后果。）")

    print()
    print(f"只出现一次的低价值模式：{len(result['noise_patterns'])} 个")
    print("  这些是可考虑清理的候选；建议先确认没有拼写错误导致的重复。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
