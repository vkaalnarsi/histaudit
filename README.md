# histaudit

**你的历史里，有几条是你自己也想再确认一遍的。**

`histaudit` 只读分析 shell 历史文件：统计真正的常用命令模式，把命中具体风险写法的条目单独列出来，
并给出可考虑清理的冗余范围。

- 零依赖，Python 3.10+ 标准库即可运行
- 单文件，`histaudit.py` 拷走就能用
- **只读不执行**：它从不运行历史里的任何一条命令

<!-- ipos:install -->
## 安装

零依赖，Python 3.10+ 即可：

```bash
pip install vkaalnarsi-histaudit
```

装完直接用命令名调用：

```bash
histaudit --help
```

也可以不用安装，直接把 `histaudit.py` 拷走用——它只依赖标准库。

## 为什么需要它

翻自己的历史时，通常会发现两件不太一样的事：

- **真正高频的命令其实很集中**。几十条命令撑起了日常使用，剩下的多是当时的一次性操作。
- **偶尔出现、值得确认的那几条**，往往带着破坏性——但它们混在几百条记录里，没人单独看过。

`histaudit` 把这两件事分开处理。风险判定只按**具体写法**来，不按命令名泛化：

| 命中的写法 | 判定 | 建议 |
|---|---|---|
| `rm -rf /` | 递归删除根目录 | 确认是否有意为之；这类写法通常来自复制粘贴 |
| `git push ... --force` | 强制推送覆盖远端 | 确认远端没有他人提交会被覆盖 |
| `git reset --hard` | 丢弃未提交的改动 | 确认工作区没有需要保留的内容 |
| `curl ... \| sh` | 下载并直接执行远程脚本 | 先下载查看内容再执行 |
| `dd ... of=/dev/...` | 直接写块设备 | 确认目标设备，误写会损坏磁盘 |

没命中的条目也会被明确说明：**这不等于安全**，规则只看写法不看后果。

## 用法

```bash
# 自动找常见历史文件
python histaudit.py

# 指定文件
python histaudit.py --file ~/.bash_history

# 列出更多高频模式
python histaudit.py --file ~/.bash_history --top 20

# 只看需要复核的
python histaudit.py --file ~/.bash_history --only-risky

# 机器可读输出
python histaudit.py --file ~/.bash_history --json
```

输出：

```
分析文件：C:\Users\louis\.bash_history
命令总数：412
不同模式：138
前 10 个模式占全部命令的 46.1%（最高一个占 18.2%）

高频命令模式（前 10 个）：
   38 次  git status
   26 次  ls
   21 次  git log --oneline
   ...
需要复核的条目：2 条

  第 118 条：git push origin main --force
    - 强制推送覆盖远端
      建议：确认远端没有他人提交会被覆盖

  第 233 条：rm -rf /tmp/build
    - 递归删除家目录
      建议：检查路径是否完整

只出现一次的低价值模式：97 个
  这些是可考虑清理的候选；建议先确认没有拼写错误导致的重复。
```

## 命令模式是怎么归一的

`normalize_command` 把一条命令抹成骨架：抹掉参数值，保留命令与子命令结构。

```
git log --oneline -n 20   →  git log --oneline
git log --oneline -n 5    →  git log --oneline     ← 同一条模式
ls -la /tmp              →  ls
cd /var/log              →  cd
```

布尔型标志（`-a`、`--no-trunc`）保留，带值的参数抹掉；
`git`/`docker`/`kubectl`/`npm`/`systemctl` 这类命令会额外保留子命令。

## 已知局限

- **不执行命令，也不判断命令的实际效果**。规则是模式匹配，`rm -rf /tmp/build` 会命中「递归删除家目录」这一条——因为它长得像，实际影响的是 `/tmp/build`。这是刻意的保守取舍，提示你去看一眼而不是替你判断。
- **不做语义去重**。`git log --oneline` 和 `git log --pretty=oneline` 会被算成两条模式。
- **只读文件里的当前内容**，不解析 bash 的 `HISTTIMEFORMAT` 之外的时间信息，只跳过 `#epoch` 行。
- **不识别别名与函数**。历史里记的是展开前的写法，别名会被当成普通命令名。
- **不处理多行命令**。反斜杠续行的命令会被当成多条独立记录。

## 跑测试

```bash
python test_histaudit.py
```

测试里有一条专门验证「历史中的命令绝不被执行」：往历史文件里写一条 `touch` 命令，
跑完断言目标文件没有被创建。

## 为什么还要做这个

现有的 shell 历史工具大多只做一件事——按时间列出或者按前缀搜索。
但真正的问题不是「找一条命令」，而是「搞清楚我平时到底在干什么，
以及哪几条值得再看一眼」。

`histaudit` 的取舍很明确：宁可少报也不乱报，命中不了就写「需人工确认」，
并且永远不替你执行任何东西。

## 来源与参考

- Bash 手册的历史功能章节：<https://www.gnu.org/software/bash/manual/html_node/History-Builtins.html>
- POSIX shell 命令行规范：<https://pubs.opengroup.org/onlinepubs/9699919799/utilities/V3_chap02.html>
- Python `shlex` 模块文档：<https://docs.python.org/3/library/shlex.html>

---

## 关于作者

vkaalnarsi 做 shell 历史审阅。这个工具先列结论，再给证据，最后给处理范围。

- 个人主页：<https://vkaalnarsi.github.io>
- 仓库：<https://github.com/vkaalnarsi/histaudit>

## 许可

MIT，见 [LICENSE](LICENSE)。
