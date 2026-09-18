# stock-intel-ticker（公开空壳 · 只做定时触发）

**这个仓库故意是公开的，里面没有任何敏感内容。** 它只有一个定时工作流，
到点去调用私有仓库 `Maty-XQ/stock-intel-cloud` 的 `workflow_dispatch`。

## 为什么需要它

GitHub 免费个人账号在**私有仓库**上**不会触发 `schedule`**（实测：`event=schedule` 的运行数长期为 0；
`workflow_dispatch` 手动触发却一切正常）。这是 GitHub 未写进正式文档的限制，
要用私有仓库的定时任务得升级 GitHub Pro（$4/月）。

而**公开仓库的 `schedule` 是正常的**。所以把"定时器"放这里，"真正干活的代码"留在私有仓库：

```
公开仓库 stock-intel-ticker          私有仓库 stock-intel-cloud
  cron 到点 ──► POST workflow_dispatch ──► 取数 → 查已发送标记 → 发群消息 / 静默跳过
   (只有一条 curl)                        (intel_cloud.py + 持仓 + WECOM_WEBHOOK 密钥)
```

## 安全边界

| 项目 | 是否在本仓库 | 说明 |
|---|---|---|
| 持仓、策略、止损止盈 | ❌ 不在 | 全在私有仓库 `positions.json` / `state.json` |
| 推送脚本 `intel_cloud.py` | ❌ 不在 | 私有仓库 |
| 企业微信 Webhook | ❌ 不在 | 私有仓库 Secrets |
| 触发令牌 | ⚠️ 在 Secrets | GitHub 加密存储，fork 不继承、他人不可见 |
| 定时表达式 | ✅ 公开 | 别人只能看出"有个每日推送的私有项目"，无实质信息 |

## 时点（北京时间）

| 时间 | 对应 | 说明 |
|---|---|---|
| 08:55 | 盘前简报 | 本机 08:30 先发、云端兜底 |
| 09:50 | 盘内异动（开盘） | 同上 |
| 13:30 | 盘内异动（午后） | 同上 |
| 14:45 | 盘内异动（尾盘） | **云端准点**（本机让位会滑出收盘） |
| 15:55 | 盘后复盘 | 本机 15:30 先发、云端兜底 |
| 周五 16:55 | 每周总结 | 本机 16:30 先发、云端兜底 |

## 维护

- 改时间：编辑 `.github/workflows/tick.yml` 里的 cron（UTC = 北京时间 -8），
  同时确认下面 `case "$SCHED"` 的映射跟着改（写错会静默跑错模式）。
- 换令牌：Settings → Secrets and variables → Actions → `INTEL_DISPATCH_TOKEN`。
  建议用 fine-grained token，**只勾选目标私有仓库 + Actions: Read and write**，不要给 Contents 权限。
- 部署脚本：`deploy_ticker.py`（`status` / `push` / `secret` / `run <mode>` / `runs`）。
