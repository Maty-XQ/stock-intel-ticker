#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
在 cron-job.org 上用 API 一次性建好 6 条"触发云端推送"的定时任务。

为什么有这个脚本：用户不想在网页上手工填 6 遍（6 个时间 + 头部 + body + 时区 + 星期）。
有了 cron-job.org 的 API Key，这 6 条可以一条命令建完。见 FALLBACK_cron-job.org.md。

密钥（脚本只读取、绝不打印内容）：
  E:\\Claw\\.secrets\\cronjob-api.txt     cron-job.org 的 API Key（Console → Settings → API）
  E:\\Claw\\.secrets\\github-token-fg.txt  fine-grained 令牌（只勾 stock-intel-cloud + Actions: Read and write）
                                          没建就用旧的 E:\\Claw\\.secrets\\github-token.txt

用法：
  python deploy_cronjobs.py preflight   # 只自检：有没有密钥、GitHub 令牌能不能真触发（dry=true，零副作用）
  python deploy_cronjobs.py check       # 列出 cron-job.org 现有任务 + 预测的下次执行时间
  python deploy_cronjobs.py create      # 建齐 6 条（同名任务自动跳过，可重复执行）
  python deploy_cronjobs.py update      # 【改时点用这个】原地把 6 条改到 SLOTS 里的新时间（不新建、不留僵尸）
  python deploy_cronjobs.py detail <jobId>    # 看某条任务的完整配置（Headers/Body 已打码）
  python deploy_cronjobs.py probe [mode]      # 最硬的验收：临时建一条 3 分钟后的任务（dry=true，不发消息），
                                              # 盯到它真的 204 触发后自动删除。API 没有"立即执行"，这是等效做法
  python deploy_cronjobs.py history <jobId>   # 看某条任务的实际执行记录（响应码、plan vs 实际时间）
  python deploy_cronjobs.py runs          # 看 GitHub 侧最近几次运行（确认 dry 自检真的 success）
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SECRETS = r"E:\Claw\.secrets"

GH_REPO = "Maty-XQ/stock-intel-cloud"
GH_WORKFLOW = "intel.yml"
GH_DISPATCH_URL = (
    f"https://api.github.com/repos/{GH_REPO}/actions/workflows/{GH_WORKFLOW}/dispatches"
)

CJ_ENDPOINT = "https://api.cron-job.org"

# 6 个时点。wdays: 0=周日 … 5=周五。weekdays=周一~周五。
# 【2026-09-21 改】本机 6 条业务任务已暂停，改为云端独立承担推送 → 云端不再"让位"，
# 时点改回名义时间。**尾盘 14:45 永远不动**（一后移就滑出收盘）。
# 改这里的数字时，必须同时改 intel.yml 的 cron 与 intel_cloud.py 的 SCHED_TO_SLOT / NOMINAL。
SLOTS = [
    ("premarket", "股票情报 08:30 盘前", 8, 30, [1, 2, 3, 4, 5]),
    ("alert", "股票情报 09:35 异动", 9, 35, [1, 2, 3, 4, 5]),
    ("alert", "股票情报 13:15 异动", 13, 15, [1, 2, 3, 4, 5]),
    ("alert", "股票情报 14:45 异动", 14, 45, [1, 2, 3, 4, 5]),
    ("postclose", "股票情报 15:30 盘后", 15, 30, [1, 2, 3, 4, 5]),
    ("weekly", "股票情报 周五 16:30 周报", 16, 30, [5]),
]

# 2026-09-20 建好的 6 条任务的 jobId，与 SLOTS **顺序一一对应**。
# 有它才能"原地改时间"（PUT /jobs/<id>）而不是"新建 6 条 + 旧 6 条变成僵尸"。
JOB_IDS_ORDERED = [8475743, 8475746, 8475747, 8475748, 8475749, 8475750]


# --------------------------------------------------------------------------- #
# 密钥
# --------------------------------------------------------------------------- #
def _read_secret(*names):
    for n in names:
        p = os.path.join(SECRETS, n)
        if os.path.exists(p):
            v = open(p, encoding="utf-8-sig").read().strip()
            # 容许用户粘贴时带上 "Bearer xxx" 或引号
            if v.lower().startswith("bearer "):
                v = v[7:].strip()
            v = v.strip().strip('"').strip("'")
            if v:
                return p, v
    return None, None


