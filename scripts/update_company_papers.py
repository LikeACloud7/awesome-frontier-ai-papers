#!/usr/bin/env python3
"""
Static data builder for the company-paper website.

This script collects papers for tracked AI companies/labs, merges them with the
existing archive, and writes data/company_papers.json for the Next.js static app.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = REPO_ROOT / "public" / "data" / "company_papers.json"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from fetch_papers import (  # noqa: E402
    date_in_range,
    enrich_papers,
    fetch_arxiv_company_papers,
    fetch_huggingface_company_papers,
    fetch_official_report_papers,
    fetch_openalex_company_papers,
    get_company_registry,
    get_paper_key,
    is_excluded_company_paper,
    is_frontier_ai_relevant_paper,
    load_config,
    merge_paper_lists,
    repair_text_encoding,
)


def apply_collection_overrides(
    config: dict,
    *,
    comprehensive: bool = False,
    include_openalex: bool = False,
    include_arxiv: bool = False,
) -> dict:
    """Runtime knobs for one-off comprehensive backfills without rewriting config."""
    tracking = config.setdefault("company_tracking", {})
    openalex_config = tracking.setdefault("openalex", {})
    arxiv_config = tracking.setdefault("arxiv_company_search", {})
    report_config = tracking.setdefault("official_reports", {})

    if comprehensive:
        include_openalex = True
        report_config["max_huggingface_org_repos"] = max(
            int(report_config.get("max_huggingface_org_repos", 0) or 0),
            20,
        )
        report_config["max_github_org_repos"] = max(
            int(report_config.get("max_github_org_repos", 0) or 0),
            20,
        )
        openalex_config["max_results_per_org"] = max(
            int(openalex_config.get("max_results_per_org", 0) or 0),
            3000,
        )

    if include_arxiv:
        arxiv_config["max_results_per_query"] = max(
            int(arxiv_config.get("max_results_per_query", 0) or 0),
            1000,
        )
        arxiv_config["query_chunk_size"] = min(
            int(arxiv_config.get("query_chunk_size", 8) or 8),
            8,
        )

    if include_openalex:
        openalex_config["enabled"] = True
    if include_arxiv:
        arxiv_config["enabled"] = True

    return config


def load_existing_archive() -> dict:
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"papers": [], "companies": []}


def truncate_text(value: str, limit: int) -> str:
    value = (value or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "..."


def clean_display_text(value: str) -> str:
    value = repair_text_encoding(html.unescape(html.unescape(str(value or "")))).replace("\xa0", " ")
    value = value.replace("\ufffd", " ")
    return " ".join(value.split())


def clean_display_list(values: list[str], limit: int) -> list[str]:
    return [clean_display_text(value) for value in values[:limit] if clean_display_text(value)]


def normalize_paper(paper: dict) -> dict:
    companies = paper.get("matched_orgs") or paper.get("companies", [])
    company_groups = paper.get("company_groups", [])
    company_regions = paper.get("company_regions", [])
    return {
        "id": get_paper_key(paper),
        "title": clean_display_text(paper.get("title", "")),
        "url": paper.get("url", ""),
        "published": paper.get("published", ""),
        "authors": clean_display_list(paper.get("authors", []), 12),
        "abstract": truncate_text(clean_display_text(paper.get("abstract", "")), 900),
        "companies": companies,
        "matched_orgs": companies,
        "company_groups": company_groups,
        "company_regions": company_regions,
        "sources": paper.get("sources") or [paper.get("source", "unknown")],
        "source": paper.get("source", "unknown"),
        "work_type": paper.get("work_type", ""),
        "doi": paper.get("doi", ""),
        "openalex_id": paper.get("openalex_id", ""),
        "cited_by_count": int(paper.get("cited_by_count", 0) or 0),
        "quality_score": int(paper.get("quality_score", 0) or 0),
        "matched_keywords": clean_display_list(paper.get("matched_keywords", []), 20),
        "author_affiliations": clean_display_list(paper.get("author_affiliations", []), 40),
        "concepts": paper.get("concepts", [])[:8],
        "official_report": bool(paper.get("official_report", False)),
        "quality_signals": paper.get("quality_signals", {}),
    }


def company_sort_key(company: dict) -> tuple:
    latest = company.get("latest_paper_date") or ""
    count = company.get("paper_count", 0)
    return (latest, count, company.get("name", ""))


def build_company_rows(registry: list[dict], papers: list[dict]) -> list[dict]:
    rows = []
    for org in registry:
        if not org.get("group_id", "").startswith("company_"):
            continue

        org_papers = [
            paper for paper in papers
            if org["name"] in paper.get("companies", [])
        ]
        latest = max([p.get("published", "") for p in org_papers], default="")
        rows.append({
            "id": org["name"].lower().replace("/", "-").replace(".", "").replace(" ", "-"),
            "name": org["name"],
            "group_id": org.get("group_id", ""),
            "group_name": org.get("group_name", ""),
            "region": org.get("region", ""),
            "aliases": org.get("aliases", []),
            "paper_count": len(org_papers),
            "latest_paper_date": latest,
        })

    return sorted(rows, key=company_sort_key, reverse=True)


def collect_fresh_papers_by_source(
    config: dict,
    registry: list[dict],
    days: int,
    since: str | None = None,
) -> list[dict]:
    collectors = {
        "openalex": lambda: fetch_openalex_company_papers(config, registry, days, since=since),
        "huggingface": lambda: fetch_huggingface_company_papers(config, registry, days, since=since),
        "official_reports": lambda: fetch_official_report_papers(config, registry, days, since=since),
        "arxiv": lambda: fetch_arxiv_company_papers(config, registry, days, since=since),
    }
    merge_order = ["official_reports", "openalex", "huggingface", "arxiv"]
    results: dict[str, list[dict]] = {}

    with ThreadPoolExecutor(max_workers=len(collectors)) as executor:
        futures = {
            executor.submit(collector): source
            for source, collector in collectors.items()
        }
        for future in as_completed(futures):
            source = futures[future]
            try:
                results[source] = future.result()
            except Exception as e:
                print(f"{source} 수집 실패: {e}", file=sys.stderr)
                results[source] = []

    return merge_paper_lists(*(results[source] for source in merge_order))


def source_notes_for_config(config: dict) -> list[str]:
    tracking = config.get("company_tracking", {})
    openalex_enabled = tracking.get("openalex", {}).get("enabled", True)
    arxiv_enabled = tracking.get("arxiv_company_search", {}).get("enabled", True)
    report_config = tracking.get("official_reports", {})

    notes = [
        "Configured official company publication pages and feeds are collected where available.",
        "Official company technical reports are collected from configured company-owned HuggingFace and GitHub repositories.",
        "HuggingFace Papers search is accepted only when organization or author metadata matches a tracked company.",
        "Default archive filtering keeps explicit reports or papers matching configured frontier-AI model keywords.",
    ]
    if openalex_enabled:
        notes.append("OpenAlex authorship institution metadata is collected to catch papers whose author affiliations name a tracked lab.")
    else:
        notes.append("OpenAlex authorship institution metadata is disabled for this run.")
    if arxiv_enabled:
        notes.append("arXiv company-name search is enabled for metadata/text matches in configured AI categories.")
    else:
        notes.append("arXiv company-name fallback search is disabled for this run.")
    if int(report_config.get("max_huggingface_org_repos", 0) or 0) > 0:
        notes.append("Company-owned HuggingFace model repositories are scanned for PDF technical reports.")
    notes.append("PDF-only affiliations not exposed by OpenAlex, arXiv metadata, or configured official sources may still require an additional PDF-text adapter.")
    return notes


def update_archive(
    days: int,
    max_papers: int,
    since: str | None = None,
    comprehensive: bool = False,
    include_openalex: bool = False,
    include_arxiv: bool = False,
) -> dict:
    config = apply_collection_overrides(
        load_config(),
        comprehensive=comprehensive,
        include_openalex=include_openalex,
        include_arxiv=include_arxiv,
    )
    registry = get_company_registry(config)
    existing = load_existing_archive()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if since:
        print(f"Collecting company papers since {since}...", file=sys.stderr)
    else:
        print(f"Collecting company papers for the last {days} days...", file=sys.stderr)

    fresh_papers = enrich_papers(
        collect_fresh_papers_by_source(config, registry, days, since=since),
        config,
        registry,
        allow_text_org_matches=False,
    )
    if since and not fresh_papers:
        raise RuntimeError(
            "No papers were collected for a full backfill. "
            "Check network access and source configuration before overwriting the archive."
        )

    existing_papers = [] if since else existing.get("papers", [])
    merged = merge_paper_lists(existing_papers, [normalize_paper(p) for p in fresh_papers])
    normalized = [
        normalize_paper(p)
        for p in enrich_papers(merged, config, registry, allow_text_org_matches=False)
    ]
    normalized = [
        paper for paper in normalized
        if (
            paper.get("companies")
            and paper.get("title")
            and paper.get("url")
            and date_in_range(paper.get("published", ""), "0000-01-01", today)
            and (not since or date_in_range(paper.get("published", ""), since, today))
            and (
                paper.get("source") in {"official_report", "official_repository_scan"}
                or is_frontier_ai_relevant_paper(paper, config)
            )
            and not is_excluded_company_paper(paper, config)
        )
    ]
    normalized.sort(
        key=lambda p: (p.get("published", ""), p.get("quality_score", 0), p.get("title", "")),
        reverse=True,
    )
    normalized = normalized[:max_papers]

    archive = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "collection": {
            "since": since,
            "days": days if not since else None,
            "comprehensive": comprehensive,
            "openalex_enabled": config.get("company_tracking", {}).get("openalex", {}).get("enabled", True),
            "arxiv_company_search_enabled": config.get("company_tracking", {}).get("arxiv_company_search", {}).get("enabled", True),
        },
        "source_notes": source_notes_for_config(config),
        "totals": {
            "papers": len(normalized),
            "companies": len([c for c in build_company_rows(registry, normalized) if c["paper_count"] > 0]),
            "tracked_companies": len([o for o in registry if o.get("group_id", "").startswith("company_")]),
        },
        "companies": build_company_rows(registry, normalized),
        "papers": normalized,
    }

    DATA_FILE.parent.mkdir(exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(archive, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")

    return archive


def main():
    parser = argparse.ArgumentParser(description="Update static company-paper archive")
    parser.add_argument("--days", type=int, default=30, help="Lookback window for fresh collection")
    parser.add_argument("--since", help="Collect fresh papers from YYYY-MM-DD instead of using --days")
    parser.add_argument("--max-papers", type=int, default=2000, help="Maximum papers kept in the static archive")
    parser.add_argument("--comprehensive", action="store_true", help="Enable OpenAlex affiliation and official repository scans for a 2024+ backfill")
    parser.add_argument("--include-openalex", action="store_true", help="Enable OpenAlex affiliation collection for this run")
    parser.add_argument("--include-arxiv", action="store_true", help="Enable arXiv company-name fallback search for this run")
    args = parser.parse_args()

    archive = update_archive(
        args.days,
        args.max_papers,
        args.since,
        comprehensive=args.comprehensive,
        include_openalex=args.include_openalex,
        include_arxiv=args.include_arxiv,
    )
    print(
        f"Wrote {DATA_FILE} with {archive['totals']['papers']} papers "
        f"across {archive['totals']['companies']} companies.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
