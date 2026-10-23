#!/usr/bin/env python3
"""Print the settings.json "hooks" snippet for THIS machine. Does not write any file.

    python install.py                 # uses the Python running this script
    python install.py --python python3
    python install.py --only claim,retreat,pipe,secret
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
HOOKS = {
    "claim": ("Stop", None, "stop_claim_audit.py", 10),
    "retreat": ("Stop", None, "stop_retreat_gate.py", 10),
    # PowerShell: on Windows Claude Code may run shell commands through its PowerShell tool
    # (and without Git Bash it registers no Bash tool at all). Same tool_input.command field.
    "pipe": ("PreToolUse", "Bash|PowerShell", "pre_pipe_exitcode.py", 10),
    "secret": ("PreToolUse", "Bash|PowerShell", "pre_push_secret_scan.py", 30),
}


def ui_lang():
    """'zh' if CGH_LANG=zh, or (no CGH_LANG) the system locale is Chinese; else 'en'."""
    forced = (os.environ.get("CGH_LANG") or "").strip().lower()[:2]
    if forced in ("en", "zh"):
        return forced
    names = [os.environ.get(v) or "" for v in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE")]
    try:
        import locale
        names.append(locale.getlocale()[0] or "")  # Windows gives e.g. 'Chinese (Simplified)_China'
    except (ValueError, TypeError, AttributeError):
        pass
    for n in names:
        n = n.strip().lower()
        if n:
            return "zh" if n.startswith(("zh", "chinese")) else "en"
    return "en"


def quote(p):
    return '"' + p + '"' if " " in p else p


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    ap =argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--python", default=sys.executable.replace("\\", "/"), help="interpreter command for the hooks")
    ap.add_argument("--only", default="claim,retreat,pipe,secret", help="comma list: claim,retreat,pipe,secret")
    args = ap.parse_args()

    groups = {}
    for key in [k.strip() for k in args.only.split(",") if k.strip()]:
        if key not in HOOKS:
            sys.exit("unknown hook: " + key)
        event, matcher, script, timeout = HOOKS[key]
        entry = {"type": "command",
                 "command": "{} {}".format(quote(args.python), quote(HERE + "/hooks/" + script)),
                 "timeout": timeout}
        groups.setdefault((event, matcher), []).append(entry)

    hooks = {}
    for (event, matcher), entries in groups.items():
        group = {"matcher": matcher, "hooks": entries} if matcher else {"hooks": entries}
        hooks.setdefault(event, []).append(group)

    zh = ui_lang() == "zh"
    if zh:
        print("把下面的 \"hooks\" 合并进 ~/.claude/settings.json（已有 hooks 的话，把条目加进对应数组，别整段覆盖）。\n")
    else:
        print("Merge this \"hooks\" block into ~/.claude/settings.json "
              "(if you already have hooks, append these entries to the matching arrays; do not overwrite the whole block).\n")
    print(json.dumps({"hooks": hooks}, indent=2, ensure_ascii=False))
    print("\n{}  {} {}".format("装好后先跑测试：" if zh else "Then run the tests to check:",
                               quote(args.python), quote(HERE + "/tests/run_tests.py")))


if __name__ == "__main__":
    main()
