# 🐑 Freeload — 薅羊毛自动化

自动监控各大电商平台（京东、淘宝、拼多多），发现羊毛机会后第一时间薅取，只下单不付款，通过邮件通知你去付款。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt
python -m playwright install chromium

# 2. 启动守护进程
python src/daemon.py

# 3. 打开 Web 面板，在面板中扫码登录各平台
# http://localhost:9528
# 平台 → 点击"登录"按钮 → 扫码 → 自动检测完成
```

也可以命令行单独登录：
```bash
python src/login.py -p jd       # 京东
python src/login.py -p taobao   # 淘宝
python src/login.py -p pdd      # 拼多多
```

## 架构

```
┌─────────┐  ┌─────────┐  ┌─────────┐
│ 京东     │  │ 淘宝     │  │ 拼多多   │
│ Watcher  │  │ Watcher  │  │ Watcher  │
└────┬────┘  └────┬────┘  └────┬────┘
     └────────────┬─────────────┘
                  ▼
          ┌──────────────┐
          │  事件队列      │
          │  (优先级排序)   │
          └──────┬───────┘
                 ▼
          ┌──────────────┐
          │  执行引擎      │
          │  (Playwright) │
          └──────┬───────┘
                 ▼
          ┌──────────────┐
          │  邮件通知      │
          └──────────────┘
```

- **常驻运行**：不是定时任务，而是持续监控
- **事件驱动**：发现羊毛立刻出手，高价值事件优先
- **下单不付款**：抢到后帮你锁库存，你去付款
- **邮件通知**：抢到后面发送邮件含订单号、金额、截止时间
- **Web 面板**：浏览器访问 http://localhost:9527 查看运行状态

## Web 面板

访问 `http://localhost:9528`：

| 页面 | 路由 | 说明 |
|------|------|------|
| 📊 仪表盘 | `/` | 收益统计、Watcher 状态、最近事件 |
| 📡 平台 | `/platforms` | 各平台运行详情、登录状态、扫码登录入口 |
| ⚙️ 配置 | `/config` | 配置文件 + 环境变量预览 |
| 📋 日志 | `/logs` | 可筛选的任务历史记录 |
| 🔑 登录 | `/login/{platform}` | 截图式扫码登录 |

## 配置

编辑 `config/config.yaml` 或用环境变量覆盖：

| 环境变量 | 说明 |
|---------|------|
| `FREELOAD_EMAIL_FROM` | 发件邮箱（SMTP） |
| `FREELOAD_EMAIL_PASS` | 邮箱授权码 |
| `FREELOAD_EMAIL_TO` | 收件邮箱 |

## 项目结构

```
freeload/
├── src/
│   ├── daemon.py         # 主守护进程
│   ├── event.py          # 事件模型与优先级队列
│   ├── auth.py           # 登录管理
│   ├── login.py          # 手动登录 CLI
│   ├── browser.py        # 浏览器池
│   ├── config.py         # 配置加载
│   ├── executor.py       # 执行引擎
│   ├── storage.py        # SQLite 存储
│   ├── watchers/         # 各平台监控器
│   ├── notify/           # 通知模块
│   └── web/              # Web 管理面板
├── config/
│   └── config.yaml       # 配置文件
└── cookies/              # cookie 持久化
```
