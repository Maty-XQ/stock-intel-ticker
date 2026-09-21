# 后备方案：用 cron-job.org 当定时器（把触发时点钉准）

## 什么时候用它

先看结论：**GitHub 自己的定时器不可靠 —— 但不是"不触发"，而是"不准时 + 大量丢点"**（早期结论已作废，见下）：

- 私有仓库：免费账号的 `schedule` **会被触发，属 best-effort 级别**。2026-09-18 实测 4 个业务时点全部触发，
  但分别迟到 **4h27m / 5h02m / 4h23m / 4h15m**：
  `13:30→17:57`、`14:45→19:47`、`15:55→20:28`、`16:55→21:10`；
  同期一条 `*/5 * * * *` 的诊断 cron，**两天只跑了 29 次**（理论 576 次）。
- **已作废的错误结论**：早先"私有仓库 0 次、疑似被平台禁用"和"公开空壳仓库同样 0 次"两条判断，
  都是在 **16:19** 查的 —— 那时迟到的运行还没到，属于**采样过早**导致的误判，不要再引用。
  （教训：没有证据 ≠ 证据表明没有；要等所有时点都过完再下结论。）
- 迟到 4~5 小时对盘前/盘中提醒等于废掉（收盘后才发出来的"盘中提醒"没有意义）。

→ 所以要**外部定时器**把 6 个触发时点钉准。本文件就是那个"外部定时器"的做法。

> 云端脚本另有一道**迟到闸门**（`intel_cloud.py` 里的 `LATE_DEADLINE`）：过了该时点可接受的最后时间就直接不发，
> 避免像 09-18 那样 17:57 补发一条"盘中提醒"。`postclose` / `weekly` 例外，23:00 前允许补发并标注"⏰ 延迟补发"。

## 第 1 步：建一把"只能干这一件事"的令牌

现在的令牌是通用的（classic，`repo + workflow` 全权限），第三方服务不该拿这种。
新建一把 fine-grained 令牌，只授权一个仓库、一个权限。

### 快车道（推荐）

直接点下面这条链接 —— **名称、用途、资源所有者、有效期、"Actions: Read and write" 都已预填**：

```
https://github.com/settings/personal-access-tokens/new?name=cronjob-intel-dispatch&description=cron-job.org%20trigger%20only&target_name=Maty-XQ&expires_in=90&actions=write
```

打开后你只剩两件事：

1. **Repository access** 选 `Only select repositories` → 勾 **`stock-intel-cloud`**（URL 参数管不到这里，只能手选）
2. 拉到页面底部点 **Generate token** → 复制那串 `github_pat_...`（只显示一次）

> 如果打开后 Permissions 区**没有**自动填上 Actions，说明参数名对不上，照下面"手工路径"改一下即可，不影响其它字段。

**建好后存一行到 `E:\Claw\.secrets\github-token-fg.txt`**（末尾不要有空格），
`deploy_cronjobs.py` 会自动用它；脚本只读取、不打印内容。

### 手工路径（更想自己点的话）

1. 打开 **`https://github.com/settings/personal-access-tokens/new`**
2. **Token name**：`cronjob-intel-dispatch`
3. **Expiration**：90 days（到期重建，或改用方案 4）
4. **Resource owner**：`Maty-XQ`
5. **Repository access** → `Only select repositories` → 只勾 **`stock-intel-cloud`**
6. **Permissions** → 展开 **`Repository permissions`** → 在长长的权限列表里找 **`Actions`**
   → 它右边是个**下拉框**（默认 `No access`），点开选 **`Read and write`** → 拉到底 **Generate**

### 找不到第 6 点？先确认你在哪个页面

这是最常见的坑：**你在 classic 令牌页**。判断方法 ——

| 你看到的 | 说明 |
|---|---|
| 页面上是一排**复选框**（`repo` / `workflow` / `admin:org` …），标题写着 **Select scopes** | ❌ 这是 **classic** 页（`settings/tokens/new`），**根本没有** "Repository permissions"，怪不得找不到 |
| 页面上有 **Token name / Expiration / Resource owner / Repository access / Permissions** 五段 | ✅ 这才是 fine-grained 页 |

fine-grained 页 `Permissions` 那一大段里，是分成 `Repository permissions`、`Organization permissions`、
`Account permissions` **三个可展开区块**的；只有 **`Repository permissions`** 里有 `Actions`
（列表很长，最上面几项依次是 Actions、Administration、Attestations、Checks、Codespaces…，
顶部还有个搜索框，直接搜 `actions` 更快）：

- `Actions` 旁边是**下拉框**，不是勾选框
- `Metadata` 会自动变成 `Read-only`，不用管
- **`Organization permissions` 和 `Account permissions` 两块不要动**（个人账号下通常也是空的）
- 勾完不要点别的东西，直接翻到底部 `Generate token`

> 这把令牌就算泄露，别人也只能"触发这个仓库的运行"，看不到、改不了任何代码和数据。

### 实在不想弄新令牌？那就先用旧的

