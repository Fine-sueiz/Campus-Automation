from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    env_file: Path
    app_config: Path
    schedule_config: Path
    state_file: Path

    @classmethod
    def from_root(cls, root: Path | str = ".") -> "ProjectPaths":
        root_path = Path(root).resolve()
        return cls(
            root=root_path,
            env_file=root_path / ".env",
            app_config=root_path / "config" / "app.yml",
            schedule_config=root_path / "config" / "schedule.yml",
            state_file=root_path / "data" / "state.json",
        )


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        values[key] = value
        os.environ.setdefault(key, value)
    return values


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"配置文件不存在：{path}")

    raw = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError as exc:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            raise ConfigError(
                "缺少 PyYAML。请先运行：python -m pip install -r requirements.txt"
            ) from exc
    else:
        parsed = yaml.safe_load(raw)

    if not isinstance(parsed, dict):
        raise ConfigError(f"配置文件格式不正确：{path}")
    return parsed


def get_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def get_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"环境变量 {name} 必须是整数，目前是：{value}") from exc


def load_project(root: Path | str = ".") -> tuple[ProjectPaths, dict[str, Any], dict[str, Any]]:
    paths = ProjectPaths.from_root(root)
    load_env_file(paths.env_file)
    app_config = load_yaml(paths.app_config)
    schedule_config = load_yaml(paths.schedule_config)
    return paths, app_config, schedule_config
