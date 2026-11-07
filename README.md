# sdyxy 直播课自动观看脚本

用 `curl_cffi`（浏览器级 TLS 指纹）+ `invisible_playwright`（C++ 层反检测 Firefox）实现
`http://sdyxy.wx.superchutou.com`（微信移动端 H5）的身份证 + 密码自动登录、
进入「我的教务 → 直播安排」等待直播房间出现并驻留观看。

## 项目结构（uv workspace 多包模式）

```
auto-learn/
├── pyproject.toml              # workspace 根（virtual root, members = core/contracts/browser/sdyxy/app）
├── core/                       # 基础层: 配置(core/config.py) + 错误类型(core/errors.py)
├── contracts/                  # 端口层: LoginDriver 抽象接口 + Cookie/LaunchOptions
│                               #   业务只依赖本接口, 不碰任何浏览器库
├── browser/                    # 能力层: 唯一允许 import invisible_playwright/playwright 的包
│   ├── engine.py               #   StealthBrowser 实例: 主编排开一次, 全程共用
│   └── driver.py               #   LoginDriver 实现 + 全部 CSS 选择器等 DOM 细节
├── sdyxy/                      # 业务层: 只依赖 contracts 端口
│   ├── preflight.py            #   curl_cffi Chrome TLS 指纹预检 + Cookie 预热
│   ├── login.py                #   登录流程编排(填表/提交)
│   ├── session.py              #   会话复用 + 登录态判定(首页白屏/欢迎文本)
│   └── live.py                 #   我的教务 → 直播安排 → 轮询等房间 → 驻留观看
├── app/                        # 装配入口: __main__.py + scheduler.py(周六调度)
└── output/                     # 运行产物: 截图 / storage_state.json / cookies.txt
```

(每个包均为标准 src 布局: `<pkg>/src/<pkg>/…`)

依赖方向: `app → sdyxy(业务) → contracts(端口) ← browser(能力层)`,公共配置在 `core`。

## 环境要求

- Windows x86_64, Python ≥ 3.11（uv workspace 管理）
- 首次运行已下载反检测引擎（~240MB，缓存于 `%LOCALAPPDATA%\invisible-playwright`）

## 使用步骤

1. **配置账号**：`.env` 已存在则直接填；没有就复制 `.env.example` 为 `.env`：

   ```
   SDYXY_ID_CARD=你的身份证号
   SDYXY_PASSWORD=你的密码
   ```

2. **单次运行**（在项目根目录，登录 + 进入直播安排等一次房间）：

   ```
   uv run python -m app
   ```

3. **周六定时观看（推荐）**：常驻调度器，每周六北京时间四个上课时段自动执行：

   | 时段 | 上课时间 | 自动启动 |
   |---|---|---|
   | 1 | 08:30 – 10:00 | 08:28 |
   | 2 | 10:10 – 11:40 | 10:08 |
   | 3 | 14:30 – 16:00 | 14:28 |
   | 4 | 16:10 – 17:30 | 16:08 |

   每个时段开始前 2 分钟自动登录并进入直播安排页，每 5 秒刷新等待房间出现；
   房间出现后尽力点击进入并驻留观看到该时段结束（期间每分钟检查掉线并自动刷新），
   结束后关闭浏览器休眠等待下一时段。

   ```
   uv run python -m app schedule
   ```

   测试模式（不等到周六，15 秒后触发，窗口 3 分钟）：

   ```
   SDYXY_SCHEDULE_TEST=1 SDYXY_ROOM_GRACE_S=20 uv run python -m app schedule
   ```

   开机自启：把上面的命令做成 Windows 计划任务（开机/登录时启动即可，
   调度器自己会等到周六）。注意周六上课时段电脑不能休眠。

3. 脚本流程：
   - `core` 读配置 → `sdyxy/preflight` 以 Chrome TLS 指纹预检并预热 Cookie
   - 主编排打开反检测 Firefox（**全程只开一次**），注入预热 Cookie
   - 业务步骤按顺序复用同一浏览器：`sdyxy/login` 登录 →（后续步骤在 app/__main__.py 接着加）
   - 全部步骤结束才关闭浏览器

## 可用环境变量

| 变量 | 说明 |
|---|---|
| `SDYXY_ID_CARD` | 身份证号（必填） |
| `SDYXY_PASSWORD` | 密码（必填） |
| `SDYXY_LOGIN_URL` | 登录页完整 URL；首页不含登录表单时必填 |
| `SDYXY_BASE_URL` | 站点地址，默认 `http://sdyxy.wx.superchutou.com`（该站只有 http，443 是 404） |
| `SDYXY_HEADLESS` | `1`=无头模式（默认 `0` 有头；定时任务建议 `1`） |
| `SDYXY_TIMEOUT_MS` | 页面操作超时，默认 30000 |
| `SDYXY_ROOM_WAIT_S` | 单次模式等房间的最长秒数，默认 3600 |
| `SDYXY_ROOM_GRACE_S` | （仅测试模式用）定时模式已改为到下课时刻硬关，不为拖堂延长 |
| `SDYXY_SCHEDULE_TEST` | `1`=调度器测试模式（15 秒后触发，窗口 3 分钟） |

## 常见问题

- **"页面上没找到密码输入框"**：该站首页可能是跳转页，请人工在浏览器里找到登录页 URL，
  填入 `SDYXY_LOGIN_URL` 再运行。
- **登录后停在原页面**：查看 `output/after_login.png` 截图确认是验证码、密码错误还是登录
  成功但 URL 未变。
- **免登录复用**：下次可把 `output/storage_state.json` 传给 `new_context(storage_state=...)`。

## 已知限制

- **该站只有 http**：https 证书不覆盖 `sdyxy.wx.` 二级域名且 443 返回 404，脚本已按 http 处理。
- **会话无法跨进程复用**：登录态由服务端绑定（实测保存 Cookie 后冷启动仍被跳回登录页），
  所以每次运行/每个时段都会重新登录（已验证稳定，无验证码）。

## 直播间机制（反编译实测）

- 列表接口 `GET /service/supercollegeapi/ArrangeCurriculum/GetLiveCurriculumList?StuID=…`
  返回全部课程（含未开始/已结束），Status：1=进行中，2=已结束，3=未开始。
  脚本直接轮询该接口按 Status==1 判定开播，比刷新页面找文本可靠。
- 直播是**保利威**（LiveType=0，LiveDomain=sdyxy.web.superchutou.com），
  房间 URL 可直接构造：`https://<LiveDomain>/polyv/?channelId=<ID>&name=…&userid=<StuID>&viewerid=<StuID>`，
  脚本进房不依赖页面按钮，也无需处理新标签页。
- 未开播的房间页显示倒计时+"预约直播"，开播后同一 URL 自动变播放器+聊天室；
  脚本观看期间检测到"当前未直播"会定期刷新拉起画面。
- 已实测用明天的真实课程（ChannelId 8743124）打开房间页成功，倒计时正常显示。

## 风险提示

仅用于登录你本人有权限使用的账号。请遵守目标网站的服务条款及所在地法律法规。
