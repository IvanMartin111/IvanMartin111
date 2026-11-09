#!/usr/bin/env python3
"""Generate an aggregate monthly commit chart for the GitHub profile README."""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


USERNAME = "IvanMartin111"
EXTRA_REPOSITORIES = ("chaconlab/AbRaCD", "chaconlab/RCD")
API_ROOT = "https://api.github.com"
OUTPUT = Path("assets/statistics/activity.svg")
MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def api_get(path: str, token: str, params: dict[str, str | int] | None = None):
    query = f"?{urlencode(params)}" if params else ""
    request = Request(
        f"{API_ROOT}{path}{query}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "IvanMartin111-profile-statistics",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        if error.code == 409 and path.endswith("/commits"):
            return []
        raise RuntimeError(f"GitHub API returned HTTP {error.code}.") from None
    except URLError as error:
        raise RuntimeError("Could not connect to the GitHub API.") from None


def month_window(now: datetime) -> tuple[datetime, list[tuple[int, int]]]:
    current_index = now.year * 12 + now.month - 1
    first_index = current_index - 11
    first_year, first_month_zero = divmod(first_index, 12)
    start = datetime(first_year, first_month_zero + 1, 1, tzinfo=timezone.utc)
    months = []
    for index in range(first_index, current_index + 1):
        year, month_zero = divmod(index, 12)
        months.append((year, month_zero + 1))
    return start, months


def owned_repositories(token: str) -> set[str]:
    repositories: set[str] = set()
    page = 1
    while True:
        batch = api_get(
            "/user/repos",
            token,
            {"affiliation": "owner", "visibility": "all", "per_page": 100, "page": page},
        )
        if not isinstance(batch, list):
            raise RuntimeError("GitHub returned an unexpected repository list.")
        repositories.update(repo["full_name"] for repo in batch if repo.get("full_name"))
        if len(batch) < 100:
            break
        page += 1
    repositories.update(EXTRA_REPOSITORIES)
    return repositories


def authored_commits(token: str, repositories: set[str], start: datetime, now: datetime):
    since = start.isoformat(timespec="seconds").replace("+00:00", "Z")
    until = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    counts: Counter[tuple[int, int]] = Counter()
    seen_shas: set[str] = set()

    for full_name in sorted(repositories):
        owner, repo = full_name.split("/", 1)
        page = 1
        while True:
            batch = api_get(
                f"/repos/{owner}/{repo}/commits",
                token,
                {
                    "author": USERNAME,
                    "since": since,
                    "until": until,
                    "per_page": 100,
                    "page": page,
                },
            )
            if not isinstance(batch, list):
                raise RuntimeError("GitHub returned an unexpected commit list.")

            for item in batch:
                author = item.get("author") or {}
                sha = item.get("sha")
                login = (author.get("login") or "").casefold()
                if login != USERNAME.casefold() or not sha or sha in seen_shas:
                    continue
                commit_date = ((item.get("commit") or {}).get("committer") or {}).get("date")
                if not commit_date:
                    continue
                date = datetime.fromisoformat(commit_date.replace("Z", "+00:00")).astimezone(timezone.utc)
                if start <= date <= now:
                    counts[(date.year, date.month)] += 1
                    seen_shas.add(sha)

            if len(batch) < 100:
                break
            page += 1

    return counts


def render_svg(months: list[tuple[int, int]], counts: Counter[tuple[int, int]]) -> str:
    width, height = 480, 96
    plot_left, plot_right = 28, 472
    plot_top, plot_bottom = 36, 70
    plot_height = plot_bottom - plot_top
    slot_width = (plot_right - plot_left) / len(months)
    bar_width = min(19.0, slot_width * 0.58)
    peak = max((counts[month] for month in months), default=0)
    scale_peak = max(peak, 1)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<style>text{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;fill:#777}.title{font-size:15px;fill:#0366d6}.subtitle{font-size:10px}.month{font-size:9px}.value{font-size:9px}.grid{stroke:#8c959f;stroke-opacity:.24;stroke-width:1}.bar{fill:#167d8d}</style>",
        '<text class="title" x="12" y="20">Monthly authored commits</text>',
        '<text class="subtitle" x="468" y="19" text-anchor="end">Last 12 months</text>',
    ]

    for fraction in (0, 0.5, 1):
        y = plot_bottom - fraction * plot_height
        parts.append(f'<line class="grid" x1="{plot_left}" y1="{y:.1f}" x2="{plot_right}" y2="{y:.1f}"/>')

    for index, month in enumerate(months):
        value = counts[month]
        center = plot_left + (index + 0.5) * slot_width
        bar_height = (value / scale_peak) * plot_height if value else 0
        x = center - bar_width / 2
        y = plot_bottom - bar_height
        if value:
            parts.append(f'<rect class="bar" x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" rx="2"/>')
            value_y = max(plot_top - 2, y - 3)
            parts.append(f'<text class="value" x="{center:.1f}" y="{value_y:.1f}" text-anchor="middle">{value}</text>')
        label = f"{MONTH_NAMES[month[1] - 1]} {str(month[0])[-2:]}"
        parts.append(f'<text class="month" x="{center:.1f}" y="87" text-anchor="middle">{label}</text>')

    if peak == 0:
        parts.append('<text class="subtitle" x="250" y="56" text-anchor="middle">No attributed commits found in this period</text>')

    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> int:
    token = os.environ.get("METRICS_TOKEN", "").strip()
    if not token:
        print("METRICS_TOKEN is required to generate the contribution chart.", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    start, months = month_window(now)
    repositories = owned_repositories(token)
    counts = authored_commits(token, repositories, start, now)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render_svg(months, counts), encoding="utf-8")
    print(f"Generated a 12-month aggregate chart from {len(repositories)} accessible repositories.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, KeyError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
