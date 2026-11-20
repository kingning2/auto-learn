//! sdyxy 直播自动观看 · Rust 常驻看门狗
//!
//! 职责:
//!   1. 每 5 分钟检查 scheduler.py 是否存活(读 output/scheduler.pid + OpenProcess 验证),
//!      死了就静默拉起(CREATE_NO_WINDOW)
//!   2. 周六上课时段([start-2min, end+10min])调 SetThreadExecutionState 防休眠
//!   3. 所有动作写 output/watchdog-rs.log
//!
//! 设计说明: 本机用进程名/命令行枚举(sysinfo, 甚至 Get-Process)检测不可靠,
//! 因此存活检测改用 PID 文件 + OpenProcess; 单实例守卫用命名互斥量。

#![windows_subsystem = "windows"] // 无控制台窗口(GUI 子系统)

use std::fs::OpenOptions;
use std::os::windows::process::CommandExt;
use std::path::Path;
use std::process::Command;
use std::time::Duration;

use chrono::{DateTime, Datelike, NaiveTime, Timelike, Utc};
use tokio::time::{interval, MissedTickBehavior};
use windows_sys::Win32::Foundation::{CloseHandle, ERROR_ALREADY_EXISTS, GetLastError};
use windows_sys::Win32::System::Power::{
    SetThreadExecutionState, ES_CONTINUOUS, ES_SYSTEM_REQUIRED,
};
use windows_sys::Win32::System::Threading::{
    CreateMutexW, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
};

const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const ROOT: &str = concat!(env!("CARGO_MANIFEST_DIR"), "\\..");
const RS_LOG: &str = concat!(env!("CARGO_MANIFEST_DIR"), "\\..\\output\\watchdog-rs.log");
const SCHED_LOG: &str = concat!(env!("CARGO_MANIFEST_DIR"), "\\..\\output\\scheduler.log");
const SCHED_PID: &str = concat!(env!("CARGO_MANIFEST_DIR"), "\\..\\output\\scheduler.pid");
const VENV_PY: &str = concat!(env!("CARGO_MANIFEST_DIR"), "\\..\\.venv\\Scripts\\python.exe");
const SCHEDULER: &str = concat!(env!("CARGO_MANIFEST_DIR"), "\\..\\app\\src\\app\\scheduler.py");

/// 周六上课时段: (开课, 下课) 北京时间, 与 app/src/app/scheduler.py 保持一致
const WINDOWS: [(NaiveTime, NaiveTime); 4] = [
    (NaiveTime::from_hms_opt(8, 30, 0).unwrap(), NaiveTime::from_hms_opt(10, 0, 0).unwrap()),
    (NaiveTime::from_hms_opt(10, 10, 0).unwrap(), NaiveTime::from_hms_opt(11, 40, 0).unwrap()),
    (NaiveTime::from_hms_opt(14, 30, 0).unwrap(), NaiveTime::from_hms_opt(16, 0, 0).unwrap()),
    (NaiveTime::from_hms_opt(16, 10, 0).unwrap(), NaiveTime::from_hms_opt(17, 30, 0).unwrap()),
];

/// 北京时间 = UTC+8 固定偏移(无夏令时)
fn beijing_now() -> DateTime<Utc> {
    Utc::now() + chrono::Duration::hours(8)
}

fn log(msg: &str) {
    let ts = beijing_now().format("%Y-%m-%d %H:%M:%S");
    let line = format!("{ts} {msg}\n");
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(RS_LOG) {
        use std::io::Write;
        let _ = f.write_all(line.as_bytes());
    }
}

/// 当前是否处于周六上课窗口(提前 2 分钟 ~ 下课 + 10 分钟宽限)
fn in_window(now: &DateTime<Utc>) -> bool {
    if now.weekday() != chrono::Weekday::Sat {
        return false;
    }
    let t = NaiveTime::from_hms_opt(now.hour(), now.minute(), now.second()).unwrap();
    WINDOWS.iter().any(|&(s, e)| {
        let s = s - chrono::Duration::minutes(2);
        let e = e + chrono::Duration::minutes(10);
        t >= s && t <= e
    })
}

/// PID 对应的进程是否存活(OpenProcess 查询权限即可, 不需要名字)
fn pid_alive(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    unsafe {
        let h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if h.is_null() {
            return false;
        }
        CloseHandle(h);
        true
    }
}

/// 调度器是否在跑: 读 PID 文件并验证该 PID 活着
fn scheduler_alive() -> bool {
    match std::fs::read_to_string(SCHED_PID) {
        Ok(s) => match s.trim().parse::<u32>() {
            Ok(pid) => pid_alive(pid),
            Err(_) => false,
        },
        Err(_) => false,
    }
}

fn start_scheduler() {
    log("调度器未运行, 静默拉起 scheduler.py…");
    let log_file = OpenOptions::new().create(true).append(true).open(SCHED_LOG);
    let f = match log_file {
        Ok(f) => f,
        Err(e) => {
            log(&format!("打不开调度器日志文件({e}), 不启动"));
            return;
        }
    };
    match Command::new(VENV_PY)
        .args(["-u", SCHEDULER])
        .current_dir(ROOT)
        .stdout(f.try_clone().expect("dup stdout"))
        .stderr(f.try_clone().expect("dup stderr"))
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONUTF8", "1")
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
    {
        Ok(_) => log("调度器已启动"),
        Err(e) => log(&format!("调度器启动失败: {e}")),
    }
}

fn tick() {
    let now = beijing_now();
    if in_window(&now) {
        // 上课时段: 阻止系统睡眠
        unsafe { SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) };
        log("上课窗口内, 已临时禁止系统睡眠");
    } else {
        // 非窗口期: 不额外干预电源
        unsafe { SetThreadExecutionState(ES_CONTINUOUS) };
    }

    if scheduler_alive() {
        log("调度器运行中, 无需处理");
    } else {
        start_scheduler();
    }
}

#[tokio::main]
async fn main() {
    // 单实例守卫: 命名互斥量, 拿不到就退出(任务计划重复触发也安全)
    unsafe {
        let h = CreateMutexW(std::ptr::null(), 0, windows_sys::w!("Global\\SDYXY_WatchdogMutex"));
        if h.is_null() || GetLastError() == ERROR_ALREADY_EXISTS {
            log("已有看门狗实例在跑(互斥量占用), 本实例退出");
            std::process::exit(0);
        }
    }

    log(&format!("Rust 看门狗启动 (pid={})", std::process::id()));
    let mut itv = interval(Duration::from_secs(5 * 60));
    itv.set_missed_tick_behavior(MissedTickBehavior::Skip);
    // tokio interval 的首次 tick 立即完成, 先消耗掉, 否则启动时会跑两轮
    itv.tick().await;
    tick();
    loop {
        itv.tick().await;
        tick();
    }
}

// 让 Rust 知道 ROOT 被用于 spawn 的 current_dir(避免未使用警告)
#[allow(dead_code)]
fn _root_used() -> &'static Path {
    Path::new(ROOT)
}
