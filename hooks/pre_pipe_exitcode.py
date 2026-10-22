#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash): a pipe ending in tail/head swallows the exit code.

    npm test 2>&1 | tail -5 && git commit -m "fix"

`&&` checks the exit code of the LAST stage (tail), which is almost always 0. Tests
fail, the commit happens anyway. Same with `pytest | tail; echo $?` (prints tail's code).

Blocks when:
  1) <cmd> | <display filter> && / || <consequential action>  (commit, push, deploy, rm ...)
  2) <cmd> | <display filter> ; echo $?
  3) strict mode only (CGH_PIPE_STRICT=1): any test runner piped into a display filter.
Allows: `set -o pipefail`, `PIPESTATUS`, grep/jq/awk as last stage (their exit code means
something), quoted text, harmless right-hand sides like `&& echo done`.

Disable: CGH_DISABLE_PIPE_EXITCODE=1.  Exit 2 = block (stderr goes to Claude).
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

NAME = "PIPE_EXITCODE"

# Display filters that practically always exit 0. grep/jq/awk/sed are left out on purpose.
SWALLOWERS = {"head", "tail", "tee", "cat", "cut", "tr", "nl", "rev", "column", "fmt",
              "sort", "uniq", "wc", "less", "more", "Select-Object", "Out-Host"}

CONSEQUENTIAL = re.compile(
    r"\b(?:git\s+(?:commit|push|merge|tag|rebase|reset|clean)"
    r"|gh\s+(?:pr\s+merge|release\s+create|workflow\s+run)"
    r"|(?:npm|pnpm|yarn)\s+publish|twine\s+upload|cargo\s+publish"
    r"|vercel(?:\s+\S+)*\s+(?:deploy|promote|--prod)|docker\s+push|kubectl\s+apply"
    r"|rm\s+-\w|mv\s|supabase\s+db\s+push|terraform\s+apply)"
)

TEST_RUNNER = re.compile(
    r"(?:^|\s)(?:npx\s+|pnpm\s+|yarn\s+|bunx\s+)?(?:pytest|vitest|jest|mocha|tox|nox|phpunit|rspec)\b"
    r"|\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test\b|\bpython3?\s+-m\s+(?:pytest|unittest)\b"
    r"|\b(?:go|cargo|dotnet|mix|swift)\s+test\b|\b(?:mvn|gradle|\./gradlew)\s+\S*test"
)

AWARE = re.compile(r"set\s+-o\s+pipefail|set\s+-[a-zA-Z]*o\s+pipefail|PIPESTATUS|pipestatus")


def mask_quoted(cmd):
    """Replace characters inside quotes with X (same length) so quoted | and && are not structure."""
    out, quote = [], None
    for ch in cmd:
        if quote:
            out.append(ch if ch == quote else "X")
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        else:
            out.append(ch)
    return "".join(out)


def strip_comments(cmd):
    return "\n".join(re.split(r"(?:^|\s)#", ln, maxsplit=1)[0] for ln in cmd.split("\n"))


def last_stage(pipeline):
    stages = re.split(r"(?<!\|)\|(?!\|)", pipeline)
    if len(stages) < 2:
        return None
    tok = stages[-1].split()
    while tok and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tok[0]):
        tok = tok[1:]
    if not tok:
        return None
    return re.split(r"[\\/]", tok[0])[-1]


def check(cmd):
    """Return a short explanation if the command should be blocked, else None."""
    cmd = strip_comments(mask_quoted(cmd))
    if AWARE.search(cmd):
        return None
    for stmt in re.split(r"[;\n]", cmd):
        parts = re.split(r"&&|\|\|", stmt)
        for i in range(len(parts) - 1):
            sw = last_stage(parts[i])
            if sw not in SWALLOWERS:
                continue
            m = CONSEQUENTIAL.search(" ".join(parts[i + 1:]))
            if m:
                return ("管道以 `{0}` 结尾，`&&` 看的是 `{0}` 的退出码（几乎总是 0）。前面失败了，`{1}` 照样执行。\n"
                        "EN: the pipe ends in `{0}`, so `&&` sees its exit code (almost always 0); `{1}` runs even if the "
                        "command before it failed.".format(sw, m.group(0).strip()))
    stmts = re.split(r"[;\n]", cmd)
    for prev, cur in zip(stmts, stmts[1:]):
        sw = last_stage(re.split(r"&&|\|\|", prev)[-1])
        if sw in SWALLOWERS and re.search(r"\$\?|\$LASTEXITCODE", cur):
            return ("`$?` 拿到的是管道末段 `{0}` 的退出码，不是你要量的那条命令。\n"
                    "EN: `$?` is the exit code of `{0}`, not of the command you meant to check.".format(sw))
    if os.environ.get("CGH_PIPE_STRICT") == "1":
        for stmt in stmts:
            for part in re.split(r"&&|\|\|", stmt):
                sw = last_stage(part)
                if sw in SWALLOWERS and TEST_RUNNER.search(part.split("|")[0]):
                    return ("（严格模式）测试命令接了 `{0}`，测试失败也会显示成功。\n"
                            "EN: (strict) test output piped into `{0}` hides a failing exit code.".format(sw))
    return None


FIX = (
    "改法（任选一种） / Fix (pick one):\n"
    "  1. 去掉管道 / drop the pipe:            npm test && git commit ...\n"
    "  2. 开 pipefail / enable pipefail:       set -o pipefail; npm test 2>&1 | tail -5 && git commit ...\n"
    "  3. 取前段退出码 / check the real code:  npm test 2>&1 | tail -5; [ ${PIPESTATUS[0]} -eq 0 ] && git commit ...\n"
)


def main():
    c.setup_utf8()
    if c.disabled(NAME):
        return 0
    data = c.read_input()
    if not data:
        return 0
    cmd = (data.get("tool_input") or {}).get("command") or ""
    why = check(cmd) if isinstance(cmd, str) else None
    if not why:
        return 0
    c.log_event("pipe_exitcode", why[:30])
    sys.stderr.write("管道退出码闸 / pipe exit-code gate: 这条命令的 && / $? 守不住它看起来守着的东西。\n"
                     + why + "\n" + FIX)
    return 2


if __name__ == "__main__":
    c.run_safely(main)
