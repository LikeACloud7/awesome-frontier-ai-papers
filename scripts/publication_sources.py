"""Official publication pagination and metadata enrichment.

The source registry, not individual paper titles, drives discovery.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit, urlencode

from bs4 import BeautifulSoup

from collection_status import http_request, record_source_error, record_source_limit
import fetch_papers as f


def soup_at(url: str):
    return BeautifulSoup(f.fetch_text_url(url), "html.parser")


def text(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def page_limit(source: dict, page_count: int) -> bool:
    limit = int(source.get("max_pages", 0) or 0)
    if limit and page_count >= limit:
        record_source_limit(f"Configured page limit reached: {limit}")
        return True
    return False


def publication(entry: dict, org: dict, source: dict):
    paper = f.official_publication_page_paper(entry, org)
    paper["source_url"] = source.get("url", "")
    paper["ai_scope"] = source.get("ai_scope", "")
    if entry.get("paper_url"):
        paper["paper_url"] = entry["paper_url"]
    return paper


def next_data_objects(page_html: str):
    """Decode Next's JSON string transport without evaluating page JavaScript."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"self\.__next_f\.push\((\[.*?\])\)\s*;?", page_html, re.DOTALL):
        try:
            frame = json.loads(match.group(1))
        except ValueError:
            continue
        if len(frame) < 2 or not isinstance(frame[1], str):
            continue
        payload = frame[1]
        # Each post is a complete JSON object inside the RSC transport.
        for start in re.finditer(r'\{\s*"_type"\s*:\s*"post"', payload):
            try:
                obj, _ = decoder.raw_decode(payload[start.start():])
                yield obj
            except ValueError:
                continue


def anthropic_publications(org: dict, source: dict, start: str, end: str):
    page = f.fetch_text_url(source["url"])
    papers = {}
    post_count = 0
    for post in next_data_objects(page):
        slug = (post.get("slug") or {}).get("current", "")
        if not slug or not post.get("title"):
            continue
        post_count += 1
        published = f.parse_publication_date(post.get("publishedOn", ""))
        if not f.date_in_range(published, start, end):
            continue
        url = urljoin(source["url"] + "/", slug)
        subjects = [x.get("label", "") for x in post.get("subjects", [])]
        papers[url] = publication({"title": post["title"], "url": url,
            "published": published, "abstract": post.get("summary") or "",
            "matched_keywords": subjects, "work_type": "research_post",
            "company_match_source": f"official Anthropic research catalogue {source['url']}"}, org, source)
    if not post_count:
        raise ValueError("Anthropic research catalogue could not be parsed; refusing silent empty success")
    return list(papers.values())


def alignment_publications(org: dict, source: dict, start: str, end: str):
    soup = soup_at(source["url"])
    cards = soup.select("a.paper, a.note")
    if not cards:
        raise ValueError("Alignment Science paper cards not found")
    papers = []
    for card in cards:
        title = text(card.find(["h2", "h3"]))
        if not title:
            continue
        url = urljoin(source["url"], card.get("href", ""))
        # The closest preceding month heading also covers absolute external links.
        heading = card.find_previous(["h2", "h4"])
        published = f.parse_publication_date(text(heading))
        if not published:
            year = re.search(r"20\d{2}", url + " " + text(card.select_one(".byline")))
            published = year.group() if year else ""
        if not f.date_in_range(published, start, end):
            continue
        papers.append(publication({"title": title, "url": url, "published": published,
            "abstract": text(card.select_one(".description")), "work_type": "research_post",
            "company_match_source": f"official Anthropic Alignment Science catalogue {source['url']}"}, org, source))
    return papers


