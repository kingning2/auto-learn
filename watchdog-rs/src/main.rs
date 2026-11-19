//! sdyxy 直播自动观看 · Rust 常驻看门狗
//!
//! 职责(与原 scripts/watchdog.py 一致, 但常驻 + 无窗口 + 低占用):
//!   1. 每 5 分钟检查 scheduler.py 进程, 死了就静默拉起(CREATE_NO_WINDOW)
//!   2. 周六上课时段([start-2min, end+10min])调 SetThreadExecutionState 防休眠
//!   3. 所有动作写 output/watchdog-rs.log
//!
//! 由 Windows 计划任务在"登录时/开机时"拉起一次(本程序自己循环,
//! 不再需要每 5 分钟重复触发, 因此不会再有定时弹窗问题)。
//! 多实例自守卫: 发现已有本程序在跑就直接退出。

#![windows_subsystem = "windows"] // 无控制台窗口(GUI 子系统)

use std::fs::OpenOptions;
use std::os::windows::process::CommandExt;
use std::process::Command;
use std::time::Duration;

use chrono::{DateTime, Datelike, NaiveTime, Timelike, Utc};
use tokio::time::{interval, MissedTickBehavior};
use windows_sys::Win32::System::Power::{
    SetThreadExecutionState, ES_CONTINUOUS, ES_SYSTEM_REQUIRED,
};

const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const ROOT: &str = concat!(env!("CARGO_MANIFEST_DIR"), "\\..");
const RS_LOG: &str = concat!(env!("CARGO_MANIFEST_DIR"), "\\..\\output\\watchdog-rs.log");
const SCHED_LOG: &str = concat!(env!("CARGO_MANIFEST_DIR"), "\\..\\output\\scheduler.log");
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

/// 检测 scheduler.py 是否在跑, 以及是否有另一个本程序实例
fn scan_processes() -> (bool, bool) {
    let mut sys = sysinfo::System::new();
    sys.refresh_processes(sysinfo::ProcessesToUpdate::All, true);
    let mut sched_alive = false;
    let mut self_alive = false;
    let my_pid = std::process::id();
    for (pid, proc) in sys.processes() {
        let name = proc.name().to_string_lossy().to_lowercase();
        let cmd = proc
            .cmd()
            .iter()
            .map(|s| s.to_string_lossy().to_lowercase())
            .collect::<Vec<_>>()
            .join(" ");
        if name.starts_with("python") && cmd.contains("scheduler.py") {
            sched_alive = true;
        }
        if name.contains("sdyxy-watchdog") && pid.as_u32() != my_pid {
            self_alive = true;
        }
    }
    (sched_alive, self_alive)
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

    let (sched_alive, self_dup) = scan_processes();
    if self_dup {
        log("检测到另一个看门狗实例在跑, 本实例退出");
        std::process::exit(0);
    }
    if sched_alive {
        log("调度器运行中, 无需处理");
    } else {
        start_scheduler();
    }
}

#[tokio::main]
async fn main() {
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
