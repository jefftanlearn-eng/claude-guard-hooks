# Changelog

## v0.2.0 — 2026-10-23

- 繁体中文（台湾）：两个 Stop hook 先把回复逐字从繁体折回简体再比对（`_common.to_simp`，一对一不改长度，命中的原文照样显示），所以「已修復，測試全部通過」「先放著」「沒辦法」都会拦；豁免规则（未来式「接下來要…」、否定「還沒…」、问句「…嗎？」、「使用者說」引用、「選項」表头、「可以改回來」、「題 3 你還沒答」、「已試路徑」表）也同样认繁体，避免误拦。
- 补了几个台湾用语：「已修正」、「背景執行」（后台任务）、「計畫」（未来式）、「先擱著」、「使用者」（引用归属）。
- PowerShell：`install.py` 生成的 PreToolUse matcher 从 `Bash` 改为 `Bash|PowerShell`。Windows 上 Claude Code 用 PowerShell 工具执行命令时，`pre_pipe_exitcode` 和 `pre_push_secret_scan` 以前根本不会被调用。**v0.1.0 用户请手动改 settings.json 里的 matcher。**
- `pre_push_secret_scan` 认得 PowerShell 的 `Set-Location` / `Push-Location` / `sl` 进入目录；声称对账把后台执行的 PowerShell 也算作后台任务证据。
- 测试 86 → 139 条：每个繁体说法都有「会拦 / 不会拦」用例、PowerShell 用例、matcher 检查，以及「规则里每个汉字的台湾繁体都能折回简体」的覆盖检查（装了 opencc 才跑）。


## v0.1.0 — 2026-10-22

首个版本 / First release.

- `stop_claim_audit.py`：回复声称「已修复 / 测试通过 / 已推送 / 已派子代理 / 找到根因」时，核对本轮 transcript 有没有对应的工具调用；「修好 / 通过 / 完成」类要求验证发生在最后一次改档之后。
- `stop_retreat_gate.py`：回复说「做不到 / 改方向 / 先放着」却没有「已试路径」表时拦下。跳过选项表、引用块、行内代码、对用户原话的引用。
- `pre_pipe_exitcode.py`：`cmd | tail && git commit` 这类被管道吃掉退出码的命令；可选严格模式（测试命令接 tail/head）。
- `pre_push_secret_scan.py`：`git push` 前扫描还没推上远端的 commit 新增行和新增的 `.env` / 私钥档。
- 每个 hook 都有 `CGH_DISABLE_*` 开关，内部出错一律放行（fail open）；Stop hook 每轮最多拦 `CGH_MAX_BLOCKS` 次（默认 2）。
- `install.py` 打印本机可用的 settings.json 片段（不写档）。
- `tests/run_tests.py` + fixtures：每个 hook 都有应拦 / 应放的合成用例，另有「正则不含控制字符」检查。