def deepmind_publications(org: dict, source: dict, start: str, end: str):
    url = source["url"]
    seen_urls, seen_pages, papers = set(), set(), []
    page_count = 0
    while url:
        if url in seen_pages:
            raise ValueError("DeepMind pagination repeated a page")
        seen_pages.add(url)
        try:
            soup = soup_at(url)
            cards = soup.select("li.list-group__item")
            if not cards:
                raise ValueError(f"DeepMind publication cards not found at {url}")
            for card in cards:
                anchor = card.find("a", href=True)
                if not anchor:
                    continue
                link = urljoin(url, anchor["href"])
                if link in seen_urls:
                    continue
                seen_urls.add(link)
                published = f.parse_publication_date(text(card.select_one(".list-group__date")))
                if f.date_in_range(published, start, end):
                    papers.append(publication({"title": text(card.select_one(".list-group__description")),
                        "url": link, "published": published,
                        "company_match_source": f"official DeepMind publications {url}"}, org, source))
            nxt = soup.select_one('a[aria-label="Next page"]')
            url = urljoin(url, nxt["href"]) if nxt and nxt.get("href") != "#" else ""
            page_count += 1
            if url and page_limit(source, page_count):
                break
        except Exception as error:
            record_source_error(error)
            break
    return papers


def hf_org_publications(org: dict, source: dict, start: str, end: str):
    papers = {}
    for owner in source.get("hf_orgs", []):
        url = f"https://huggingface.co/{owner}/papers?sort=published_at"
        seen, page_count = set(), 0
        while url:
            if url in seen:
                record_source_error(f"Hugging Face pagination repeated {url}")
                break
            seen.add(url)
            try:
                html = f.fetch_text_url(url)
                soup = BeautifulSoup(html, "html.parser")
                profile = soup.select_one('[data-target="OrgProfile"][data-props]')
                props = json.loads(profile["data-props"]) if profile else {}
                if isinstance(props.get("orgPapers"), list):
                    cards = []
                    for item in props["orgPapers"]:
                        data = item.get("paper") or {}
                        if not data.get("id") or not (data.get("title") or item.get("title")):
                            continue
                        cards.append({"id": data["id"], "title": data.get("title") or item["title"],
                            "published": f.parse_publication_date(data.get("publishedAt") or item.get("publishedAt", "")),
                            "abstract": data.get("summary", ""), "authors": f.huggingface_paper_authors(data)})
                else:
                    cards = f.huggingface_org_paper_cards(html)
                # Current OrgProfile hydration embeds the full catalogue while
                # the HTML shows a page-sized slice. Future server-paged payloads
                # still follow Next when they contain only the visible slice.
                embedded_full_catalogue = "orgPapers" in props and len(cards) > len(f.huggingface_org_paper_cards(html))
                if not cards and "orgPapers" not in props and not re.search(r"0 papers|no papers|hasn.t.*papers", soup.get_text(), re.I):
                    raise ValueError(f"No parseable organization paper cards: {url}")
                for card in cards:
                    published = card.get("published", "")
                    if not f.date_in_range(published, start, end):
                        continue
                    aid = f.get_arxiv_id(card["id"])
                    papers[aid] = publication({"id": f"arxiv:{aid}", "title": card["title"],
                        "url": f"https://huggingface.co/papers/{aid}",
                        "paper_url": f"https://arxiv.org/abs/{aid}", "published": published,
                        "abstract": card.get("abstract", ""), "authors": card.get("authors", []),
                        "work_type": "preprint", "matched_keywords": ["HuggingFace organization papers"],
                        "company_match_source": f"official HuggingFace organization papers {url}"}, org, source)
                nxt = next((a for a in soup.find_all("a", href=True)
                    if text(a) == "Next" and a.get("href") and a.get("aria-disabled") != "true"
                    and "pointer-events-none" not in a.get("class", [])), None)
                url = urljoin(url, nxt["href"]) if nxt and not embedded_full_catalogue else ""
                page_count += 1
                if url and page_limit(source, page_count):
                    break
            except Exception as error:
                record_source_error(error)
                break
    return list(papers.values())