def cj_key():
    p, v = _read_secret("cronjob-api.txt")
    if not v:
        die(
            "没有找到 cron-job.org 的 API Key。\n"
            f"  去 cron-job.org → 右上角头像 → Settings → API → 复制那串，\n"
            f"  存成一行到 {SECRETS}\\cronjob-api.txt"
        )
    return v


def gh_token():
    p, v = _read_secret("github-token-fg.txt", "github-token.txt")
    if not v:
        die(
            "没有找到 GitHub 令牌。\n"
            f"  把新令牌存成一行到 {SECRETS}\\github-token-fg.txt\n"
            f"  （旧的 {SECRETS}\\github-token.txt 也能用，但权限大，验证完请轮换）"
        )
    kind = "fine-grained" if v.startswith("github_pat_") else "classic"
    return p, v, kind


def die(msg):
    print("✗ " + msg)
    sys.exit(1)


# --------------------------------------------------------------------------- #
# HTTP 小工具
# --------------------------------------------------------------------------- #
def http(method, url, headers, body=None, timeout=30):
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"_raw": raw.decode("utf-8", "replace")[:400]}
    except Exception as e:  # 网络层
        return 0, {"_err": f"{type(e).__name__}: {e}"}


def gh_headers(token):
    return {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        # GitHub 要求带 User-Agent（缺了会被 403），显式给一个比赌 cron-job.org 默认值稳
        "User-Agent": "stock-intel-cron",
        "Content-Type": "application/json",
    }


