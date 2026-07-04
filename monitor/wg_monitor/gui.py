from __future__ import annotations

import contextlib
import io
import json
import os
import queue
import sys
import threading
from pathlib import Path
from tkinter import BooleanVar, END, StringVar, Tk, filedialog, messagebox
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any

from .config import load_env_file, load_project
from .monitor import run_once, validate_project
from .schedule import DAY_ALIASES


DEFAULT_APP_CONFIG: dict[str, Any] = {
    "monitor": {
        "feed_urls": [{"name": "本地测试公众号", "url": "examples/fake_feed.xml"}],
        "keywords": {
            "required_any": ["勤工助学", "助学岗位"],
            "focus_any": ["岗位", "招聘", "报名", "下学期", "申请", "助学"],
        },
        "check_interval_seconds": 600,
        "article_timeout_seconds": 15,
        "fetch_article_html": True,
    },
    "volunteer": {
        "enabled": False,
        "confirm_by_email": True,
        "required_any": ["志愿服务", "志愿活动", "志愿者", "志愿时长"],
        "focus_any": ["报名", "招募", "参加", "志愿时长"],
        "notify_email": "你的邮箱",
        "token_expires_hours": 48,
        "allow_submit_when_schedule_conflict": False,
    },
    "safety": {
        "auto_send": True,
        "require_unique_email": True,
        "require_schedule": True,
        "min_keyword_hits": 2,
    },
    "email": {
        "notify_on_uncertain": True,
        "subject_template": "勤工助学岗位申请 - {name} - 可用时间",
        "opportunity_subject_template": "校园机会申请 - {name} - {title}",
        "body_mode": "llm",
        "context_markdown": "config/personal_availability.md",
        "required_profile_fields": ["name", "phone", "contact_email"],
        "auto_send_opportunities": True,
        "auto_send_categories": ["work_study", "volunteer", "competition", "scholarship"],
    },
    "user": {
        "name": "你的姓名",
        "phone": "你的手机号",
        "student_id": "",
        "major": "",
        "contact_email": "你的邮箱",
        "resume_path": "",
    },
}

DEFAULT_SCHEDULE_CONFIG: dict[str, Any] = {
    "timezone": "Asia/Shanghai",
    "day_start": "08:00",
    "day_end": "22:00",
    "days": {
        "monday": {"label": "周一", "busy": [{"name": "示例课程", "start": "08:00", "end": "09:40"}]},
        "tuesday": {"label": "周二", "busy": []},
        "wednesday": {"label": "周三", "busy": []},
        "thursday": {"label": "周四", "busy": []},
        "friday": {"label": "周五", "busy": []},
        "saturday": {"label": "周六", "busy": []},
        "sunday": {"label": "周日", "busy": []},
    },
}

DEFAULT_ENV: dict[str, str] = {
    "EMAIL_DRY_RUN": "true",
    "SMTP_HOST": "smtp.qq.com",
    "SMTP_PORT": "465",
    "SMTP_USE_SSL": "true",
    "SMTP_USER": "your-email@qq.com",
    "SMTP_PASSWORD": "your-smtp-auth-code",
    "EMAIL_SENDER": "your-email@qq.com",
    "NOTIFY_EMAIL": "your-email@qq.com",
    "EMAIL_BODY_MODE": "llm",
    "EMAIL_LLM_PROVIDER": "deepseek",
    "EMAIL_CONTEXT_MARKDOWN": "config/personal_availability.md",
    "EMAIL_LLM_TIMEOUT_SECONDS": "25",
    "DEEPSEEK_API_KEY": "",
    "OPENAI_API_KEY": "",
    "VOLUNTEER_MONITOR_ENABLED": "false",
    "VOLUNTEER_CONFIRM_BY_EMAIL": "true",
    "IMAP_HOST": "imap.qq.com",
    "IMAP_PORT": "993",
    "IMAP_USE_SSL": "true",
    "IMAP_USER": "your-email@qq.com",
    "IMAP_PASSWORD": "",
    "MAIL_TRIGGER_POLL_SECONDS": "60",
}

