#!/usr/bin/env python3
"""
Static data builder for the company-paper website.

This script collects papers for tracked AI companies/labs, merges them with the
existing archive, and writes data/company_papers.json for the Next.js static app.
"""

import argparse
import copy
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import html
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = REPO_ROOT / "public" / "data" / "company_papers.json"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from collection_status import collect_with_status, record_source_error, safe_message
from publication_sources import enrich_publication

from fetch_papers import (  # noqa: E402
    date_in_range,
    ai_topic_evidence,
    enrich_papers,
    fetch_arxiv_company_papers,
    fetch_official_publication_page,
    fetch_huggingface_company_papers,
    fetch_official_report_papers,
    fetch_openalex_company_papers,
    get_company_registry,
    get_paper_key,
    OUTPUT_DIR,
    is_excluded_company_paper,
    is_frontier_ai_relevant_paper,
    load_config,
    merge_paper_lists,
    repair_text_encoding,
)


def apply_collection_overrides(config: dict, *, comprehensive: bool = False,
                               include_openalex: bool = False, include_arxiv: bool = False) -> dict:
    config = copy.deepcopy(config)
    tracking = config.setdefault("company_tracking", {})
    if comprehensive or include_openalex:
        tracking.setdefault("openalex", {})["enabled"] = True
    if include_arxiv:
        # Broad company mentions are candidates, not proof of authorship.
        tracking.setdefault("arxiv_affiliations", {})["enabled"] = True
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
        "url": (paper.get("url", "") or "").strip(),
        "paper_url": (paper.get("paper_url", "") or "").strip(),
        "alternate_urls": list(dict.fromkeys(url.strip() for url in paper.get("alternate_urls", []) if url.strip())),
        "alternate_titles": paper.get("alternate_titles", []),
        "published": paper.get("published", ""),
        "authors": clean_display_list(paper.get("authors", []), 1000),
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
        "evidence": paper.get("evidence", []),
        "topic_evidence": paper.get("topic_evidence", {}),
        "ai_research_source": paper.get("ai_research_source", ""),
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


