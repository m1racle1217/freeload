# 开发进度

## 2026-05-13

- [x] 步骤 1-12：项目骨架搭建完成，已推送 GitHub
- [x] 修复: auth.py 登录 URL + 等待策略 + 检测频率
- [x] 修复: 登录检测逻辑，根据平台特征 cookie 判断而非数量
- [x] 修复: 登录改为手动确认（扫码后按 Enter），彻底避免误判
- [x] 修复: Web 面板 Internal Server Error（TemplateResponse 参数顺序错误）
- [x] 修复: 硬编码端口 9527 在启动日志中（改为读取配置实际值）
- [x] 增强: 端口冲突时自动 +1 寻找可用端口
- [x] UI: 全新深色侧边栏设计，更现代的卡片、响应式布局
- [x] 功能: Web 页面扫码登录（浏览器截图 → 前端显示 → 自动检测登录）
- [x] 改进: 平台页面显示各平台登录状态 + 一键登录入口
- [x] 修复: 配置页 Internal Server Error（daemon 未传入模板上下文）
- [x] 修复: 登录 URL（淘宝/拼多多/小程序）更正
- [x] 修复: Web 登录反 headless 检测（UA + webdriver 隐藏）
- [x] 修复: 日志页无记录（API 合并 DB + Executor 内存数据）
- [x] 修复: 淘宝/拼多多 headless 拦截 → 前端提示 CLI 命令，浏览器资源泄漏修复
- [x] 增强: CLI 登录加入反检测（UA 伪装 / navigator.webdriver 隐藏 / 防自动化标记）
- [x] 增强: CLI 登录 URL 被拒时自动尝试备选地址
- [x] 增强: CLI 登录失败时给出平台特定解决方案提示
- [x] 增强: 启动时自动检测旧 freeload 实例 (9527/9528) 并 kill 重启
- [x] 新增: playwright-stealth 集成（修补 20+ 检测向量）
- [x] 新增: src/stealth.py — 统一反检测模块（插件化，所有组件共用）
- [x] 新增: 自动检测系统 Chrome/Edge，内置浏览器被拦截时自动切换
- [x] 改进: auth.py CLI 登录重构 — 去重代码 + stealth + 系统浏览器备用
- [x] 改进: web/server.py Web 登录用 stealth 模块替代手写反检测
- [x] 改进: browser.py BrowserPool 创建 context 时应用 stealth
- [x] 改进: jd_watcher.py 秒杀检测使用 BrowserPool + stealth
- [x] 修复: login.py Windows 终端 emoji 编码问题
- [x] 修复: server.py login 路由浏览器资源泄漏（dir() 错误用法）
- [x] 验证: 京东扫码登录可用（CLI + Web）
- [ ] 已知: 淘宝/拼多多为网络层拦截（IP/地区），非 JS 反检测可解，需手动导出 cookie 或使用代理
- [x] 新增: src/handlers.py — 京东任务处理器（签到/秒杀/领券）
- [x] 改进: daemon.py 启动时注册处理器到执行引擎

## 2026-05-14

- [x] 修复: 风控验证流程 — 触发时即时邮件提醒，超时统一退出
- [x] 增强: 京东 handler 增加速率限制（8-20s 随机延迟），降低风控触发率
- [x] 增强: 首次执行前进行会话预热（访问 m.jd.com + 模拟滚动）
- [x] 增强: 验证完成后自动重试一次，避免错过单次窗口
- [x] 调整: 京东轮询间隔 30s → 90s，验证超时 120s → 180s（可配置）
- [x] 新增: src/stealth.py HumanBehavior — 随机延迟/鼠标抖动/滚动/预热
- [x] 重构: JDInteractiveMixin 接收 config + notifier 注入
- [x] 修复: test_handlers.py 断言适配会话预热新行为
- [ ] 已知: 风控完全自动绕过不在范围内，依赖手动完成验证
- [ ] 搁置: 淘宝/拼多多仍受网络层拦截，本轮不处理

## 当前状态

- **核心通路**: 京东签到/领券/秒杀 + 限速 + 会话预热 + 验证通知 + 重试一次
- **风控策略**: 降低触发率为主，触发后通知人工处理为辅
- **未解决**: 淘宝/拼多多网络层拦截
- **待补充**: 邮件 SMTP 配置（用户侧）

## 最后更新

2026-05-14 — 渐进修复风控验证卡点
