#!/usr/bin/env python3
"""Stop hook: claim audit.

If the reply says something is already done ("fixed", "tests pass", "pushed",
"dispatched a subagent", "found the root cause"...) but this turn's transcript has
no matching tool call, block the stop. Claude must either do it now or rewrite the
sentence as a plan ("I will...", "not yet run").

For "fixed / tests pass / done" the evidence must come AFTER the last file edit:
editing code and then saying "fixed" without running anything is exactly the miss.

Only checks that a matching tool was called, not that its output supports the claim.
Disable: CGH_DISABLE_CLAIM_AUDIT=1   Tune: CGH_CLAIM_LOOKBACK (user turns, default 2)
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

NAME = "CLAIM_AUDIT"
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
RUN = {"Bash", "Agent", "Task", "PowerShell"}
LOOK = RUN | {"Read", "Grep", "Glob", "WebFetch", "mcp__"}

# (label {zh,en}, pattern, tools that count as evidence, evidence must follow the last edit?, tip {zh,en})
# The pattern stays at index 1: tests/run_tests.py reads it there for the Traditional coverage check.
CLAIMS = [
    ({"zh": "修好 / 测试通过", "en": "fixed / tests pass"}, re.compile(
        r"已(?:经)?(?:修复|修好|修正|解决)|修好了|修复(?:完成|好了)|bug\s*(?:已|已经)?(?:修|解决)(?:好|掉)?了"
        r"|测试(?:已|全部|都)?(?:通过|过了|全绿)|全绿|(?:all\s+)?(?:\d+\s+)?tests?\s+(?:now\s+)?pass(?:ed|es|ing)?\b"
        r"|\b(?:is|are|now)\s+(?:now\s+)?fixed\b|\bI(?:'ve| have)\s+fixed\b", re.I),
     RUN, True, {"zh": "要宣布修好 / 测试通过，最后一次改档之后必须真的跑过验证命令。",
                 "en": "To say something is fixed or tests pass, a check command must actually run after your last file edit."}),
    ({"zh": "完成", "en": "done"}, re.compile(
        r"已(?:经)?(?:全部)?完成|做完了|搞定了|全部完成|大功告成"
        r"|\bI(?:'ve| have)\s+(?:finished|completed)\b|\ball\s+done\b|\bdone\s+and\s+verified\b", re.I),
     LOOK, True, {"zh": "说做完了，改完之后至少要有一次检查（跑命令 / 读回结果）。",
                  "en": "To say it is done, there must be at least one check after the last edit (run a command or read the result back)."}),
    ({"zh": "找到根因", "en": "root cause"}, re.compile(
        r"找到(?:了)?(?:根因|病因|真因|根本原因)|根因(?:就)?是|root\s+cause\s+(?:is|was|found)|found\s+the\s+root\s+cause", re.I),
     LOOK, False, {"zh": "下根因结论前，本轮至少要真的看过输入（读档 / 跑命令 / 查日志）。",
                   "en": "Before naming a root cause, actually look at the input this turn (read the file, run a command, check the logs)."}),
    ({"zh": "跑过 / 验证过", "en": "ran / verified"}, re.compile(
        r"(?:已|我)(?:经)?\s*(?:跑过|跑了|跑完|执行过|验证过|实测过|测过|核过)"
        r"|(?:跑|验证|实测)\s*(?:过了|完了)|\bI(?:'ve| have)\s+(?:run|ran|verified|tested)\b|\bI\s+ran\b", re.I),
     RUN, False, {"zh": "说跑过 / 验证过，本轮就要有真的执行记录。",
                  "en": "To say you ran or verified something, this turn must contain a real execution."}),
    ({"zh": "commit / push", "en": "commit / push"}, re.compile(
        r"已(?:经)?\s*(?:commit|提交|push|推送|推上去|推到|推了)|(?:commit|push|推送)\s*(?:好|完)了"
        r"|\bI(?:'ve| have)\s+(?:committed|pushed)\b|\bpushed\s+to\s+(?:origin|remote|github)\b", re.I),
     {"Bash", "PowerShell"}, False, {"zh": "git 操作只能经由命令执行；没跑就别说已提交 / 已推送。",
                                     "en": "git only happens through a shell command; if none ran, do not say it was committed or pushed."}),
    ({"zh": "派子代理", "en": "subagent"}, re.compile(
        r"(?:已|我)已?经?(?:派|开|起)了?(?:一?个|几个|两个|三个)?\s*(?:后台)?\s*(?:sub-?agent|agent|子代理)"
        r"|派了(?:一?个|几个|两个|三个)?\s*(?:后台)?\s*(?:sub-?agent|agent|子代理)"
        r"|\bI(?:'ve| have)\s+(?:dispatched|launched|spawned|started)\s+(?:a|an|the|\d+)?\s*(?:sub-?)?agents?\b", re.I),
     {"Agent", "Task"}, False, {"zh": "要么真的调用 Agent 派出去，要么改成「接下来要派…」。",
                                "en": "Either actually call the Agent tool, or rewrite it as \"next I will dispatch...\"."}),
    ({"zh": "后台任务", "en": "background task"}, re.compile(
        r"(?:已经?|正在)\s*(?:放|丢|挂)?(?:到|在)?(?:后台|背景)(?:跑|运行|执行|处理)|(?:后台|背景)(?:已经?|正在)\s*(?:跑|查|抓|做|运行|执行)"
        r"|\b(?:is|are|now)\s+running\s+in\s+the\s+background\b", re.I),
     {"Agent", "Task", "Bash:background"}, False, {"zh": "后台任务要有 Agent 或后台 Bash 的记录；没有就改成未来式。",
                                                   "en": "A background task needs an Agent call or a background Bash run on record; without one, use the future tense."}),
    ({"zh": "下载 / 读过", "en": "downloaded / read"}, re.compile(
        r"已(?:经)?\s*(?:下载|抓取|抓下来|拉下来)|(?:下载|抓取)\s*(?:好|完)了|(?:已|我)\s*(?:亲眼)?\s*(?:读过|看过|打开看了|逐张看)"
        r"|\bI(?:'ve| have)\s+(?:downloaded|read|opened|looked\s+at)\b", re.I),
     LOOK | {"fetch", "browser", "playwright", "chrome"}, False, {"zh": "说下载 / 看过，本轮要有对应的读取工具记录。",
                                                                 "en": "To say you downloaded or read something, this turn needs a matching read/fetch tool call."}),
]

FUTURE = re.compile(
    r"(?:接下来|等下|稍后|待会|马上|现在就|打算|准备|将要|将会|要不要|该不该|需要|应该|想|计划|计画|如果|等)\s*(?:要|会|去|得|该)?\s*$"
    r"|(?:要|会|去|得|该|能|可以|才算|才能)\s*$|(?:没有?|未|尚未|还没|不曾|不能|别)\s*(?:有)?\s*$"
    r"|\b(?:will|'ll|going\s+to|need\s+to|should|must|can|could|to|not|haven'?t|hasn'?t|once|after|before|if|until|when)\s+(?:be\s+)?$",
    re.I,
)
QUESTION_AFTER = re.compile(r"^[^。.\n!！]{0,8}(?:吗|么|\?|？)")
WINDOW = 14


def tool_match(name, wanted):
    low = name.lower()
    return any(low == w.lower() or w.lower() in low for w in wanted)


def has_evidence(calls, wanted, after_edit):
    start = 0
    if after_edit:
        for i, n in enumerate(calls):
            if n in EDIT_TOOLS:
                start = i + 1
    return any(tool_match(n, wanted) for n in calls[start:])


def audit(reply, calls):
    raw = c.neutralize(reply)
    text = c.to_simp(raw)  # Traditional -> Simplified, 1:1 per char, so positions stay aligned with raw
    misses = []
    for label, pat, wanted, after_edit, tip in CLAIMS:
        if has_evidence(calls, wanted, after_edit):
            continue
        for m in pat.finditer(text):
            if FUTURE.search(text[max(0, m.start() - WINDOW):m.start()]):
                continue
            if QUESTION_AFTER.search(text[m.end():m.end() + 12]):
                continue
            misses.append((label, raw[m.start():m.end()].strip(), wanted, after_edit, tip))
            break
    return misses


def block_message(misses, lang):
    lines = []
    for label, hit, wanted, after_edit, tip in misses:
        tools = "/".join(sorted(w for w in wanted if w != "mcp__"))
        if lang == "zh":
            where = "最后一次改档之后" if after_edit else "本轮"
            lines.append("- {}：你写了「{}」，但{}没有调用过 {}。{}".format(label["zh"], hit, where, tools, tip["zh"]))
        else:
            where = "after your last file edit" if after_edit else "this turn"
            lines.append("- {}: you wrote \"{}\", but there was no {} call {}. {}".format(
                label["en"], hit, tools, where, tip["en"]))
    if lang == "zh":
        return ("声称对账闸：回复里有「已经做了」的说法，但 transcript 里找不到对应的工具调用。\n"
                + "\n".join(lines)
                + "\n二选一：① 现在真的去做（调用工具，贴出命令和输出）；② 把话改成未来式（「接下来要…」「还没跑」）。")
    return ("Claim audit: your reply says some work is already done, but the transcript has no matching tool call.\n"
            + "\n".join(lines)
            + "\nPick one: (1) do it now (call the tool, show the command and its output); "
            "(2) rewrite it as a plan (\"next I will...\", \"not run yet\").")


def main():
    c.setup_utf8()
    if c.disabled(NAME):
        return 0
    data = c.read_input()
    if not data:
        return 0
    rows = c.load_rows(data.get("transcript_path"))
    if rows is None:
        return 0  # no transcript, no judgement
    reply = c.last_assistant_text(data, rows)
    if not reply:
        return 0
    calls = c.turn_tool_calls(rows, c.env_int("CLAIM_LOOKBACK", 2))
    misses = audit(reply, calls)
    if not misses or not c.allow_block("claim", data):
        return 0
    c.log_event("claim_audit", misses[0][1])
    lang = c.pick_lang(reply)
    print(json.dumps({"decision": "block", "reason": block_message(misses, lang)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    c.run_safely(main)
