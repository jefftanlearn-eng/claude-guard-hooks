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
                name, inp = (("Bash", {"command": "make", "run_in_background": True}) if val == "Bash:bg"
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
        got, _, detail = run_hook("pre_pipe_exitcode.py", {"tool_name": "Bash", "tool_input": {"command": case["command"]}},
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
        payload = {"tool_name": "Bash", "cwd": cwd, "tool_input": {"command": fill(case["command"], repo)}}
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