def google_research_publications(org: dict, source: dict, start: str, end: str):
    papers, seen_pages = {}, set()
    page = 1
    while True:
        url = source["url"] + ("?" + urlencode({"page": page}) if page > 1 else "")
        try:
            soup = soup_at(url)
            cards = soup.select(".publications-list .row-card") or soup.select(".publication") or soup.select(".publication-card")
            if not cards:
                # Google serves cards as glue-filter result elements.
                cards = soup.select(".glue-filter__result")
            parsed = []
            for card in cards:
                anchor = next((a for a in card.find_all("a", href=True)
                    if "/pubs/" in a["href"] and text(a) not in {"View details", ""}), None)
                if not anchor:
                    continue
                title = text(anchor)
                link = urljoin(url, anchor["href"])
                year = card.get("data-glue-filter-year", "")
                if not year:
                    year_text = " ".join(text(n) for n in card.select(".row-card__subheading__item"))
                    match = re.search(r"\b(20\d{2}|19\d{2})\b", year_text)
                    year = match.group() if match else ""
                abstract = text(card.select_one(".glue-tooltip__body")) or text(card.select_one(".publication__abstract")) or text(card.select_one(".preview-abstract"))
                if not abstract:
                    abstract = text(card)
                if f.date_in_range(str(year), start, end):
                    parsed.append(publication({"title": title, "url": link, "published": str(year),
                        "abstract": abstract, "company_match_source": f"official Google Research publications {url}"}, org, source))
            if not cards:
                raise ValueError(f"Google Research publication cards not found: {url}")
            signature = tuple(p["url"] for p in parsed)
            if signature and signature in seen_pages:
                raise ValueError(f"Google Research pagination repeated page {page}")
            seen_pages.add(signature)
            for paper in parsed:
                papers[paper["url"]] = paper
            # The server's result count is authoritative; do not assume a fixed cap.
            result_text = soup.get_text(" ", strip=True)
            total_match = re.search(r"\d[\d,]*\s*[-–]\s*([\d,]+)\s+of\s+([\d,]+)\s+publications", result_text)
            if not total_match:
                raise ValueError("Google Research total result count not found")
            shown_end = int(total_match.group(1).replace(",", ""))
            total = int(total_match.group(2).replace(",", ""))
            if shown_end >= total:
                break
            years = [re.findall(r"\b(?:20|19)\d{2}\b", " ".join(text(n) for n in card.select(".row-card__subheading__item"))) for card in cards]
            latest_years = [max(values) for values in years if values]
            if start and len(latest_years) == len(cards) and max(latest_years) < start[:4]:
                break
            if page_limit(source, page):
                break
            page += 1
        except Exception as error:
            record_source_error(error)
            break
    return list(papers.values())


def enrich_publication(paper: dict, cache_dir: Path | None = None) -> dict:
    """Read the actual abstract/subject before rejecting a sparse listing card."""
    url = paper.get("url", "")
    if not url or url.lower().endswith(".pdf"):
        return paper
    cache_path = None
    if cache_dir:
        cache_path = cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text())
                # Metadata caches expire so missing/changed abstracts are revisited.
                age = datetime.now().timestamp() - cache_path.stat().st_mtime
                if age < 7 * 86400:
                    return {**paper, **cached}
            except (ValueError, OSError):
                pass
    response = http_request("GET", url, retries=1, timeout=(8, 12))
    response.raise_for_status()
    if response.encoding in {None, "ISO-8859-1", "iso-8859-1"}:
        response.encoding = "utf-8"
    soup = BeautifulSoup(response.text, "html.parser")
    main = soup.find("main") or soup.find("article") or soup
    for tag in main.find_all(["nav", "footer", "script", "style"]):
        tag.decompose()
    metadata = {}
    heading = next((h for h in main.find_all(["h2", "h3"]) if text(h).lower() == "abstract"), None)
    if heading:
        blocks = []
        for sibling in heading.next_siblings:
            if getattr(sibling, "name", None) in {"h1", "h2", "h3"}:
                break
            if hasattr(sibling, "get_text"):
                blocks.append(text(sibling))
        abstract = " ".join(blocks).strip()
    else:
        meta = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
        abstract = meta.get("content", "") if meta else ""
        if len(abstract) < 100:
            abstract = text(main)[:12000]
    if len(abstract) > len(paper.get("abstract", "")):
        metadata["abstract"] = f.clean_markdown_text(abstract)[:12000]
    for anchor in main.find_all("a", href=True):
        target = urljoin(url, anchor["href"])
        label = text(anchor).casefold()
        if re.search(r"arxiv\.org/(abs|pdf)/", target) and (
            label in {"view publication", "download", "paper", "read paper", "read the paper"}
            or label.startswith("https://arxiv.org/")):
            metadata["paper_url"] = target
            break
    if cache_path and metadata:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(metadata, ensure_ascii=False))
    return {**paper, **metadata}
