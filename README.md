# claude-guard-hooks

**不让 Claude Code 再说「做完了」，其实没做。**

4 个 Claude Code hook，Python 3.8+，只用标准库，Windows / macOS / Linux 都能用。装上之后，Claude 想说「已修复」「测试通过」「做不到」「已推送」的时候，要先拿出证据，否则会被拦下来，让它回去补做。简体、繁体（台湾）、英文的说法都认。

| Hook | 什么时候触发 | 抓什么 |
|---|---|---|
| `stop_claim_audit.py` | Claude 回复结束时（Stop） | 说「已修复 / 测试通过 / 已推送 / 已派子代理 / 找到根因」，但这一轮根本没调用过对应的工具 |
| `stop_retreat_gate.py` | Claude 回复结束时（Stop） | 说「做不到 / 换个方向 / 先放着」，但没列出自己试过哪几条路 |
| `pre_pipe_exitcode.py` | 执行 Bash / PowerShell 命令之前（PreToolUse） | `npm test \| tail -5 && git commit` 这种测试红了照样提交的命令 |
| `pre_push_secret_scan.py` | 执行 Bash / PowerShell 命令之前（PreToolUse） | `git push` 前，要推上去的 commit 里有 API key、私钥、`.env` |

---

## 每个 hook 拦什么（前后对比）

### 1. 声称对账闸 `stop_claim_audit.py`

**没装之前**：Claude 改了一个文件，然后回复：

> 已修复，测试全部通过。

可是这一轮它根本没跑测试。

**装了之后**：回复被拦下，Claude 收到：

```
声称对账闸：回复里有「已经做了」的说法，但 transcript 里找不到对应的工具调用。
- 修好 / 测试通过 fixed / tests pass：你写了「已修复」，但最后一次改档之后没有调用过 Agent/Bash/PowerShell/Task。
二选一：① 现在真的去做（调用工具，贴出命令和输出）；② 把话改成未来式（「接下来要…」「还没跑」）。
```

然后它会去真的跑测试，或者老实改成「改好了，还没跑测试」。

检查的说法（简体、繁体、英文都认，例如「已修復，測試全部通過」「正在背景執行」也会拦）：已修复 / 修好了 / 测试通过 / 全绿 / tests pass、已完成 / 做完了、找到根因、跑过 / 验证过、已提交 / 已推送、已派子代理、正在后台跑、已下载 / 看过。
「修好 / 测试通过 / 完成」这几类，**验证必须发生在最后一次改档之后**：先跑测试、再改代码、再说「修好了」，一样会被拦。

不会拦：未来式（「接下来要跑」「I will push」）、问句（「测试通过了吗？」）、引用用户的话、行内代码、选项表里的选项、上一轮跑过这一轮才报告的结果。

### 2. 退路闸 `stop_retreat_gate.py`

**没装之前**：

> 试了一下，做不到，先放着吧。

（其实只试了一种方法。）

**装了之后**：被拦下，Claude 被要求先想出至少 3 条机制不同的做法、真的试过，再在回复里附上：

```
已试路径：
| 路 | 机制 | 证据 | 结果 |
|---|---|---|---|
| 调参数 | 配置 | build.log 第 40 行 | 仍报错 |
| 换函数库 | 依赖 | 测试输出 | 不支持该格式 |
| 自己实现 | 代码 | 截图 | 可行但慢 3 倍 |
```

有这张表（至少 3 行，英文表头 `| Path | Mechanism | Evidence | Result |` 也可以）就放行。

不会拦：
- 你自己说了「先放着 / 换方向 / 算了」；
- 选项表或编号选项里的「先放着」（例如 `| **11** | … | **a.** 开 PR **b.** 先放着 |`，这是把决定交给你，不是 Claude 自己放弃）；
- 行内代码、`>` 引用块、「」里引用你的原话；
- 否定句（「不是在说做不到」）、描述工具的事实（「旧版界面做不了批量导出，所以我加了按钮」）、「可以改回来」这类可逆说明。

仍然会拦：只给一个「选项」的假选项、状态表里写「走不通，先放着」、把整句退路话塞进长引号或代码块。

### 3. 管道退出码闸 `pre_pipe_exitcode.py`

**没装之前**：

```bash
npm test 2>&1 | tail -5 && git commit -m "fix" && git push
```

`&&` 看的是 `tail` 的退出码（几乎永远是 0），测试红了，commit 和 push 照样执行。`pytest | tail; echo $?` 同理，印出来的是 `tail` 的码。

**装了之后**：命令执行前被挡下，并给出三种改法：

```bash
npm test && git commit -m "fix"                                   # 去掉管道
set -o pipefail; npm test 2>&1 | tail -5 && git commit -m "fix"   # 开 pipefail
npm test 2>&1 | tail -5; [ ${PIPESTATUS[0]} -eq 0 ] && git commit -m "fix"
```

