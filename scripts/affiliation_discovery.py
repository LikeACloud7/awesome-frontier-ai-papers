"""Discover arXiv candidates and require author-affiliation evidence.

Model mentions in abstracts or references never establish lab authorship.
The rotating author queue and pending candidates survive daily runs.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

from bs4 import BeautifulSoup
from pdf_text import first_page_text
from safe_http import MAX_PDF_BYTES

import fetch_papers as f
from collection_status import http_get, record_source_error, record_source_limit


def affiliation_terms(org: dict):
    # Deliberately exclude model-family aliases (GPT, Phi, GLM, etc.).
    return org.get("affiliation_terms") or org.get("openalex_search_terms") or [org["name"]]


def match_affiliation_text(value: str, org: dict):
    normalized = re.sub(r"(?<=\d)(?=[A-Za-z])|(?<=[A-Za-z])(?=\d)", " ", value or "")
    return [term for term in affiliation_terms(org)
            if re.search(f.term_pattern(term), normalized, re.I)]


def affiliation_evidence(arxiv_id: str, org: dict) -> dict:
    response = http_get(f"https://arxiv.org/html/{arxiv_id}")
    if response.status_code == 200:
        soup = BeautifulSoup(response.text, "html.parser")
        blocks = soup.select(".ltx_authors .ltx_affiliation, .ltx_authors .ltx_role_affiliation, .ltx_authors .ltx_contact, .ltx_authors")
        for block in blocks:
            value = block.get_text(" ", strip=True)
            matched = match_affiliation_text(value, org)
            if matched:
                return {"affiliation_source": "arXiv author block", "matched_affiliation_terms": matched,
                        "affiliation_evidence": value[:1500], "evidence_url": f"https://arxiv.org/html/{arxiv_id}"}
        if soup.select(".ltx_authors .ltx_affiliation, .ltx_authors .ltx_role_affiliation"):
            return {}
    elif response.status_code not in {404, 406}:
        response.raise_for_status()
    response = http_get(f"https://arxiv.org/pdf/{arxiv_id}", timeout=60, max_bytes=MAX_PDF_BYTES)
    response.raise_for_status()
    first = first_page_text(response.content)
    # Only the author/affiliation block preceding Abstract/Introduction is evidence.
    # Do not scan references or accept arbitrary mentions elsewhere in the PDF.
    boundary = re.search(r"(?im)^\s*(?:abstract\b|1\s+introduction\b)", first)
    if not boundary:
        boundary = re.search(r"\bAbstract\b", first[:5000])
    if not boundary:
        raise ValueError(f"Cannot isolate arXiv author affiliation block for {arxiv_id}")
    block = first[:boundary.start()]
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    evidence = []
    for index, line in enumerate(lines):
        matched = match_affiliation_text(line, org)
        # Organization names in a title are insufficient; require an affiliation
        # marker, organizational address/email, or a line naming an institution.
        if matched and (re.search(r"(?:^|\s)[\d*†‡]+|@|university|academy|laborator|research|group|team|inc\.?|ltd\.?", line, re.I)
                        or (index > 0 and len(line) < 180 and any(line.casefold().strip(' ,.') == t.casefold() for t in matched))):
            evidence.append(line)
    value = " ".join(evidence)
    matched = match_affiliation_text(value, org)
    if not matched:
        return {}
    return {"affiliation_source": "arXiv PDF author affiliation block", "matched_affiliation_terms": matched,
            "affiliation_evidence": value[:1500], "evidence_url": f"https://arxiv.org/pdf/{arxiv_id}"}


def fetch_affiliation_papers(config: dict, org: dict, seed_papers: list[dict], days: int, since: str | None = None):
    settings = config.get("company_tracking", {}).get("arxiv_affiliations", {})
    if not settings.get("enabled", True):
        return []
    root = f.OUTPUT_DIR / "affiliation_queue"
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / (hashlib.sha256(org["name"].encode()).hexdigest()[:16] + ".json")
    try:
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
    except ValueError:
        state = {}
    pending = state.get("pending", {})
    checked = state.get("checked", {})
    for aid, check in list(checked.items()):
        if not check.get("matched") and check.get("checked_at"):
            age = datetime.now() - datetime.fromisoformat(check["checked_at"])
            if age.days >= 30:
                checked.pop(aid)
    known = {f.get_arxiv_id(p.get("paper_url") or p.get("url", "")) for p in seed_papers
             if org["name"] in (p.get("companies") or p.get("matched_orgs") or [])}
    pending = {aid: paper for aid, paper in pending.items() if aid not in known}
    cutoff_month = (config.get("company_tracking", {}).get("archive_since", "2024-01-01"))[2:4] + config.get("company_tracking", {}).get("archive_since", "2024-01-01")[5:7]
    repo_path = f.OUTPUT_DIR / "repository_scans" / state_path.name
    if repo_path.exists():
        for aid in json.loads(repo_path.read_text()).get("candidates", {}):
            if aid not in checked and aid not in known and aid[:4] >= cutoff_month:
                pending.setdefault(aid, {"id": f"arxiv:{aid}", "url": f"https://arxiv.org/abs/{aid}"})
    query_offsets = state.get("query_offsets", {})
    author_counts = {}
    for p in seed_papers:
        if org["name"] not in (p.get("companies") or p.get("matched_orgs") or []):
            continue
        for author in p.get("authors", []):
            if len(author.split()) >= 2 and author not in {org["name"], "et al."} and len(author) < 100:
                author_counts[author] = author_counts.get(author, 0) + 1
    authors = state.get("authors", [])
    existing_authors = set(authors)
    authors.extend(sorted((a for a in author_counts if a not in existing_authors), key=lambda a: (-author_counts[a], a)))
    batch_size = int(settings.get("authors_per_run", 6))
    scope = since or "recent"
    batches = [authors[index:index + batch_size] for index in range(0, len(authors), batch_size)]
    batch_indices = state.get("author_batches", {})
    batch_index = int(batch_indices.get(scope, 0)) % max(len(batches), 1)
    selected = batches[batch_index] if batches else []
    queries = []
    terms = affiliation_terms(org)
    queries.append("(" + " OR ".join(f.arxiv_quote(term) for term in terms) + ")")
    if selected:
        queries.append("(" + " OR ".join('au:"' + a.replace('"', '') + '"' for a in selected) + ")")
    discovery_failed = False
    author_batch_complete = not selected
    for query in queries:
        try:
            query_key = hashlib.sha256((query + "|" + (since or "recent")).encode()).hexdigest()
            cap = int(settings.get("candidates_per_query", 200))
            query_offset = int(query_offsets.get(query_key, 0))
            candidates = f.fetch_arxiv_query(query, days, cap,
                since=since, request_delay_seconds=3.1, start_offset=query_offset)
            query_offsets[query_key] = query_offset + cap if len(candidates) == cap else 0
            if query.startswith('(au:'):
                author_batch_complete = len(candidates) < cap
            if len(candidates) == cap:
                record_source_limit("arXiv discovery continues from its saved result offset on the next run")
            for paper in candidates:
                aid = f.get_arxiv_id(paper.get("url", ""))
                if aid not in checked and aid not in known:
                    pending[aid] = paper
        except Exception as error:
            discovery_failed = True
            record_source_error(error)
    papers = [entry["paper"] for entry in checked.values() if entry.get("paper")]
    max_checks = int(settings.get("pdf_checks_per_run", 30))
    selected_ids = sorted(pending, key=lambda aid: pending[aid].get("last_attempt", ""))[:max_checks]
    for aid in selected_ids:
        try:
            evidence = affiliation_evidence(aid, org)
            paper = dict(pending[aid])
            if evidence and not paper.get("title"):
                response = http_get(f"https://arxiv.org/abs/{aid}")
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                def meta(name):
                    node = soup.find("meta", attrs={"name": name})
                    return node.get("content", "") if node else ""
                paper.update({"title": meta("citation_title"),
                    "authors": [m.get("content", "") for m in soup.find_all("meta", attrs={"name": "citation_author"})],
                    "published": meta("citation_date").replace("/", "-")[:10],
                    "abstract": (soup.select_one(".abstract").get_text(" ", strip=True) if soup.select_one(".abstract") else "")})
                if not paper["title"]:
                    raise ValueError(f"Missing arXiv metadata for {aid}")
            pending.pop(aid)
            checked[aid] = {"matched": bool(evidence), "checked_at": datetime.now().isoformat(timespec="seconds")}
            if evidence:
                paper.update({"id": f"arxiv:{aid}", "source": "arxiv_affiliation", "matched_orgs": [org["name"]],
                    "affiliations": [org["name"]], "author_affiliations": evidence["matched_affiliation_terms"],
                    "quality_signals": {**evidence, "company_match_source": "verified arXiv author affiliation"}})
                papers.append(paper)
                checked[aid]["paper"] = paper
        except Exception as error:
            record_source_error(error)
            pending[aid]["last_attempt"] = datetime.now().isoformat(timespec="seconds")
    if pending:
        record_source_limit(f"{len(pending)} affiliation candidates queued for the next run")
    if len(authors) > len(selected):
        record_source_limit(f"Author discovery rotates {len(selected)} of {len(authors)} known authors per run")
    if not discovery_failed and author_batch_complete:
        batch_indices[scope] = (batch_index + 1) % max(len(batches), 1)
    else:
        batch_indices[scope] = batch_index
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"pending": pending, "checked": checked, "authors": authors,
                                    "author_batches": batch_indices, "query_offsets": query_offsets}, ensure_ascii=False))
    temporary.replace(state_path)
    return papers
