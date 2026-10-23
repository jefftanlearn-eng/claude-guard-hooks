"""Shared helpers for claude-guard-hooks. Python 3.8+, stdlib only.

Nothing in here may raise into the caller's session: every hook wraps its main()
with run_safely(), which turns any internal error into "allow" (fail open).
"""
import json
import os
import re
import sys
import tempfile
import time

PREFIX = "CGH_"


# ── Traditional Chinese (Taiwan) ──────────────────────────────────────────
# Every rule below is written once, in Simplified. Text is folded Traditional -> Simplified
# with a 1:1 per-character table first, so string positions stay identical (a hit can be
# sliced back out of the original text). The table covers every Traditional (s2tw) form of
# every CJK character used in any hook pattern; tests/run_tests.py re-checks this with
# opencc when it is installed. Characters outside the rules are left alone on purpose.
_TRAD = "丟並來個備兩別劃問啟嗎執實寫將對幾張後徑復應戶掛換據擱攔於時暫會條棄樣機決沒測準無現畫確結經綠纔臺著處親計設試話該說講證議讀載辦這運過選還開隨項題馬驗麼"
_SIMP = "丢并来个备两别划问启吗执实写将对几张后径复应户挂换据搁拦于时暂会条弃样机决没测准无现画确结经绿才台着处亲计设试话该说讲证议读载办这运过选还开随项题马验么"
_TO_SIMP = str.maketrans(_TRAD, _SIMP)


def to_simp(s):
    """Fold Traditional characters used by the rules to Simplified (same length)."""
    return s.translate(_TO_SIMP)


# ── config ────────────────────────────────────────────────────────────────
def disabled(name):
    """CGH_DISABLE=1 turns off every hook; CGH_DISABLE_<NAME>=1 turns off one."""
    return os.environ.get(PREFIX + "DISABLE") == "1" or os.environ.get(PREFIX + "DISABLE_" + name) == "1"


def env_int(name, default):
    try:
        return int(os.environ.get(PREFIX + name, default))
    except ValueError:
        return default


def setup_utf8():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def read_input():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def run_safely(main):
    """Run main(); on any internal error print a note to stderr and exit 0 (allow)."""
    try:
        code = main() or 0
    except Exception as e:  # noqa: BLE001 - fail open on purpose
        sys.stderr.write("[claude-guard-hooks] internal error, not blocking: %r\n" % (e,))
        code = 0
    sys.exit(code)


def log_event(hook, hit):
    """Optional: CGH_LOG_FILE=/path/blocks.jsonl records hook name + matched word only (never reply text)."""
    path = os.environ.get(PREFIX + "LOG_FILE")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": int(time.time()), "hook": hook, "hit": str(hit)[:40]}, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ── Stop-hook loop guard ──────────────────────────────────────────────────
def allow_block(hook, data):
    """True = go ahead and block. Caps blocks per turn at CGH_MAX_BLOCKS (default 2) so a
    misfire can never trap the session. Counter resets on each fresh Stop (stop_hook_active false)."""
    limit = env_int("MAX_BLOCKS", 2)
    key = re.sub(r"[^A-Za-z0-9_-]", "_", str(data.get("session_id") or "nosession"))[:80]
    d = os.path.join(tempfile.gettempdir(), "claude-guard-hooks")
    path = os.path.join(d, hook + "-" + key)
    try:
        os.makedirs(d, exist_ok=True)
        count = 0
        if data.get("stop_hook_active") and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                count = int(f.read().strip() or 0)
        if count >= limit:
            return False
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(count + 1))
        return True
    except (OSError, ValueError):
        return True


# ── transcript ────────────────────────────────────────────────────────────
TAIL_BYTES = 8 * 1024 * 1024
NOT_USER_PREFIX = ("[Request interrupted", "<system-reminder", "<command-name", "<local-command",
                   "Stop hook feedback", "hook blocking error", "<task-notification", "Caveat:")


