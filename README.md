# Epic 免费游戏提醒

检查 Epic 美国区的公开促销目录，通过 Server酱、飞书、ntfy 或钉钉推送免费游戏和开始时间预告。当前价格为零的限免活动会附上领取链接。多款游戏可一起打开 Epic 结账页，在浏览器中登录并确认领取。

项目使用 Python 3.12+ 和标准库。

## 配置通知

在仓库的 **Settings → Secrets and variables → Actions** 添加所需渠道。配置多个渠道时会逐个发送。

| Secret | 用途 |
| --- | --- |
| `SERVERCHAN_KEY` | Server酱 Turbo 的 SendKey |
| `FEISHU_WEBHOOK` | 飞书群自定义机器人的 Webhook |
| `FEISHU_SECRET` | 飞书启用签名校验时使用的密钥 |
| `NTFY_TOPIC` | ntfy 订阅主题 |
| `DINGTALK_WEBHOOK` | 钉钉群自定义机器人的 Webhook |

自建 ntfy 可在 **Variables** 添加 `NTFY_BASE`。默认服务地址为 `https://ntfy.sh`，本地运行时同样可以从环境变量传入。

## 运行

- 周报在每周四 23:17 UTC 检查（北京时间周五 07:17）。
- 12 月和 1 月 1–8 日每天检查最近 25 小时内开始的限免。当天与周报重叠时由周报发送。
- 在 **Actions → Epic 免费游戏提醒 → Run workflow** 可以手动发送周报。
- 公开仓库的定时任务可能因 60 天无活动被停用。仓库保活工作流在每月 1 日和 16 日提交活动记录。[GitHub 定时任务文档](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

本地读取实时目录并预览消息。

```bash
python3 scripts/epic_free_games.py --dry-run
```

本地运行测试，使用模拟促销和通知响应。

```bash
python3 -m unittest discover -s tests
```

## 排查

目录接口失败时，检查任务会记录 HTTP 状态或解析错误。某个渠道发送失败时，其余已配置渠道会继续尝试。失败记录会显示渠道名称和 HTTP 状态或业务错误码，便于在对应平台核对配置。