你本机已经有一把**实测可用**的令牌：**`E:\Claw\.secrets\github-token.txt`**
（2026-09-20 复核：classic 型、scopes = `repo, workflow`、归属 `Maty-XQ`、有效）。
它本来就能触发 `workflow_dispatch`，直接填进 cron-job.org 的 `Authorization: Bearer ...` 就能跑。

- 代价：权限大（能读写你所有仓库），**不适合长期挂在第三方服务上**
- 做法：先这样跑起来验证通路，等新令牌建好，去 cron-job.org 把这 6 条的 Header 换成新的即可
- 建议：验证通过后**尽快轮换**（GitHub 上 delete 旧令牌 → 本机 `.secrets\github-token.txt` 同步替换）

## 第 2 步：在 cron-job.org 建 6 条任务

**注册**（免费，只要邮箱）→ 头像 → **Settings → API** → 复制那串 API Key。

### 路线甲（推荐）：让脚本建，你只提供 API Key

先把两把密钥各存一行（末尾别留空格）：
- `E:\Claw\.secrets\cronjob-api.txt` ← cron-job.org 的 API Key
- `E:\Claw\.secrets\github-token-fg.txt` ← 第 1 步的新令牌（`github_pat_...`）

```bash
python E:\Claw\ticker-repo\deploy_cronjobs.py preflight        # ① 自检 + 用 dry=true 实测 GitHub 令牌（不发消息）
python E:\Claw\ticker-repo\deploy_cronjobs.py create           # ② 6 条一次建完（同名自动跳过，可重复跑）
python E:\Claw\ticker-repo\deploy_cronjobs.py check            # ③ 复核时区/时间/预测的下次执行
python E:\Claw\ticker-repo\deploy_cronjobs.py history <jobId>  # ④ 到点后看实际执行记录
```

脚本与手工填的完全一致：时区 `Asia/Shanghai`、`POST`（`requestMethod=1`）、
Header `Authorization: Bearer <令牌>` + `Accept: application/vnd.github+json`、
body `{"ref":"main","inputs":{"mode":"..."}}`、`saveResponses=true`（存响应便于排错）、
失败邮件通知开、`wdays` 周一~周五（周报那条只勾周五）。
`create` 会先跑 `preflight`：**GitHub 那半不通就不建**，免得建出一排注定失败的定时器。

### 路线乙：自己在网页上填（脚本不可用时的备份）

注册（免费）→ Create cronjob。**每条时点建一条**，五条工作日 + 一条周五：

| 项目 | 填什么 |
|---|---|
| Title | `股票情报 08:55 盘前`（按表改） |
| URL | `https://api.github.com/repos/Maty-XQ/stock-intel-cloud/actions/workflows/intel.yml/dispatches` |
| Request method | **POST** |
| Request body | `{"ref":"main","inputs":{"mode":"premarket"}}`（按表改 mode） |
| Headers | `Authorization: Bearer <第 1 步的令牌>`<br>`Accept: application/vnd.github+json`<br>`Content-Type: application/json` |
| Schedule | 按下方时点；时区选 **Asia/Shanghai**；只勾周一~周五（周报只勾周五） |
| Notifications | 建议打开"失败时邮件通知" |

**6 条时点（北京时间）**

| 时间 | mode | 说明 |
|---|---|---|
| 08:55 | premarket | 本机 08:30 先发，云端兜底 |
| 09:50 | alert | 同上 |
| 13:30 | alert | 同上 |
| 14:45 | alert | **云端准点**（本机让位会滑出收盘） |
| 15:55 | postclose | 本机 15:30 先发，云端兜底 |
| 周五 16:55 | weekly | 本机 16:30 先发，云端兜底 |

### 验证（三种，任选）

- **立刻**：`check` 会给每条 **预测的下次执行时间**（`nextExecution`）—— 对得上就说明时区/星期没填错。
- **立刻**：网页上点每条的 **TEST RUN**，或直接跑 `preflight`：**204 才是通**（200 / 404 / 401 都不对）。
- **到点后**：`history <jobId>` 看**实际执行时间 vs 计划时间**、jitter、响应码、耗时。

cron-job.org 的执行历史能直接看到每次的响应码与响应体，这也是它的好处 —— 比 GitHub 的定时器好排查。

## 第 3 步：别忘了这一步

云端是"查到本机已经发过就静默跳过"，所以**两边同时开着不会重复**，不需要额外配置。
但如果你以后停用了某条，记得把 cron-job.org 里对应的那条也删掉，别留个半死的定时器。

## 如果这个也不想用

- **`云端工作探针.md`**：WorkBuddy 的「云端工作」模式，**不用 GitHub、不用令牌**，而且带模型
  （能写"复盘反思/要闻"）。先跑那段探针把沙箱能力问清楚，全绿的话这条路比 GitHub 更好。
- 或者方案 4：腾讯云函数 SCF（国内最稳，但要建函数 + 配定时触发器 + 填密钥），
  或者干脆只在需要出差/关机的日子临时开。随时说，我来落地。
