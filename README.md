# claude-guard-hooks

English · [简体中文](#简体中文)

**Make Claude Code show proof before it says "done".**

The "No shortcuts" hook pack (repo name: `claude-guard-hooks`) is a set of 4 Claude Code hooks. A hook is a small check that Claude Code runs automatically, either when Claude is about to finish a reply or just before it runs a command. If the check fails, Claude is stopped and gets a note telling it to go back and do the missing work.

- Python 3.8 or newer, standard library only. Works on Windows, macOS and Linux.
- Nothing extra to type. Use Claude Code the way you do now; the hooks only step in when Claude claims "fixed", "tests pass", "can't be done" or "pushed" without the evidence to back it up.
- Understands English, Simplified Chinese and Traditional Chinese (Taiwan) phrasing.
- Block messages come in one language, matching the conversation: English when Claude is writing English, Simplified Chinese when it is writing Chinese (see [Message language](#message-language)).

| Hook | When it runs | What it catches |
|---|---|---|
| `stop_claim_audit.py` | When Claude finishes a reply (`Stop`) | The reply says "fixed / tests pass / pushed / dispatched a subagent / found the root cause", but no matching tool was called in this turn |
| `stop_retreat_gate.py` | When Claude finishes a reply (`Stop`) | The reply says "can't be done / change direction / park it for now", but doesn't show which approaches it actually tried |
| `pre_pipe_exitcode.py` | Before a Bash or PowerShell command runs (`PreToolUse`) | Commands like `npm test \| tail -5 && git commit`, which commit even when the tests fail |
| `pre_push_secret_scan.py` | Before a Bash or PowerShell command runs (`PreToolUse`) | A `git push` whose outgoing commits contain an API key, a private key or a `.env` file |

---

## What each hook catches (before and after)

The examples below show the real text the hooks print when the conversation is in English. In a Chinese conversation the same message is printed in Chinese instead (see [Message language](#message-language)).

### 1. Claim check: `stop_claim_audit.py`

**Before:** Claude edits a file, then replies:

> Fixed the login bug. All 12 tests pass now.

It never ran the tests in this turn.

**After:** the reply is stopped and Claude receives:

```
Claim audit: your reply says some work is already done, but the transcript has no matching tool call.
- fixed / tests pass: you wrote "All 12 tests pass", but there was no Agent/Bash/PowerShell/Task call after your last file edit. To say something is fixed or tests pass, a check command must actually run after your last file edit.
Pick one: (1) do it now (call the tool, show the command and its output); (2) rewrite it as a plan ("next I will...", "not run yet").
```

Claude then usually runs the tests, or honestly says "changed it, haven't tested it yet".

Claims it checks (examples): "fixed", "tests pass", "all done", "I've finished", "found the root cause", "I've run / verified / tested", "I've committed / pushed", "pushed to origin", "I've dispatched a subagent", "is running in the background", "I've downloaded / read". Chinese phrasings it recognises (Simplified and Traditional): 已修复 / 测试通过 / 已推送 / 已修復.

For "fixed", "tests pass" and "done", **the check must come after the last file edit**. Running the tests, then changing the code, then saying "fixed" is still blocked.

Not blocked: future tense ("I will push", "next I'll run the tests"), questions ("do the tests pass?"), quoting the user, inline code, options in an options table, and results from a command that ran in the previous turn and is only being reported now.

### 2. Retreat gate: `stop_retreat_gate.py`

**Before:**

> This can't be done with the current PDF library, so let's shelve it for now.

(It only tried one approach.)

**After:** the reply is stopped and Claude receives:

```
Retreat gate: this reply says it can't be done / wants to change direction / shelve it (matched "can't be done"), but has no tried-paths table.
Changing route means changing the method, not the goal; only the user can change the goal. First:
1) come up with >= 3 approaches that work differently (rewording a prompt does not count as a new one);
2) actually try each one in a temp directory or a separate branch (nothing in production, nothing that costs money);
3) put a marker line "Tried paths:" in the reply, then the header | Path | Mechanism | Evidence | Result |, one row per approach;
4) if every one still fails, attach the evidence and hand "change the goal" to the user as an option to decide.
```

A reply that includes a table like this is let through:

```
Tried paths:
| Path | Mechanism | Evidence | Result |
|---|---|---|---|
| Tweak the settings | Config | build.log line 40 | Still fails |
| Switch library | Dependency | Test output | Format not supported |
| Write it ourselves | Code | Screenshot | Works, but 3x slower |
```

The table needs at least 3 rows.

Phrases it reacts to include "can't be done", "not possible", "give up on", "let's park / shelve / drop it". Chinese phrasings it recognises (plus the Chinese table marker 已试路径): 做不到 / 换个方向 / 先放着.

Not blocked:
- You yourself asked to stop, change direction or park it (for example "give up", "change approach", "skip it", "shelve");
- "Park it" offered as one of several numbered options or in an options table. That hands the decision to you; it isn't Claude giving up;
- Inline code, `>` blockquotes, and your own words quoted back;
- Negations ("I'm not saying it can't be done"), facts about a tool ("the old UI can't export in bulk, so I added a button"), and notes that a change is reversible ("we can switch it back").

Still blocked: a fake "options" list with only one option, a status table that says "doesn't work, parked", and a whole retreat sentence hidden inside a long quote or code block.

### 3. Pipe exit-code gate: `pre_pipe_exitcode.py`

**Before:**

```bash
npm test 2>&1 | tail -5 && git commit -m "fix" && git push
```

`&&` looks at the exit code of the last command in the pipe, which is `tail`, and `tail` almost always succeeds. So the tests fail, and the commit and push happen anyway. The same goes for `pytest | tail; echo $?`, which prints `tail`'s exit code, not pytest's.

**After:** the command is blocked before it runs, and Claude sees:

```
Pipe exit-code gate: the && / $? in this command does not guard what it looks like it guards.
The pipe ends in `tail`, so `&&` sees the exit code of `tail` (almost always 0). If the command before it failed, `git commit` runs anyway.
Fix (pick one):
  1. Drop the pipe:          npm test && git commit ...
  2. Enable pipefail:        set -o pipefail; npm test 2>&1 | tail -5 && git commit ...
  3. Check the real code:    npm test 2>&1 | tail -5; [ ${PIPESTATUS[0]} -eq 0 ] && git commit ...
```

Claude then retries with one of the three fixes.

It only blocks when the last stage of the pipe is a display filter (such as `tail`, `head`, `tee`, `sort`, `wc`) **and** what follows is an action with consequences (such as `git commit`, `git push`, a deploy, `rm -…`, or a publish). These are allowed: `| grep -q X && …`, `&& echo done`, text inside quotes, and commands that already use `pipefail` or `PIPESTATUS`.

Want it stricter? Set `CGH_PIPE_STRICT=1` and any test command piped into `tail` / `head` is blocked, whatever follows it.

### 4. Secret scan before push: `pre_push_secret_scan.py`

**Before:** Claude puts a test API key into `config.py` and runs `git push`. The key is now public on GitHub, where anyone can use it, and you get the bill.

**After:** the `git push` is blocked:

```
Secret scan gate: the commits about to be pushed look like they contain secrets, so the push was blocked.
  - sk-... API key (OpenAI / Anthropic style): sk-A1b2A...
What to do: move the secret into .env (and add .env to .gitignore), rewrite that commit, then push again. Treat any secret that was already pushed as leaked and rotate it in the provider's dashboard right away.
Sure it is a false positive (for example a key that is public on purpose)? Set CGH_SECRET_ALLOW=<regex> to let it through, or push it yourself by hand.
```

What it scans: lines added by commits that aren't on any remote yet (if the repo has no remote, the last 3 commits), plus newly added files such as `.env`, `*.pem` or `id_rsa`. It recognizes `sk-` style API keys (OpenAI / Anthropic), Stripe, AWS, GitHub, Google and Slack tokens, private key blocks, JWTs, and hex Bearer tokens. It only shows the first 8 characters of a match, never the whole key.

It understands `cd some/dir && git push`, `git -C some/dir push`, and PowerShell's `Set-Location some\dir; git push`.

### Message language

Each block message is printed in **one** language, English or Simplified Chinese, following the conversation:

- The Stop hooks look at the reply being checked. The PreToolUse hooks (pipe gate, secret scan) look at Claude's most recent text in the transcript.
- Code blocks, inline code, URLs, corner-bracket quotes, file names, paths and snake_case identifiers are ignored. Then the message is in Chinese if CJK characters make up at least 30% of (CJK characters + English words); otherwise it is in English. An English reply that quotes one Chinese word stays English.
- If Claude ran a command without writing anything first, the hook goes by your latest message instead. No text at all (or the transcript can't be read) means English.
- `CGH_LANG=en` or `CGH_LANG=zh` forces one language (see [the settings table](#turn-off-tune-or-uninstall)).

What gets blocked does not depend on the language; only the wording of the message changes.

---

## Install

You need Python 3.8 or newer and Claude Code. Check Python with `python3 --version` (Windows: `python --version`).

### Step 1: Put the folder somewhere permanent

Your settings will store the folder's exact location, so pick a place and don't move it later. For example:

- macOS / Linux: `~/.claude/claude-guard-hooks/`
- Windows: `C:\Users\<YourName>\.claude\claude-guard-hooks\`

### Step 2: Print the settings for your computer

Run this inside the folder. It prints a settings snippet with this computer's paths. **It does not change any file.**

```bash
python3 install.py      # macOS / Linux
python install.py       # Windows
```

The output is one line of instructions, then a JSON block, then the test command to run (JSON shortened here; `<your python>` and `<folder>` are full paths on your computer):

```
Merge this "hooks" block into ~/.claude/settings.json (if you already have hooks, append these entries to the matching arrays; do not overwrite the whole block).

{
  "hooks": {
    "Stop": [ ... ],
    "PreToolUse": [ ... ]
  }
}

Then run the tests to check:  <your python> <folder>/tests/run_tests.py
```

The two text lines are in English, or in Chinese if `CGH_LANG=zh` is set or your system locale is Chinese. The JSON is the same either way.

Each `"command"` line contains the full path of the Python you ran it with, plus the full path of the hook file. Paths with spaces are wrapped in `\"…\"`. That's expected; keep them.

Options:

- Only some hooks: `python3 install.py --only claim,retreat` (names: `claim`, `retreat`, `pipe`, `secret`). A wrong name prints `unknown hook: …` and nothing else.
- A different interpreter: `python3 install.py --python python3`. By default it uses the Python that ran `install.py`.

### Step 3: Add it to settings.json

Open `~/.claude/settings.json` (Windows: `C:\Users\<YourName>\.claude\settings.json`) and merge in the printed `"hooks"` block. Back the file up first. **If the file already has `"hooks"`, add the new entries to the existing `Stop` / `PreToolUse` arrays. Don't replace the whole block.**

You can also write it by hand. It looks like this:

macOS / Linux:

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

Windows (use forward slashes, and quotes if the path has spaces):

```json
{
  "hooks": {
    "Stop": [
      { "hooks": [
        { "type": "command", "command": "python \"C:/Users/<YourName>/.claude/claude-guard-hooks/hooks/stop_claim_audit.py\"", "timeout": 10 },
        { "type": "command", "command": "python \"C:/Users/<YourName>/.claude/claude-guard-hooks/hooks/stop_retreat_gate.py\"", "timeout": 10 }
      ] }
    ],
    "PreToolUse": [
      { "matcher": "Bash|PowerShell", "hooks": [
        { "type": "command", "command": "python \"C:/Users/<YourName>/.claude/claude-guard-hooks/hooks/pre_pipe_exitcode.py\"", "timeout": 10 },
        { "type": "command", "command": "python \"C:/Users/<YourName>/.claude/claude-guard-hooks/hooks/pre_push_secret_scan.py\"", "timeout": 30 }
      ] }
    ]
  }
}
```

After saving, check the JSON is still valid: `python3 -m json.tool ~/.claude/settings.json` (Windows: `python -m json.tool "$HOME\.claude\settings.json"`). It should print your settings back with no error.

**Why is the matcher `Bash|PowerShell`?** On Windows, Claude Code may run commands through its PowerShell tool (without Git Bash installed, there is no Bash tool at all). With only `Bash`, these two hooks would never be called there.

**`python` or `python3`?** Run `python3 --version` in a terminal. If it prints a version, use `python3`. On Windows it is usually `python` (or `py`). The version must be 3.8 or newer.

### Step 4: Run the built-in tests

```bash
python3 tests/run_tests.py      # Windows: python tests\run_tests.py
```

You'll see a long list of lines starting with `ok` (many test names are in Chinese). The last line should be `PASS: N/N passed` with both numbers equal (`PASS: 187/187 passed` in v0.3.0). Two lines may say a group was skipped, and neither is an error:

- `(opencc not installed - skipped)`: a check for developers only.
- `git not installed - skipped`: git isn't installed, so the secret-scan tests are skipped.

If the last line starts with `FAIL`, stop here and check your Python version and that you have the whole folder.

### Step 5: Restart Claude Code

Close every running Claude Code session and start it again. Inside Claude Code, type `/hooks` and check that `Stop` and `PreToolUse` list the `claude-guard-hooks` entries.

---

## Check that it really works

Two safe tests. Neither touches your projects.

**Test A: the pipe gate (most reliable, do this first).** Make an empty folder, `cd` into it, start `claude`, and send:

```
This is a hook test. Please run this command exactly as written, without changing anything:
echo hello | tail -1 && git commit -m test
```

Expected: the command does not run. You see `PreToolUse:Bash hook error` (or `PreToolUse:PowerShell` on Windows, which also counts), followed by a message starting with `Pipe exit-code gate:` (shown in full in section 3 above). If you see `hello` or a git error instead, the command ran and the hook isn't loaded; check `/hooks`.

**Test B: the claim check.** Start a new conversation (`/clear`), then send:

```
This is a hook test. Don't use any tools, and don't put quotes around it. Reply with only this sentence:
All tests pass.
```

Expected: after Claude replies, a Stop hook message appears starting with `Claim audit: your reply says some work is already done, but the transcript has no matching tool call.` (shown in full in section 1 above). If Claude puts the sentence in quotes or a code block, it isn't checked (it's treated as quoting). If Claude ran any command earlier in the conversation, that counts as evidence, which is why you start with `/clear`.

---

## Upgrading

v0.3.0 prints each block message in one language that follows the conversation (see [Message language](#message-language)) instead of Chinese plus an `EN:` line. What gets blocked did not change.

**From v0.2:** close Claude Code, replace the old `claude-guard-hooks` folder with the new one **in the same location**, run the tests (step 4) and restart Claude Code. No settings change is needed: the settings `install.py` prints are the same as in v0.2.

**From v0.1:** v0.2 added Traditional Chinese (Taiwan) phrasing and made the pipe gate and the secret scan also cover commands Claude runs through PowerShell. v0.1 only watched Bash, so on Windows those two hooks often never ran.

1. Close Claude Code, then replace the old `claude-guard-hooks` folder with the new one **in the same location**. The paths in your settings stay valid.
2. In `settings.json`, under `"PreToolUse"`, change the matcher above the `claude-guard-hooks` entries from `"matcher": "Bash"` to `"matcher": "Bash|PowerShell"`. If your two entries sit in two separate groups, change both. Don't touch hooks that belong to other tools.
3. Check the JSON, run the tests (step 4), restart Claude Code and confirm with `/hooks`.

---

## Turn off, tune, or uninstall

Set environment variables. You can put them in the `"env"` section of `settings.json`, for example `"env": { "CGH_DISABLE_RETREAT_GATE": "1" }`. Restart Claude Code after changing it.

| Variable | What it does |
|---|---|
| `CGH_DISABLE=1` | Turns off all four hooks |
| `CGH_DISABLE_CLAIM_AUDIT=1` | Turns off the claim check only |
| `CGH_DISABLE_RETREAT_GATE=1` | Turns off the retreat gate only |
| `CGH_DISABLE_PIPE_EXITCODE=1` | Turns off the pipe exit-code gate only |
| `CGH_DISABLE_SECRET_SCAN=1` | Turns off the secret scan only |
| `CGH_MAX_BLOCKS=2` | How many times a Stop hook may block in the same turn (default 2, so you never get stuck) |
| `CGH_CLAIM_LOOKBACK=2` | How many user turns the claim check looks back (default 2: something run last turn and reported this turn is fine) |
| `CGH_RETREAT_MIN_PATHS=3` | Minimum number of rows in the tried-paths table |
| `CGH_PIPE_STRICT=1` | Also block any test command piped into tail/head |
| `CGH_SECRET_ALLOW=<regex>` | Matches of this regular expression don't count as secrets (for example a key you publish on purpose) |
| `CGH_LOG_FILE=<path>` | Writes one line per block (only the hook name and the matched phrase, never the reply text), to help you tune the rules |
| `CGH_LANG=en` / `CGH_LANG=zh` | Forces the language of block messages and of `install.py`'s text (default: follow the conversation; `install.py` follows the system locale) |

**To uninstall, the order matters:** first delete the `claude-guard-hooks` entries from `settings.json` (and any `Stop` / `PreToolUse` array left empty), restart Claude Code and confirm with `/hooks` that they're gone, and only then delete the folder. If you delete the folder first, the settings point to a file that no longer exists. Python then exits with an error code that Claude Code treats as "blocked", so no command can run and Claude can't finish its replies. The same happens if you move the folder; move it back, or redo steps 2 and 3.

---

## Limits (please read)

- **These are heuristics, not a security product.** They work by spotting phrases and reading the transcript. Different wording gets around them, and now and then they block something by mistake. Their job is to catch the most common "says it's done but isn't" moments, not to guarantee anything.
- The claim check only checks **whether a matching tool was called**, not whether its output supports the claim. If Claude runs the tests, they fail, and it still says "all green", this won't catch it.
- The secret scan only knows common key formats. Custom passwords, internal addresses and personal data are not recognized. It does not replace `.gitignore`, a secrets manager, or GitHub's secret scanning. Treat any key that has already been pushed as leaked and rotate it right away.
- If a hook hits its own error (can't read the transcript, git isn't installed, unexpected input), it **lets things through** and never blocks your session. Stop hooks block at most 2 times per turn.
- The rules understand Chinese (Simplified and Traditional / Taiwan) and English. Other languages will almost never trigger them. Traditional text is folded character by character into Simplified before matching; only a few Taiwan-specific phrases are covered so far. Reports of missed phrases are welcome. Messages are printed in English or Simplified Chinese only.
- Everything runs on your own computer. The hooks contain no network code and upload nothing; the secret scan only runs local `git` commands.

MIT License · Copyright (c) 2026 Verisance Corporation Sdn. Bhd.

---

## 简体中文

[English](#claude-guard-hooks) · 简体中文

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

> 登录的 bug 已修复，测试全部通过。

可是这一轮它根本没跑测试。

**装了之后**：回复被拦下，Claude 收到：

```
声称对账闸：回复里有「已经做了」的说法，但 transcript 里找不到对应的工具调用。
- 修好 / 测试通过：你写了「已修复」，但最后一次改档之后没有调用过 Agent/Bash/PowerShell/Task。要宣布修好 / 测试通过，最后一次改档之后必须真的跑过验证命令。
二选一：① 现在真的去做（调用工具，贴出命令和输出）；② 把话改成未来式（「接下来要…」「还没跑」）。
```

（本节的例子都是对话用中文时的输出；对话用英文时，同样的讯息会印成英文，见[讯息语言](#讯息语言)。）

然后它会去真的跑测试，或者老实改成「改好了，还没跑测试」。

检查的说法（简体、繁体、英文都认，例如「已修復，測試全部通過」「正在背景執行」也会拦）：已修复 / 修好了 / 测试通过 / 全绿 / tests pass、已完成 / 做完了、找到根因、跑过 / 验证过、已提交 / 已推送、已派子代理、正在后台跑、已下载 / 看过。
「修好 / 测试通过 / 完成」这几类，**验证必须发生在最后一次改档之后**：先跑测试、再改代码、再说「修好了」，一样会被拦。

不会拦：未来式（「接下来要跑」「I will push」）、问句（「测试通过了吗？」）、引用用户的话、行内代码、选项表里的选项、上一轮跑过这一轮才报告的结果。

### 2. 退路闸 `stop_retreat_gate.py`

**没装之前**：

> 现在这个 PDF 库我做不到，先放着吧。

（其实只试了一种方法。）

**装了之后**：被拦下，Claude 收到：

```
退路闸：这条回复宣布做不到 / 要改方向 / 先搁置（命中「做不到」），但没有附上「已试路径」表。
换路只换手段，不换目标；目标只有用户能改。请先：
1) 想出 >= 3 条机制不同的做法（只改提示词措辞不算新的一条）；
2) 在临时目录或独立分支里真的试过每一条（不碰生产、不花钱）；
3) 回复里放标记行「已试路径：」+ 表头 | 路 | 机制 | 证据 | 结果 |，每条一行；
4) 都试过仍不行，再附上证据，把「换目标」作为选项交给用户决定。
```

回复里附上这样的表就放行：

```
已试路径：
| 路 | 机制 | 证据 | 结果 |
|---|---|---|---|
| 调参数 | 配置 | build.log 第 40 行 | 仍报错 |
| 换函数库 | 依赖 | 测试输出 | 不支持该格式 |
| 自己实现 | 代码 | 截图 | 可行但慢 3 倍 |
```

表至少要 3 行；英文标记 `Tried paths:` 和表头 `| Path | Mechanism | Evidence | Result |` 也可以。

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

**装了之后**：命令执行前被挡下，Claude 看到：

```
管道退出码闸：这条命令的 && / $? 守不住它看起来守着的东西。
管道以 `tail` 结尾，`&&` 看的是 `tail` 的退出码（几乎总是 0）。前面失败了，`git commit` 照样执行。
改法（任选一种）：
  1. 去掉管道：      npm test && git commit ...
  2. 开 pipefail：   set -o pipefail; npm test 2>&1 | tail -5 && git commit ...
  3. 取前段退出码：  npm test 2>&1 | tail -5; [ ${PIPESTATUS[0]} -eq 0 ] && git commit ...
```

然后它会换成三种改法之一重跑。

只在「末段是 tail/head/tee/sort/wc 这类显示用的过滤器」**而且**右边是 commit / push / deploy / rm / publish 这类有后果的动作时才拦。`| grep -q X && …`、`&& echo done`、引号里的文字、已开 `pipefail` 都放行。
想更严格：设 `CGH_PIPE_STRICT=1`，任何测试命令接 `| tail` / `| head` 都拦。

### 4. 推送前密钥扫描 `pre_push_secret_scan.py`

**没装之前**：Claude 把测试用的 key 写进 `config.py`，`git push`，key 公开在 GitHub 上。

**装了之后**：`git push` 被挡下：

```
密钥扫描闸：要 push 的 commit 里疑似有密钥，push 已挡下。
  - sk-... API key (OpenAI / Anthropic style): sk-A1b2A...
处理：把密钥移到 .env（并加进 .gitignore），改写掉那个 commit 后再 push；已经推上去过的密钥要当作外泄，立刻去后台换新。
确认是误报（例如故意公开的 key）：设 CGH_SECRET_ALLOW=<正则> 放行，或由你自己手动 push。
```

扫描范围：还没推上任何远端的 commit 的新增行（没有远端时扫最近 3 个 commit），以及新加入的 `.env`、`*.pem`、`id_rsa` 等文件。认得：`sk-` 类 API key、Stripe、AWS、GitHub、Google、Slack token、私钥块、JWT、Bearer token。只显示前 8 个字符，不会把整把 key 印出来。
支持 `cd 某目录 && git push`、`git -C 某目录 push`，以及 PowerShell 的 `Set-Location 某目录; git push`。

### 讯息语言

每则拦截讯息只印**一种**语言（英文或简体中文），跟着对话走：

- Stop hook 看被检查的那段回复；PreToolUse hook（管道闸、密钥扫描）看 transcript 里 Claude 最近说的话。
- 先去掉代码块、行内代码、网址、「」引号里的字、文件名、路径和 snake_case 名称，再数：汉字数 ≥（汉字数 + 英文词数）的 30% 就用中文，否则用英文。所以夹满英文工具名的中文回复仍是中文。
- Claude 没说话就直接跑命令时，改看你最后一句话的语言；完全没有文字可判断（或读不到 transcript）才用英文。
- 设 `CGH_LANG=zh` 或 `CGH_LANG=en` 可以强制指定（见下方[关掉或调整](#关掉或调整)）。

拦什么跟语言无关，只有讯息的文字不同。

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

输出是一行说明、一段 JSON、最后是测试命令（这里把 JSON 缩短了；`<你的 python>` 和 `<文件夹>` 会是你电脑上的完整路径）：

```
把下面的 "hooks" 合并进 ~/.claude/settings.json（已有 hooks 的话，把条目加进对应数组，别整段覆盖）。

{
  "hooks": {
    "Stop": [ ... ],
    "PreToolUse": [ ... ]
  }
}

装好后先跑测试：  <你的 python> <文件夹>/tests/run_tests.py
```

系统语言是中文、或设了 `CGH_LANG=zh` 时，说明文字是中文；否则是英文。JSON 两种情况完全一样。

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

最后一行显示 `PASS: N/N passed`（两个数字相同，v0.3.0 是 `PASS: 187/187 passed`）就对了。然后重开 Claude Code（或在 `/hooks` 里确认它们已载入）。

---

## 升级

v0.3.0：每则拦截讯息只印一种语言，跟着对话走（见[讯息语言](#讯息语言)），不再是中文加一行 `EN:`。拦什么没变。

**从 v0.2 升级**：关掉 Claude Code，用新文件夹**原地**替换旧的 `claude-guard-hooks` 文件夹，跑一次测试（第 4 步），再重开 Claude Code。settings.json 不用改：`install.py` 印出来的设定跟 v0.2 一样。

**从 v0.1 升级**：同样原地替换文件夹，再把 settings.json 里 `"PreToolUse"` 下 `claude-guard-hooks` 那组的 `"matcher": "Bash"` 改成 `"Bash|PowerShell"`（两条分在两组的话两组都改，别动其他工具的 hook），检查 JSON、跑测试、重开 Claude Code，用 `/hooks` 确认。

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
| `CGH_LANG=zh` / `CGH_LANG=en` | 强制拦截讯息和 `install.py` 说明文字的语言（默认：讯息跟着对话，`install.py` 跟着系统语言） |

想永久移除某个 hook：把它那一条从 settings.json 删掉。

---

## 限制（请先读）

- **这是启发式规则，不是安全产品。** 靠关键词和 transcript 判断，换个说法就能绕过，也可能误拦。它的用处是把最常见的「嘴上说做完了」挡下来，不是保证。
- 声称对账只检查「有没有调用过对应的工具」，**不检查工具输出是不是真的支持那句话**。跑了测试、测试是红的、还说「全绿」，它抓不到。
- 密钥扫描只认常见格式；自定义格式的密码、内网地址、个人资料都不会被认出来。它不能替代 `.gitignore`、密钥管理和 GitHub 的 secret scanning。已经推上去的密钥要当作外泄，立刻换新。
- 每个 hook 自己出错（读不到 transcript、git 不在、输入格式不对）一律**放行**，不会卡住你的 session；Stop hook 同一轮最多拦 2 次。
- 规则认中文（简体、繁体／台湾用语）和英文的说法，其他语言基本不会触发。繁体是逐字折回简体再比对，台湾特有的说法目前只收了「修正」「背景執行」「計畫」等少数几个，漏掉的欢迎回报。

---

MIT License · Copyright (c) 2026 Verisance Corporation Sdn. Bhd.