def collect_fresh_papers_by_source(config: dict, registry: list[dict], days: int,
                                   since: str | None = None, *, diagnostics: list | None = None,
                                   seed_papers: list | None = None, sources: set | None = None) -> list[dict]:
    from affiliation_discovery import fetch_affiliation_papers
    from repository_sources import collect_repository_reports
    tracking = config.get("company_tracking", {})
    start = since or (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    jobs = []
    def official(org, source):
        papers = fetch_official_publication_page(org, source, start, end)
        if source.get("topic_scope") == "ai":
            label = source.get("url") or "https://huggingface.co/" + source.get("hf_orgs", [org["name"]])[0] + "/papers"
            for paper in papers:
                paper["ai_research_source"] = label
        return papers
    def add(org, family, name, collector):
        if sources is None or family in sources:
            jobs.append((org["name"], name, collector))
    for org in registry:
        for source in org.get("official_publication_pages", []):
            name = "official:" + source["type"] + ":" + (source.get("url") or ",".join(source.get("hf_orgs", [])))
            add(org, "official", name, lambda o=org, s=source: official(o, s))
        if tracking.get("official_reports", {}).get("enabled", True):
            report_org = {**org, "official_publication_pages": []}
            add(org, "repositories", "repositories", lambda o=report_org: collect_repository_reports(config, o, days, since=since))
        if tracking.get("openalex", {}).get("enabled", True):
            add(org, "openalex", "openalex", lambda o=org: fetch_openalex_company_papers(config, [o], days, since=since))
        if tracking.get("huggingface_search", {}).get("enabled", True):
            add(org, "huggingface", "huggingface_search", lambda o=org: fetch_huggingface_company_papers(config, [o], days, since=since))
        if tracking.get("arxiv_affiliations", {}).get("enabled", True):
            add(org, "affiliations", "arxiv_affiliations", lambda o=org: fetch_affiliation_papers(config, o, seed_papers or [], days, since))
    results = []
    with ThreadPoolExecutor(max_workers=int(tracking.get("max_workers", 4))) as executor:
        futures = [executor.submit(collect_with_status, lab, name, collector) for lab, name, collector in jobs]
        for future in as_completed(futures):
            papers, status = future.result()
            if diagnostics is not None:
                diagnostics.append(status)
            results.extend(papers)
            print(f"[{status['status']}] {status['lab']} / {status['source']}: {len(papers)} records", file=sys.stderr)
    return merge_paper_lists(results)


def source_notes_for_config(config: dict) -> list[str]:
    return [
        "Tracks AI research authored by the configured US and Chinese frontier labs, including papers, technical reports and research posts.",
        "Official publication catalogues are paginated; sparse records are checked against abstracts and research metadata before AI filtering.",
        "Affiliation evidence is required for search results; a third-party paper mentioning a lab's model does not establish authorship.",
        "Daily recent collection is supplemented by an automatic rotating historical reconciliation; backfills merge with the existing archive.",
        "Per-source failures, unresolved metadata and historical reconciliation status are recorded separately from archive totals.",
        "No public index guarantees every publication. Missing affiliations, unavailable sources and pending scans remain explicit coverage limits.",
    ]


def atomic_json(path: Path, value, *, compact: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, **({"separators": (",", ":")} if compact else {"indent": 2}))
        stream.write("\n")
    temporary.replace(path)


def filter_new_papers(papers: list[dict], config: dict, diagnostics: list, pending: list) -> list[dict]:
    accepted = []
    sparse = []
    for paper in papers:
        if is_frontier_ai_relevant_paper(paper, config):
            accepted.append(paper)
        elif len(paper.get("abstract", "")) < 300:
            sparse.append(paper)
        # A complete non-AI abstract has already supplied the topic evidence.
    budget = int(config.get("company_tracking", {}).get("metadata_checks_per_run", 200))
    selected = sparse[:budget] if budget else sparse
    for paper in sparse[len(selected):]:
        pending.append({"paper": paper, "reason": "queued for automatic detail metadata check"})
    def evaluate(paper):
        if is_frontier_ai_relevant_paper(paper, config):
            return paper, None
        try:
            enriched = enrich_publication(paper, OUTPUT_DIR / "publication_metadata")
            if is_frontier_ai_relevant_paper(enriched, config):
                return enriched, None
            if len(enriched.get("abstract", "")) < 120:
                return None, {"paper": enriched, "reason": "insufficient AI topic metadata"}
            return None, None
        except Exception as error:
            return None, {"paper": paper, "reason": safe_message(error)}
    with ThreadPoolExecutor(max_workers=4) as executor:
        for paper, unresolved in executor.map(evaluate, selected):
            if paper:
                accepted.append(paper)
            if unresolved:
                pending.append(unresolved)
    for paper in accepted:
        paper["topic_evidence"] = ai_topic_evidence(paper, config)
    return accepted


def update_archive(days: int = 30, max_papers: int = 0, since: str | None = None,
                   comprehensive: bool = False, include_openalex: bool = False,
                   include_arxiv: bool = False, *, output: Path | None = None,
                   labs: list[str] | None = None, sources: set | None = None,
                   reconcile: bool = False) -> dict:
    config = apply_collection_overrides(load_config(), comprehensive=comprehensive,
        include_openalex=include_openalex, include_arxiv=include_arxiv)
    full_registry = get_company_registry(config)
    registry = [o for o in full_registry if not labs or o["name"] in labs]
    if labs and len(registry) != len(set(labs)):
        raise ValueError("Unknown lab name. Use the exact configured lab name.")
    destination = output or DATA_FILE
    existing = json.loads(destination.read_text()) if destination.exists() else (
        load_existing_archive() if destination != DATA_FILE else {"papers": []})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    diagnostics = []
    fresh = collect_fresh_papers_by_source(config, registry, days, since,
        diagnostics=diagnostics, seed_papers=existing.get("papers", []), sources=sources)
    state_file = destination.parent / "collection_state.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {"next_lab": 0, "labs": {}}
    if reconcile:
        index = int(state.get("next_lab", 0)) % len(registry)
        org = registry[index]
        history_since = config.get("company_tracking", {}).get("archive_since", "2024-01-01")
        historical_status = []
        historical = collect_fresh_papers_by_source(config, [org], days, history_since,
            diagnostics=historical_status, seed_papers=existing.get("papers", []), sources=sources)
        for item in historical_status:
            item["mode"] = "historical"
            item["since"] = history_since
        diagnostics.extend(historical_status)
        fresh = merge_paper_lists(fresh, historical)
        successful = bool(historical_status) and all(s["status"] == "ok" for s in historical_status)
        previous = state.setdefault("labs", {}).get(org["name"], {})
        state["labs"][org["name"]] = {**previous, "last_attempt": today,
            "status": "ok" if successful else "partial", "since": history_since}
        if successful:
            state["labs"][org["name"]]["last_success"] = today
        state["next_lab"] = (index + 1) % len(registry)
    pending_file = destination.parent / "collection_pending.json"
    old_pending = json.loads(pending_file.read_text()) if pending_file.exists() else []
    # Retry metadata failures automatically. Carry the rest forward, never silently drop them.
    retry_limit = 200
    fresh = merge_paper_lists([x["paper"] for x in old_pending[:retry_limit]], fresh)
    pending = list(old_pending[retry_limit:])
    fresh = filter_new_papers(fresh, config, diagnostics, pending)
    fresh = enrich_papers(fresh, config, full_registry, allow_text_org_matches=False)
    # A since date bounds acquisition only. It must never delete existing records.
    merged = merge_paper_lists(copy.deepcopy(existing.get("papers", [])), fresh)
    normalized = [normalize_paper(p) for p in merged if p.get("title") and p.get("url")
        and (p.get("matched_orgs") or p.get("companies"))
        and date_in_range(p.get("published", ""), "0000-01-01", today)
        and not is_excluded_company_paper(p, config)]
    normalized.sort(key=lambda p: (p.get("published", ""), p.get("quality_score", 0), p.get("title", "")), reverse=True)
    if max_papers and len(normalized) > max_papers:
        raise ValueError(f"Archive has {len(normalized)} records; refusing to delete papers to satisfy --max-papers={max_papers}. Use 0 for unlimited.")
    failed = [s for s in diagnostics if s["status"] == "failed"]
    partial = [s for s in diagnostics if s["status"] == "partial"]
    errored = [s for s in diagnostics if s["errors"]]
    previous_status = {(s["lab"], s["source"]):s for s in existing.get("collection", {}).get("sources", [])}
    for status in diagnostics:
        old = previous_status.get((status["lab"], status["source"]), {})
        status["last_success"] = status["checked_at"] if status["status"] == "ok" else old.get("last_success")
    pending = list({get_paper_key(x["paper"]):x for x in pending}.values())
    archive = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "collection": {"scope": "frontier_lab_ai_research", "since": since, "days": days,
            "selected_labs": labs, "selected_sources": sorted(sources) if sources else None,
            "status": "partial" if failed or partial or pending else "ok",
            "source_count": len(diagnostics), "failed_sources": len(failed), "partial_sources": len(partial),
            "error_sources": len(errored),
            "pending_metadata": len(pending), "sources": diagnostics,
            "historical_reconciliation": state, "comprehensive": comprehensive,
            "openalex_enabled": config["company_tracking"].get("openalex", {}).get("enabled", True),
            "arxiv_company_search_enabled": False},
        "source_notes": source_notes_for_config(config),
        "totals": {"papers": len(normalized), "companies": len({c for p in normalized for c in p["companies"]}),
                   "tracked_companies": len(full_registry)},
        "companies": build_company_rows(full_registry, normalized), "papers": normalized}
    # Archive first: a crash can cause a harmless retry, not a lost checkpoint.
    atomic_json(destination, archive, compact=True)
    atomic_json(pending_file, pending, compact=True)
    atomic_json(state_file, state)
    atomic_json(destination.parent / "collection_health.json", archive["collection"])
    return archive


