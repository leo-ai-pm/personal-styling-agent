# 运行与部署

## 本地

按根目录 README 配置 `.env` 后执行 `python3 run.py`。默认监听 `127.0.0.1:8765`，使用扩展示例。可覆盖端口：

```sh
python3 run.py --port 8768
```

库存快照在启动和运行时按需刷新为新的模拟快照，不修改原始样例。会话保存到 `reports/m2/runtime/sessions.sqlite3`，默认 24 小时到期；该目录不会进入 Git。需要一次性内存会话时加 `--no-persistence`。

`GET /api/health` 在模型未配置时返回 503，页面仍可以打开并展示配置提示。`ready=true` 表示本地配置与资源已就绪，不代表已完成真实模型远程连通性测试。

## 公网 Demo

GitHub 仓库用于源码分发，GitHub Pages 不能运行本项目的 Python 后端。公网访问需要持续运行的主机和 HTTPS 反向代理。

```sh
python3 run.py --port 8765 --public-origin https://your-demo.example \
  --per-minute 20 --daily-calls 500 --model-concurrency 3
```

将反向代理的公开域名、Host 和 Origin 配置为同一实际域名，代理到 `127.0.0.1:8765`。上面的域名是示例，需要替换。服务默认监听回环地址；容器等需要调整绑定时显式设置 `--bind` 并配置可信入口。

启用 `--public-origin` 后默认不需要邀请码，后台自动签发 Secure / HttpOnly 访客 Cookie，各访客会话隔离。不要给公开部署误配 `YIXU_INVITE_CODE`；该变量仅用于自愿开启的受邀模式。Cookie 需要 HTTPS。

密钥放服务端环境或受保护的凭据文件中，不进入构建产物或静态页面。单进程标准库 HTTP 服务适用于演示；长期开放需要进程守护、TLS 代理、监控和实际负载验证。

请自行管理 API 预算、进程日志和到期数据。该仓库不包含作者的账号配置、隧道地址、数据库、历史对话或云端部署凭据。
