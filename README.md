# Epic 免费游戏提醒（云端版）

每周自动检查 Epic 的免费游戏，把「一键领取」链接推送到你手机。

**这个仓库不持有任何 Epic 凭据。** 它只调用一个公开接口取免费游戏列表，
所以仓库里没有任何秘密可泄露——就算仓库被公开、被 fork、被入侵，
受影响的范围也只是「别人知道了你在盯免费游戏」。

## 为什么不能全自动领取

领取需要在你账号上完成一次「结账确认」，Epic 在这一步用 Arkose FunCaptcha 保护。
任何无人值守的云端都过不去（不是 GitHub 不行，换谁的服务器都一样）。
所以流程设计成：**云端自动检测 → 推送链接 → 你点一下领取**。

## 文件

- `scripts/epic_free_games.py` —— 检查器，只依赖 Python 标准库，无第三方依赖
- `.github/workflows/epic-free-games.yml` —— 定时任务

## 部署步骤

### 1. 建仓库并推送

```bash
cd ~/epic-auto/gh-repo
git init -b main
git add -A
git commit -m "Epic 免费游戏提醒"
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

### 2. 配置通知渠道（选一个）

在仓库的 **Settings → Secrets and variables → Actions → New repository secret** 添加：

| Secret 名 | 用途 | 怎么拿 |
|---|---|---|
| `SERVERCHAN_KEY` | 推到微信 | 到 sct.ftqq.com 微信扫码登录，复制 SendKey |
| `FEISHU_WEBHOOK` | 推到飞书群 | 飞书群 → 设置 → 群机器人 → 添加机器人 → 自定义机器人 → 复制 Webhook 地址 |
| `FEISHU_SECRET` | 可选，飞书加签密钥 | 添加机器人时若勾选了「签名校验」，把密钥填这里；没勾选就不用填 |
| `NTFY_TOPIC` | 推到 ntfy App | 手机装 ntfy，订阅一个自定义主题名（这个名字就是密钥） |
| `DINGTALK_WEBHOOK` | 推到钉钉群 | 建一个只有自己的群 → 群设置 → 智能群助手 → 添加「自定义机器人」→ 复制 Webhook 地址 |

推荐 **飞书**或 **Server酱**。飞书的机器人卡片渲染最好（标题 + 加粗 + 可点击链接）；
Server酱 不用装 App，消息直接进微信。

> Telegram 不可用——实测 `api.telegram.org` 从你的网络连不上（返回 000）。
> ntfy 通道已做过真实的端到端投递验证，中文标题和 Markdown 链接都能正常送达。

### 3. 测试

仓库 **Actions** 标签页 → 左侧「Epic 免费游戏提醒」→ **Run workflow** 手动跑一次。
看到 `Server酱 推送完成，HTTP 200` 且手机收到消息，就说明通了。

## ⚠️ 关键限制：私有仓库的定时任务不会触发

**GitHub 免费账号的私有仓库，`schedule` 触发器实际不生效**——这是社区长期反映的问题
（官方文档没写清楚）。表现为：Actions 页面只显示 `workflow_dispatch`，定时运行永远不出现。

你有三个选择：

### 方案 A：把仓库设为公开（最简单，推荐）

公开仓库的 `schedule` 正常工作，且 Actions 分钟数无限免费。
由于本仓库不持有任何凭据，公开的代价仅仅是「别人能看到你在盯 Epic 免费游戏」。
Settings → General → Danger Zone → Change visibility → Public。

### 方案 B：保持私有，用外部定时器触发（复杂度中等）

1. 生成一个**细粒度 PAT**：Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → 只授权这一个仓库，权限只给 **Actions: Read and write**。
2. 到 cron-job.org 注册，建一个每周四 23:17 UTC 的定时任务，URL 填：

   ```
   https://api.github.com/repos/<用户名>/<仓库名>/actions/workflows/epic-free-games.yml/dispatches
   ```

   Method 选 POST，加两个 Header：

   ```
   Authorization: Bearer <你的 PAT>
   Accept: application/vnd.github+json
   ```

   Body 填 `{"ref":"main"}`。

代价：那个 PAT 存在 cron-job.org 手里。权限已限定到单仓库 + 仅 Actions 读写，风险可控但不是零。

### 方案 C：不用 GitHub，改用 Cloudflare Workers

如果你的真实目标是「免费的常驻定时任务」，Workers 比 GitHub 更合适：
它有原生 cron 触发器、免费额度充足、而且**使用条款没有 GitHub Actions 那条**
「不得用于与仓库软件项目无关的活动」的限制。同一个检测逻辑（几十行 JS）
直接搬到 Worker 即可。需要 CF 账号，不用手机常驻，也不用外部定时器。

## 其他注意事项

- **60 天规则**：仓库 60 天无活动，定时任务会被自动禁用。workflow 里已内置一个保活步骤
  （调用 API 重新 enable，以此重置计时）。方案 A 下这个步骤才有意义。
- **运行成本**：每次运行约 10-20 秒。免费额度 2000 分钟/月，占用可忽略。
- **延迟**：GitHub 的 schedule 可能延迟几分钟到几十分钟，所以设在轮换后 8 小时，
  留有充足余量。若某周没收到，先看 Actions 页面是否有失败记录。
- **换接口**：如果 `store-site-backend-static-ipv4.ak.epicgames.com`（Akamai 域名）
  对 GitHub runner 的 IP 返回 403，可改装 `launcher.store.epicgames.com/graphql`
  （字段名要用大写的 `Catalog`）。