def main():
    parser = argparse.ArgumentParser(description="Automatically track AI publications from frontier labs")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--since", help="Historical acquisition start; existing papers are preserved")
    parser.add_argument("--max-papers", type=int, default=0, help="Safety ceiling (0=unlimited); never truncates existing papers")
    parser.add_argument("--comprehensive", action="store_true")
    parser.add_argument("--include-openalex", action="store_true")
    parser.add_argument("--include-arxiv", action="store_true", help="Enable affiliation-verified arXiv discovery")
    parser.add_argument("--reconcile", action="store_true", help="Also reconcile the next lab's history, advancing a persistent queue")
    parser.add_argument("--lab", action="append", dest="labs")
    parser.add_argument("--sources", help="Comma-separated official,openalex,huggingface,repositories,affiliations")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true", help="Exit nonzero after preserving results if a source failed")
    args = parser.parse_args()
    archive = update_archive(args.days, args.max_papers, args.since,
        comprehensive=args.comprehensive, include_openalex=args.include_openalex,
        include_arxiv=args.include_arxiv, output=args.output, labs=args.labs,
        sources=set(args.sources.split(",")) if args.sources else None, reconcile=args.reconcile)
    health = archive["collection"]
    print(f"Wrote {archive['totals']['papers']} papers; collection={health['status']}, "
          f"sources_with_errors={health['error_sources']}, failed={health['failed_sources']}, partial={health['partial_sources']}, pending_metadata={health['pending_metadata']}", file=sys.stderr)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write(f"## Publication collection: {health['status']}\n\n"
                f"{archive['totals']['papers']} archived papers. {health['error_sources']} sources with errors; "
                f"{health['partial_sources']} partial sources; {health['pending_metadata']} records awaiting metadata.\n")
            for item in health['sources']:
                if item['status'] != 'ok':
                    stream.write(f"- {item['lab']} / {item['source']}: {item['status']}\n")
    if args.strict and (health["failed_sources"] or any(s['errors'] for s in health['sources'])):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