def cj_headers(key):
    return {
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# --------------------------------------------------------------------------- #
# preflight：先证明"GitHub 这一半"是通的（dry=true，不推送、不写标记）
# --------------------------------------------------------------------------- #
def preflight():
    print("=" * 62)
    print("① 密钥检查")
    tp, token, kind = gh_token()
    print(f"  ✓ GitHub 令牌：{tp}（{kind}，长度 {len(token)}）")
    kp = os.path.join(SECRETS, "cronjob-api.txt")
    print(
        f"  {'✓' if os.path.exists(kp) else '○'} cron-job.org API Key："
        + (kp if os.path.exists(kp) else "还没有（create 之前要补上）")
    )

    print()
    print("② GitHub 令牌实测（dry=true：会跑一次 workflow，但**不推消息、不写标记**）")
    st, d = http(
        "POST",
        GH_DISPATCH_URL,
        gh_headers(token),
        {"ref": "main", "inputs": {"mode": "postclose", "dry": "true"}},
    )
    if st == 204:
        print("  ✓ 返回 204 —— 令牌 + 权限 + 仓库 + 分支 全部正确，可以往下走")
    elif st == 404:
        print("  ✗ 返回 404 —— 令牌没覆盖到这个仓库，或仓库/分支名不对")
        print("     · 检查令牌的 Repository access 是否勾了 " + GH_REPO.split("/")[1])
    elif st == 403:
        print("  ✗ 返回 403 —— 权限不够（Actions 必须是 Read and write），或被限流")
    elif st == 401:
        print("  ✗ 返回 401 —— 令牌无效或已过期")
    elif st == 422:
        print("  ✗ 返回 422 —— 请求体/inputs 不被接受（看下面原文）")
    else:
        print(f"  ✗ 返回 {st}")
    if d:
        print("     " + json.dumps(d, ensure_ascii=False)[:400])
    print()
    print("  （跑完可在 https://github.com/%s/actions 看到这次 run，" % GH_REPO)
    print("    结论应是 success 且日志里有 dry-run 字样、群里没有新消息。）")
    print("=" * 62)
    return st == 204


# --------------------------------------------------------------------------- #
# cron-job.org
# --------------------------------------------------------------------------- #
def build_job(slot, token):
    mode, title, hh, mm, wdays = slot
    return {
        "job": {
            "url": GH_DISPATCH_URL,
            "enabled": True,
            "title": title,
            "saveResponses": True,  # 存响应，出问题时能在历史里看到原始返回
            "requestMethod": 1,  # 1 = POST
            "requestTimeout": 30,
            "redirectSuccess": False,
            "schedule": {
                "timezone": "Asia/Shanghai",
                "expiresAt": 0,  # 不过期
                "hours": [hh],
                "minutes": [mm],
                "mdays": [-1],  # 每天
                "months": [-1],  # 每月
                "wdays": wdays,
            },
            "extendedData": {
                "headers": {
                    "Authorization": "Bearer " + token,
                    "Accept": "application/vnd.github+json",
                    "Content-Type": "application/json",
                    "User-Agent": "stock-intel-cron",
                },
                "body": json.dumps(
                    {"ref": "main", "inputs": {"mode": mode}}, separators=(",", ":")
                ),
            },
            "notification": {
                "onFailure": True,
                "onFailureCount": 1,
                "onSuccess": False,
                "onDisable": True,
            },
        }
    }


def fmt_ts(ts):
    if not ts:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def check(verbose=True):
    key = cj_key()
    st, d = http("GET", CJ_ENDPOINT + "/jobs", cj_headers(key))
    if st != 200:
        print(f"✗ 列任务失败 HTTP {st}: {json.dumps(d, ensure_ascii=False)[:300]}")
        return None
    jobs = d.get("jobs", [])
    want = {t for _, t, *_ in SLOTS}
    have = {j.get("title") for j in jobs}
    if verbose:
        print(f"cron-job.org 现有 {len(jobs)} 条任务：")
        for j in jobs:
            sc = j.get("schedule", {})
            when = f"{sc.get('hours')}时{sc.get('minutes')}分 · 周{sc.get('wdays')}"
            print(
                f"  [{j.get('jobId')}] {j.get('title')}\n"
                f"       {when} · 时区 {sc.get('timezone')} · "
                f"{'启用' if j.get('enabled') else '停用'}\n"
                f"       上次 {fmt_ts(j.get('lastExecution'))} "
                f"状态 {j.get('lastStatus')} · 下次 {fmt_ts(j.get('nextExecution'))}"
            )
        missing = want - have
        print()
        print("缺的：" + ("、".join(sorted(missing)) if missing else "无，6 条齐了 ✓"))
    return jobs


def create():
    if not preflight():
        print("\n⚠ GitHub 这一半没通过，先别建（建了也是白发）。")
        return 1
    key = cj_key()
    _, token, _ = gh_token()

    jobs = check(verbose=True) or []
    have = {j.get("title") for j in jobs}
    have_ids = {j.get("jobId") for j in jobs}

    todo = [s for s, jid in zip(SLOTS, JOB_IDS_ORDERED)
            if s[1] not in have and jid not in have_ids]
    if not todo:
        print("\n6 条都在了，什么都不用建（改时间请用 update）。")
        return 0

    print(f"\n开始建 {len(todo)} 条（cron-job.org 限 5 条/分钟，每条之间等 13 秒）……")
    made = []
    for i, slot in enumerate(todo):
        if i:
            time.sleep(13)
        st, d = http("PUT", CJ_ENDPOINT + "/jobs", cj_headers(key), build_job(slot, token))
        ok = st == 200 and d.get("jobId")
        print(f"  {'✓' if ok else '✗'} {slot[1]}  → HTTP {st} {json.dumps(d, ensure_ascii=False)[:160]}")
        if ok:
            made.append((slot[1], d["jobId"]))

    print("\n再查一遍，确认 6 条齐了、时间/时区都对：")
    time.sleep(2)
    check(verbose=True)
    if made:
        print("\n新建的 jobId：" + "、".join(f"{t}={i}" for t, i in made))
    return 0


def update():
    """把已建好的 6 条任务**原地**改到 SLOTS 里的新时点（PUT /jobs/<jobId>）。

    为什么不用"删掉重建"：重建会得到 6 个新 jobId，而旧的那些如果漏删就成了"僵尸任务"——
    到点白发一次请求（虽然云端靠标记与迟到闸门能挡住，但排查成本很高）。
    """
    if not preflight():
        print("\n⚠ GitHub 这一半没通过，先别改（改了也发不出去）。")
        return 1
    key = cj_key()
    _, token, _ = gh_token()

    st, d = http("GET", CJ_ENDPOINT + "/jobs", cj_headers(key))
    if st != 200:
        die("列任务失败 HTTP %s: %s" % (st, json.dumps(d, ensure_ascii=False)[:200]))
    have = {j.get("jobId"): j.get("title") for j in (d.get("jobs") or [])}

    print("原地更新 6 条任务（**改任务用 PATCH**；cron-job.org 限 5 条/分钟，每条之间等 13 秒）……")
    for i, (slot, jid) in enumerate(zip(SLOTS, JOB_IDS_ORDERED)):
        if jid not in have:
            print("  ✗ jobId=%s 不在账号里（应为 %s），跳过" % (jid, slot[1]))
            continue
        if i:
            time.sleep(13)
        # 2026-09-21 实测：改任务必须用 PATCH /jobs/<id>（PUT 是"新建"，对已有 id 返回 404）。
        # 这里保留 PATCH→POST→PUT 的兜底顺序，免得将来 API 变了又得重新踩一遍。
        st, d2, used = 0, {}, None
        for method in ("PATCH", "POST", "PUT"):
            st, d2 = http(method, "%s/jobs/%s" % (CJ_ENDPOINT, jid), cj_headers(key),
                          build_job(slot, token))
            used = method
            if st in (200, 201):
                break
            if st not in (404, 405):   # 400/401/403 要立刻停下看原因，别把三种方法都试一遍
                break
        ok = st in (200, 201)
        print("  %s [%s] %s → %s%s"
              % ("✓" if ok else "✗", jid, have.get(jid), slot[1],
                 "" if ok else "  %s HTTP %s %s" % (used, st, json.dumps(d2, ensure_ascii=False)[:160])))

    print("\n再查一遍确认（看时点、时区与下次执行时间）：")
    time.sleep(2)
    check(verbose=True)
    return 0


def history(job_id):
    key = cj_key()
    st, d = http("GET", f"{CJ_ENDPOINT}/jobs/{job_id}/history", cj_headers(key))
    if st != 200:
        print(f"✗ HTTP {st}: {json.dumps(d, ensure_ascii=False)[:300]}")
        return
    for it in d.get("history", [])[:20]:
        print(
            f"  {fmt_ts(it.get('date'))}  计划 {fmt_ts(it.get('datePlanned'))}  "
            f"jitter {it.get('jitter')}ms  时长 {it.get('duration')}ms  "
            f"HTTP {it.get('httpStatus')}  {it.get('statusText')}"
        )
    preds = d.get("predictions", [])
    if preds:
        print("\n接下来几次预计执行：")
        for p in preds:
            print("  " + fmt_ts(p))


def probe(mode="postclose", lead=3):
    """
    cron-job.org 的 API 没有"立即执行"接口（那是网页上的 TEST RUN）。
    这里用等效做法：临时建一条 3 分钟后的任务、body 带 dry=true（跑全链路但不发消息、不写标记），
    盯它的执行历史，拿到 204 后**立刻删掉**这条临时任务。
    这是唯一能证明"cron-job.org 真的会按点发、且发的内容 GitHub 认"的办法。
    """
    key = cj_key()
    _, token, _ = gh_token()

    t = time.localtime(time.time() + lead * 60)
    hh, mm, wd = t.tm_hour, t.tm_min, (t.tm_wday + 1) % 7  # cron-job.org: 0=周日
    print(f"临时任务将安排在 {time.strftime('%Y-%m-%d %H:%M', t)}（约 {lead} 分钟后）")

    job = build_job((mode, "[自检] cron-job.org 探针（会自动删除）", hh, mm, [wd]), token)
    job["job"]["extendedData"]["body"] = json.dumps(
        {"ref": "main", "inputs": {"mode": mode, "dry": "true"}}, separators=(",", ":")
    )

    st, d = http("PUT", CJ_ENDPOINT + "/jobs", cj_headers(key), job)
    if st != 200 or not d.get("jobId"):
        die(f"建临时任务失败 HTTP {st}: {json.dumps(d, ensure_ascii=False)[:200]}")
    jid = d["jobId"]
    print(f"✓ 已建临时任务 jobId={jid}，body={job['job']['extendedData']['body']}")

    hit = None
    try:
        print("等它触发（最多轮询 6 分钟）……")
        for i in range(36):
            time.sleep(10)
            st, d = http("GET", f"{CJ_ENDPOINT}/jobs/{jid}/history", cj_headers(key))
            hist = d.get("history") or []
            if hist:
                hit = hist[0]
                break
        if not hit:
            print("✗ 6 分钟内没有执行记录")
        else:
            actual = fmt_ts(hit.get("date"))
            planned = fmt_ts(hit.get("datePlanned"))
            print(
                f"✓ 真的触发了！\n"
                f"    计划 {planned}  实际 {actual}  "
                f"jitter {hit.get('jitter')}ms  耗时 {hit.get('duration')}ms\n"
                f"    HTTP {hit.get('httpStatus')}  {hit.get('statusText')}"
                f"   （**204 才是通**）"
            )
            print("\n去 GitHub 侧确认这一次是不是 success：")
            time.sleep(3)
            runs()
    finally:
        st, d = http("DELETE", f"{CJ_ENDPOINT}/jobs/{jid}", cj_headers(key))
        print(f"\n清理：{'✓ 临时任务已删除' if st == 200 else f'✗ 删除失败 HTTP {st}'} ({jid})")


def detail(job_id):
    key = cj_key()
    st, d = http("GET", f"{CJ_ENDPOINT}/jobs/{job_id}", cj_headers(key))
    if st != 200:
        print(f"✗ HTTP {st}: {json.dumps(d, ensure_ascii=False)[:300]}")
        return
    j = d.get("jobDetails", {})
    ed = j.get("extendedData", {}) or {}
    hs = dict(ed.get("headers", {}) or {})
    auth = hs.get("Authorization", "")
    if auth:
        hs["Authorization"] = auth[:20] + "…（已打码，共 %d 字）" % len(auth)
    print(f"[{j.get('jobId')}] {j.get('title')}")
    print(f"  URL        {j.get('url')}")
    print(f"  method     {j.get('requestMethod')}（1=POST）  enabled={j.get('enabled')}  "
          f"saveResponses={j.get('saveResponses')}  timeout={j.get('requestTimeout')}s")
    print(f"  schedule   {json.dumps(j.get('schedule'), ensure_ascii=False)}")
    print(f"  headers    {json.dumps(hs, ensure_ascii=False)}")
    print(f"  body       {ed.get('body')}")
    print(f"  notify     {json.dumps(j.get('notification'), ensure_ascii=False)}")
    print(f"  下次执行   {fmt_ts(j.get('nextExecution'))}   上次 {fmt_ts(j.get('lastExecution'))} "
          f"状态 {j.get('lastStatus')}")


def runs(n=8):
    """看 GitHub 侧最近的运行，确认 dry 自检真的 success（而不只是 204 收到了）。"""
    _, token, _ = gh_token()
    st, d = http(
        "GET",
        f"https://api.github.com/repos/{GH_REPO}/actions/runs?per_page={n}",
        {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "User-Agent": "stock-intel-cron",
        },
    )
    if st != 200:
        print(f"✗ HTTP {st}: {json.dumps(d, ensure_ascii=False)[:300]}")
        return
    print(f"{GH_REPO} 最近 {len(d.get('workflow_runs', []))} 次运行：")
    for r in d.get("workflow_runs", []):
        print(
            f"  {r.get('created_at', '')[:19]}Z  {r.get('event'):18s} "
            f"{r.get('status'):10s} {r.get('conclusion')}  #{r.get('run_number')}"
        )


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "preflight"
    if cmd == "preflight":
        sys.exit(0 if preflight() else 1)
    elif cmd == "check":
        check()
    elif cmd == "create":
        sys.exit(create())
    elif cmd == "update":
        sys.exit(update())
    elif cmd == "history":
        if len(sys.argv) < 3:
            die("用法：python deploy_cronjobs.py history <jobId>")
        history(sys.argv[2])
    elif cmd == "detail":
        if len(sys.argv) < 3:
            die("用法：python deploy_cronjobs.py detail <jobId>")
        detail(sys.argv[2])
    elif cmd == "runs":
        runs()
    elif cmd == "probe":
        probe(sys.argv[2] if len(sys.argv) > 2 else "postclose")
    else:
        die("用法：preflight | check | create | update | probe [mode] | detail <jobId> | history <jobId> | runs")
