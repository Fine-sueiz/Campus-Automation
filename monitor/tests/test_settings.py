from __future__ import annotations

import os

from wg_monitor.settings import invalidate_project_cache, load_project_cached


def make_project(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "app.yml").write_text(
        "monitor:\n  check_interval_seconds: 111\n", encoding="utf-8"
    )
    (tmp_path / "config" / "schedule.yml").write_text(
        'timezone: Asia/Shanghai\nday_start: "08:00"\nday_end: "22:00"\ndays: {}\n',
        encoding="utf-8",
    )
    return tmp_path


def test_cached_load_returns_same_bundle_until_file_changes(tmp_path):
    root = make_project(tmp_path)
    invalidate_project_cache()

    first = load_project_cached(root)
    second = load_project_cached(root)
    assert first is second
    assert first[1]["monitor"]["check_interval_seconds"] == 111

    app_yml = root / "config" / "app.yml"
    app_yml.write_text("monitor:\n  check_interval_seconds: 222\n", encoding="utf-8")
    stat = app_yml.stat()
    os.utime(app_yml, (stat.st_atime, stat.st_mtime + 10))

    third = load_project_cached(root)
    assert third is not first
    assert third[1]["monitor"]["check_interval_seconds"] == 222


def test_invalidate_forces_reload(tmp_path):
    root = make_project(tmp_path)
    invalidate_project_cache()
    first = load_project_cached(root)
    invalidate_project_cache(root)
    second = load_project_cached(root)
    assert first is not second
    assert second[1] == first[1]