只在「末段是 tail/head/tee/sort/wc 这类显示用的过滤器」**而且**右边是 commit / push / deploy / rm / publish 这类有后果的动作时才拦。`| grep -q X && …`、`&& echo done`、引号里的文字、已开 `pipefail` 都放行。
想更严格：设 `CGH_PIPE_STRICT=1`，任何测试命令接 `| tail` / `| head` 都拦。

### 4. 推送前密钥扫描 `pre_push_secret_scan.py`

**没装之前**：Claude 把测试用的 key 写进 `config.py`，`git push`，key 公开在 GitHub 上。

**装了之后**：`git push` 被挡下：

```
密钥扫描闸：要 push 的 commit 里疑似有密钥，push 已挡下。
  - sk-... API key (OpenAI / Anthropic style): sk-A1b2A...
```

扫描范围：还没推上任何远端的 commit 的新增行（没有远端时扫最近 3 个 commit），以及新加入的 `.env`、`*.pem`、`id_rsa` 等文件。认得：`sk-` 类 API key、Stripe、AWS、GitHub、Google、Slack token、私钥块、JWT、Bearer token。只显示前 8 个字符，不会把整把 key 印出来。
支持 `cd 某目录 && git push`、`git -C 某目录 push`，以及 PowerShell 的 `Set-Location 某目录; git push`。

---

## 安装

### 第 1 步：放好文件夹

把整个 `claude-guard-hooks` 文件夹放到一个固定位置，例如：

