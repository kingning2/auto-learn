# -*- coding: utf-8 -*-
"""直播业务: 首页 -> 我的教务 -> 直播安排 -> API 轮询 -> 进入房间 -> 驻留观看.

平台知识(来自 liveTeaching 分包反编译 + 实测):
  - 列表接口: GET /service/supercollegeapi/ArrangeCurriculum/GetLiveCurriculumList?StuID=<StuID>
    返回 Data: [{CuName, PlanList: [{ProductName, StreamTime, Status, ChannelId,
    LiveDomain, LiveType, HasPlayBackFiles, ...}]}]
  - Status 枚举: 1=进行中(playing), 2=已结束(end), 3=未开始(noStart)
  - 进入按钮"进入直播"只在 Status==1 时渲染, 点击为 window.open 新标签:
    LiveType==1(腾讯): /pages/liveRoom/indexTencent?channelId=<ChannelId>
    其他(保利威): https://<LiveDomain>/polyv/?channelId=<ChannelId>&name=<Name>
                 &userid=<StuID>&avatar=<HeadImg>&viewerid=<StuID>
  - 保利威房间页可直接构造 URL 打开, 未开播显示"当前未直播/预约直播",
    开播后同一 URL 变成播放器; 开场前打开不会自动切到直播画面, 需要轮询刷新.

动作全部走 LoginDriver 端口(evaluate 用于同源 fetch 与读 localStorage).
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, time as dtime, timezone, timedelta
from urllib.parse import quote

from contracts.browser_port import LoginDriver
from core.config import Settings
from core.errors import LoginError
from core.notify import notify, notify_error, notify_image, notify_info

# 页面入口文案
MY_EDU_ENTRY = "我的教务"
LIVE_ENTRY = "直播安排"

# 直播列表接口(相对路径, 在站点同源页面内 fetch, 自动带 sessionId Cookie)
LIVE_LIST_API = "/service/supercollegeapi/ArrangeCurriculum/GetLiveCurriculumList"
STATUS_PLAYING = 1  # 1=进行中, 2=已结束, 3=未开始

# 轮询参数: 每 5 秒查一次列表接口, 最长等待默认 1 小时(可用 SDYXY_ROOM_WAIT_S 覆盖)
REFRESH_INTERVAL_S = 5
DEFAULT_ROOM_WAIT_S = 3600

# 北京时间(固定 UTC+8, 无夏令时)
BEIJING = timezone(timedelta(hours=8))

# 房间出现后, 等待/观看的额外宽限(课程准点下课后老师可能延时几分钟)
# 可用 SDYXY_ROOM_GRACE_S 覆盖(测试模式把它调小, 避免长轮询)
ROOM_GRACE_S = int(os.getenv("SDYXY_ROOM_GRACE_S", str(10 * 60)))

# 观看阶段检查间隔(秒)
WATCH_CHECK_S = 60

# 房间页"未开播"特征文本(出现则说明播放器还没起来, 定期刷新拉起直播画面)
ROOM_NOT_LIVE_TEXT = "当前未直播"
# 保利威房间页在主播关播后显示的文案(与开播前的"当前未直播"不同)
LIVE_END_TEXT = "当前暂无直播"

# 连续判定会话失效的失败次数阈值
SESSION_FAIL_LIMIT = 4


def human_duration(seconds: int) -> str:
    """秒数人性化: 40 -> '40秒', 125 -> '2分05秒', 3750 -> '1小时02分'."""
    if seconds < 60:
        return f"{seconds}秒"
    if seconds < 3600:
        return f"{seconds // 60}分{seconds % 60:02d}秒"
    return f"{seconds // 3600}小时{(seconds % 3600) // 60:02d}分"


def open_my_edu(driver: LoginDriver, settings: Settings) -> None:
    """登录后首页: 点击'我的教务'宫格入口."""
    print("[直播] 点击 我的教务…")
    if not driver.click_text(MY_EDU_ENTRY):
        driver.screenshot(settings.output_dir / "no_my_edu.png")
        raise LoginError(f"找不到'{MY_EDU_ENTRY}'入口, 已截图 output/no_my_edu.png")
    print(f"       已进入: {driver.url}")


def open_live_schedule(driver: LoginDriver, settings: Settings) -> None:
    """我的教务页: 点击'直播安排'列表项."""
    print("[直播] 点击 直播安排…")
    if not driver.click_text(LIVE_ENTRY):
        driver.screenshot(settings.output_dir / "no_live_entry.png")
        raise LoginError(f"找不到'{LIVE_ENTRY}'入口, 已截图 output/no_live_entry.png")
    print(f"       已进入: {driver.url}")


def _user_info(driver: LoginDriver) -> dict:
    """从 localStorage 读 userInfo(StuID/Name/HeadImg), 失败则空字段."""
    raw = driver.evaluate(
        "() => { try { return localStorage.getItem('userInfo') || '{}'; } catch { return '{}'; } }"
    )
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        data = {}
    return {
        "StuID": data.get("StuID", ""),
        "Name": data.get("Name", ""),
        "HeadImg": data.get("HeadImg", ""),
    }


def fetch_live_list(driver: LoginDriver, stuid: str) -> list | None:
    """同源 fetch 直播列表. 返回 Data 列表; 会话失效/异常返回 None."""
    script = (
        f"() => fetch('{LIVE_LIST_API}?StuID={stuid}')"
        ".then(r => r.text()).catch(e => 'FETCH_ERR')"
    )
    body = driver.evaluate(script)
    if not body or body == "FETCH_ERR":
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("SuccessResponse"):
        return None
    result = data.get("Data")
    # 接口异常时 Data 可能是字符串/对象而非列表 —— 统一拦住, 避免下游 _flatten 炸
    if not isinstance(result, list):
        print(f"[直播] 列表接口返回异常形状({type(result).__name__}), 下轮重试")
        return None
    return result


def _flatten_plans(live_list: list) -> list[dict]:
    """Data: [{PlanList: [...]}] 拍平成课程计划列表, 补充所属课程名."""
    plans = []
    for cu in live_list:
        if not isinstance(cu, dict):
            continue
        for p in cu.get("PlanList", []) or []:
            if not isinstance(p, dict):
                continue
            p["CuName"] = cu.get("CuName", "")
            plans.append(p)
    return plans


def _parse_stream_range(stream_time: str) -> tuple[dtime, dtime] | None:
    """解析 StreamTime 如 '2026/11/07 08:30 - 10:00' -> (08:30, 10:00)."""
    m = re.search(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", stream_time or "")
    if not m:
        return None
    try:
        s = dtime(*map(int, m.group(1).split(":")))
        e = dtime(*map(int, m.group(2).split(":")))
        return s, e
    except ValueError:
        return None


def _pick_playing(plans: list[dict], now: datetime) -> dict | None:
    """从进行中课程里挑一个: 优先当前时刻落在 StreamTime 区间内的(处理
    上一节拖堂/晚开播导致两节课同时'进行中'的情形), 否则取列表第一个."""
    playing = [p for p in plans if p.get("Status") == STATUS_PLAYING]
    if not playing:
        return None
    for p in playing:
        rng = _parse_stream_range(p.get("StreamTime", ""))
        if rng and rng[0] <= now.time() <= rng[1]:
            return p
    return playing[0]


def wait_for_playing(
    driver: LoginDriver, settings: Settings, max_wait_s: int
) -> dict:
    """轮询直播列表接口, 直到出现 Status==1(进行中)的课程, 返回该计划 dict.

    比刷新页面找文本可靠: 直接消费列表 API, 精确按状态字段判定.
    每分钟打印一次待上课程(未开始列表), 便于观察.

    Raises:
        LoginError: 超时仍未出现, 或会话连续失效.
    """
    userinfo = _user_info(driver)
    stuid = userinfo["StuID"]
    print(f"[直播] 每 {REFRESH_INTERVAL_S} 秒轮询列表接口, 等待'进行中'课程(最长 {max_wait_s} 秒)…")
    deadline = time.time() + max_wait_s
    n = 0
    fails = 0
    while time.time() < deadline:
        live_list = fetch_live_list(driver, stuid)
        if live_list is None:
            fails += 1
            if fails >= SESSION_FAIL_LIMIT:
                driver.screenshot(settings.output_dir / "live_list_dead.png")
                raise LoginError(
                    "直播列表接口连续失效(登录态可能过期), 已截图 output/live_list_dead.png"
                )
            driver.reload()  # 拉回站点上下文, 顺带刷新会话 Cookie
        else:
            fails = 0
            plans = _flatten_plans(live_list)
            picked = _pick_playing(plans, datetime.now(BEIJING))
            if picked:
                p = picked
                print(
                    f"\n[成功] 直播进行中! {p.get('CuName','')} | {p.get('ProductName','')}"
                    f" | {p.get('StreamTime','')} | ChannelId={p.get('ChannelId')}"
                )
                return p
            n += 1
            if n % 12 == 0:  # 每分钟报一次进度 + 未开始课程概要
                upcoming = [
                    f"{p.get('StreamTime','')}"
                    for p in plans
                    if p.get("Status") == 3
                ][:3]
                print(f"       …已等待 {n * REFRESH_INTERVAL_S} 秒, 未开始: {upcoming}")
        time.sleep(REFRESH_INTERVAL_S)

    driver.screenshot(settings.output_dir / "room_timeout.png")
    # 群通知: 没等到直播(带实际等待时长 + 截图), 需要人工确认
    notify_image(
        settings.output_dir / "room_timeout.png",
        f"❗ 等待 {human_duration(max_wait_s)} 仍无进行中直播，"
        f"本时段可能未开播或开播延迟，请人工确认是否需要介入",
    )
    raise LoginError(f"等待 {human_duration(max_wait_s)} 仍无'进行中'直播, 已截图 output/room_timeout.png")


def room_url_for(plan: dict, userinfo: dict, base_url: str) -> str:
    """按分包反编译逻辑构造房间 URL(与页面 window.open 完全一致)."""
    if plan.get("LiveType") == 1:  # 腾讯直播: 同源路由
        return f"{base_url}/pages/liveRoom/indexTencent?channelId={plan['ChannelId']}"
    # 保利威直播: 外部 LiveDomain
    return (
        f"https://{plan['LiveDomain']}/polyv/?channelId={plan['ChannelId']}"
        f"&name={quote(userinfo.get('Name') or 'student', safe='')}"
        f"&userid={userinfo['StuID']}"
        f"&avatar={quote(userinfo.get('HeadImg') or '', safe='')}"
        f"&viewerid={userinfo['StuID']}"
    )


def enter_room(driver: LoginDriver, settings: Settings, plan: dict) -> str:
    """点击「进入直播」按钮进房间(真实用户路径), 返回房间 URL.

    按钮 click 后 window.open 新标签页, 用 switch_to_new_tab 接管;
    被拦截降级为同页导航时直接沿用当前页.
    """
    print("[观看] 点击「进入直播」按钮…")
    if not driver.click_text("进入直播"):
        raise LoginError("找不到「进入直播」按钮, 已截图 output/no_enter_btn.png")
    switched = driver.switch_to_new_tab(timeout_ms=15000)
    url = driver.url
    print(f"       {'新标签页' if switched else '当前页'}: {url}")

    # 等播放器 SDK 初始化(一般 3~8 秒), 出现 video 元素即就绪
    video_ready = False
    for i in range(15):
        v = driver.evaluate(
            "() => {const x=document.querySelector('video');"
            "return x?{paused:x.paused,ready:x.readyState}:null}"
        )
        if v and v != "null":
            video_ready = True
            print(f"       播放器就绪({i + 1}s): {v}")
            break
        time.sleep(1)
    if not video_ready:
        print("       [警告] 15 秒内播放器未就绪(可能未开播或 SDK 未加载)")

    # 等真正开始播放: JS 只返回简单字符串, 避免 evaluate 对象转 str 后误用 .get
    played = False
    for i in range(30):
        state = driver.evaluate(
            "() => {const x=document.querySelector('video');"
            "return (x && !x.paused && x.currentTime > 0) ? 'PLAYING' : 'NOT'}"
        )
        if state == "PLAYING":
            played = True
            print(f"       直播播放中({i + 1}s)")
            break
        time.sleep(1)
    if played:
        driver.screenshot(settings.output_dir / "playback_started.png")
        notify_image(
            settings.output_dir / "playback_started.png",
            f"▶️ 直播播放中: {driver.evaluate('() => document.title') or ''}",
        )
    else:
        # 已进房但画面始终没播出来 —— 算错误, 需要人工看一眼
        driver.screenshot(settings.output_dir / "playback_stuck.png")
        notify_error(Exception("进入直播间后 30 秒内画面未开始播放, 请人工确认"))

    driver.screenshot(settings.output_dir / "room_entered.png")
    title = driver.evaluate("() => document.title")
    print(f"       房间页标题: {title}")
    return url


def stay_in_room(driver: LoginDriver, settings: Settings, until_epoch: float | None) -> None:
    """观看驻留: 保持房间页打开直到 until_epoch(None 则直到手动 Ctrl+C).

    每分钟检查:
      - 页面白屏(可能掉线) -> reload 拉回
      - 房间页还显示'当前未直播'(开场前打开/直播中断恢复页) -> reload 拉起直播画面
    每 10 分钟滚动覆盖截图 output/watching.png, 方便事后确认.
    """
    if until_epoch is not None:
        remaining = until_epoch - time.time()
        print(f"[观看] 进入观看模式, 驻留到截止时间(剩余 {remaining / 60:.0f} 分钟)…")
    else:
        print("[观看] 进入观看模式(单次运行: 手动 Ctrl+C 结束)…")
    n = 0
    end_hits = 0  # 连续命中"直播结束"文案的次数(防切流抖动, 连续2次才确认)
    stall_hits, last_t, resumed = 0, None, False  # 无声卡死检测状态
    while until_epoch is None or time.time() < until_epoch:
        try:
            time.sleep(WATCH_CHECK_S)
        except KeyboardInterrupt:
            driver.screenshot(settings.output_dir / "watch_end.png")
            print("\n[观看] 手动结束观看. 截图: output/watch_end.png")
            return
        if until_epoch is not None and time.time() >= until_epoch:
            break
        n += 1
        remaining = None if until_epoch is None else until_epoch - time.time()
        try:
            # 会话被顶/踢(如用户自己在别的设备登录): 页面跳回登录页,
            # 必须抛错让调度器重新登录, 否则会对着登录页"看"到下课
            if (
                driver.has_password_field()
                or "/pages/login" in driver.url
            ):
                raise LoginError("会话被顶掉(检测到登录页), 需要重新登录")
            if driver.is_blank():
                print(f"[观看] 页面白屏(可能掉线), 刷新拉回… {fmt_remaining(remaining)}")
                notify_info("⚠️ 页面白屏(可能掉线)，已自动刷新拉回", dedup=True)
                driver.reload()
                end_hits = 0
            elif driver.evaluate(
                f"() => document.body && document.body.innerText.includes('{LIVE_END_TEXT}')"
            ) == "True":
                # 主播关播: 连续 2 次命中(~10-15s)才确认, 避免切流瞬间的误判
                end_hits += 1
                if end_hits >= 2:
                    driver.screenshot(settings.output_dir / "live_ended.png")
                    print(f"[观看] 直播已结束(页面显示'{LIVE_END_TEXT}'), 提前收工."
                          f" 截图: {settings.output_dir / 'live_ended.png'}")
                    notify_info("📺 直播已结束，本时段提前收工")
                    return
                print(f"[观看] 疑似直播结束({end_hits}/2), 再确认一轮…")
            elif driver.evaluate(
                f"() => document.body && document.body.innerText.includes('{ROOM_NOT_LIVE_TEXT}')"
            ) == "True":
                print(f"[观看] 房间未开播/画面未拉起, 刷新… {fmt_remaining(remaining)}")
                notify_info("⚠️ 房间画面未拉起，已自动刷新", dedup=True)
                driver.reload()
            else:
                # 无声卡死检测: video 暂停且进度不动(排除'休息一会'等有文案的暂停,
                # 文案暂停会自己恢复, 不算卡死)。连续 3 轮(~15s)不动先 play(),
                # 仍不动再刷新。正常播放/暂停恢复时进度都在走。
                vid = driver.evaluate(
                    "() => {const x=document.querySelector('video');"
                    "return x ? (x.paused ? 'P' + x.currentTime : 'R') : 'NO'}"
                )
                if vid == "R" or not vid:
                    stall_hits, last_t, resumed = 0, None, False
                elif vid == "NO":
                    pass  # 播放器重建中/休息页可能藏住 video, 交给文案分支处理
                elif vid.startswith("P"):
                    t = vid[1:]
                    if t == last_t:
                        stall_hits += 1
                    else:
                        stall_hits, resumed = 0, False
                    last_t = t
                    if stall_hits == 3 and not resumed:
                        print("[观看] 画面疑似卡住(暂停且进度不动), 尝试恢复播放…")
                        driver.evaluate(
                            "() => {const x=document.querySelector('video');"
                            "if(x && x.play){try{x.play()}catch(e){}}}"
                        )
                        resumed = True
                    elif stall_hits >= 6:
                        print("[观看] 恢复播放无效, 刷新房间拉回…")
                        notify_info("⚠️ 直播画面卡住，已自动刷新恢复", dedup=True)
                        driver.reload()
                        stall_hits, last_t, resumed = 0, None, False
                # 每 5 分钟报一次进度并滚动截图(不发群, 只本地留档)
                if n % 5 == 0:
                    title = driver.evaluate("() => document.title")
                    print(f"[观看] 直播观看中… {fmt_remaining(remaining)} 页面: {title}")
                    driver.screenshot(settings.output_dir / "watching.png")
        except Exception as exc:  # noqa: BLE001 —— 观看期间页面崩溃也要拉回
            print(f"[观看] 检查异常({exc!r}), 尝试回到房间列表重新等直播…")
            notify_error("观看期间页面异常，正在尝试自动恢复", exc)
            try:
                driver.open(f"{settings.base_url}/pages/myEducationalAdmin/liveTeaching")
            except Exception:
                pass
    driver.screenshot(settings.output_dir / "watch_end.png")
    print(f"[观看] 到达截止时间, 观看结束. 截图: {settings.output_dir / 'watch_end.png'}")
    # 群通知: 本时段正常收工
    notify_info("✅ 本时段观看结束(到达下课时间)")


def fmt_remaining(remaining: float | None) -> str:
    return "" if remaining is None else f"剩余 {remaining / 60:.0f} 分钟"


def goto_live_room(
    driver: LoginDriver, settings: Settings, watch_until_epoch: float | None = None
) -> str:
    """登录后的完整链路: 我的教务 -> 直播安排 -> 等进行中直播 -> 进房间 -> 驻留观看.

    Args:
        watch_until_epoch: 定时模式 —— 观看截止时间戳, 轮询上限顺延到 截止+宽限;
                           None(单次模式)则等到出现直播后进房间, 驻留到手动停止.

    Returns:
        房间 URL.
    """
    open_my_edu(driver, settings)
    open_live_schedule(driver, settings)
    if watch_until_epoch is not None:
        # 定时模式: 轮询上限就是下课时刻, 不为拖堂延长 —— 到点硬关
        max_wait_s = int(watch_until_epoch - time.time())
    else:
        max_wait_s = int(os.getenv("SDYXY_ROOM_WAIT_S", str(DEFAULT_ROOM_WAIT_S)))

    plan = wait_for_playing(driver, settings, max_wait_s)
    url = enter_room(driver, settings, plan)
    stay_in_room(driver, settings, watch_until_epoch)
    return url
