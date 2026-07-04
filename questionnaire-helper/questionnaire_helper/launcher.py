import asyncio
import contextlib
import json
import math
import os
import queue
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from tkinter import BooleanVar, END, StringVar, Text, Tk, messagebox
from tkinter import ttk
from typing import Any

from fill_questionnaire import run


def resource_dir() -> Path:
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parent


def work_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


RESOURCE_DIR = resource_dir()
WORK_DIR = work_dir()
TEMPLATE_CONFIG = RESOURCE_DIR / "config.json"
GENERATED_CONFIG = WORK_DIR / "generated_config.json"


PROFILE_FIELD_DEFS = [
    ("姓名", ["姓名", "名字", "请输入你的名字", "请输入姓名"], "name"),
    ("学号", ["学号", "学生编号", "学生证号", "请输入你的学号", "请填写学生编号"], "student_id"),
    ("手机号", ["手机号", "手机号码", "联系电话", "联系方式", "请输入手机号"], "phone"),
    ("微信号", ["微信号", "微信", "请输入你的微信号", "请填写微信"], "wechat"),
]


class QueueWriter:
    def __init__(self, output_queue: queue.Queue[str], log_file: Any | None = None) -> None:
        self.output_queue = output_queue
        self.log_file = log_file

    def write(self, text: str) -> int:
        if text:
            self.output_queue.put(text)
            if self.log_file:
                self.log_file.write(text)
                self.log_file.flush()
        return len(text)

    def flush(self) -> None:
        pass


def load_template_config() -> dict[str, Any]:
    with TEMPLATE_CONFIG.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def parse_urls(raw: str) -> list[str]:
    urls = [line.strip() for line in raw.replace(",", "\n").splitlines()]
    return [url for url in urls if url]


