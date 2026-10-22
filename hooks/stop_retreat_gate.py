#!/usr/bin/env python3
"""Stop hook: retreat gate.

If the reply announces "can't be done / let's change direction / shelve it" but contains
no tried-paths table, block the stop and ask Claude to actually try >= 3 different
approaches first (or show the table of what it tried).

Skips text that is not Claude retreating: code, quoted user words, blockquotes,
option/question tables, negated mentions ("not saying it can't be done"), and turns
where the user themselves asked to stop or change direction.

Disable: CGH_DISABLE_RETREAT_GATE=1   Tune: CGH_RETREAT_MIN_PATHS (default 3)
Heuristic only: rephrasing gets around it. It is a nudge, not an enforcer.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

NAME = "RETREAT_GATE"

BAD = re.compile(
    r"做不到|做不了|办不到|没办法|无法(?:实现|做到|解决|完成|处理)"
    r"|就是这样|本来就这样|天生(?:就)?这样"
    r"|建议改回|改回|改用别的|换个方向|换一个方向|换条路|换个(?:思路|做法|方案)(?:吧|好了)"
    r"|先放着|先搁置|先跳过|暂时放弃|放弃(?:这条|这个|它)|这条路不通|走不通|行不通"
    r"|要不(?:然)?就(?:不做|算了|换)|不如(?:换|改|放弃)"
    r"|can'?t be done|cannot be done|not possible|isn'?t possible|is impossible"
    r"|give up on|let'?s (?:drop|shelve|park) (?:it|this)|(?:shelve|park) (?:it|this) for now",
    re.I,
)

# Tried-paths table: a marker line + a fixed header, Chinese or English.
MARK = re.compile(r"已试路径\s*[:：]|tried paths\s*:", re.I)
HEADER = re.compile(r"^\s*\|\s*路\s*\|\s*机制\s*\|\s*证据\s*\|\s*结果\s*\|\s*$"
                    r"|^\s*\|\s*path\s*\|\s*mechanism\s*\|\s*evidence\s*\|\s*result\s*\|\s*$", re.I | re.M)

# The user asked to stop / change direction: allowed.
USER_OK = re.compile(
    r"改方向|换(?:个|一个|条)?(?:方向|路|方案|做法)|改回|算了|不做了|放弃|先放着|搁置|别做了|停(?:掉|下)?吧|换成"
    r"|change (?:direction|approach)|give up|drop it|skip it|shelve|park it|revert to|switch to",
    re.I,
)

VAGUE = re.compile(r"做不到|做不了|办不到|没办法")
SHELVE = re.compile(r"先放着|先搁置|先跳过|暂时放弃|放弃(?:这条|这个|它)|建议改回|改回|改用别的|换个方向|换一个方向|换条路"
                    r"|不如(?:换|改|放弃)|要不(?:然)?就(?:不做|算了|换)|let'?s (?:drop|shelve|park)|(?:shelve|park) (?:it|this)", re.I)
NEG_BEFORE = re.compile(r"(?:不是|并非|并不是|没说|不说|不算|不叫)\s*(?:在)?\s*(?:说|讲|是)?\s*$|(?:not|never)\s+(?:saying\s+)?(?:that\s+)?(?:it\s+)?$", re.I)
FILLER = re.compile(r"所以|因此|于是|实在|恐怕|看来|确实|还是|真的|根本|也|就|都|还|而且|但是|可是|不过")
CLAUSE_DELIM =re.compile(r"[，,。；;！!？?\n|（(：:]")
THIS_WAY_DESC = re.compile(r"^\s*(?:做|[写排算处实跑设定]|的|[被拦])")
REVERSIBLE_HEAD = re.compile(r"(?:能|可以|可|随时|还能|也能)\s*$")
REVERSIBLE_PAREN = re.compile(r"可逆\s*[（(]\s*$")
RELAY = re.compile(r"题\s*\d+|你还没答|等你(?:定|答|回|裁定|选)|waiting for you|your call", re.I)
OPTLINE = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?(?:\d{1,3}[a-d]?[.、):：]|[a-dA-D][.、):：]|\(?[a-dA-D]\))")


def find_hit(s):
    """First retreat phrase that is not exempted, or None. `s` is neutralized text."""
    option_lines = sum(1 for ln in s.split("\n") if OPTLINE.search(ln))
    for m in BAD.finditer(s):
        w = m.group(0)
        head = s[max(0, m.start() - 12):m.start()]
        line = c.line_of(s, m.start())
        if NEG_BEFORE.search(head):
            continue
        if VAGUE.fullmatch(w):
            # Only "I can't" counts; "the old UI can't do X" is describing a fact.
            clause = CLAUSE_DELIM.split(s[max(0, m.start() - 40):m.start()])[-1]
            clause = FILLER.sub("", re.sub(r"[\s*#>\-•]+", "", clause))
            if clause and "我" not in clause:
                continue
        if w == "就是这样" and THIS_WAY_DESC.search(s[m.end():m.end() + 3]):
            continue
        if w == "改回":
            if REVERSIBLE_HEAD.search(head) and s[m.end():m.end() + 1] in ("来", "去"):
                continue  # "可以改回来" = it is reversible
            if REVERSIBLE_PAREN.search(head):
                continue  # "可逆（改回 true）"
            if line.lstrip().startswith("|") and re.search(r"可逆|可还原|restore|undo", line, re.I):
                continue
        if SHELVE.fullmatch(w):
            if RELAY.search(line):
                continue  # restating a question the user has not answered yet
            if OPTLINE.search(line) and option_lines >= 2:
                continue  # one of several options handed back to the user
        return m
    return None


def has_tried_table(reply):
    if not MARK.search(reply):
        return False
    hm = HEADER.search(reply)
    if not hm:
        return False
    rows = 0
    for ln in reply[hm.end():].split("\n")[1:]:
        s = ln.strip()
        if not s.startswith("|"):
            if rows or s:
                break
            continue
        if re.fullmatch(r"[|\s:\-]+", s):
            continue
        rows += 1
    return rows >= c.env_int("RETREAT_MIN_PATHS", 3)


MSG_ZH = (
    "退路闸：这条回复宣布做不到 / 要改方向 / 先搁置（命中「{hit}」），但没有附上「已试路径」表。\n"
    "换路只换手段，不换目标；目标只有用户能改。请先：\n"
    "1) 想出 >= {n} 条机制不同的做法（只改提示词措辞不算新的一条）；\n"
    "2) 在临时目录或独立分支里真的试过每一条（不碰生产、不花钱）；\n"
    "3) 回复里放标记行「已试路径：」+ 表头 | 路 | 机制 | 证据 | 结果 |，每条一行；\n"
    "4) 都试过仍不行，再附上证据，把「换目标」作为选项交给用户决定。"
)
MSG_EN = ("EN: You announced a retreat (\"{hit}\") without a tried-paths table. Try >= {n} different approaches, "
          "then add a line 'Tried paths:' and a table | Path | Mechanism | Evidence | Result |.")


def main():
    c.setup_utf8()
    if c.disabled(NAME):
        return 0
    data = c.read_input()
    if not data:
        return 0
    rows = c.load_rows(data.get("transcript_path"))
    reply = c.last_assistant_text(data, rows)
    if not reply or has_tried_table(reply):
        return 0
    hit = find_hit(c.neutralize(reply))
    if not hit:
        return 0
    if USER_OK.search(c.recent_user_text(rows)):
        return 0
    if not c.allow_block("retreat", data):
        return 0
    c.log_event("retreat_gate", hit.group(0))
    n = c.env_int("RETREAT_MIN_PATHS", 3)
    reason = MSG_ZH.format(hit=hit.group(0), n=n) + "\n" + MSG_EN.format(hit=hit.group(0), n=n)
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    c.run_safely(main)
