# stock-intel-ticker（公开空壳 · 只做手动触发）

**这个仓库故意是公开的，里面没有任何敏感内容。** 它只干一件事：被触发时调用私有仓库
`Maty-XQ/stock-intel-cloud` 的 `workflow_dispatch`。

> **本仓库现在没有自己的定时器了（2026-09-21 起）。** 定时由外部服务（cron-job.org）直接触发私有仓库；
> 本仓库只保留 `workflow_dispatch`，用于手动排查或应急补发。

## 现状：谁负责"到点触发"

| 角色 | 承担者 | 可靠性 |
|---|---|---|
| **主路** | cron-job.org 的 6 条任务（外部定时器，直接 dispatch 私有仓库） | 准点，jitter 约 5~32 秒（实测） |
| **兜底** | 私有仓库**自己**的 GitHub `schedule` | 会触发但迟到几十分钟~几小时、且大量丢点 |
| 手动 | 本仓库 `tick.yml`（`workflow_dispatch`） | 人为发起，随时可用 |

```
cron-job.org ──(POST dispatch)──► 私有仓库 stock-intel-cloud
                                     取数 → 查已发送标记 → 发群消息 / 静默跳过
                                     (intel_cloud.py + 持仓 + WECOM_WEBHOOK 密钥)

手动/脚本 ──► 本仓库 tick.yml ──(curl dispatch)──► 同上
```

## 为什么本仓库的定时器被删了（留档，别再装回来）

早期判断是"私有仓库不触发 `schedule`，只有公开仓库正常"，于是把定时器放在这个公开仓库。
**这个判断已被实测推翻**（2026-09-20/21 核实）：两边**都会**触发，但都是 GitHub 的 best-effort ——
延迟以小时计且大量丢点。2026-09-21 本仓库的 schedule 当天计划 6 条只触发 2 条（13:34、15:15）。

删掉它的两个理由：

1. **多余运行**：它迟到触发时会再 dispatch 一次私有仓库，产生一条"计划外运行"（当天两条：
   云端 run #18 / #22），排查成本高；
2. **更严重的隐患**：它 dispatch 时**只传 mode、不传 slot**，而三条盘中异动的 cron 共用
   `mode=alert`。一旦迟到到错误的时间，私有仓库只能按当前时间猜时点 —— 可能猜成 `alert1315`
   并写掉它的"已发送"标记，导致当天午后异动被静默跳过。删掉 schedule 就同时消除这两个风险。
   （私有仓库自己的 `schedule` 没这个毛病：它会把自己的触发表达式透传给脚本，时点判定是确定性的。）

## 安全边界

| 项目 | 是否在本仓库 | 说明 |
|---|---|---|
| 持仓、策略、止损止盈 | ❌ 不在 | 全在私有仓库 `positions.json` / `state.json` |
| 推送脚本 `intel_cloud.py` | ❌ 不在 | 私有仓库 |
| 企业微信 Webhook | ❌ 不在 | 私有仓库 Secrets |
| 触发令牌 | ⚠️ 在 Secrets | GitHub 加密存储，fork 不继承、他人不可见 |
| 触发表达式 | ✅ 公开 | 别人只能看出"有个每日推送的私有项目"，无实质信息 |

## 时点（北京时间 · 配置在别处，不在本仓库）

| 时间 | 对应 |
|---|---|
| 08:30 | 盘前简报 |
| 09:35 | 盘内异动（开盘） |
| 13:15 | 盘内异动（午后） |
| 14:45 | 盘内异动（尾盘 · 绝不后移，否则滑出收盘） |
| 15:30 | 盘后复盘 |
| 周五 16:30 | 每周总结 |

改时间要同时改**三处**：cron-job.org 的 6 条任务、私有仓库 `.github/workflows/intel.yml` 的 cron、
以及 `intel_cloud.py` 的 `SCHED_TO_SLOT` / `NOMINAL`。工具：`deploy_cronjobs.py update`（原地改，不新建）。

## 维护

- 手动触发一次（排查用）：`python deploy_ticker.py run postclose`
- 看本仓库运行历史：`python deploy_ticker.py runs` / `check`
- 换令牌：Settings → Secrets and variables → Actions → `INTEL_DISPATCH_TOKEN`。
  建议 fine-grained token，**只勾选目标私有仓库 + Actions: Read and write**，不要给 Contents 权限。
- 同步本目录的文件到仓库：`python deploy_ticker.py push`
  （文件清单在脚本的 `FILES` 里 —— **删掉的 workflow 一定要从那里一起删，否则下次 push 会长回来**）
