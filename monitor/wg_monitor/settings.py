from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import ProjectPaths, load_env_file, load_yaml

ProjectBundle = tuple[ProjectPaths, dict[str, Any], dict[str, Any]]

_cache: dict[Path, tuple[tuple[float, float, float], ProjectBundle]] = {}


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def load_project_cached(root: Path | str = ".") -> ProjectBundle:
    """与 config.load_project 行为一致，但按文件 mtime 缓存。

    服务端每个请求/每轮扫描都要读配置，直接 load_project 会反复解析磁盘文件；
    这里在 .env/app.yml/schedule.yml 任一文件变化时自动重新加载，
    保留“改配置文件不用重启服务”的热更新语义。
    调用方不得原地修改返回的配置 dict。
    """
    paths = ProjectPaths.from_root(root)
    stamp = (_mtime(paths.env_file), _mtime(paths.app_config), _mtime(paths.schedule_config))
    cached = _cache.get(paths.root)
    if cached and cached[0] == stamp:
        return cached[1]
    load_env_file(paths.env_file)
    bundle: ProjectBundle = (paths, load_yaml(paths.app_config), load_yaml(paths.schedule_config))
    _cache[paths.root] = (stamp, bundle)
    return bundle


def invalidate_project_cache(root: Path | str | None = None) -> None:
    if root is None:
        _cache.clear()
        return
    _cache.pop(ProjectPaths.from_root(root).root, None)