def parse_optional_lines(raw: str) -> list[str]:
    lines = [line.strip() for line in raw.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return lines


def normalize_schedule_time(raw: str, label: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(value, fmt).strftime("%H:%M:%S")
        except ValueError:
            continue
    raise ValueError(f"{label}格式应为 YYYY-MM-DD HH:MM:SS 或 HH:MM，例如 2026-05-18 09:00:00 / 09:00")


def normalize_start_time(raw: str) -> str:
    return normalize_schedule_time(raw, "开始时间")


def normalize_submit_time(raw: str) -> str:
    return normalize_schedule_time(raw, "提交时间")


def parse_nonnegative_seconds(raw: str, label: str) -> float:
    value = raw.strip()
    if not value:
        return 0.0
    try:
        seconds = float(value)
    except ValueError as exc:
        raise ValueError(f"{label}必须是数字，例如 10 或 0.5") from exc
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(f"{label}不能小于 0")
    return seconds


def json_number(value: float) -> int | float:
    return int(value) if value.is_integer() else value


def update_label_answer(config: dict[str, Any], label: str, value: str) -> None:
    ml_config = config.get("ml_classifier")
    if not isinstance(ml_config, dict):
        return
    label_answers = ml_config.get("label_answers")
    if not isinstance(label_answers, dict):
        return
    answer = label_answers.get(label)
    if isinstance(answer, dict):
        answer["value"] = value


class LauncherApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("问卷自动填写助手")
        self.root.geometry("900x820")
        self.output_queue: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None

        template = load_template_config()
        first_questionnaire = (template.get("questionnaires") or [{}])[0]
        values = self._template_values(template)

        self.urls_var = StringVar(value=self._questionnaire_field_lines(template, "url"))
        self.submit_times_var = StringVar(value=self._questionnaire_field_lines(template, "submit_time"))
        self.start_time_var = StringVar(value=str(template.get("start_time") or first_questionnaire.get("start_time", "")))
        self.min_submit_after_start_var = StringVar(value=str(template.get("min_submit_after_start_seconds", 0)))
        self.name_var = StringVar(value=values.get("姓名", ""))
        self.student_id_var = StringVar(value=values.get("学号", ""))
        self.phone_var = StringVar(value=values.get("手机号", ""))
        self.wechat_var = StringVar(value=values.get("微信号", ""))
        self.college_var = StringVar(value=self._answer_value(template, "学院"))
        self.grade_var = StringVar(value=self._answer_value(template, "年级") or "大一")
        self.participation_var = StringVar(value=self._answer_value(template, "是否愿意参加") or "参加")
        self.api_key_var = StringVar(value="")
        self.headless_var = BooleanVar(value=bool(template.get("headless", False)))
        self.auto_submit_var = BooleanVar(value=bool(template.get("auto_submit", True)))
        self.save_qr_code_images_var = BooleanVar(value=bool(template.get("save_qr_code_images", True)))
        self.llm_enabled_var = BooleanVar(value=bool((template.get("llm_validation") or {}).get("enabled", True)))

        self._build_ui()
        self.root.after(100, self._drain_output)

    def _questionnaire_field_lines(self, template: dict[str, Any], key: str) -> str:
        entries = template.get("questionnaires")
        if not isinstance(entries, list) or not entries:
            if key == "url":
                return str(template.get("url", ""))
            return str(template.get(key, ""))

        lines = []
        for entry in entries:
            if isinstance(entry, dict):
                lines.append(str(entry.get(key, "")))
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines)

    def _template_values(self, template: dict[str, Any]) -> dict[str, str]:
        values: dict[str, str] = {}
        for field in template.get("profile_fields", []):
            if isinstance(field, dict):
                values[str(field.get("label", ""))] = str(field.get("value", ""))
        return values

    def _answer_value(self, template: dict[str, Any], label: str) -> str:
        for answer in template.get("answers", []):
            if isinstance(answer, dict) and answer.get("label") == label:
                return str(answer.get("value", ""))
        return ""

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=14)
        main.pack(fill="both", expand=True)

        form = ttk.LabelFrame(main, text="运行信息", padding=12)
        form.pack(fill="x")

        ttk.Label(form, text="问卷链接（多个链接可换行）").grid(row=0, column=0, sticky="nw", pady=4)
        self.urls_text = Text(form, height=5, width=82)
        self.urls_text.grid(row=0, column=1, sticky="ew", pady=4)
        self.urls_text.insert("1.0", self.urls_var.get())

        ttk.Label(form, text="开始时间").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(form, textvariable=self.start_time_var, width=36).grid(row=1, column=1, sticky="w", pady=4)
        ttk.Label(form, text="全局默认；留空则立即开始，支持 09:00").grid(row=1, column=1, sticky="e", pady=4)

        ttk.Label(form, text="提交时间（可选，逐行对应链接）").grid(row=2, column=0, sticky="nw", pady=4)
        self.submit_times_text = Text(form, height=4, width=82)
        self.submit_times_text.grid(row=2, column=1, sticky="ew", pady=4)
        self.submit_times_text.insert("1.0", self.submit_times_var.get())

        ttk.Label(form, text="开始后至少等待秒数再提交").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(form, textvariable=self.min_submit_after_start_var, width=12).grid(row=3, column=1, sticky="w", pady=4)
        ttk.Label(form, text="例如 10；留空或 0 表示不限制").grid(row=3, column=1, sticky="e", pady=4)

        info = ttk.LabelFrame(main, text="个人信息", padding=12)
        info.pack(fill="x", pady=(10, 0))
        fields = [
            ("姓名", self.name_var),
            ("学号", self.student_id_var),
            ("手机号", self.phone_var),
            ("微信号", self.wechat_var),
            ("学院", self.college_var),
            ("年级", self.grade_var),
            ("是否参加", self.participation_var),
        ]
        for index, (label, var) in enumerate(fields):
            row = index // 2
            col = (index % 2) * 2
            ttk.Label(info, text=label).grid(row=row, column=col, sticky="w", padx=(0, 8), pady=4)
            ttk.Entry(info, textvariable=var, width=34).grid(row=row, column=col + 1, sticky="ew", padx=(0, 18), pady=4)

        options = ttk.LabelFrame(main, text="选项", padding=12)
        options.pack(fill="x", pady=(10, 0))
        ttk.Checkbutton(options, text="自动提交", variable=self.auto_submit_var).grid(row=0, column=0, sticky="w", padx=(0, 24))
        ttk.Checkbutton(options, text="浏览器无头模式", variable=self.headless_var).grid(row=0, column=1, sticky="w", padx=(0, 24))
        ttk.Checkbutton(options, text="保存提交后的二维码", variable=self.save_qr_code_images_var).grid(row=0, column=2, sticky="w", padx=(0, 24))
        ttk.Checkbutton(options, text="启用大模型兜底", variable=self.llm_enabled_var).grid(row=0, column=3, sticky="w")

        ttk.Label(options, text="DeepSeek API Key（可留空，使用当前环境变量）").grid(row=1, column=0, sticky="w", pady=(10, 4))
        ttk.Entry(options, textvariable=self.api_key_var, width=70, show="*").grid(row=1, column=1, columnspan=2, sticky="ew", pady=(10, 4))

        actions = ttk.Frame(main)
        actions.pack(fill="x", pady=(12, 8))
        self.start_button = ttk.Button(actions, text="生成配置并开始", command=self.start_run)
        self.start_button.pack(side="left")
        ttk.Button(actions, text="只生成配置", command=self.save_config_only).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="退出", command=self.root.destroy).pack(side="right")

        output_frame = ttk.LabelFrame(main, text="运行日志", padding=8)
        output_frame.pack(fill="both", expand=True)
        self.output = Text(output_frame, height=18)
        self.output.pack(fill="both", expand=True)

        for frame in (form, info, options):
            frame.columnconfigure(1, weight=1)

    def build_config(self) -> dict[str, Any]:
        config = load_template_config()
        urls = parse_urls(self.urls_text.get("1.0", END))
        if not urls:
            raise ValueError("请至少填写一个问卷链接")

        start_time = normalize_start_time(self.start_time_var.get())
        submit_time_lines = parse_optional_lines(self.submit_times_text.get("1.0", END))
        if any(line for line in submit_time_lines) and len(submit_time_lines) > len(urls):
            raise ValueError("提交时间行数不能多于问卷链接行数")

        min_submit_after_start = parse_nonnegative_seconds(self.min_submit_after_start_var.get(), "开始后至少等待秒数")
        config["min_submit_after_start_seconds"] = json_number(min_submit_after_start)

        questionnaires = []
        for index, url in enumerate(urls, 1):
            item = {"label": f"问卷{index}", "url": url}
            if start_time:
                item["start_time"] = start_time
            if index <= len(submit_time_lines) and submit_time_lines[index - 1]:
                item["submit_time"] = normalize_submit_time(submit_time_lines[index - 1])
            questionnaires.append(item)
        config["questionnaires"] = questionnaires
        config["auto_submit"] = bool(self.auto_submit_var.get())
        config["headless"] = bool(self.headless_var.get())
        config["save_qr_code_images"] = bool(self.save_qr_code_images_var.get())

        personal = {
            "name": self.name_var.get().strip(),
            "student_id": self.student_id_var.get().strip(),
            "phone": self.phone_var.get().strip(),
            "wechat": self.wechat_var.get().strip(),
        }
        config["profile_fields"] = [
            {"label": label, "keywords": keywords, "value": personal[key]}
            for label, keywords, key in PROFILE_FIELD_DEFS
            if personal[key]
        ]

        answers = []
        college = self.college_var.get().strip()
        if college:
            answers.append(
                {
                    "label": "学院",
                    "keywords": ["请选择你的学院", "你的学院", "您的学院", "你所在的学院", "您所在的学院", "所在学院", "学院", "院系"],
                    "type": "single",
                    "value": college,
                    "required": True,
                }
            )
        grade = self.grade_var.get().strip()
        if grade:
            answers.append(
                {
                    "label": "年级",
                    "keywords": ["请选择你的年级", "你的年级", "您的年级", "你所在的年级", "您所在的年级", "所在年级", "年级", "入学年级"],
                    "type": "single",
                    "value": grade,
                    "required": True,
                }
            )
        participation = self.participation_var.get().strip()
        if participation:
            answers.append(
                {
                    "label": "是否愿意参加",
                    "keywords": ["是否愿意参加", "参加还是不参加这个活动", "参加还是不参加", "是否参加", "是否参加这个活动", "是否报名参加"],
                    "type": "single",
                    "value": participation,
                    "required": True,
                }
            )
        config["answers"] = answers

        update_label_answer(config, "name", personal["name"])
        update_label_answer(config, "student_id", personal["student_id"])
        update_label_answer(config, "phone", personal["phone"])
        update_label_answer(config, "wechat", personal["wechat"])
        update_label_answer(config, "college", college)
        update_label_answer(config, "grade", grade)

        llm_config = dict(config.get("llm_validation", {}))
        llm_config["enabled"] = bool(self.llm_enabled_var.get())
        config["llm_validation"] = llm_config
        ml_config = config.get("ml_classifier")
        if isinstance(ml_config, dict) and not Path(str(ml_config.get("model_path", ""))).is_absolute():
            model_path = RESOURCE_DIR / str(ml_config.get("model_path", "question_model_new.json"))
            ml_config["model_path"] = str(model_path)

        api_key = self.api_key_var.get().strip()
        if api_key:
            os.environ[str(llm_config.get("api_key_env", "DEEPSEEK_API_KEY"))] = api_key

        return config

    def save_config(self) -> Path:
        config = self.build_config()
        with GENERATED_CONFIG.open("w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        if bool(config.get("save_qr_code_images", True)):
            qr_dir = Path(str(config.get("qr_code_output_dir", "saved_qr_codes")).strip() or "saved_qr_codes")
            if not qr_dir.is_absolute():
                qr_dir = GENERATED_CONFIG.parent / qr_dir
            qr_dir.mkdir(parents=True, exist_ok=True)
        return GENERATED_CONFIG

    def save_config_only(self) -> None:
        try:
            path = self.save_config()
        except Exception as exc:
            messagebox.showerror("配置错误", str(exc))
            return
        messagebox.showinfo("已生成", f"配置已保存到：\n{path}")

    def start_run(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("正在运行", "当前任务还在运行中")
            return
        try:
            path = self.save_config()
        except Exception as exc:
            messagebox.showerror("配置错误", str(exc))
            return

        self.output.delete("1.0", END)
        self._log(f"[launcher] generated config: {path}\n")
        self.start_button.config(state="disabled")
        self.worker = threading.Thread(target=self._run_worker, args=(path,), daemon=True)
        self.worker.start()

    def _run_worker(self, config_path: Path) -> None:
        log_dir = WORK_DIR / "run_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        self.output_queue.put(f"[launcher] log file: {log_path}\n")
        try:
            with log_path.open("w", encoding="utf-8") as log_file:
                writer = QueueWriter(self.output_queue, log_file)
                log_file.write(f"[launcher] generated config: {config_path}\n")
                log_file.write(f"[launcher] log file: {log_path}\n")
                log_file.flush()
                with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                    code = asyncio.run(run(config_path))
                    print(f"[launcher] finished with exit code {code}")
        except Exception:
            self.output_queue.put(traceback.format_exc())
        finally:
            self.output_queue.put("[launcher] task ended\n")
            self.root.after(0, lambda: self.start_button.config(state="normal"))

    def _log(self, text: str) -> None:
        self.output.insert(END, text)
        self.output.see(END)

    def _drain_output(self) -> None:
        try:
            while True:
                self._log(self.output_queue.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self._drain_output)


def main() -> None:
    root = Tk()
    LauncherApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
