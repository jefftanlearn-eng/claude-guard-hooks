#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash): scan outgoing commits for secrets before `git push`.

Looks at lines ADDED by commits that are not on any remote yet (falls back to the
last 3 commits), plus newly added files named like .env / *.pem / id_rsa.
Non-push commands exit immediately. Git errors or anything unexpected = allow.

Disable: CGH_DISABLE_SECRET_SCAN=1
Allowlist: CGH_SECRET_ALLOW=<regex>   matches that fit this regex are ignored
           (e.g. a public Supabase anon key you ship on purpose).
Exit 2 = block (stderr goes to Claude).
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

NAME = "SECRET_SCAN"

PATTERNS = [
    # \b matters: without it, long hyphenated words like file names ending in "...sk-..." match.
    ("sk-... API key (OpenAI / Anthropic style)", r"\bsk-[A-Za-z0-9_-]{20,}"),
    ("Stripe secret / restricted key", r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}"),
    ("Stripe webhook secret", r"\bwhsec_[A-Za-z0-9]{20,}"),
    ("AWS access key id", r"\bAKIA[0-9A-Z]{16}\b"),
    ("GitHub token", r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"),
    ("Google API key", r"\bAIza[0-9A-Za-z_-]{35}"),
    ("Slack token", r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    ("Private key block", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("JWT (e.g. service-role key)", r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}"),
    ("Hex bearer token", r"\bBearer\s+[0-9a-fA-F]{32,}"),
]
COMPILED = [(n, re.compile(p)) for n, p in PATTERNS]

SECRET_FILE = re.compile(r"(?:^|/)(?:\.env(?:\.[\w-]+)?|id_rsa|id_ed25519|[^/]*\.pem|[^/]*\.p12|credentials\.json)$")
SAFE_FILE = re.compile(r"\.(?:example|sample|template|dist)$|\.pub$")
PUSH = re.compile(r"(?:^|[;&|\n]\s*)git(?:\s+-C\s+(\"[^\"]+\"|'[^']+'|\S+))?(?:\s+-c\s+\S+)*\s+push\b")
CD = re.compile(r"(?:^|&&|;)\s*cd\s+(\"[^\"]+\"|'[^']+'|[^\s;&]+)")


def to_native(p):
    """On Windows, Git-Bash style /c/Users/... -> C:/Users/... (the hook gets the raw string)."""
    if os.name == "nt":
        m = re.match(r"^/([A-Za-z])(/.*)?$", p)
        if m:
            return "{}:{}".format(m.group(1).upper(), m.group(2) or "/")
    return os.path.expanduser(p)


def repo_dir(cmd, default):
    d = default
    m = CD.search(cmd)
    if m:
        p = to_native(m.group(1).strip("\"'"))
        p = p if os.path.isabs(p) else os.path.join(default, p)
        if os.path.isdir(p):
            d = p
    pm = PUSH.search(cmd)
    if pm and pm.group(1):
        p = to_native(pm.group(1).strip("\"'"))
        p = p if os.path.isabs(p) else os.path.join(d, p)
        if os.path.isdir(p):
            d = p
    return d


def outgoing_diff(cwd):
    def git(*args):
        return subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=20)
    r = git("log", "HEAD", "--not", "--remotes", "-p", "--no-color", "--no-ext-diff")
    if r.returncode != 0 or not r.stdout.strip():
        r2 = git("rev-parse", "--verify", "-q", "@{u}")
        if r.returncode == 0 and r2.returncode == 0:
            return ""  # everything already on the remote
        r = git("log", "-3", "-p", "--no-color", "--no-ext-diff")
    return r.stdout if r.returncode == 0 else None


def scan(diff, allow=None):
    """Return a list of human-readable hits (truncated, never the full secret)."""
    added, files, hits = [], [], []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            files.append(line[6:].strip())
        elif line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
    body = "\n".join(added)
    for name, pat in COMPILED:
        for m in pat.finditer(body):
            if allow and allow.search(m.group(0)):
                continue
            hits.append("  - {}: {}...".format(name, m.group(0)[:8]))
            break
    for f in sorted(set(files)):
        if SECRET_FILE.search(f) and not SAFE_FILE.search(f):
            hits.append("  - secret-looking file added: " + f)
    return hits


def main():
    c.setup_utf8()
    if c.disabled(NAME):
        return 0
    data = c.read_input()
    if not data:
        return 0
    cmd = (data.get("tool_input") or {}).get("command") or ""
    if not isinstance(cmd, str) or not PUSH.search(cmd):
        return 0
    diff = outgoing_diff(repo_dir(cmd, data.get("cwd") or os.getcwd()))
    if not diff:
        return 0
    allow_src = os.environ.get("CGH_SECRET_ALLOW")
    hits = scan(diff, re.compile(allow_src) if allow_src else None)
    if not hits:
        return 0
    c.log_event("secret_scan", hits[0][:30])
    sys.stderr.write(
        "密钥扫描闸：要 push 的 commit 里疑似有密钥，push 已挡下。\n" + "\n".join(hits) + "\n"
        "处理：把密钥移到 .env（并加进 .gitignore），改写掉那个 commit 后再 push；已经推上去过的密钥要当作外泄，立刻去后台换新。\n"
        "确认是误报（例如故意公开的 key）：设 CGH_SECRET_ALLOW=<正则> 放行，或由你自己手动 push。\n"
        "EN: Outgoing commits look like they contain secrets; push blocked. Move them to .env, rewrite the commit, "
        "rotate anything already pushed. False positive? Set CGH_SECRET_ALLOW=<regex> or push manually.\n"
    )
    return 2


if __name__ == "__main__":
    c.run_safely(main)
