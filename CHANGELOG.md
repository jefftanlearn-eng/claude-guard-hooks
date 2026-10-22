# Changelog

## v0.1.0 — 2026-10-22

首个版本 / First release.

- `stop_claim_audit.py`：回复声称「已修复 / 测试通过 / 已推送 / 已派子代理 / 找到根因」时，核对本轮 transcript 有没有对应的工具调用；「修好 / 通过 / 完成」类要求验证发生在最后一次改档之后。
- `stop_retreat_gate.py`：回复说「做不到 / 改方向 / 先放着」却没有「已试路径」表时拦下。跳过选项表、引用块、行内代码、对用户原话的引用。
- `pre_pipe_exitcode.py`：`cmd | tail && git commit` 这类被管道吃掉退出码的命令；可选严格模式（测试命令接 tail/head）。
- `pre_push_secret_scan.py`：`git push` 前扫描还没推上远端的 commit 新增行和新增的 `.env` / 私钥档。
- 每个 hook 都有 `CGH_DISABLE_*` 开关，内部出错一律放行（fail open）；Stop hook 每轮最多拦 `CGH_MAX_BLOCKS` 次（默认 2）。
- `install.py` 打印本机可用的 settings.json 片段（不写档）。
- `tests/run_tests.py` + fixtures：每个 hook 都有应拦 / 应放的合成用例，另有「正则不含控制字符」检查。
