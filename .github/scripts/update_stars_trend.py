#!/usr/bin/env python3
"""Update the daily Stars snapshot and render light/dark README charts."""

from __future__ import annotations

import argparse
import json
import math
import os
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_FILE = ROOT / ".github" / "data" / "stars-history.json"
DEFAULT_OUTPUT_DIR = ROOT / "docs" / "images"
THEMES = {
    "light": {
        "title": "#1f2328",
        "subtitle": "#59636e",
        "count": "#1f2328",
        "star": "#9a6700",
        "grid": "#d0d7de",
        "axis": "#59636e",
        "line": "#0969da",
        "point": "#0969da",
    },
    "dark": {
        "title": "#f0f6fc",
        "subtitle": "#8b949e",
        "count": "#f0f6fc",
        "star": "#f2cc60",
        "grid": "#30363d",
        "axis": "#8b949e",
        "line": "#58a6ff",
        "point": "#58a6ff",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", help="GitHub repository in owner/name form")
    parser.add_argument("--star-count", type=int, help="Use this count instead of GitHub API")
    parser.add_argument("--date", help="Snapshot date in YYYY-MM-DD (default: UTC today)")
    parser.add_argument("--data-file", type=Path, default=DEFAULT_DATA_FILE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def parse_day(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def validate_data(payload: Any, repository: str | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported Stars history schema")
    stored_repository = str(payload.get("repository") or "").strip().lower()
    if stored_repository.count("/") != 1:
        raise ValueError("repository must use owner/name format")
    if repository and stored_repository != repository.lower():
        raise ValueError("history data targets a different repository")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("Stars history requires at least one record")
    previous_day: date | None = None
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("each Stars record must be an object")
        current_day = parse_day(str(record.get("date") or ""))
        count = record.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("Stars count must be a non-negative integer")
        if previous_day and current_day <= previous_day:
            raise ValueError("Stars records must be strictly chronological")
        previous_day = current_day
    return payload


def fetch_star_count(repository: str, token: str) -> int:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"{repository} Stars trend updater",
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            count = payload.get("stargazers_count")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("GitHub response omitted stargazers_count")
            return count
        except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(attempt * 2)
    raise RuntimeError(f"GitHub repository request failed: {last_error}")


def update_record(payload: dict[str, Any], snapshot_day: date, count: int) -> bool:
    records = payload["records"]
    last_day = parse_day(records[-1]["date"])
    if snapshot_day < last_day:
        raise ValueError("snapshot date is older than the latest history record")
    if snapshot_day == last_day:
        if records[-1]["count"] == count:
            return False
        records[-1]["count"] = count
    else:
        records.append({"date": snapshot_day.isoformat(), "count": count})
    payload["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return True


def nice_axis_max(maximum: int) -> tuple[int, int]:
    if maximum <= 5:
        return 5, 1
    rough_step = maximum / 5
    magnitude = 10 ** math.floor(math.log10(rough_step))
    normalized = rough_step / magnitude
    multiplier = next(value for value in (1, 2, 5, 10) if normalized <= value)
    step = int(multiplier * magnitude)
    axis_max = max(step * 5, math.ceil(maximum / step) * step)
    return axis_max, step


def selected_indices(size: int, limit: int = 9) -> list[int]:
    if size <= limit:
        return list(range(size))
    return sorted({round(index * (size - 1) / (limit - 1)) for index in range(limit)})


def render_svg(repository: str, records: list[dict[str, Any]], theme: str) -> str:
    colors = THEMES[theme]
    width, height = 900, 400
    left, top, chart_width, chart_height = 60, 88, 800, 242
    axis_max, tick_step = nice_axis_max(max(record["count"] for record in records))
    denominator = max(len(records) - 1, 1)
    points = [
        (
            left + index * chart_width / denominator,
            top + chart_height - record["count"] / axis_max * chart_height,
        )
        for index, record in enumerate(records)
    ]
    if len(records) == 1:
        points[0] = (left + chart_width, points[0][1])
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    y_grid: list[str] = []
    tick = 0
    while tick <= axis_max:
        y = top + chart_height - tick / axis_max * chart_height
        y_grid.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + chart_width}" '
            f'y2="{y:.1f}" class="grid" />'
            f'<text x="{left - 16}" y="{y + 5:.1f}" class="axis" '
            f'text-anchor="end">{tick}</text>'
        )
        tick += tick_step

    labels: list[str] = []
    visible_indices = selected_indices(len(records))
    for index in visible_indices:
        record = records[index]
        x, y = points[index]
        label = escape(record["date"][5:])
        labels.append(
            f'<text x="{x:.1f}" y="{top + chart_height + 32}" class="axis" '
            f'text-anchor="middle">{label}</text>'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" class="point" />'
        )

    first_day = records[0]["date"]
    last_day = records[-1]["date"]
    current_count = records[-1]["count"]
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
  <title id="title">{escape(repository)} GitHub Stars trend ({theme})</title>
  <desc id="desc">GitHub Stars increased from {records[0]["count"]} to {current_count} between {first_day} and {last_day}.</desc>
  <metadata>Generated by .github/scripts/update_stars_trend.py from .github/data/stars-history.json</metadata>
  <defs>
    <style>
      text {{ font-family: Inter, "Segoe UI", Arial, "Noto Sans CJK SC", sans-serif; }}
      .title {{ fill: {colors["title"]}; font-size: 22px; font-weight: 600; }}
      .subtitle {{ fill: {colors["subtitle"]}; font-size: 12px; }}
      .count {{ fill: {colors["count"]}; font-size: 24px; font-weight: 600; }}
      .count-label {{ fill: {colors["star"]}; font-size: 13px; font-weight: 600; }}
      .grid {{ stroke: {colors["grid"]}; stroke-width: 1; opacity: 0.65; }}
      .axis {{ fill: {colors["axis"]}; font-size: 11px; }}
      .line {{ fill: none; stroke: {colors["line"]}; stroke-width: 3; stroke-linecap: round; stroke-linejoin: round; }}
      .point {{ fill: {colors["point"]}; }}
    </style>
  </defs>
  <text x="60" y="34" class="title">Stars 趋势</text>
  <text x="60" y="56" class="subtitle">{escape(repository)} · {first_day} — {last_day}</text>
  <text x="860" y="34" class="count" text-anchor="end">{current_count}</text>
  <text x="860" y="55" class="count-label" text-anchor="end">★ Stars</text>
  {''.join(y_grid)}
  <polyline points="{line}" class="line" />
  {''.join(labels)}
  <text x="860" y="386" class="subtitle" text-anchor="end">更新于 {last_day}</text>
</svg>
'''
    ET.fromstring(svg)
    return svg


def write_if_changed(path: Path, content: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def main() -> int:
    args = parse_args()
    payload = validate_data(json.loads(args.data_file.read_text(encoding="utf-8")))
    repository = (args.repository or os.environ.get("GITHUB_REPOSITORY") or payload["repository"]).strip().lower()
    validate_data(payload, repository)
    snapshot_day = parse_day(args.date) if args.date else datetime.now(timezone.utc).date()
    count = args.star_count
    if count is None:
        count = fetch_star_count(repository, os.environ.get("GITHUB_TOKEN", "").strip())
    if count < 0:
        raise ValueError("Stars count must be non-negative")

    data_changed = update_record(payload, snapshot_day, count)
    if data_changed:
        write_if_changed(args.data_file, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    chart_changes = []
    for theme in THEMES:
        destination = args.output_dir / f"stars-trend-{theme}.svg"
        if write_if_changed(destination, render_svg(repository, payload["records"], theme)):
            try:
                chart_changes.append(str(destination.relative_to(ROOT)))
            except ValueError:
                chart_changes.append(str(destination))

    status = "updated" if data_changed or chart_changes else "current"
    print(f"[{status}] {repository}: {count} Stars on {snapshot_day.isoformat()}")
    for path in chart_changes:
        print(f"[rendered] {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