- macOS / Linux：`~/.claude/claude-guard-hooks/`
- Windows：`C:\Users\<你的用户名>\.claude\claude-guard-hooks\`

### 第 2 步：生成设定片段

在文件夹里执行（会按你这台电脑的路径打印出片段，**不会改任何文件**）：

```bash
python3 install.py      # macOS / Linux
python install.py       # Windows
```

只要其中几个：`python3 install.py --only claim,retreat`。

### 第 3 步：贴进 settings.json

打开 `~/.claude/settings.json`（Windows 是 `C:\Users\<你>\.claude\settings.json`），把印出来的 `"hooks"` 合并进去。**已经有 `hooks` 的话，把条目加进对应的 `Stop` / `PreToolUse` 数组，不要整段覆盖。**

手写也可以，长这样：

macOS / Linux：

```json
{
  "hooks": {
    "Stop": [
      { "hooks": [
        { "type": "command", "command": "python3 ~/.claude/claude-guard-hooks/hooks/stop_claim_audit.py", "timeout": 10 },
        { "type": "command", "command": "python3 ~/.claude/claude-guard-hooks/hooks/stop_retreat_gate.py", "timeout": 10 }
      ] }
    ],
    "PreToolUse": [
      { "matcher": "Bash|PowerShell", "hooks": [
        { "type": "command", "command": "python3 ~/.claude/claude-guard-hooks/hooks/pre_pipe_exitcode.py", "timeout": 10 },
        { "type": "command", "command": "python3 ~/.claude/claude-guard-hooks/hooks/pre_push_secret_scan.py", "timeout": 30 }
      ] }
    ]
  }
}
```

Windows（路径用正斜线，有空格就加引号）：

```json
{
  "hooks": {
    "Stop": [
      { "hooks": [
        { "type": "command", "command": "python \"C:/Users/<你>/.claude/claude-guard-hooks/hooks/stop_claim_audit.py\"", "timeout": 10 },
        { "type": "command", "command": "python \"C:/Users/<你>/.claude/claude-guard-hooks/hooks/stop_retreat_gate.py\"", "timeout": 10 }
      ] }
    ],
    "PreToolUse": [
      { "matcher": "Bash|PowerShell", "hooks": [
        { "type": "command", "command": "python \"C:/Users/<你>/.claude/claude-guard-hooks/hooks/pre_pipe_exitcode.py\"", "timeout": 10 },
        { "type": "command", "command": "python \"C:/Users/<你>/.claude/claude-guard-hooks/hooks/pre_push_secret_scan.py\"", "timeout": 30 }
      ] }
    ]
  }
}
```

**为什么 matcher 是 `Bash|PowerShell`？** Windows 上 Claude Code 可能用 PowerShell 工具执行命令（没装 Git Bash 时根本没有 Bash 工具），只写 `Bash` 的话这两个 hook 永远不会被调用。v0.1.0 装过的人请把 `"matcher": "Bash"` 改成 `"Bash|PowerShell"`。

**python 还是 python3？** 在终端打 `python3 --version`，有版本号就用 `python3`；Windows 通常是 `python`（或 `py`）。版本要 3.8 以上。

### 第 4 步：确认能用

```bash
python3 tests/run_tests.py
```

最后一行显示 `PASS: N/N passed` 就对了。然后重开 Claude Code（或在 `/hooks` 里确认它们已载入）。

---

## 关掉或调整

设环境变量就行（可以写在 settings.json 的 `"env"` 里）：

| 变量 | 作用 |
|---|---|
| `CGH_DISABLE=1` | 全部关掉 |
| `CGH_DISABLE_CLAIM_AUDIT=1` | 只关声称对账闸 |
| `CGH_DISABLE_RETREAT_GATE=1` | 只关退路闸 |
| `CGH_DISABLE_PIPE_EXITCODE=1` | 只关管道退出码闸 |
| `CGH_DISABLE_SECRET_SCAN=1` | 只关密钥扫描 |
| `CGH_MAX_BLOCKS=2` | Stop hook 同一轮最多拦几次（防止卡死，默认 2） |
| `CGH_CLAIM_LOOKBACK=2` | 声称对账往回看几轮用户消息（默认 2：上一轮跑的、这一轮才报不算错） |
| `CGH_RETREAT_MIN_PATHS=3` | 已试路径表至少几行 |
| `CGH_PIPE_STRICT=1` | 测试命令接 tail/head 也拦 |
| `CGH_SECRET_ALLOW=<正则>` | 符合这个正则的匹配不算密钥（例如故意公开的 key） |
| `CGH_LOG_FILE=<路径>` | 每次拦截记一行（只记 hook 名和命中的词，不记回复内容），方便你调规则 |

想永久移除某个 hook：把它那一条从 settings.json 删掉。

---

## 限制（请先读）

- **这是启发式规则，不是安全产品。** 靠关键词和 transcript 判断，换个说法就能绕过，也可能误拦。它的用处是把最常见的「嘴上说做完了」挡下来，不是保证。
- 声称对账只检查「有没有调用过对应的工具」，**不检查工具输出是不是真的支持那句话**。跑了测试、测试是红的、还说「全绿」，它抓不到。
- 密钥扫描只认常见格式；自定义格式的密码、内网地址、个人资料都不会被认出来。它不能替代 `.gitignore`、密钥管理和 GitHub 的 secret scanning。已经推上去的密钥要当作外泄，立刻换新。
- 每个 hook 自己出错（读不到 transcript、git 不在、输入格式不对）一律**放行**，不会卡住你的 session；Stop hook 同一轮最多拦 2 次。
- 规则认中文（简体、繁体／台湾用语）和英文的说法，其他语言基本不会触发。繁体是逐字折回简体再比对，台湾特有的说法目前只收了「修正」「背景執行」「計畫」等少数几个，漏掉的欢迎回报。

---

## English

**Stop Claude Code from saying "done" when it isn't.** Four Claude Code hooks, Python 3.8+ stdlib only, cross-platform.

- `stop_claim_audit.py` (Stop): the reply claims "fixed / tests pass / pushed / dispatched a subagent / found the root cause" but no matching tool ran this turn. For "fixed / tests pass / done", verification must come *after* the last file edit.
- `stop_retreat_gate.py` (Stop): the reply says "can't be done / change direction / shelve it" without a `Tried paths:` table (`| Path | Mechanism | Evidence | Result |`, >= 3 rows). Option/question tables, blockquotes, inline code and quoted user words are ignored.
- `pre_pipe_exitcode.py` (PreToolUse, Bash and PowerShell): `npm test | tail -5 && git commit` — `&&` checks `tail`'s exit code, so failing tests still get committed. `CGH_PIPE_STRICT=1` also blocks any test command piped into tail/head.
- `pre_push_secret_scan.py` (PreToolUse, Bash and PowerShell): before `git push`, scans lines added by commits not yet on any remote, plus newly added `.env` / key files.

**Languages:** Chinese rules match both Simplified and Traditional (Taiwan) text, plus English.

**Windows:** the PreToolUse matcher is `Bash|PowerShell`, because Claude Code on Windows may run commands through its PowerShell tool (and registers no Bash tool without Git Bash). If you installed v0.1.0, change `"matcher": "Bash"` to `"Bash|PowerShell"`.

**Install:** copy the folder somewhere stable, run `python3 install.py` (Windows: `python install.py`) to print a settings.json snippet with this machine's paths (it writes nothing), merge it into `~/.claude/settings.json`, then run `python3 tests/run_tests.py`.

**Disable:** `CGH_DISABLE=1` (all) or `CGH_DISABLE_CLAIM_AUDIT` / `CGH_DISABLE_RETREAT_GATE` / `CGH_DISABLE_PIPE_EXITCODE` / `CGH_DISABLE_SECRET_SCAN` = `1`. See the table above for tuning variables.

**Limits:** keyword heuristics, easy to bypass, can misfire; not a security product. Every hook fails open on its own errors, and Stop hooks block at most `CGH_MAX_BLOCKS` (default 2) times per turn.

MIT License · Copyright (c) 2026 Verisance Corporation Sdn. Bhd.