DAY_LABELS = {
    "monday": "周一",
    "tuesday": "周二",
    "wednesday": "周三",
    "thursday": "周四",
    "friday": "周五",
    "saturday": "周六",
    "sunday": "周日",
}
DAY_KEYS = list(DAY_LABELS)


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def yaml_dump(data: dict[str, Any]) -> str:
    import yaml  # type: ignore

    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def ensure_project_files(root: Path) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(parents=True, exist_ok=True)
    if not (root / "config" / "app.yml").exists():
        (root / "config" / "app.yml").write_text(yaml_dump(DEFAULT_APP_CONFIG), encoding="utf-8")
    if not (root / "config" / "schedule.yml").exists():
        (root / "config" / "schedule.yml").write_text(yaml_dump(DEFAULT_SCHEDULE_CONFIG), encoding="utf-8")
    if not (root / ".env").exists():
        write_env_file(root / ".env", DEFAULT_ENV)


def write_env_file(path: Path, values: dict[str, str]) -> None:
    lines = [
        "# 由桌面软件保存。不要把 SMTP 授权码发给别人。",
        f"EMAIL_DRY_RUN={values.get('EMAIL_DRY_RUN', 'true')}",
        f"SMTP_HOST={values.get('SMTP_HOST', '')}",
        f"SMTP_PORT={values.get('SMTP_PORT', '465')}",
        f"SMTP_USE_SSL={values.get('SMTP_USE_SSL', 'true')}",
        f"SMTP_USER={values.get('SMTP_USER', '')}",
        f"SMTP_PASSWORD={values.get('SMTP_PASSWORD', '')}",
        f"EMAIL_SENDER={values.get('EMAIL_SENDER', '')}",
        f"NOTIFY_EMAIL={values.get('NOTIFY_EMAIL', '')}",
        f"EMAIL_BODY_MODE={values.get('EMAIL_BODY_MODE', 'llm')}",
        f"EMAIL_LLM_PROVIDER={values.get('EMAIL_LLM_PROVIDER', 'deepseek')}",
        f"EMAIL_CONTEXT_MARKDOWN={values.get('EMAIL_CONTEXT_MARKDOWN', 'config/personal_availability.md')}",
        f"EMAIL_LLM_TIMEOUT_SECONDS={values.get('EMAIL_LLM_TIMEOUT_SECONDS', '25')}",
        f"DEEPSEEK_API_KEY={values.get('DEEPSEEK_API_KEY', '')}",
        f"OPENAI_API_KEY={values.get('OPENAI_API_KEY', '')}",
        f"VOLUNTEER_MONITOR_ENABLED={values.get('VOLUNTEER_MONITOR_ENABLED', 'false')}",
        f"VOLUNTEER_CONFIRM_BY_EMAIL={values.get('VOLUNTEER_CONFIRM_BY_EMAIL', 'true')}",
        f"IMAP_HOST={values.get('IMAP_HOST', 'imap.qq.com')}",
        f"IMAP_PORT={values.get('IMAP_PORT', '993')}",
        f"IMAP_USE_SSL={values.get('IMAP_USE_SSL', 'true')}",
        f"IMAP_USER={values.get('IMAP_USER', values.get('SMTP_USER', ''))}",
        f"IMAP_PASSWORD={values.get('IMAP_PASSWORD', '')}",
        f"MAIL_TRIGGER_POLL_SECONDS={values.get('MAIL_TRIGGER_POLL_SECONDS', '60')}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for key, value in values.items():
        os.environ[key] = value


def load_gui_state(root: Path) -> dict[str, Any]:
    path = root / "data" / "gui_state.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_gui_state(root: Path, state: dict[str, Any]) -> None:
    path = root / "data" / "gui_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_feed_text(text: str) -> list[dict[str, str]]:
    feeds: list[dict[str, str]] = []
    for index, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            name, url = [part.strip() for part in line.split("|", 1)]
        else:
            name, url = f"公众号{index}", line
        if url:
            feeds.append({"name": name or f"公众号{index}", "url": url})
    return feeds


def format_feed_text(feed_urls: list[dict[str, Any]]) -> str:
    return "\n".join(f"{item.get('name', '')}|{item.get('url', '')}" for item in feed_urls)


def parse_schedule_text(text: str, day_start: str, day_end: str) -> dict[str, Any]:
    schedule = {
        "timezone": "Asia/Shanghai",
        "day_start": day_start.strip() or "08:00",
        "day_end": day_end.strip() or "22:00",
        "days": {day: {"label": label, "busy": []} for day, label in DAY_LABELS.items()},
    }
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            raise ValueError(f"课表行格式不正确：{line}")
        day_key = DAY_ALIASES.get(parts[0])
        if not day_key:
            raise ValueError(f"无法识别星期：{parts[0]}")
        time_range = parts[1].replace("～", "-").replace("~", "-").replace("至", "-").replace("到", "-")
        if "-" not in time_range:
            raise ValueError(f"无法识别时间段：{line}")
        start, end = [part.strip() for part in time_range.split("-", 1)]
        name = " ".join(parts[2:]).strip() or "课程/占用"
        schedule["days"][day_key]["busy"].append({"name": name, "start": start, "end": end})
    return schedule


def format_schedule_text(schedule_config: dict[str, Any]) -> str:
    lines: list[str] = []
    days = schedule_config.get("days") or {}
    for day_key in DAY_KEYS:
        day_data = days.get(day_key) or {}
        label = day_data.get("label") or DAY_LABELS[day_key]
        for item in day_data.get("busy") or []:
            name = item.get("name") or "课程/占用"
            lines.append(f"{label} {item.get('start', '')}-{item.get('end', '')} {name}")
    return "\n".join(lines)


class QueueWriter(io.TextIOBase):
    def __init__(self, log_queue: queue.Queue[str]):
        self.log_queue = log_queue

    def write(self, text: str) -> int:
        if text:
            self.log_queue.put(text)
        return len(text)

    def flush(self) -> None:
        pass


class MonitorApp:
    def __init__(self) -> None:
        self.root_dir = app_root()
        ensure_project_files(self.root_dir)
        self.window = Tk()
        self.window.title("公众号勤工助学岗位监测器")
        self.window.geometry("980x760")
        self.window.minsize(880, 640)
        self.window.protocol("WM_DELETE_WINDOW", self.on_window_close)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()
        self.monitor_thread: threading.Thread | None = None
        self.config_visible = BooleanVar(value=False)
        self.real_send = BooleanVar(value=False)

        self.feed_text: ScrolledText
        self.schedule_text: ScrolledText
        self.log_text: ScrolledText
        self.vars: dict[str, StringVar] = {}

        self.build_ui()
        self.load_values_into_form()
        state = load_gui_state(self.root_dir)
        if state.get("profile_saved"):
            self.hide_config()
            self.start_monitoring()
        else:
            self.show_config()
            self.append_log("第一次使用：请先填写信息，保存后会开始监测。\n")
        self.window.after(200, self.drain_log_queue)

    def build_ui(self) -> None:
        shell = ttk.Frame(self.window, padding=14)
        shell.pack(fill="both", expand=True)

        header = ttk.Frame(shell)
        header.pack(fill="x")
        ttk.Label(header, text="公众号勤工助学岗位监测器", font=("Microsoft YaHei UI", 18, "bold")).pack(side="left")
        self.status_var = StringVar(value="未开始")
        ttk.Label(header, textvariable=self.status_var, foreground="#0f766e").pack(side="right")

        toolbar = ttk.Frame(shell)
        toolbar.pack(fill="x", pady=(12, 8))
        ttk.Button(toolbar, text="修改/查看配置", command=self.toggle_config).pack(side="left")
        ttk.Button(toolbar, text="保存并开始监测", command=self.save_and_start).pack(side="left", padx=8)
        ttk.Button(toolbar, text="立即检查一次", command=self.run_check_once).pack(side="left")
        ttk.Button(toolbar, text="退出监测并关闭", command=self.exit_app).pack(side="right")

        self.config_frame = ttk.LabelFrame(shell, text="配置")
        self.config_frame.pack(fill="x", pady=(0, 10))
        self.build_config_form(self.config_frame)

        log_frame = ttk.LabelFrame(shell, text="运行日志")
        log_frame.pack(fill="both", expand=True)
        self.log_text = ScrolledText(log_frame, height=16, wrap="word")
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)

    def build_config_form(self, parent: ttk.Frame) -> None:
        for name, default in {
            "name": "",
            "phone": "",
            "student_id": "",
            "major": "",
            "contact_email": "",
            "resume_path": "",
            "smtp_host": "smtp.qq.com",
            "smtp_port": "465",
            "smtp_user": "",
            "smtp_password": "",
            "email_sender": "",
            "notify_email": "",
            "interval": "600",
            "day_start": "08:00",
            "day_end": "22:00",
        }.items():
            self.vars[name] = StringVar(value=default)

        row = ttk.Frame(parent)
        row.pack(fill="x", padx=8, pady=6)
        self.entry(row, "姓名", "name", 0)
        self.entry(row, "手机号", "phone", 1)
        self.entry(row, "学号", "student_id", 2)
        self.entry(row, "专业", "major", 3)

        row = ttk.Frame(parent)
        row.pack(fill="x", padx=8, pady=6)
        self.entry(row, "联系邮箱", "contact_email", 0)
        self.entry(row, "SMTP邮箱", "smtp_user", 1)
        self.entry(row, "发件邮箱", "email_sender", 2)
        self.entry(row, "提醒邮箱", "notify_email", 3)

        row = ttk.Frame(parent)
        row.pack(fill="x", padx=8, pady=6)
        self.entry(row, "SMTP服务器", "smtp_host", 0)
        self.entry(row, "端口", "smtp_port", 1, width=10)
        self.entry(row, "授权码", "smtp_password", 2, show="*")
        ttk.Checkbutton(row, text="真实发送邮件", variable=self.real_send).grid(row=0, column=6, padx=(12, 6), sticky="w")

        row = ttk.Frame(parent)
        row.pack(fill="x", padx=8, pady=6)
        self.entry(row, "检查间隔(秒)", "interval", 0, width=12)
        self.entry(row, "每天开始", "day_start", 1, width=12)
        self.entry(row, "每天结束", "day_end", 2, width=12)
        ttk.Label(row, text="简历附件").grid(row=0, column=6, padx=(12, 4), sticky="e")
        ttk.Entry(row, textvariable=self.vars["resume_path"], width=38).grid(row=0, column=7, sticky="we")
        ttk.Button(row, text="选择", command=self.choose_resume).grid(row=0, column=8, padx=(6, 0))

        paned = ttk.PanedWindow(parent, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=8, pady=8)

        feed_box = ttk.LabelFrame(paned, text="公众号 feed，每行：名称|地址")
        self.feed_text = ScrolledText(feed_box, height=7, wrap="word")
        self.feed_text.pack(fill="both", expand=True, padx=6, pady=6)
        paned.add(feed_box, weight=1)

        schedule_box = ttk.LabelFrame(paned, text="课表占用时间，每行：周一 08:00-09:40 课程名")
        self.schedule_text = ScrolledText(schedule_box, height=7, wrap="word")
        self.schedule_text.pack(fill="both", expand=True, padx=6, pady=6)
        paned.add(schedule_box, weight=1)

    def entry(self, parent: ttk.Frame, label: str, key: str, col: int, width: int = 18, show: str | None = None) -> None:
        ttk.Label(parent, text=label).grid(row=0, column=col * 2, padx=(0, 4), sticky="e")
        ttk.Entry(parent, textvariable=self.vars[key], width=width, show=show).grid(
            row=0, column=col * 2 + 1, padx=(0, 10), sticky="we"
        )

    def load_values_into_form(self) -> None:
        paths, app_config, schedule_config = load_project(self.root_dir)
        env_values = load_env_file(paths.env_file)
        user = app_config.get("user") or {}
        monitor = app_config.get("monitor") or {}

        for key in ("name", "phone", "student_id", "major", "contact_email", "resume_path"):
            self.vars[key].set(str(user.get(key) or ""))
        self.vars["interval"].set(str(monitor.get("check_interval_seconds") or "600"))
        self.vars["day_start"].set(str(schedule_config.get("day_start") or "08:00"))
        self.vars["day_end"].set(str(schedule_config.get("day_end") or "22:00"))
        self.vars["smtp_host"].set(env_values.get("SMTP_HOST", "smtp.qq.com"))
        self.vars["smtp_port"].set(env_values.get("SMTP_PORT", "465"))
        self.vars["smtp_user"].set(env_values.get("SMTP_USER", ""))
        self.vars["smtp_password"].set(env_values.get("SMTP_PASSWORD", ""))
        self.vars["email_sender"].set(env_values.get("EMAIL_SENDER", env_values.get("SMTP_USER", "")))
        self.vars["notify_email"].set(env_values.get("NOTIFY_EMAIL", env_values.get("SMTP_USER", "")))
        self.real_send.set((env_values.get("EMAIL_DRY_RUN", "true").lower() in {"false", "0", "no"}))

        self.feed_text.delete("1.0", END)
        self.feed_text.insert("1.0", format_feed_text(monitor.get("feed_urls") or []))
        self.schedule_text.delete("1.0", END)
        self.schedule_text.insert("1.0", format_schedule_text(schedule_config))

    def save_form(self) -> None:
        paths, app_config, _schedule_config = load_project(self.root_dir)
        feeds = parse_feed_text(self.feed_text.get("1.0", END))
        if not feeds:
            raise ValueError("至少填写一个公众号 feed 地址")

        app_config.setdefault("monitor", {})["feed_urls"] = feeds
        app_config["monitor"]["check_interval_seconds"] = int(self.vars["interval"].get().strip() or "600")
        app_config.setdefault("user", {}).update(
            {
                "name": self.vars["name"].get().strip(),
                "phone": self.vars["phone"].get().strip(),
                "student_id": self.vars["student_id"].get().strip(),
                "major": self.vars["major"].get().strip(),
                "contact_email": self.vars["contact_email"].get().strip(),
                "resume_path": self.vars["resume_path"].get().strip(),
            }
        )
        schedule_config = parse_schedule_text(
            self.schedule_text.get("1.0", END),
            self.vars["day_start"].get(),
            self.vars["day_end"].get(),
        )

        paths.app_config.write_text(yaml_dump(app_config), encoding="utf-8")
        paths.schedule_config.write_text(yaml_dump(schedule_config), encoding="utf-8")
        write_env_file(
            paths.env_file,
            {
                "EMAIL_DRY_RUN": "false" if self.real_send.get() else "true",
                "SMTP_HOST": self.vars["smtp_host"].get().strip(),
                "SMTP_PORT": self.vars["smtp_port"].get().strip() or "465",
                "SMTP_USE_SSL": "true",
                "SMTP_USER": self.vars["smtp_user"].get().strip(),
                "SMTP_PASSWORD": self.vars["smtp_password"].get().strip(),
                "EMAIL_SENDER": self.vars["email_sender"].get().strip() or self.vars["smtp_user"].get().strip(),
                "NOTIFY_EMAIL": self.vars["notify_email"].get().strip() or self.vars["email_sender"].get().strip(),
                "EMAIL_BODY_MODE": os.getenv("EMAIL_BODY_MODE", "llm"),
                "EMAIL_LLM_PROVIDER": os.getenv("EMAIL_LLM_PROVIDER", "deepseek"),
                "EMAIL_CONTEXT_MARKDOWN": os.getenv("EMAIL_CONTEXT_MARKDOWN", "config/personal_availability.md"),
                "EMAIL_LLM_TIMEOUT_SECONDS": os.getenv("EMAIL_LLM_TIMEOUT_SECONDS", "25"),
                "DEEPSEEK_API_KEY": os.getenv("DEEPSEEK_API_KEY", ""),
                "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""),
                "VOLUNTEER_MONITOR_ENABLED": os.getenv("VOLUNTEER_MONITOR_ENABLED", "false"),
                "VOLUNTEER_CONFIRM_BY_EMAIL": os.getenv("VOLUNTEER_CONFIRM_BY_EMAIL", "true"),
                "IMAP_HOST": os.getenv("IMAP_HOST", "imap.qq.com"),
                "IMAP_PORT": os.getenv("IMAP_PORT", "993"),
                "IMAP_USE_SSL": os.getenv("IMAP_USE_SSL", "true"),
                "IMAP_USER": os.getenv("IMAP_USER", self.vars["smtp_user"].get().strip()),
                "IMAP_PASSWORD": os.getenv("IMAP_PASSWORD", ""),
                "MAIL_TRIGGER_POLL_SECONDS": os.getenv("MAIL_TRIGGER_POLL_SECONDS", "60"),
            },
        )
        save_gui_state(self.root_dir, {"profile_saved": True})

    def save_and_start(self) -> None:
        try:
            self.save_form()
            self.append_log("配置已保存。\n")
            self.hide_config()
            self.start_monitoring()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("保存失败", str(exc))

    def start_monitoring(self) -> None:
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.append_log("监测已经在运行。\n")
            return
        self.stop_event.clear()
        self.monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.monitor_thread.start()
        self.status_var.set("监测中")

    def monitor_loop(self) -> None:
        writer = QueueWriter(self.log_queue)
        while not self.stop_event.is_set():
            try:
                with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                    paths, app_config, schedule_config = load_project(self.root_dir)
                    validate_project(paths, app_config, schedule_config, check_network=False)
                    run_once(paths, app_config, schedule_config)
                    interval = int((app_config.get("monitor") or {}).get("check_interval_seconds", 600))
            except Exception as exc:  # noqa: BLE001
                self.log_queue.put(f"本轮监测失败：{exc}\n")
                interval = 60
            self.stop_event.wait(max(10, interval))
        self.log_queue.put("监测已停止。\n")

    def run_check_once(self) -> None:
        try:
            self.save_form()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("配置错误", str(exc))
            return
        threading.Thread(target=self.check_once_thread, daemon=True).start()

    def check_once_thread(self) -> None:
        writer = QueueWriter(self.log_queue)
        with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
            paths, app_config, schedule_config = load_project(self.root_dir)
            run_once(paths, app_config, schedule_config, reprocess=True)

    def drain_log_queue(self) -> None:
        while True:
            try:
                text = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.append_log(text)
        self.window.after(200, self.drain_log_queue)

    def append_log(self, text: str) -> None:
        self.log_text.insert(END, text)
        self.log_text.see(END)

    def choose_resume(self) -> None:
        path = filedialog.askopenfilename(
            title="选择简历附件",
            filetypes=[("常见文档", "*.pdf *.doc *.docx"), ("所有文件", "*.*")],
        )
        if path:
            self.vars["resume_path"].set(path)

    def toggle_config(self) -> None:
        if self.config_visible.get():
            self.hide_config()
        else:
            self.show_config()

    def show_config(self) -> None:
        self.config_frame.pack(fill="x", pady=(0, 10), before=self.log_text.master)
        self.config_visible.set(True)

    def hide_config(self) -> None:
        self.config_frame.pack_forget()
        self.config_visible.set(False)

    def on_window_close(self) -> None:
        if self.monitor_thread and self.monitor_thread.is_alive():
            messagebox.showinfo("正在监测", "请点击“退出监测并关闭”按钮来结束程序。")
        else:
            self.window.destroy()

    def exit_app(self) -> None:
        self.stop_event.set()
        self.status_var.set("正在退出")
        self.window.after(300, self.window.destroy)

    def run(self) -> None:
        self.window.mainloop()


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    root = app_root()
    ensure_project_files(root)
    if "--self-test" in argv:
        load_project(root)
        print(f"GUI self-test OK: {root}")
        return 0
    app = MonitorApp()
    app.run()
    return 0
