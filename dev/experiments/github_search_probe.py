"""
Standalone GitHub subtitle search probe.

Use this script to quickly compare search strategies without touching app runtime state.
Examples:
  python github_search_probe.py --query "Food Wars" --strategy season --season 1
  python github_search_probe.py --query "Food Wars" --strategy name
  python github_search_probe.py --query "Terraformars" --strategy movie
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List

import requests


API_URL = "https://api.github.com/search/code"


@dataclass
class SearchConfig:
    owner: str
    repo: str
    token: str | None
    per_page: int
    timeout_sec: float
    max_pages: int


def normalize_query(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def build_headers(token: str | None) -> Dict[str, str]:
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "subtitle-search-probe",
    }
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def run_search_once(session: requests.Session, query: str, per_page: int, page: int, timeout_sec: float) -> requests.Response:
    params = {"q": query, "per_page": per_page, "page": page}
    return session.get(API_URL, params=params, timeout=timeout_sec)


def run_query_with_retry(
    session: requests.Session,
    query: str,
    per_page: int,
    page: int,
    timeout_sec: float,
    retries: int = 2,
) -> requests.Response | None:
    for attempt in range(retries + 1):
        try:
            resp = run_search_once(session, query, per_page, page, timeout_sec)
        except requests.RequestException as exc:
            print(f"[warn] network error (attempt {attempt + 1}/{retries + 1}): {exc}")
            if attempt < retries:
                time.sleep(1.0 + attempt)
                continue
            return None

        if resp.status_code == 500:
            print(f"[warn] GitHub returned 500 on page {page} (attempt {attempt + 1}/{retries + 1})")
            if attempt < retries:
                time.sleep(1.5 + attempt)
                continue
        return resp
    return None


def collect_items_for_query(cfg: SearchConfig, query: str) -> List[Dict]:
    session = requests.Session()
    session.headers.update(build_headers(cfg.token))
    out: List[Dict] = []
    seen_paths: set[str] = set()

    for page in range(1, cfg.max_pages + 1):
        resp = run_query_with_retry(
            session=session,
            query=query,
            per_page=cfg.per_page,
            page=page,
            timeout_sec=cfg.timeout_sec,
        )
        if resp is None:
            break

        if resp.status_code != 200:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            print(f"[warn] search failed: status={resp.status_code} page={page} query={query!r}")
            print(f"[warn] response: {body}")
            break

        payload = resp.json()
        items = payload.get("items", [])
        if not items:
            break

        for it in items:
            path = it.get("path")
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)
            out.append(
                {
                    "name": it.get("name") or os.path.basename(path),
                    "path": path,
                    "sha": it.get("sha"),
                    "url": it.get("html_url"),
                }
            )

        if len(items) < cfg.per_page:
            break
        time.sleep(0.12)

    return out


def build_strategy_queries(cfg: SearchConfig, anime_query: str, strategy: str, season: int | None) -> Iterable[str]:
    repo_prefix = f"repo:{cfg.owner}/{cfg.repo}"
    q = normalize_query(anime_query)

    if strategy == "season":
        s = int(season or 1)
        season_token = f"s{s:02d}"
        yield f'{repo_prefix} path:subtitles extension:srt in:path "{q} {season_token} Netflix"'
        yield f'{repo_prefix} path:subtitles extension:srt in:path "{q} {season_token} Amazon"'
        yield f'{repo_prefix} path:subtitles extension:ass in:path "{q} {season_token} Netflix"'
        yield f'{repo_prefix} path:subtitles extension:ass in:path "{q} {season_token} Amazon"'
        return

    if strategy == "name":
        yield f'{repo_prefix} path:subtitles/anime_tv extension:srt in:path "{q}"'
        yield f'{repo_prefix} path:subtitles/anime_tv extension:ass in:path "{q}"'
        yield f'{repo_prefix} path:subtitles/drama_tv extension:srt in:path "{q}"'
        yield f'{repo_prefix} path:subtitles/drama_tv extension:ass in:path "{q}"'
        return

    if strategy == "movie":
        for sub_path in ("subtitles/anime_movie", "subtitles/drama_movie", "subtitles/anime_tv", "subtitles/drama_tv"):
            yield f'{repo_prefix} path:{sub_path} extension:srt in:path "{q}"'
            yield f'{repo_prefix} path:{sub_path} extension:ass in:path "{q}"'
        return

    raise ValueError(f"Unknown strategy: {strategy}")


def dedupe_items(items: Iterable[Dict]) -> List[Dict]:
    out: List[Dict] = []
    seen: set[str] = set()
    for it in items:
        p = (it.get("path") or "").strip()
        if not p or p in seen:
            continue
        seen.add(p)
        out.append(it)
    out.sort(key=lambda x: (x.get("path") or "").lower())
    return out


def default_output_path(query: str, strategy: str) -> str:
    safe_query = re.sub(r"[^A-Za-z0-9._-]+", "_", normalize_query(query)).strip("_") or "query"
    ts = dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    folder = os.path.join("src", "SubtitlePlayer", "github_search")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"probe_{safe_query}_{strategy}_{ts}.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe GitHub subtitle search strategies.")
    parser.add_argument("--query", required=True, help="Anime/movie title query")
    parser.add_argument("--strategy", choices=("season", "name", "movie"), default="name")
    parser.add_argument("--season", type=int, default=1, help="Season number for --strategy season")
    parser.add_argument("--owner", default="Ajatt-Tools")
    parser.add_argument("--repo", default="kitsunekko-mirror")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    parser.add_argument("--per-page", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--timeout-sec", type=float, default=15.0)
    parser.add_argument("--save", action="store_true", help="Save full result JSON")
    args = parser.parse_args()

    cfg = SearchConfig(
        owner=str(args.owner),
        repo=str(args.repo),
        token=str(args.token) if args.token else None,
        per_page=max(1, min(int(args.per_page), 100)),
        timeout_sec=max(3.0, float(args.timeout_sec)),
        max_pages=max(1, int(args.max_pages)),
    )

    queries = list(build_strategy_queries(cfg, str(args.query), str(args.strategy), int(args.season)))
    all_items: List[Dict] = []
    print(f"[info] strategy={args.strategy} query={normalize_query(args.query)!r} generated_queries={len(queries)}")
    for idx, q in enumerate(queries, 1):
        print(f"[info] ({idx}/{len(queries)}) {q}")
        items = collect_items_for_query(cfg, q)
        print(f"[info]   -> items: {len(items)}")
        all_items.extend(items)

    deduped = dedupe_items(all_items)
    print(f"[result] unique subtitle candidates: {len(deduped)}")
    for sample in deduped[:20]:
        print(f" - {sample.get('path')}")

    if args.save:
        out_path = default_output_path(str(args.query), str(args.strategy))
        payload = {
            "query": normalize_query(args.query),
            "strategy": str(args.strategy),
            "season": int(args.season),
            "owner": cfg.owner,
            "repo": cfg.repo,
            "created_at_utc": dt.datetime.utcnow().isoformat() + "Z",
            "count": len(deduped),
            "items": deduped,
        }
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"[result] wrote {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

