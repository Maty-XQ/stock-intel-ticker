# -*- coding: utf-8 -*-
"""
公开"空壳"定时仓库的维护工具（token 从 E:\\Claw\\.secrets\\github-token.txt 读）

为什么要有这个仓库：最初是想借"公开仓库的 schedule"当定时器。2026-09-21 实测更正：
GitHub 自带 schedule（公开/私有都一样）**会触发但严重不准时且大量丢点**，
所以**主路已改为外部定时器 cron-job.org**（见 FALLBACK_cron-job.org.md）。
本仓库的 tick.yml 降级为"次选兜底"，到点用 workflow_dispatch 通知私有仓库干活。

注意：`push` 只同步 FILES 里的文件；tick-diag.yml（当年的诊断实验，已完成使命）已于 2026-09-21 从
本目录与远程仓库一起删除，**不要再把它加回来**（加回来 = 每 5 分钟白跑一次）。

用法：
    python deploy_ticker.py create          # 建公开仓库（已存在则跳过）
    python deploy_ticker.py push            # 把 ticker-repo\\ 下的文件同步上去
    python deploy_ticker.py secret          # 把本机 PAT 写成仓库密钥 INTEL_DISPATCH_TOKEN
    python deploy_ticker.py run postclose   # 手动触发 tick.yml（跑哪一段）
    python deploy_ticker.py runs            # 看本仓库最近几次运行（含 event 字段）
    python deploy_ticker.py check           # 统计 schedule 事件是否出现过（核心验收）
    python deploy_ticker.py probe postclose # 不经 tick，直接触发私有仓库（排查用）
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
OWNER = "Maty-XQ"
PUB = "stock-intel-ticker"          # 本仓库：公开空壳，只放定时器
PRIV = "stock-intel-cloud"          # 私有仓库：真正干活的
SRC = r"E:\Claw\ticker-repo"
TOKEN_FILE = r"E:\Claw\.secrets\github-token.txt"
FILES = ["README.md", ".github/workflows/tick.yml", "deploy_ticker.py", "deploy_cronjobs.py",
         "FALLBACK_cron-job.org.md", "云端工作探针.md"]


def token():
    if not os.path.exists(TOKEN_FILE):
        sys.exit("找不到 token 文件：%s" % TOKEN_FILE)
    return open(TOKEN_FILE, encoding="utf-8").read().strip()


def call(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(API + path, data=data, method=method)
    r.add_header("Authorization", "Bearer " + token())
    r.add_header("Accept", "application/vnd.github+json")
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=40) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            return resp.status, (json.loads(raw) if raw[:1] in "{[" else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:300]}


def cmd_create():
    st, js = call("POST", "/user/repos", {
        "name": PUB, "private": False,
        "description": "公开空壳·只做定时触发（不含任何持仓/策略/密钥）",
        "has_issues": False, "has_wiki": False, "has_projects": False,
        "auto_init": False,
    })
    if st == 201:
        print("已创建公开仓库 %s/%s" % (OWNER, PUB))
    elif st == 422:
        print("仓库已存在，跳过（%s）" % js.get("message"))
    else:
        print("创建失败 HTTP %s %s" % (st, js))


def cmd_push(names=None):
    for rel in (names or FILES):
        local = os.path.join(SRC, rel.replace("/", os.sep))
        if not os.path.exists(local):
            print("跳过（本地不存在）：%s" % rel)
            continue
        # 路径必须 URL 编码：仓库里可能放中文名的文档，未编码会 UnicodeEncodeError（2026-09-21 踩过）
        rel_q = urllib.parse.quote(rel)
        st, js = call("GET", "/repos/%s/%s/contents/%s?ref=main" % (OWNER, PUB, rel_q))
        sha = js.get("sha") if st == 200 else None
        body = {"message": "chore: 同步 %s" % rel,
                "content": base64.b64encode(open(local, "rb").read()).decode("ascii"),
                "branch": "main"}
        if sha:
            body["sha"] = sha
        st, js = call("PUT", "/repos/%s/%s/contents/%s" % (OWNER, PUB, rel_q), body)
        print("%-34s -> %s %s" % (rel, st, "OK" if st in (200, 201) else js))


def cmd_secret():
    """把本机 PAT 加密后写成仓库密钥（用仓库公钥做 SealedBox）"""
    from nacl import encoding, public
    st, js = call("GET", "/repos/%s/%s/actions/secrets/public-key" % (OWNER, PUB))
    if st != 200:
        print("取公钥失败 HTTP %s %s" % (st, js))
        return
    key_id, pub_key = js["key_id"], js["key"]
    sealed = public.SealedBox(public.PublicKey(pub_key, encoding.Base64Encoder())) \
        .encrypt(token().encode("utf-8"))
    st, js = call("PUT", "/repos/%s/%s/actions/secrets/INTEL_DISPATCH_TOKEN" % (OWNER, PUB),
                  {"encrypted_value": base64.b64encode(sealed).decode("ascii"),
                   "key_id": key_id})
    print("写入密钥 INTEL_DISPATCH_TOKEN -> %s %s" % (st, "OK" if st in (200, 201) else js))


def cmd_run(mode):
    st, js = call("POST", "/repos/%s/%s/actions/workflows/tick.yml/dispatches" % (OWNER, PUB),
                  {"ref": "main", "inputs": {"mode": mode}})
    print("触发 tick.yml（mode=%s）-> HTTP %s" % (mode, st))
    if st >= 300:
        print(js); return
    for _ in range(10):
        time.sleep(12)
        st, js = call("GET", "/repos/%s/%s/actions/runs?per_page=1" % (OWNER, PUB))
        runs = js.get("workflow_runs", [])
        if runs:
            r = runs[0]
            print("  %s | %s | %s/%s" % (r["created_at"], r["event"], r["status"], r["conclusion"]))
            if r["status"] == "completed":
                print("  ", r["html_url"])
                break


def cmd_runs():
    st, js = call("GET", "/repos/%s/%s/actions/runs?per_page=20" % (OWNER, PUB))
    runs = js.get("workflow_runs", [])
    if not runs:
        print("还没跑过"); return
    for r in runs:
        print("  %s | %-18s | %-10s -> %s | %s" % (
            r["created_at"], r["event"], r["status"], r["conclusion"], r["name"]))


def cmd_check():
    """核心验收：schedule 事件到底出现过没有"""
    from collections import Counter
    st, js = call("GET", "/repos/%s/%s/actions/runs?per_page=50" % (OWNER, PUB))
    runs = js.get("workflow_runs", [])
    c = Counter(r["event"] for r in runs)
    print("最近 %d 次运行的事件分布：%s" % (len(runs), dict(c)))
    for r in runs:
        if r["event"] == "schedule":
            print("  ✅ 发现 schedule 运行：%s | %s/%s | %s" % (
                r["created_at"], r["status"], r["conclusion"], r["name"]))
    if not c.get("schedule"):
        print("  ⚠️ 仍未出现 schedule 运行（新 cron 需要 15 分钟~1 小时被 GitHub 注册，请稍后再看）")


def cmd_probe(mode):
    """不经 tick 仓库，直接触发私有仓库（排查"是 tick 坏了还是私有仓库坏了"）"""
    st, js = call("POST", "/repos/%s/%s/actions/workflows/intel.yml/dispatches" % (OWNER, PRIV),
                  {"ref": "main", "inputs": {"mode": mode}})
    print("直接触发私有仓库（mode=%s）-> HTTP %s" % (mode, st))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    if cmd == "create":
        cmd_create()
    elif cmd == "push":
        cmd_push(sys.argv[2:] or None)
    elif cmd == "secret":
        cmd_secret()
    elif cmd == "run":
        cmd_run(arg or "postclose")
    elif cmd == "runs":
        cmd_runs()
    elif cmd == "check":
        cmd_check()
    elif cmd == "probe":
        cmd_probe(arg or "postclose")
    else:
        print(__doc__)