def load_rows(path):
    """Last ~8 MB of the JSONL transcript as dicts, oldest first. None if unreadable."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            raw = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    rows = []
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _texts(row):
    content = (row.get("message") or {}).get("content")
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    return []


def is_real_user_msg(row):
    """A message the human typed (not a tool_result, hook feedback, meta or interrupt marker)."""
    if row.get("type") != "user" or row.get("isMeta") or row.get("isSidechain"):
        return False
    content = (row.get("message") or {}).get("content")
    if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return False
    texts = [t for t in _texts(row) if t.strip()]
    return bool(texts) and not all(t.lstrip().startswith(NOT_USER_PREFIX) for t in texts)


def recent_user_text(rows, lookback=2):
    out = []
    for row in reversed(rows or []):
        if is_real_user_msg(row):
            out.append("\n".join(t for t in _texts(row) if not t.lstrip().startswith(NOT_USER_PREFIX)))
            if len(out) >= lookback:
                break
    return "\n".join(out)


def turn_tool_calls(rows, lookback=2):
    """Main-thread tool calls since the `lookback`-th most recent real user message, oldest first.
    Background Bash / PowerShell shows up as an extra pseudo-tool 'Bash:background'."""
    calls, seen = [], 0
    for row in reversed(rows or []):
        if row.get("isSidechain"):
            continue
        if is_real_user_msg(row):
            seen += 1
            if seen >= lookback:
                break
            continue
        if row.get("type") == "user":
            res = row.get("toolUseResult")
            blob = json.dumps(row.get("message") or {}, ensure_ascii=False)
            if (isinstance(res, dict) and res.get("backgroundTaskId")) or "moved to the background" in blob:
                calls.append("Bash:background")
            continue
        if row.get("type") != "assistant":
            continue
        content = (row.get("message") or {}).get("content")
        for b in reversed(content if isinstance(content, list) else []):
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name"):
                if b["name"] in ("Bash", "PowerShell") and (b.get("input") or {}).get("run_in_background"):
                    calls.append("Bash:background")
                calls.append(b["name"])
    calls.reverse()
    return calls


def last_assistant_text(data, rows):
    """Prefer the hook field; fall back to assistant text after the last real user message."""
    msg = data.get("last_assistant_message")
    if isinstance(msg, str) and msg.strip():
        return msg.strip()
    parts = []
    for row in reversed(rows or []):
        if is_real_user_msg(row):
            break
        if row.get("type") == "assistant" and not row.get("isSidechain"):
            parts[:0] = _texts(row)
    return "\n".join(parts).strip()


# ── output language ───────────────────────────────────────────────────────
# Block messages are printed in ONE language: "zh" (Simplified Chinese) or "en".
#   1) CGH_LANG=en / CGH_LANG=zh overrides everything.
#   2) Otherwise look at what Claude wrote: the reply being checked (Stop hooks) or the most
#      recent assistant text in the transcript (PreToolUse hooks; if Claude has said nothing yet,
#      the user's latest message). No text / unreadable = "en".
# Rule: drop code, URLs and 「quoted」 bits, then count CJK ideographs against Latin WORDS
# (one Chinese character carries about as much as one English word, not one letter).
# zh when CJK chars make up >= 30% of (CJK chars + Latin words). So a Chinese reply
# full of English tool names stays zh, and an English reply quoting one Chinese word stays en.
LANG_ZH_SHARE = 0.30
_LANG_STRIP = re.compile(r"```.*?(?:```|\Z)|`[^`\n]*`|https?://\S+|「[^」\n]*」|『[^』\n]*』", re.S)
# File names, paths and snake_case identifiers are not English prose: "改了 stop_claim_audit.py" is Chinese.
_LANG_IDENT = re.compile(r"[A-Za-z0-9:]*[\/_][\w.\-:\/]*|\b[A-Za-z0-9\-]+\.[A-Za-z0-9]+\b")
_CJK_CHAR = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_LATIN_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def detect_lang(text):
    """'zh' or 'en' for a piece of assistant text (no env override here)."""
    if not isinstance(text, str) or not text.strip():
        return "en"
    s = _LANG_IDENT.sub(" ", _LANG_STRIP.sub(" ", text))
    cjk = len(_CJK_CHAR.findall(s))
    words = len(_LATIN_WORD.findall(s))
    return "zh" if cjk and cjk >= LANG_ZH_SHARE * (cjk + words) else "en"


def _lang_units(text):
    s = _LANG_IDENT.sub(" ", _LANG_STRIP.sub(" ", text))
    return len(_CJK_CHAR.findall(s)) + len(_LATIN_WORD.findall(s))


def recent_assistant_text(rows, max_chars=600, enough_units=5):
    """Most recent main-thread assistant text in the transcript. Newest first, and it stops as
    soon as the newest text says enough to judge the language (>= enough_units CJK chars + Latin
    words), so a short new Chinese line is not outvoted by an older long English one.
    Tool calls, tool results and malformed blocks are skipped. '' if there is none."""
    parts, size, units = [], 0, 0
    for row in reversed(rows or []):
        if row.get("type") != "assistant" or row.get("isSidechain"):
            continue
        for t in reversed(_texts(row)):
            if isinstance(t, str) and t.strip():
                parts.insert(0, t)
                size += len(t)
                units += _lang_units(t)
        if units >= enough_units or size >= max_chars:
            break
    return "\n".join(parts)


def pick_lang(text=None, data=None):
    """Output language for a block message. Stop hooks pass the reply as `text`; PreToolUse
    hooks pass the hook input as `data` and the transcript at data['transcript_path'] is read."""
    forced = (os.environ.get(PREFIX + "LANG") or "").strip().lower()[:2]  # zh, zh-TW, zh_CN, en...
    if forced in ("en", "zh"):
        return forced
    if text is None:
        try:
            rows = load_rows((data or {}).get("transcript_path"))
            # Claude may run a command before saying anything: then go by what the user typed.
            text = recent_assistant_text(rows) or recent_user_text(rows, lookback=1)
        except Exception:  # noqa: BLE001 - never let language detection break a block
            text = ""
    return detect_lang(text)


# ── text that should not count as the assistant's own claim ───────────────
FENCE = re.compile(r"```.*?(?:```|\Z)", re.S)
INLINE_CODE = re.compile(r"`[^`\n]+`")
QUOTE = re.compile(r"「[^」\n]*」|『[^』\n]*』|“[^”\n]*”|\"[^\"\n]{1,120}\"")
ATTRIBUTION = re.compile(r"(?:你|用户|使用者|您|对方|他|她)(?:说|问|写|讲|提到|原话)[了过]?\s*[:：]?\s*$|(?:you|user)\s+(?:said|wrote|asked)\s*[:,]?\s*$", re.I)
BLOCKQUOTE = re.compile(r"^\s*>.*$", re.M)
CJK = re.compile(r"[一-鿿]")
OPTION_MARK = re.compile(r"(?:^|[\s|*(（])\(?([a-dA-D])[.)、:：）]\**\s")
OPTION_HEADER = re.compile(r"选项|待答|你选|你定|option|choice", re.I)
SHORT_QUOTE = 20


def is_prose(s):
    """Chinese sentence disguised as code/quote: >= 8 chars and mostly CJK."""
    s = s.strip("`\"'「」“” ")
    return len(s) >= 8 and len(CJK.findall(s)) * 2 >= len(s)


def _fence(m):
    for line in m.group(0).split("\n"):
        s = line.strip("` ").strip()
        if s.startswith(("#", "//", "/*", "*", "--", "<!--")):
            continue
        if is_prose(s):
            return m.group(0)  # prose wrapped in a code block still counts
    return " "


def _inline(m):
    return m.group(0) if is_prose(m.group(0)) else " "


def strip_option_tables(text):
    """Blank out markdown tables that offer choices/questions to the user."""
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        if not lines[i].lstrip().startswith("|"):
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].lstrip().startswith("|"):
            j += 1
        block = lines[i:j]
        marks = {m.group(1).lower() for row in block for m in OPTION_MARK.finditer(row)}
        if len(marks) >= 2 or OPTION_HEADER.search(to_simp(block[0])):
            for k in range(i, j):
                lines[k] = ""
        i = j
    return "\n".join(lines)


def neutralize(text):
    """Remove text that is not the assistant speaking for itself: code, quoted user words,
    blockquotes, option/question tables. Long prose wrapped in quotes/code is kept on purpose."""
    text = FENCE.sub(_fence, text)
    text = INLINE_CODE.sub(_inline, text)
    text = BLOCKQUOTE.sub(" ", text)
    text = strip_option_tables(text)
    out, pos = [], 0
    for m in QUOTE.finditer(text):
        out.append(text[pos:m.start()])
        inner = m.group(0)[1:-1]
        head = text[max(0, m.start() - 12):m.start()]
        out.append(" " if len(inner) <= SHORT_QUOTE or ATTRIBUTION.search(to_simp(head)) else m.group(0))
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def line_of(s, pos):
    a = s.rfind("\n", 0, pos) + 1
    b = s.find("\n", pos)
    return s[a:(b if b >= 0 else len(s))]
