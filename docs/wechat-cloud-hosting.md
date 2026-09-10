# 微信云托管部署

本后端应部署为一个常驻的微信云托管容器，不要拆成普通云函数。服务同时承载
REST、WebSocket 和进程内的实时房间状态。

## 创建服务

1. 在与小游戏相同的云开发环境中创建云托管服务。
2. 代码目录选择后端仓库根目录，使用仓库内 `Dockerfile` 构建。
3. 配置最小实例数 `1`、最大实例数 `1`，关闭缩容到零。
4. 健康检查使用 `GET /api/health`，就绪检查使用 `GET /api/ready`。
5. 容器端口不写死；平台注入的 `PORT` 会由 Docker 启动命令读取。

必须保持单实例、单 worker。房间注册表、WebSocket 连接和对局协程当前都在 Python
进程内；直接扩容会让创建、加入和连接请求落到不同实例。需要多实例时，应先把实时
房间状态迁移到 Redis，并设计房间级路由或跨实例消息总线。

## 环境变量

在云托管密钥或环境变量中配置：

```text
REQUIRE_POSTGRES=true
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DATABASE?sslmode=require
WECHAT_APP_ID=<小游戏 AppID>
WECHAT_APP_SECRET=<小游戏 AppSecret>
WECHAT_TOKEN_SECRET=<至少 32 字节随机密钥>
ROOM_INVITE_SECRET=<至少 32 字节、与登录令牌不同的随机密钥>
ROOM_INVITE_TTL_SECONDS=900
ROOM_MAX=<容量评估后的房间数>
DOCS_SHOW=False
LOG_TO_FILE=0
```

若服务在微信云托管中启用了「云调用 → 开放接口服务」，平台会在容器内代理
`api.weixin.qq.com` 并使用自签名证书。为使微信 `code2Session` 登录请求走该平台内代理，
还需配置：

```text
WECHAT_CODE2SESSION_URL=http://api.weixin.qq.com/sns/jscode2session
```

这只适用于微信云托管内部调用；其他部署环境不要设置该变量，默认仍使用公开的 HTTPS 地址。

也可以不设置 `DATABASE_URL`，改用 `PG_HOST`、`PG_PORT`、`PG_USER`、
`PG_DATABASE`、`PG_PASSWORD` 和 `PG_SSLMODE=require`。生产环境设置
`REQUIRE_POSTGRES=true` 后，如果遗漏 PostgreSQL 配置，容器会启动失败，而不会静默
回退到临时 SQLite 文件。

所有密钥只放云托管配置，不写入镜像、小游戏包或 Git。数据库应允许云托管环境访问，
并强制 TLS。

## 小游戏连接

首版使用云托管提供的正式 HTTPS/WSS 访问地址，保持客户端现有的 `wx.request` 和
`wx.connectSocket` 适配器不变：

```text
VITE_WECHAT_API_BASE=https://<云托管访问域名>
```

在微信公众平台配置 request、socket 和 download 合法域名。REST 通过 `/api/*`，
WebSocket 通过 `/ws/room/*`；反向代理必须保留 `Authorization` 请求头和 WebSocket
Upgrade。若所选云环境只允许 `wx.cloud.callContainer` 等专用调用方式，则需要另加一层
CloudBase HTTP/WebSocket transport，不能只替换 API 地址。

## 发布和验证

每次发布会终止旧容器中的活跃牌局。当前版本在关闭阶段先让 `/api/ready` 返回 503、
拒绝创建新房间，再关闭房间任务和连接；它不会跨部署恢复进行中的牌局。因此应在低峰
发布，并在客户端显示断线和房间失效提示。

上线前至少验证：

- 微信登录、建房、分享、三位好友从卡片加入；
- 四台真机完成东风场和半庄场；
- 前后台切换、Wi-Fi/蜂窝网络切换和 WebSocket 重连；
- 邀请过期、房满、重复打开邀请和容器重启；
- `/api/health` 始终用于存活探测，`/api/ready` 在下线时返回 503；
- PostgreSQL 中能查询到房间、场次和结算记录。
