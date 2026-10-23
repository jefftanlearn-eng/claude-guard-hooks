#!/usr/bin/env python3
"""Run every fixture against the real hook scripts (as subprocesses, like Claude Code does).

    python tests/run_tests.py          # exit 1 if anything fails

Stdlib only. Fake secrets are assembled at runtime so this repo never contains a real-looking key.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(ROOT, "hooks")
FIX = os.path.join(ROOT, "tests", "fixtures")
TMP = tempfile.mkdtemp(prefix="cgh-test-")

FAKE = {
    "{SK}": "sk-" + "A1b2" * 6,
    "{GHP}": "ghp_" + "x9" * 15,
    "{PEM}": "-----BEGIN " + "RSA PRIVATE KEY-----",
}

results = []  # (group, name, ok, detail)


def base_env(extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CGH_")}
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra or {})
    return env


def run_hook(script, payload, env=None, cwd=None):
    raw = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    r = subprocess.run([sys.executable, os.path.join(HOOKS, script)], input=raw.encode("utf-8"),
                       capture_output=True, env=base_env(env), cwd=cwd or TMP)
    out, err = r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")
    blocked = r.returncode == 2 or '"decision": "block"' in out
    return ("block" if blocked else "pass"), r.returncode, out + err


def write_transcript(case):
    path = os.path.join(TMP, uuid.uuid4().hex + ".jsonl")
    events = case.get("transcript")
    if events is None:
        events = [["user", case.get("user", "帮我处理一下这个问题")]] + [["tool", t] for t in case.get("tools", [])]
    with open(path, "w", encoding="utf-8") as f:
        for kind, val in events:
            if kind == "user":
                row = {"type": "user", "message": {"role": "user", "content": val}}
            elif kind == "assistant":
                row = {"type": "assistant", "message": {"content": [{"type": "text", "text": val}]}}
            else:
                name, inp = ((val[:-3], {"command": "make", "run_in_background": True}) if val.endswith(":bg")
                             else (val, {}))
                row = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def stop_payload(case, session=None, active=False):
    return {"session_id": session or uuid.uuid4().hex, "transcript_path": write_transcript(case),
            "hook_event_name": "Stop", "stop_hook_active": active,
            "last_assistant_message": case["reply"]}


def check(group, name, expect, got, detail):
    results.append((group, name, got == expect, "expect={} got={}".format(expect, got) + ("" if got == expect else "\n" + detail)))


def test_stop(script, fixture):
    for case in json.load(open(os.path.join(FIX, fixture), encoding="utf-8")):
        got, _, detail = run_hook(script, stop_payload(case), case.get("env"))
        check(fixture, case["name"], case["expect"], got, detail)


def test_pipe():
    for case in json.load(open(os.path.join(FIX, "pipe_exitcode.json"), encoding="utf-8")):
        got, _, detail = run_hook("pre_pipe_exitcode.py", {"tool_name": case.get("tool", "Bash"),
                                                          "tool_input": {"command": case["command"]}},
                                  case.get("env"))
        check("pipe_exitcode.json", case["name"], case["expect"], got, detail)


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
                   + list(args), cwd=cwd, check=True, capture_output=True)


def fill(s, repo=""):
    for k, v in FAKE.items():
        s = s.replace(k, v)
    return s.replace("{REPO}", repo.replace("\\", "/"))


def test_secret():
    if not shutil.which("git"):
        results.append(("secret_scan.json", "git not installed - skipped", True, ""))
        return
    for i, case in enumerate(json.load(open(os.path.join(FIX, "secret_scan.json"), encoding="utf-8"))):
        parent = os.path.join(TMP, "s%d" % i)
        repo = os.path.join(parent, "repo")
        os.makedirs(repo)
        git(repo, "init", "-q")
        for n, files in enumerate(case["commits"]):
            for rel, body in files.items():
                p = os.path.join(repo, rel)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(fill(body))
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "c%d" % n)
            if case.get("pushed_first") and n + 1 == case["pushed_first"]:
                bare = os.path.join(parent, "remote.git")
                git(parent, "init", "-q", "--bare", bare)
                git(repo, "remote", "add", "origin", bare)
                git(repo, "push", "-q", "origin", "HEAD:refs/heads/main")
                git(repo, "fetch", "-q", "origin")
        cwd = parent if case.get("cwd") == "parent" else repo
        payload = {"tool_name": case.get("tool", "Bash"), "cwd": cwd, "tool_input": {"command": fill(case["command"], repo)}}
        got, _, detail = run_hook("pre_push_secret_scan.py", payload, case.get("env"), cwd=cwd)
        check("secret_scan.json", case["name"], case["expect"], got, detail)
        if i == 0:
            got = run_hook("pre_push_secret_scan.py", payload, {"CGH_DISABLE_SECRET_SCAN": "1"}, cwd=cwd)[0]
            check("secret_scan.json", case["name"] + " + CGH_DISABLE_SECRET_SCAN=1", "pass", got, "")


def test_general():
    g = "general"
    # 1) No regex may contain control characters (catches "\b" written without r"" = backspace).
    bad = []
    for fn in sorted(os.listdir(HOOKS)):
        if not fn.endswith(".py"):
            continue
        spec = importlib.util.spec_from_file_location("m_" + fn[:-3], os.path.join(HOOKS, fn))
        mod = importlib.util.module_from_spec(spec)
        sys.path.insert(0, HOOKS)
        spec.loader.exec_module(mod)
        pats = []

        def walk(v):
            if isinstance(v, re.Pattern):
                pats.append(v.pattern)
            elif isinstance(v, (list, tuple, set)):
                for x in v:
                    walk(x)
            elif isinstance(v, dict):
                for x in v.values():
                    walk(x)
        for v in vars(mod).values():
            walk(v)
        for p in pats:
            if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", p if isinstance(p, str) else p.decode("latin-1")):
                bad.append("{}: {!r}".format(fn, p[:60]))
    check(g, "所有正则不含控制字符 (no control chars in patterns)", "ok", "ok" if not bad else "bad", "\n".join(bad))

    # 2) Fail open: garbage stdin, and an internal crash (tool_input of the wrong type).
    for script in ("stop_claim_audit.py", "stop_retreat_gate.py", "pre_pipe_exitcode.py", "pre_push_secret_scan.py"):
        got, code, detail = run_hook(script, "not json {")
        check(g, script + " 坏输入放行 (garbage stdin)", ("pass", 0), (got, code), detail)
    got, code, detail = run_hook("pre_pipe_exitcode.py", {"tool_input": ["oops"]})
    check(g, "内部异常放行 (internal error fails open)", ("pass", 0), (got, code), detail)
    got, code, detail = run_hook("stop_claim_audit.py", {"transcript_path": TMP, "last_assistant_message": "已修复"})
    check(g, "transcript 是目录也放行", ("pass", 0), (got, code), detail)

    # 3) Disable switches.
    retreat = {"reply": "我做不到，先放着吧。"}
    cases = [("stop_retreat_gate.py", stop_payload(retreat), "CGH_DISABLE_RETREAT_GATE"),
             ("stop_claim_audit.py", stop_payload({"reply": "已修复，测试全部通过。", "tools": ["Edit"]}), "CGH_DISABLE_CLAIM_AUDIT"),
             ("pre_pipe_exitcode.py", {"tool_input": {"command": "npm test | tail && git push"}}, "CGH_DISABLE_PIPE_EXITCODE")]
    for script, payload, var in cases:
        check(g, script + " 未关闭时拦", "block", run_hook(script, payload)[0], "")
        check(g, script + " " + var + "=1 放行", "pass", run_hook(script, payload, {var: "1"})[0], "")
        check(g, script + " CGH_DISABLE=1 放行", "pass", run_hook(script, payload, {"CGH_DISABLE": "1"})[0], "")

    # 4) Loop guard: same session, Claude keeps retrying -> at most CGH_MAX_BLOCKS blocks, then let it stop.
    sid = uuid.uuid4().hex
    seq = [run_hook("stop_retreat_gate.py", stop_payload(retreat, sid, active=(k > 0)))[0] for k in range(3)]
    check(g, "防死循环：同一轮最多拦 2 次", ["block", "block", "pass"], seq, "")
    seq = run_hook("stop_retreat_gate.py", stop_payload(retreat, sid, active=False))[0]
    check(g, "新一轮重新计数", "block", seq, "")

    # 5) install.py prints valid JSON pointing at files that exist, and writes nothing.
    r = subprocess.run([sys.executable, os.path.join(ROOT, "install.py")], capture_output=True, env=base_env())
    out = r.stdout.decode("utf-8", "replace")
    try:
        snippet = json.loads(out[out.index("{"):out.rindex("}") + 1])
        cmds = [h["command"] for groups in snippet["hooks"].values() for grp in groups for h in grp["hooks"]]
        ok = len(cmds) == 4 and all(os.path.isfile(re.findall(r'"([^"]+\.py)"|(\S+\.py)', x)[-1][0]
                                                   or re.findall(r'"([^"]+\.py)"|(\S+\.py)', x)[-1][1]) for x in cmds)
    except (ValueError, KeyError, IndexError):
        ok = False
    check(g, "install.py 输出可用的 JSON 片段", True, ok, out[:400])

    # 6) PreToolUse hooks must also fire for the PowerShell tool (Windows). A matcher made only of
    #    letters and "|" is an exact-name list in Claude Code, so it must list both names exactly.
    try:
        matchers = [grp.get("matcher") for grp in snippet["hooks"]["PreToolUse"]]
        ok = bool(matchers) and all(m and set(m.split("|")) == {"Bash", "PowerShell"} for m in matchers)
    except (NameError, KeyError, TypeError):
        ok, matchers = False, None
    check(g, "install.py PreToolUse matcher = Bash|PowerShell", True, ok, repr(matchers))

    # 7) Traditional Chinese coverage: every CJK char in any hook pattern, written in Taiwan
    #    Traditional (opencc s2tw), must fold back to itself via _common.to_simp. Skipped without opencc.
    try:
        import opencc  # noqa: F401 - optional, dev only
        cc = opencc.OpenCC("s2tw")
    except Exception:  # noqa: BLE001
        cc = None
    if cc is None:
        results.append((g, "繁体覆盖检查 (opencc not installed - skipped)", True, ""))
    else:
        sys.path.insert(0, HOOKS)
        import _common
        chars, missing = set(), []
        for fn in sorted(os.listdir(HOOKS)):
            if fn.endswith(".py"):
                spec = importlib.util.spec_from_file_location("t_" + fn[:-3], os.path.join(HOOKS, fn))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                for v in vars(mod).values():
                    for p in ([v] if isinstance(v, re.Pattern) else
                              [x[1] for x in v if isinstance(x, tuple) and len(x) > 1 and isinstance(x[1], re.Pattern)]
                              if isinstance(v, list) else []):
                        chars |= set(re.findall(r"[一-鿿]", p.pattern))
        fold = getattr(_common, "to_simp", lambda x: x)
        for ch in sorted(chars):
            t = cc.convert(ch)
            if fold(t) != ch:
                missing.append("{}->{}".format(ch, t))
        check(g, "繁体覆盖：规则里每个汉字的台湾繁体都能折回简体 ({} chars)".format(len(chars)), [], missing, " ".join(missing))


CJK_OUT = re.compile(r"[　-〿㐀-䶿一-鿿＀-￯]")


def lang_check(name, out, lang, must):
    """Block message must be in exactly one language: `must` texts present, and for en no CJK
    at all, for zh no English gate title / old 'EN:' line."""
    missing = [m for m in must if m not in out]
    if lang == "en":
        bad = CJK_OUT.findall(out)
        ok = not missing and not bad
        why = "missing={} cjk={}".format(missing, "".join(bad[:20]))
    else:
        eng = [w for w in ("EN:", "Claim audit", "Retreat gate", "Pipe exit-code gate", "Secret scan gate") if w in out]
        ok = not missing and not eng and bool(CJK_OUT.search(out))
        why = "missing={} english={}".format(missing, eng)
    check("language", name, True, ok, why + "\n" + out[:600])


def make_secret_repo(tag):
    parent = os.path.join(TMP, "lang-" + tag)
    repo = os.path.join(parent, "repo")
    os.makedirs(repo)
    git(repo, "init", "-q")
    with open(os.path.join(repo, "config.py"), "w", encoding="utf-8") as f:
        f.write(fill("API_KEY = '{SK}'\n"))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "c0")
    return repo


def test_lang():
    g = "language"
    sys.path.insert(0, HOOKS)
    import _common
    # 0) The detector itself.
    for text, want in [("I fixed the 登录 page and all tests pass.", "en"),
                       ("I've pushed it. The user wrote 「先放着吧，等明天再说」 in the ticket.", "en"),
                       ("用 Claude Code 的 PreToolUse hook 检查 git push", "zh"),
                       ("这道闸会抓 `already fixed` 和 `tests pass` 这种说法。", "zh"),
                       ("已修復，測試全部通過。", "zh"),
                       ("```\n# 中文注释 中文注释 中文注释\n```\nDone, see above.", "en"),
                       ("改了 stop_claim_audit.py、stop_retreat_gate.py、pre_pipe_exitcode.py 和 pre_push_secret_scan.py", "zh"),
                       ("已修复 C:/Users/me/project/governance/LESSONS.md", "zh"),
                       ("Updated src/app/page.tsx and lib/auth_helpers.ts as planned.", "en"),
                       ("", "en")]:
        check(g, "detect_lang({!r:.40}) = {}".format(text, want), want, _common.detect_lang(text), "")

    # 0b) PreToolUse source text: the newest assistant line wins; malformed blocks are skipped.
    def arow(text):
        return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}
    long_en = arow("I looked through the whole module and the failing case comes from the date parser, "
                   "so I will run the tests now.")
    for name, rows, want in [
        ("newest short Chinese line beats older long English", [long_en, {"type": "user", "message": {"content": "ok"}},
                                                                arow("好的，马上跑测试。")], "zh"),
        ("malformed text block (null / int) is skipped, not fatal", [arow("好的，马上跑测试。"), arow(None), arow(5)], "zh"),
    ]:
        check(g, "recent_assistant_text: " + name, want,
              _common.detect_lang(_common.recent_assistant_text(rows)), "")

    # 0c) Claude ran a command before saying anything: fall back to the user's message.
    silent_zh = write_transcript({"transcript": [["user", "跑测试然后提交到主分支"], ["tool", "Bash"]]})
    old = os.environ.pop("CGH_LANG", None)
    try:
        check(g, "pick_lang: Claude silent, Chinese user message -> zh", "zh",
              _common.pick_lang(data={"transcript_path": silent_zh}), "")
    finally:
        if old is not None:
            os.environ["CGH_LANG"] = old

    # 1) Stop hooks: language of the reply being checked.
    en_claim = {"user": "please push the branch", "tools": ["Read"], "reply": "I've pushed the fix for the 登录 page to origin."}
    zh_claim = {"tools": ["Read", "Edit"], "reply": "已修复，测试全部通过。"}
    for name, case, env, lang, must in [
        ("claim: English reply (one Chinese word) -> English", en_claim, None, "en",
         ["Claim audit", "I've pushed", "Pick one", "rewrite it as a plan"]),
        ("claim: Chinese reply -> Chinese", zh_claim, None, "zh", ["声称对账闸", "已修复", "二选一"]),
        ("claim: CGH_LANG=en overrides a Chinese reply", zh_claim, {"CGH_LANG": "en"}, "en",
         ["Claim audit", "after your last file edit"]),
    ]:
        got, _, out = run_hook("stop_claim_audit.py", stop_payload(case), env)
        check(g, name + " (blocks)", "block", got, out)
        if lang == "en" and env:  # the hit is quoted from the Chinese reply, so only check the rest
            out = out.replace("已修复", "")
        lang_check(name, out, lang, must)

    en_ret = {"user": "fix the parser", "reply": "This can't be done with the current API, so let's shelve it for now."}
    zh_ret = {"reply": "我做不到，先放着吧。"}
    for name, case, env, lang, must in [
        ("retreat: English reply -> English", en_ret, None, "en",
         ["Retreat gate", "Tried paths:", "| Path | Mechanism | Evidence | Result |", ">= 3 approaches"]),
        ("retreat: Chinese reply -> Chinese", zh_ret, None, "zh", ["退路闸", "已试路径：", "| 路 | 机制 | 证据 | 结果 |"]),
        ("retreat: CGH_LANG=zh overrides an English reply", en_ret, {"CGH_LANG": "zh"}, "zh", ["退路闸", "已试路径"]),
    ]:
        got, _, out = run_hook("stop_retreat_gate.py", stop_payload(case), env)
        check(g, name + " (blocks)", "block", got, out)
        lang_check(name, out, lang, must)

    # 2) PreToolUse hooks: most recent assistant text in the transcript; none / unreadable -> English.
    en_tx = write_transcript({"transcript": [["user", "run the tests and commit"],
                                             ["assistant", "Running the test suite, then I'll commit."], ["tool", "Bash"]]})
    zh_tx = write_transcript({"transcript": [["user", "跑测试然后提交"],
                                             ["assistant", "先跑一遍测试，再提交。"], ["tool", "Bash"]]})
    nowhere = os.path.join(TMP, "no-such-transcript.jsonl")
    pipe_cmd = "npm test 2>&1 | tail -5 && git commit -m fix"
    pipe_en = ["Pipe exit-code gate", "Drop the pipe", "Enable pipefail", "Check the real code", "PIPESTATUS"]
    for name, tx, env, lang, must in [
        ("pipe: English conversation -> English", en_tx, None, "en", pipe_en),
        ("pipe: Chinese conversation -> Chinese", zh_tx, None, "zh", ["管道退出码闸", "去掉管道", "开 pipefail", "取前段退出码"]),
        ("pipe: CGH_LANG=zh overrides an English conversation", en_tx, {"CGH_LANG": "zh"}, "zh", ["管道退出码闸"]),
        ("pipe: transcript missing -> English", nowhere, None, "en", pipe_en),
        ("pipe: no transcript_path at all -> English", None, None, "en", pipe_en),
    ]:
        payload = {"tool_name": "Bash", "tool_input": {"command": pipe_cmd}}
        if tx:
            payload["transcript_path"] = tx
        got, _, out = run_hook("pre_pipe_exitcode.py", payload, env)
        check(g, name + " (blocks)", "block", got, out)
        lang_check(name, out, lang, must)

    if not shutil.which("git"):
        results.append((g, "secret scan language - git not installed, skipped", True, ""))
    else:
        repo = make_secret_repo("s")
        sec_en = ["Secret scan gate", "sk-... API key", "rotate", "CGH_SECRET_ALLOW"]
        for name, tx, env, lang, must in [
            ("secret: English conversation -> English", en_tx, None, "en", sec_en),
            ("secret: Chinese conversation -> Chinese", zh_tx, None, "zh", ["密钥扫描闸", "CGH_SECRET_ALLOW"]),
            ("secret: CGH_LANG=en overrides a Chinese conversation", zh_tx, {"CGH_LANG": "en"}, "en", sec_en),
            ("secret: transcript missing -> English", nowhere, None, "en", sec_en),
        ]:
            payload = {"tool_name": "Bash", "cwd": repo, "transcript_path": tx, "tool_input": {"command": "git push"}}
            got, _, out = run_hook("pre_push_secret_scan.py", payload, env, cwd=repo)
            check(g, name + " (blocks)", "block", got, out)
            lang_check(name, out, lang, must)

    # 3) install.py: header/footer in one language, JSON identical in both.
    outs = {}
    for name, env, lang, must in [
        ("install.py: CGH_LANG=en -> English", {"CGH_LANG": "en"}, "en", ["Merge this \"hooks\" block", "Then run the tests"]),
        ("install.py: CGH_LANG=zh -> Chinese", {"CGH_LANG": "zh"}, "zh", ["合并进", "装好后先跑测试"]),
        ("install.py: Chinese locale (LC_ALL=zh_CN.UTF-8) -> Chinese", {"LC_ALL": "zh_CN.UTF-8"}, "zh", ["合并进"]),
        ("install.py: English locale (LC_ALL=en_US.UTF-8) -> English", {"LC_ALL": "en_US.UTF-8"}, "en", ["Merge this"]),
    ]:
        r = subprocess.run([sys.executable, os.path.join(ROOT, "install.py")], capture_output=True, env=base_env(env))
        out = r.stdout.decode("utf-8", "replace")
        outs[name] = out[out.index("{"):out.rindex("}") + 1] if "{" in out else ""
        lang_check(name, out, lang, must)
    check(g, "install.py: JSON is the same in both languages", 1, len(set(outs.values())), repr(list(outs.values()))[:400])


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    try:
        test_stop("stop_retreat_gate.py", "retreat_gate.json")
        test_stop("stop_claim_audit.py", "claim_audit.json")
        test_pipe()
        test_secret()
        test_general()
        test_lang()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    fails = 0
    group = None
    for grp, name, ok, detail in results:
        if grp != group:
            print("\n== " + grp)
            group = grp
        print("  {} {}  ({})".format("ok  " if ok else "FAIL", name, detail.split("\n")[0]))
        if not ok:
            fails += 1
            print("       " + detail.replace("\n", "\n       ")[:800])
    print("\n{}: {}/{} passed".format("PASS" if not fails else "FAIL", len(results) - fails, len(results)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
