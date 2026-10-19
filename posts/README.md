# posts/

Pages Claude writes for the owner's Discord channels (10-19: webhooks only, one-way). Each is sent by
`.github/workflows/post.yml` → `python -m hero.discord_post <channel> posts/<channel>/<file>.md`.

| 频道 | 文件夹 | GitHub 密钥 | 什么时候 |
|---|---|---|---|
| #今日跟单 | posts/follow/ | DC_WEBHOOK_FOLLOW | 交易日 09:48 ET |
| #财报预测 | posts/earnings/ | DC_WEBHOOK_EARNINGS | 交易日 15:05 ET 判断推送后 |
| #每日总结 | posts/daily/ | DC_WEBHOOK_DAILY | 午盘 12:22、收盘 17:25 交流后 |
| #研究和规则 | posts/research/ | DC_WEBHOOK_RESEARCH | 有研究结果或规则改动时 |
| #成交（已有） | — | DC_WEBHOOK | 每个交易周期自动（hero/notify.py） |

写法：第一行 `# 标题`，每个 `## 小标题` 一张卡片；表格会变成「a · b · c」行；每张卡片底部自动加
「Alpaca 模拟盘，参考记录，不是投资建议」。页面里像钥匙、订单 / 账户 ID、webhook 地址的内容会被拒发。
密钥没设的频道自动跳过。
