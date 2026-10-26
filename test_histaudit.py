"""histaudit 的自检测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from histaudit import analyze, find_risks, normalize_command, parse_history  # noqa: E402

ENTRY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "histaudit.py")


def test_normalize_keeps_subcommand() -> None:
    assert normalize_command("git log --oneline -n 20") == "git log --oneline"


def test_normalize_drops_flag_values() -> None:
    assert normalize_command("ls -la /tmp") == "ls"


def test_normalize_drops_positional_args() -> None:
    assert normalize_command("cd /var/log") == "cd"


def test_normalize_keeps_boolean_flags() -> None:
    result = normalize_command("docker ps -a --no-trunc")
    assert "docker ps" in result, result
    assert "-a" in result, result


def test_normalize_groups_same_pattern() -> None:
    a = normalize_command("git log --oneline -n 20")
    b = normalize_command("git log --oneline -n 5")
    assert a == b, (a, b)


def test_normalize_empty() -> None:
    assert normalize_command("") == ""
    assert normalize_command("   ") == ""


def test_parse_history_skips_timestamp_lines() -> None:
    text = "#1700000000\nls -la\n#1700000001\npwd\n"
    commands = parse_history(text)
    assert commands == ["ls -la", "pwd"], commands


def test_parse_history_skips_blank_lines() -> None:
    assert parse_history("\n\nls\n\n") == ["ls"]


def test_find_risks_detects_force_push() -> None:
    hits = find_risks("git push origin main --force")
    assert hits, "应该命中强制推送"
    assert any("强制推送" in h["description"] for h in hits), hits


def test_find_risks_detects_root_delete() -> None:
    hits = find_risks("rm -rf /")
    assert hits, hits
    assert any("根目录" in h["description"] for h in hits), hits


def test_find_risks_detects_curl_pipe_shell() -> None:
    hits = find_risks("curl -sSL https://example.com/i.sh | sh")
    assert hits, hits
    assert any("远程脚本" in h["description"] for h in hits), hits


def test_find_risks_detects_git_reset_hard() -> None:
    hits = find_risks("git reset --hard HEAD~3")
    assert hits, hits


def test_find_risks_ignores_ordinary_commands() -> None:
    for cmd in ("ls -la", "cd /tmp", "cat README.md", "python script.py"):
        assert find_risks(cmd) == [], (cmd, find_risks(cmd))


def test_analyze_counts_patterns() -> None:
    commands = ["ls -la", "ls -l", "pwd", "git status"]
    result = analyze(commands)
    assert result["total_commands"] == 4, result
    assert result["unique_patterns"] >= 2, result


def test_analyze_reports_risky_entries() -> None:
    commands = ["ls", "rm -rf /", "pwd"]
    result = analyze(commands)
    assert len(result["risky"]) == 1, result
    assert result["risky"][0]["command"] == "rm -rf /"


def test_analyze_identifies_noise_patterns() -> None:
    commands = ["ls", "pwd", "cd /tmp"]
    result = analyze(commands)
    assert len(result["noise_patterns"]) == 3, result


def test_analyze_concentration() -> None:
    commands = ["ls"] * 8 + ["pwd", "whoami"]
    result = analyze(commands)
    assert result["concentration"]["top_1_percent"] == 80.0, result


def test_cli_help_exits_zero() -> None:
    r = subprocess.run([sys.executable, ENTRY, "--help"], capture_output=True,
                       text=True, timeout=30, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    assert "histaudit" in r.stdout


def test_cli_reads_file() -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".history", delete=False,
                                     encoding="utf-8") as f:
        f.write("#1700000000\nls -la\nrm -rf /\npwd\n")
        path = f.name
    try:
        r = subprocess.run([sys.executable, ENTRY, "--file", path],
                           capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace")
        assert r.returncode == 0, r.stderr
        assert "需要复核的条目：1 条" in r.stdout, r.stdout
    finally:
        os.unlink(path)


def test_cli_json_output() -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".history", delete=False,
                                     encoding="utf-8") as f:
        f.write("ls\npwd\n")
        path = f.name
    try:
        r = subprocess.run([sys.executable, ENTRY, "--file", path, "--json"],
                           capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace")
        assert r.returncode == 0, r.stderr
        data = json.loads(r.stdout)
        assert data["total_commands"] == 2, data
    finally:
        os.unlink(path)


def test_cli_missing_file_exits_two() -> None:
    r = subprocess.run([sys.executable, ENTRY, "--file", "nope.history"],
                       capture_output=True, text=True, timeout=30,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 2, (r.returncode, r.stderr)


def test_cli_never_executes_history_commands() -> None:
    """历史文件里的命令绝不能被执行——这里放一条会创建文件的命令做验证。"""
    marker = os.path.join(tempfile.gettempdir(), "histaudit_should_not_exist.txt")
    if os.path.exists(marker):
        os.unlink(marker)
    with tempfile.NamedTemporaryFile("w", suffix=".history", delete=False,
                                     encoding="utf-8") as f:
        f.write(f"touch {marker}\n")
        path = f.name
    try:
        r = subprocess.run([sys.executable, ENTRY, "--file", path],
                           capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace")
        assert r.returncode == 0, r.stderr
        assert not os.path.exists(marker), "历史命令被执行了"
    finally:
        os.unlink(path)
        if os.path.exists(marker):
            os.unlink(marker)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  [OK  ] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [FAIL] {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n共 {len(tests)} 项，失败 {failed} 项")
    raise SystemExit(1 if failed else 0)
