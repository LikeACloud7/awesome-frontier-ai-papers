#!/usr/bin/env python3
"""Collection helpers for the static frontier-lab paper archive."""

import hashlib
import html as html_lib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urljoin

import feedparser
import requests

# Cache directory for source metadata.
OUTPUT_DIR = Path(__file__).parent.parent / "output"
OPENALEX_CACHE_FILE = OUTPUT_DIR / "openalex_institutions_cache.json"
OPENALEX_API_BASE = "https://api.openalex.org"
USER_AGENT = "awesome-frontier-ai-papers/0.2"
HF_RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
HF_MAX_RETRIES = 3
HF_MAX_TOTAL_SLEEP_SECONDS = 20
HF_BACKOFF_BASE_SECONDS = 1.5
DEFAULT_AI_RELEVANCE_KEYWORDS = [
    "artificial intelligence",
    "AI",
    "machine learning",
    "deep learning",
    "large language model",
    "LLM",
    "language model",
    "foundation model",
    "multimodal",
    "transformer",
    "generative",
    "diffusion",
    "agent",
    "reasoning",
    "alignment",
    "reinforcement learning",
    "computer vision",
    "object detection",
    "speech recognition",
    "speech synthesis",
    "natural language",
    "retrieval",
    "ranking",
    "embedding",
    "neural",
    "inference",
    "fine-tuning",
    "pretraining",
    "token",
    "robotics",
]


def retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, (parsedate_to_datetime(value) - datetime.now().astimezone()).total_seconds())
        except (TypeError, ValueError):
            return None


def huggingface_request(
    method: str,
    url: str,
    *,
    max_retries: int = HF_MAX_RETRIES,
    max_total_sleep_seconds: float = HF_MAX_TOTAL_SLEEP_SECONDS,
    **kwargs,
) -> requests.Response:
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    kwargs.setdefault("timeout", 30)
    total_sleep = 0.0

    for attempt in range(max_retries + 1):
        response = requests.request(method, url, headers=headers, **kwargs)
        if response.status_code not in HF_RETRY_STATUS_CODES or attempt >= max_retries:
            response.raise_for_status()
            return response

        retry_after = retry_after_seconds(response.headers.get("Retry-After"))
        delay = retry_after if retry_after is not None else HF_BACKOFF_BASE_SECONDS * (2 ** attempt)
        remaining_sleep = max_total_sleep_seconds - total_sleep
        if remaining_sleep <= 0:
            response.raise_for_status()
        delay = min(delay, remaining_sleep)
        print(
            f"HuggingFace 일시 실패 재시도 ({response.status_code}, {attempt + 1}/{max_retries}, {delay:.1f}s): {url}",
            file=sys.stderr,
        )
        time.sleep(delay)
        total_sleep += delay

    raise RuntimeError("unreachable HuggingFace retry state")


def get_arxiv_id(url: str) -> str:
    """URL에서 arXiv ID 추출"""
    if "arxiv.org/abs/" in url:
        raw_id = url.split("/abs/")[-1]
    elif re.match(r"^\d{4}\.\d+|^[a-z\-]+/\d+", url):
        raw_id = url
    else:
        return url
    return re.sub(r"v\d+$", "", raw_id)  # 버전 제거


def get_paper_key(paper: dict) -> str:
    """중복 제거/seen 추적용 안정 키 생성"""
    if paper.get("id"):
        return paper["id"]

    url = paper.get("url", "")
    if "arxiv.org/abs/" in url:
        return f"arxiv:{get_arxiv_id(url)}"

    if paper.get("doi"):
        return f"doi:{paper['doi'].lower()}"

    if paper.get("openalex_id"):
        return f"openalex:{paper['openalex_id'].split('/')[-1]}"

    return url


def get_url_key(paper: dict) -> str:
    """서로 다른 소스 ID가 같은 landing URL을 가리킬 때 쓰는 보조 중복 키"""
    url = paper.get("url", "")
    if not url:
        return ""
    canonical = re.sub(r"[?#].*$", "", url).rstrip("/").lower()
    if not canonical:
        return ""
    return f"url:{canonical}"


def get_title_key(paper: dict) -> str:
    """공식 페이지와 arXiv/HF가 같은 보고서를 다른 URL로 내는 경우를 병합하기 위한 키"""
    title = normalized_term_text(paper.get("title", "")).lower()
    title = re.sub(r"\s+", " ", title).strip()
    generic_titles = {
        "technical report",
        "model card",
        "system card",
        "research paper",
        "white paper",
    }
    if len(title) < 16 or title in generic_titles:
        return ""
    return f"title:{title}"


def load_config():
    """설정 파일 로드"""
    config_path = Path(__file__).parent.parent / "config" / "frontier_labs.json"
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_company_groups(config: dict) -> list[dict]:
    """설정에서 회사 추적 그룹 로드"""
    tracking = config.get("company_tracking", {})
    if not tracking.get("enabled", True):
        return []
    return tracking.get("groups", [])


def get_company_registry(config: dict) -> list[dict]:
    """회사/연구소 alias를 평탄화한 추적 레지스트리"""
    registry = []
    for group in get_company_groups(config):
        for org in group.get("organizations", []):
            name = org["name"]
            aliases = [name] + org.get("aliases", []) + org.get("arxiv_search_terms", [])
            aliases = list(dict.fromkeys([a for a in aliases if a]))
            registry.append({
                **org,
                "name": name,
                "aliases": aliases,
                "group_id": group.get("id", "company"),
                "group_name": group.get("name", "Company Watch"),
                "region": group.get("region", ""),
            })

    return registry


def term_pattern(term: str) -> str:
    """회사명/모델명 alias를 너무 넓게 잡지 않는 정규식 생성"""
    escaped = re.escape(term)
    if term and term[0].isalnum() and term[-1].isalnum():
        return rf"\b{escaped}\b"
    return escaped


def term_flags(term: str) -> int:
    """브랜드/모델명은 대소문자를 보존해 일반 단어 오탐을 줄임"""
    if any(char.isupper() for char in term) or any(char in term for char in ".-/"):
        return 0
    return re.IGNORECASE


def match_organizations(text: str, org_registry: list[dict]) -> list[str]:
    """텍스트에서 추적 회사/연구소 alias 매칭"""
    matched = []
    for org in org_registry:
        for alias in org.get("aliases", []):
            if re.search(term_pattern(alias), text, term_flags(alias)):
                matched.append(org["name"])
                break
    return matched


def normalized_term_text(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", " ", value or "").strip()


def huggingface_evidence_text(item: dict, paper_data: dict) -> str:
    """HF 조직/저자 메타데이터만 모아 공식 회사 증거 텍스트를 만든다."""
    values = []

    def add(value):
        if not value:
            return
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, dict):
            for key in ("name", "fullname", "display_name", "displayName", "username", "id"):
                add(value.get(key))

    add(item.get("organization"))
    add(paper_data.get("organization"))
    for author in paper_data.get("authors", []) or []:
        add(author)
        if isinstance(author, dict):
            add(author.get("user"))

    evidence = " ".join(values)
    normalized = normalized_term_text(evidence)
    return f"{evidence} {normalized}".strip()


def huggingface_official_terms(org: dict) -> list[str]:
    terms = [
        org.get("name", ""),
        *org.get("aliases", []),
        *org.get("huggingface_official_terms", []),
    ]
    expanded = []
    for term in terms:
        if not term or len(term) <= 2:
            continue
        expanded.append(term)
        normalized = normalized_term_text(term)
        if normalized and normalized != term:
            expanded.append(normalized)
    return list(dict.fromkeys(expanded))


def match_huggingface_company_evidence(item: dict, paper_data: dict, org: dict) -> bool:
    """HF 검색 결과가 추적 회사의 공식 조직/저자 페이지에서 나온 논문인지 확인한다."""
    evidence = huggingface_evidence_text(item, paper_data)
    if not evidence:
        return False

    return any(
        re.search(term_pattern(term), evidence, re.IGNORECASE)
        for term in huggingface_official_terms(org)
    )


def get_org_metadata(org_name: str, org_registry: list[dict]) -> dict:
    for org in org_registry:
        if org["name"] == org_name:
            return org
    return {}


def paper_match_text(paper: dict) -> str:
    return " ".join([
        paper.get("title", ""),
        paper.get("abstract", ""),
        " ".join(paper.get("authors", [])),
        " ".join(paper.get("author_affiliations", [])),
    ])


def get_ai_relevance_keywords(config: dict) -> list[str]:
    tracking = config.get("company_tracking", {})
    configured = tracking.get("ai_relevance_keywords", [])
    return list(dict.fromkeys(configured + DEFAULT_AI_RELEVANCE_KEYWORDS))


def get_frontier_ai_keywords(config: dict) -> list[str]:
    tracking = config.get("company_tracking", {})
    return tracking.get("frontier_ai_keywords", [])


def get_frontier_ai_weak_keywords(config: dict) -> list[str]:
    tracking = config.get("company_tracking", {})
    return tracking.get("frontier_ai_weak_keywords", [])


def get_frontier_ai_context_keywords(config: dict) -> list[str]:
    tracking = config.get("company_tracking", {})
    return tracking.get("frontier_ai_context_keywords", [])


def get_ai_concept_ids(config: dict) -> list[str]:
    openalex_config = config.get("company_tracking", {}).get("openalex", {})
    return openalex_config.get("ai_concept_ids", [])


def concept_matches_ai(concepts: list[dict], config: dict) -> bool:
    ai_concept_ids = set(get_ai_concept_ids(config))
    if not ai_concept_ids:
        return False

    min_score = config.get("company_tracking", {}).get("openalex", {}).get("min_ai_concept_score", 0.35)
    for concept in concepts or []:
        if concept.get("id") in ai_concept_ids and float(concept.get("score", 0) or 0) >= min_score:
            return True
    return False


def paper_matches_ai_keywords(paper: dict, config: dict) -> bool:
    text = paper_match_text(paper)
    return bool(match_keywords(text, get_ai_relevance_keywords(config)))


def is_ai_relevant_paper(paper: dict, config: dict) -> bool:
    return concept_matches_ai(paper.get("concepts", []), config) or paper_matches_ai_keywords(paper, config)


def is_frontier_ai_relevant_paper(paper: dict, config: dict) -> bool:
    if paper.get("work_type") in {"technical-report", "model_card", "system_card", "benchmark_dataset_card"}:
        return True
    keywords = get_frontier_ai_keywords(config)
    if not keywords:
        return is_ai_relevant_paper(paper, config)
    raw_text = " ".join([
        paper_match_text(paper),
        paper.get("work_type", ""),
    ])
    text = f"{raw_text} {normalized_term_text(raw_text)}"
    if match_keywords(text, keywords):
        return True

    weak_keywords = get_frontier_ai_weak_keywords(config)
    context_keywords = get_frontier_ai_context_keywords(config)
    return bool(
        weak_keywords
        and context_keywords
        and match_keywords(text, weak_keywords)
        and match_keywords(text, context_keywords)
    )


def is_excluded_company_paper(paper: dict, config: dict) -> bool:
    if paper.get("official_report"):
        return False

    patterns = config.get("company_tracking", {}).get("excluded_url_patterns", [])
    text = " ".join([
        paper.get("url", ""),
        paper.get("doi", ""),
        paper.get("openalex_id", ""),
    ]).lower()
    return any(pattern.lower() in text for pattern in patterns)


def date_in_range(published: str, start_date: str, end_date: str) -> bool:
    if not published:
        return True
    if re.match(r"^\d{4}$", published):
        return start_date[:4] <= published <= end_date[:4]
    if re.match(r"^\d{4}-\d{2}$", published):
        return start_date[:7] <= published <= end_date[:7]
    return start_date <= published[:10] <= end_date


def official_report_key(url: str, title: str = "") -> str:
    stable = (normalized_term_text(title) or url).strip().lower()
    digest = hashlib.sha1(stable.encode("utf-8")).hexdigest()[:16]
    return f"official:{digest}"


def mojibake_score(value: str) -> int:
    return sum(value.count(marker) for marker in ("Ã", "Â", "â", "\ufffd"))


def repair_text_encoding(value: str) -> str:
    text = str(value or "")
    if not any(marker in text for marker in ("Ã", "Â", "â")):
        return text

    best = text
    best_score = mojibake_score(text)
    for encoding in ("latin-1", "cp1252"):
        try:
            candidate = text.encode(encoding).decode("utf-8")
        except UnicodeError:
            continue
        candidate_score = mojibake_score(candidate)
        if candidate_score < best_score:
            best = candidate
            best_score = candidate_score
    return best


def clean_markdown_text(value: str) -> str:
    value = repair_text_encoding(value)
    value = re.sub(r"\u00e2\u0080[\u00a0-\u00bf]?", "", value or "")
    value = re.sub(r"\u00e2[\u0080-\u00bf]?", "", value)
    value = value.replace("\ufffd", " ")
    value = re.sub(r"[\u0000-\u001f\u007f-\u009f]", "", value)
    value = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", value)
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"[*_`>†‡]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_publication_date(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if re.match(r"^\d{4}$", value):
        return value
    if re.match(r"^\d{4}-\d{2}$", value):
        return value[:7]
    if re.match(r"^\d{4}-\d{2}-\d{2}", value):
        return value[:10]
    for fmt in ("%B %Y", "%b %Y"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m")
        except ValueError:
            pass
    for fmt in ("%d %B %Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError, IndexError, AttributeError):
        return ""


def timestamp_millis_to_date(value) -> str:
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return ""
    if millis <= 0:
        return ""
    return datetime.fromtimestamp(millis / 1000).date().isoformat()


def markdown_heading(readme: str) -> str:
    for line in readme.splitlines():
        match = re.match(r"^#\s+(.+)$", line.strip())
        if match:
            return clean_markdown_text(match.group(1))
    return ""


def markdown_pdf_link_title(readme: str, pdf_name: str) -> str:
    pdf_basename = pdf_name.split("/")[-1].lower()
    for title, href in re.findall(r"\[([^\]]+)\]\(([^)]+\.pdf)\)", readme, flags=re.IGNORECASE):
        if href.split("/")[-1].lower() == pdf_basename:
            return clean_markdown_text(title)
    return ""


def pdf_report_allowed(path: str, readme: str = "") -> bool:
    lower = path.lower()
    if not lower.endswith(".pdf"):
        return False
    report_terms = ("paper", "report", "technical", "whitepaper", "white-paper")
    if any(term in lower for term in report_terms):
        return True
    link_title = markdown_pdf_link_title(readme, path).lower()
    return any(term in link_title for term in report_terms)


def markdown_abstract(readme: str) -> str:
    lines = readme.splitlines()
    for idx, line in enumerate(lines):
        if "abstract" not in line.lower():
            continue
        text = clean_markdown_text(line)
        text = re.sub(r"^abstract:?\s*", "", text, flags=re.IGNORECASE).strip()
        following = []
        for next_line in lines[idx + 1:]:
            stripped = next_line.strip()
            if not stripped:
                if following:
                    break
                continue
            if stripped.startswith("#") or stripped.startswith("**Key"):
                break
            following.append(clean_markdown_text(stripped))
            if len(" ".join(following)) > 650:
                break
        abstract = " ".join([text] + following).strip()
        if abstract:
            return abstract
    return ""


def official_report_to_paper(report: dict, org: dict, source: str = "official_report") -> dict:
    title = clean_markdown_text(report.get("title", ""))
    url = report.get("url", "")
    return {
        "id": report.get("id") or official_report_key(url, title),
        "source": source,
        "work_type": report.get("work_type", "technical-report"),
        "title": title,
        "authors": report.get("authors") or [org["name"]],
        "abstract": clean_markdown_text(report.get("abstract", "")),
        "categories": [],
        "url": url,
        "published": report.get("published", ""),
        "doi": report.get("doi", ""),
        "openalex_id": "",
        "affiliations": [org["name"]],
        "author_affiliations": report.get("author_affiliations", [org["name"]]),
        "matched_keywords": report.get("matched_keywords", []),
        "matched_orgs": [org["name"]],
        "company_groups": [org.get("group_id", "company")],
        "company_regions": [org.get("region", "")],
        "official_report": True,
        "quality_signals": {
            **report.get("quality_signals", {}),
            "company_match_source": report.get("company_match_source", "official company report source"),
        },
    }


def official_publication_page_paper(entry: dict, org: dict) -> dict:
    return official_report_to_paper({
        "id": entry.get("id"),
        "title": entry.get("title", ""),
        "url": entry.get("url", ""),
        "published": entry.get("published", ""),
        "authors": entry.get("authors") or [org["name"]],
        "abstract": entry.get("abstract", ""),
        "work_type": entry.get("work_type", "publication"),
        "matched_keywords": entry.get("matched_keywords", []),
        "company_match_source": entry.get("company_match_source", "official company publication page"),
    }, org, source="official_publication_page")


def fetch_text_url(url: str) -> str:
    if url.startswith("https://huggingface.co/"):
        response = huggingface_request("GET", url)
    else:
        response = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    if not response.encoding or response.encoding.lower() in {"iso-8859-1", "latin-1"}:
        response.encoding = "utf-8"
    return response.text


def url_allowed_for_source(url: str, source: dict) -> bool:
    include_patterns = source.get("include_url_patterns", [])
    exclude_patterns = source.get("exclude_url_patterns", [])
    lower = url.lower()
    if include_patterns and not any(pattern.lower() in lower for pattern in include_patterns):
        return False
    if any(pattern.lower() in lower for pattern in exclude_patterns):
        return False
    return True


def split_author_text(value: str) -> list[str]:
    value = clean_markdown_text(value)
    value = re.sub(r"[†‡*]+", "", value or "")
    return [author.strip() for author in value.split(",") if author.strip()]


def fetch_rss_publication_page(org: dict, source: dict) -> list[dict]:
    url = source.get("url", "")
    try:
        response = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"공식 RSS 수집 실패 ({org['name']} / {url}): {e}", file=sys.stderr)
        return []

    feed = feedparser.parse(response.content)
    include_categories = {category.lower() for category in source.get("include_categories", [])}
    include_title_keywords = [keyword.lower() for keyword in source.get("include_title_keywords", [])]
    papers = []
    for entry in feed.entries:
        link = urljoin(url, entry.get("link", ""))
        if not link or not url_allowed_for_source(link, source):
            continue
        title = entry.get("title", "")
        summary = entry.get("summary", "")
        if include_title_keywords:
            keyword_text = f"{title} {summary}".lower()
            if not any(keyword in keyword_text for keyword in include_title_keywords):
                continue
        categories = [tag.get("term", "") for tag in entry.get("tags", []) if tag.get("term")]
        if include_categories and not any(category.lower() in include_categories for category in categories):
            continue
        authors = []
        for author in entry.get("authors", []) or []:
            name = author.get("name")
            if name:
                authors.append(name)
        if not authors and entry.get("author"):
            authors = split_author_text(entry.get("author", ""))
        papers.append(official_publication_page_paper({
            "title": title,
            "url": link,
            "published": parse_publication_date(entry.get("published", "") or entry.get("updated", "")),
            "authors": authors or [org["name"]],
            "abstract": summary,
            "work_type": source.get("work_type", "publication"),
            "matched_keywords": categories,
            "company_match_source": f"official RSS feed {url}",
        }, org))
    return papers


def fetch_apple_next_data_publications(org: dict, source: dict) -> list[dict]:
    url = source.get("url", "")
    try:
        html = fetch_text_url(url)
    except requests.RequestException as e:
        print(f"Apple 공식 publication 페이지 수집 실패 ({org['name']} / {url}): {e}", file=sys.stderr)
        return []

    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html)
    if not match:
        return []
    try:
        data = json.loads(html_lib.unescape(match.group(1)))
    except json.JSONDecodeError:
        return []

    posts = data.get("props", {}).get("pageProps", {}).get("posts", [])
    papers = []
    for post in posts:
        if post.get("type") != source.get("post_type", "paper"):
            continue
        slug = post.get("slug", "")
        link = source.get("url_template", "https://machinelearning.apple.com/research/{slug}").format(slug=slug)
        if not slug or not url_allowed_for_source(link, source):
            continue
        papers.append(official_publication_page_paper({
            "id": f"apple:{post.get('documentId') or slug}",
            "title": post.get("title", ""),
            "url": link,
            "published": parse_publication_date(post.get("published", "")),
            "authors": split_author_text(post.get("authorsOrdered", "")) or [org["name"]],
            "abstract": post.get("body", "") or post.get("description", ""),
            "company_match_source": f"official Apple Machine Learning page {url}",
        }, org))
    return papers


def fetch_deepmind_publication_list(org: dict, source: dict) -> list[dict]:
    url = source.get("url", "")
    try:
        html = fetch_text_url(url)
    except requests.RequestException as e:
        print(f"DeepMind 공식 publication 페이지 수집 실패 ({org['name']} / {url}): {e}", file=sys.stderr)
        return []

    item_pattern = re.compile(
        r'<li class=list-group__item>.*?href=([^ >]+).*?'
        r'<span class=list-group__date>\s*(.*?)\s*</span>.*?'
        r'<span class=list-group__description>(.*?)</span>',
        re.DOTALL,
    )
    papers = []
    for href, date_text, title in item_pattern.findall(html):
        link = urljoin(url, href.strip('"\''))
        if not url_allowed_for_source(link, source):
            continue
        papers.append(official_publication_page_paper({
            "title": html_lib.unescape(clean_markdown_text(title)),
            "url": link,
            "published": parse_publication_date(html_lib.unescape(clean_markdown_text(date_text))),
            "authors": [org["name"]],
            "abstract": "",
            "company_match_source": f"official Google DeepMind publications page {url}",
        }, org))
    return papers


def parse_deepmind_model_card_date(value: str) -> str:
    value = re.sub(r"^\s*Updated\s+", "", html_text(value), flags=re.IGNORECASE)
    return parse_publication_date(value)


def fetch_deepmind_model_cards(org: dict, source: dict) -> list[dict]:
    url = source.get("url", "")
    try:
        html = fetch_text_url(url)
    except requests.RequestException as e:
        print(f"DeepMind 공식 model cards 페이지 수집 실패 ({org['name']} / {url}): {e}", file=sys.stderr)
        return []

    row_pattern = re.compile(
        r"<tr>\s*<th scope=row>(?P<title>.*?)</th>\s*"
        r"<td>(?P<date>.*?)</td>\s*"
        r"<td[^>]*>\s*<a[^>]*href=(?P<href>\"[^\"]+\"|'[^']+'|[^\s>]+)[^>]*>\s*"
        r"View model card\s*</a>\s*</td>\s*</tr>",
        re.DOTALL,
    )
    papers = []
    seen_urls = set()
    default_abstract = source.get("default_abstract", "Official Google DeepMind model card.")
    for match in row_pattern.finditer(html):
        title = html_text(match.group("title"))
        published = parse_deepmind_model_card_date(match.group("date"))
        href = match.group("href").strip("\"'")
        link = urljoin(url, html_lib.unescape(href))
        if not title or not published or link in seen_urls or not url_allowed_for_source(link, source):
            continue
        seen_urls.add(link)
        papers.append(official_publication_page_paper({
            "title": f"{title} Model Card",
            "url": link,
            "published": published,
            "authors": [org["name"]],
            "abstract": default_abstract,
            "work_type": source.get("work_type", "model_card"),
            "matched_keywords": ["model card", title],
            "company_match_source": f"official Google DeepMind model cards page {url}",
        }, org))
    return papers


def html_text(value: str) -> str:
    return html_lib.unescape(html_lib.unescape(clean_markdown_text(value))).replace("\xa0", " ")


def huggingface_org_paper_ids(page_html: str) -> list[str]:
    paper_ids = []
    for match in re.finditer(r'href="/papers/([^"#?]+)', page_html):
        paper_id = match.group(1).strip()
        if paper_id and paper_id not in paper_ids:
            paper_ids.append(paper_id)
    return paper_ids


def decode_json_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return value


def arxiv_id_month(arxiv_id: str) -> str:
    match = re.match(r"^(\d{2})(\d{2})\.\d+", arxiv_id or "")
    if not match:
        return ""
    return f"20{match.group(1)}-{match.group(2)}"


def huggingface_org_paper_cards(page_html: str) -> list[dict]:
    decoded = html_lib.unescape(page_html)
    papers_by_id = {}

    card_pattern = re.compile(
        r'<h3[^>]*>\s*<a href="/papers/(?P<id>[^"#?]+)"[^>]*>(?P<title>.*?)</a>',
        re.DOTALL,
    )
    for match in card_pattern.finditer(decoded):
        paper_id = match.group("id")
        title = html_text(match.group("title"))
        if paper_id and title:
            papers_by_id[paper_id] = {"id": paper_id, "title": title, "published": arxiv_id_month(paper_id)}

    json_title_pattern = re.compile(
        r'"id":"(?P<id>\d{4}\.\d+)","title":"(?P<title>(?:\\.|[^"])*)"',
        re.DOTALL,
    )
    for match in json_title_pattern.finditer(decoded):
        paper_id = match.group("id")
        title = clean_markdown_text(decode_json_string(match.group("title")))
        if paper_id and title and paper_id not in papers_by_id:
            papers_by_id[paper_id] = {"id": paper_id, "title": title, "published": arxiv_id_month(paper_id)}

    json_date_pattern = re.compile(
        r'"id":"(?P<id>\d{4}\.\d+)".{0,800}?"publishedAt":"(?P<published>[^"]+)"',
        re.DOTALL,
    )
    for match in json_date_pattern.finditer(decoded):
        paper = papers_by_id.get(match.group("id"))
        if paper:
            paper["published"] = parse_publication_date(match.group("published")) or paper.get("published", "")

    ordered = []
    for paper_id in huggingface_org_paper_ids(decoded):
        paper = papers_by_id.get(paper_id)
        if paper and paper not in ordered:
            ordered.append(paper)
    return ordered


def huggingface_paper_authors(paper_data: dict) -> list[str]:
    authors = []
    for author in paper_data.get("authors", []) or []:
        if isinstance(author, dict):
            name = author.get("name") or author.get("fullname")
        else:
            name = str(author)
        if name:
            authors.append(clean_markdown_text(name))
    return authors


def fetch_huggingface_org_papers(
    org: dict,
    source: dict,
    start_date: str = "",
    end_date: str = "",
) -> list[dict]:
    hf_orgs = source.get("hf_orgs") or source.get("authors") or []
    hf_orgs = [hf_orgs] if isinstance(hf_orgs, str) else list(hf_orgs)
    hf_orgs = [value for value in hf_orgs if value]
    if source.get("hf_org"):
        hf_orgs.append(source["hf_org"])
    if not hf_orgs and source.get("url"):
        match = re.search(r"huggingface\.co/([^/]+)/papers", source["url"])
        if match:
            hf_orgs.append(match.group(1))
    hf_orgs = list(dict.fromkeys(hf_orgs))
    max_papers = int(source.get("max_papers", 100))
    fetch_api_metadata = source.get("fetch_api_metadata", False)
    papers_by_url = {}

    for hf_org in hf_orgs:
        page_url = source.get("url") if len(hf_orgs) == 1 and source.get("url") else f"https://huggingface.co/{hf_org}/papers"
        try:
            page_html = fetch_text_url(page_url)
        except requests.RequestException as e:
            print(f"HuggingFace 공식 papers 페이지 수집 실패 ({org['name']} / {page_url}): {e}", file=sys.stderr)
            continue

        for card in huggingface_org_paper_cards(page_html)[:max_papers]:
            paper_id = card["id"]
            paper_data = {}
            if fetch_api_metadata:
                try:
                    response = huggingface_request("GET", f"https://huggingface.co/api/papers/{paper_id}")
                    paper_data = response.json()
                except (requests.RequestException, ValueError) as e:
                    print(f"HuggingFace 공식 paper 메타데이터 수집 실패 ({org['name']} / {paper_id}): {e}", file=sys.stderr)
                    paper_data = {}

            published = parse_publication_date(paper_data.get("publishedAt", "")) or card.get("published", "")
            if not date_in_range(published, start_date, end_date):
                continue
            if paper_data.get("organization") and not match_huggingface_company_evidence(
                {"organization": paper_data.get("organization")},
                paper_data,
                org,
            ):
                continue

            title = clean_markdown_text(paper_data.get("title") or card.get("title", ""))
            if not title:
                continue
            url = f"https://huggingface.co/papers/{paper_id}"
            keywords = paper_data.get("ai_keywords", []) or []
            papers_by_url[url] = official_publication_page_paper({
                "id": f"hf-org-paper:{hf_org}:{paper_id}",
                "title": title,
                "url": url,
                "published": published,
                "authors": huggingface_paper_authors(paper_data) or [org["name"]],
                "abstract": clean_markdown_text(paper_data.get("summary", "")),
                "work_type": source.get("work_type", "technical-report"),
                "matched_keywords": list(dict.fromkeys([*keywords, "HuggingFace org papers", hf_org])),
                "company_match_source": f"official HuggingFace org papers page {page_url}",
            }, org)

    return list(papers_by_url.values())


def fetch_baidu_ernie_publication_page(
    org: dict,
    source: dict,
    start_date: str = "",
    end_date: str = "",
) -> list[dict]:
    url = source["url"]
    try:
        page_html = fetch_text_url(url)
    except requests.RequestException as e:
        print(f"Baidu ERNIE 공식 publication 페이지 수집 실패 ({org['name']} / {url}): {e}", file=sys.stderr)
        return []

    papers = []
    pattern = re.compile(r"<li>(?P<citation>.*?)<a\s+href=\"(?P<href>[^\"]+)\"[^>]*>(?P<title>.*?)</a>.*?</li>", re.DOTALL)
    for match in pattern.finditer(page_html):
        citation = html_text(match.group("citation"))
        title = html_text(match.group("title"))
        href = html_lib.unescape(match.group("href"))
        year_match = re.search(r"\((20\d{2})\)", citation)
        if not year_match:
            continue
        published = year_match.group(1)
        if not title or not href or not date_in_range(published, start_date, end_date):
            continue
        link = urljoin(url, href)
        if not url_allowed_for_source(link, source):
            continue
        papers.append(official_publication_page_paper({
            "id": f"baidu-ernie:{official_report_key(link, title)}",
            "title": title,
            "url": link,
            "published": published,
            "authors": split_author_text(citation.split(".")[0]) or [org["name"]],
            "abstract": "Official ERNIE/Baidu publication page entry.",
            "work_type": source.get("work_type", "technical-report"),
            "matched_keywords": ["ERNIE", "Baidu", "technical report"],
            "company_match_source": f"official ERNIE publication page {url}",
        }, org))

    return papers


META_PUBLICATION_DATE_RE = re.compile(r"^[A-Z][a-z]+ \d{1,2}, \d{4}$")


class MetaPublicationCardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cards = []
        self.card = None
        self.div_depth = 0
        self.active_text_tag = ""
        self.active_text_parts = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attrs_dict = {key: value or "" for key, value in attrs}
        classes = attrs_dict.get("class", "").split()
        if self.card is None:
            if tag == "div" and "_8034" in classes:
                self.card = {"h4": [], "p": [], "hrefs": []}
                self.div_depth = 1
            return

        if tag == "div":
            self.div_depth += 1
        elif tag == "a" and "/research/publications/" in attrs_dict.get("href", ""):
            self.card["hrefs"].append(attrs_dict["href"])

        if tag in {"h4", "p"}:
            self.active_text_tag = tag
            self.active_text_parts = []

    def handle_data(self, data: str):
        if self.card is not None and self.active_text_tag:
            self.active_text_parts.append(data)

    def handle_endtag(self, tag: str):
        if self.card is None:
            return

        if tag == self.active_text_tag:
            self.card[self.active_text_tag].append(html_text(" ".join(self.active_text_parts)))
            self.active_text_tag = ""
            self.active_text_parts = []

        if tag == "div":
            self.div_depth -= 1
            if self.div_depth == 0:
                self.cards.append(self.card)
                self.card = None


def parse_meta_publication_cards(page_html: str, page_url: str) -> list[dict]:
    parser = MetaPublicationCardParser()
    parser.feed(page_html)
    entries = []

    for card in parser.cards:
        href = next((value for value in card["hrefs"] if "/research/publications/" in value), "")
        if not href:
            continue

        headings = [value for value in card["h4"] if value]
        title = headings[-1] if headings else ""
        categories = [
            value.replace("Learni9ng", "Learning")
            for value in headings[:-1]
            if value and value != title
        ]
        paragraphs = [value for value in card["p"] if value]
        date_text = next((value for value in paragraphs if META_PUBLICATION_DATE_RE.match(value)), "")
        authors_text_candidates = [
            value
            for value in paragraphs
            if value
            and value != "Publication"
            and not META_PUBLICATION_DATE_RE.match(value)
            and value not in headings
        ]
        authors_text = max(authors_text_candidates, key=len) if authors_text_candidates else ""

        if not title or not date_text:
            continue

        entries.append({
            "title": title,
            "url": urljoin(page_url, href),
            "published": parse_publication_date(date_text),
            "authors": split_author_text(authors_text),
            "abstract": "Official Meta AI publication page.",
            "matched_keywords": categories,
        })

    return entries


def fetch_meta_publication_search(
    org: dict,
    source: dict,
    start_date: str = "",
    end_date: str = "",
) -> list[dict]:
    url = source.get("url", "https://ai.meta.com/results/")
    max_pages = int(source.get("max_pages", 50))
    papers_by_url = {}

    for page in range(1, max_pages + 1):
        page_url = f"{url}?{urlencode({'content_types[0]': 'publication', 'page': page})}"
        try:
            page_html = fetch_text_url(page_url)
        except requests.RequestException as e:
            print(f"Meta 공식 publication 검색 수집 실패 ({org['name']} / {page_url}): {e}", file=sys.stderr)
            break

        entries = parse_meta_publication_cards(page_html, page_url)
        if not entries:
            break

        page_dates = [entry["published"] for entry in entries if entry.get("published")]
        for entry in entries:
            link = entry.get("url", "")
            published = entry.get("published", "")
            if not link or not url_allowed_for_source(link, source):
                continue
            if start_date and end_date and not date_in_range(published, start_date, end_date):
                continue
            papers_by_url[link] = official_publication_page_paper({
                **entry,
                "authors": entry.get("authors") or [org["name"]],
                "company_match_source": f"official Meta AI publication search {page_url}",
            }, org)

        if start_date and page_dates and max(page_dates) < start_date:
            break

    return list(papers_by_url.values())


def fetch_amazon_publication_list(
    org: dict,
    source: dict,
    start_date: str = "",
    end_date: str = "",
) -> list[dict]:
    url = source.get("url", "")
    max_pages = int(source.get("max_pages", 100))
    papers = []

    for page in range(1, max_pages + 1):
        page_url = url if page == 1 else f"{url}?{urlencode({'p': page})}"
        try:
            html = fetch_text_url(page_url)
        except requests.RequestException as e:
            print(f"Amazon 공식 publication 페이지 수집 실패 ({org['name']} / {page_url}): {e}", file=sys.stderr)
            break

        page_papers = []
        for block in re.findall(r'<li class="SearchResultsModule-results-item">(.*?)</li>', html, re.DOTALL):
            title_match = re.search(
                r'<div class="PromoF-title">\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                block,
                re.DOTALL,
            )
            if not title_match:
                continue
            authors_match = re.search(r'<div class="PromoF-authors">(.*?)</div>', block, re.DOTALL)
            date_match = re.search(r'<div class="PromoF-date">\s*(.*?)\s*</div>', block, re.DOTALL)
            description_match = re.search(r'<div[^>]+class="PromoF-description"[^>]*>(.*?)</div>', block, re.DOTALL)
            category_match = re.search(r'<div class="PromoF-category">.*?>(.*?)</a>', block, re.DOTALL)
            abstract = html_text(description_match.group(1)) if description_match else ""
            category = html_text(category_match.group(1)) if category_match else ""
            if category:
                abstract = f"{abstract} Category: {category}".strip()
            page_papers.append(official_publication_page_paper({
                "title": html_text(title_match.group(2)),
                "url": urljoin(url, title_match.group(1)),
                "published": parse_publication_date(html_text(date_match.group(1)) if date_match else ""),
                "authors": split_author_text(html_text(authors_match.group(1))) if authors_match else [org["name"]],
                "abstract": abstract,
                "matched_keywords": [category] if category else [],
                "company_match_source": f"official Amazon Science publications page {page_url}",
            }, org))

        if not page_papers:
            break
        papers.extend(page_papers)
        if start_date and end_date and all(
            paper.get("published") and not date_in_range(paper.get("published", ""), start_date, end_date)
            and paper.get("published", "")[:4] < start_date[:4]
            for paper in page_papers
        ):
            break

    return papers


def fetch_anthropic_research_page(org: dict, source: dict) -> list[dict]:
    url = source.get("url", "")
    try:
        html = fetch_text_url(url)
    except requests.RequestException as e:
        print(f"Anthropic 공식 research 페이지 수집 실패 ({org['name']} / {url}): {e}", file=sys.stderr)
        return []

    papers = []
    seen_urls = set()
    pattern = re.compile(r'<a href="(/research/[^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
    for href, block in pattern.findall(html):
        link = urljoin(url, href)
        if link in seen_urls or not url_allowed_for_source(link, source):
            continue
        seen_urls.add(link)
        title_match = re.search(r'<h[234][^>]*>(.*?)</h[234]>', block, re.DOTALL)
        if not title_match:
            title_match = re.search(r'<span[^>]+title[^>]*>(.*?)</span>', block, re.DOTALL)
        date_match = re.search(r'<time[^>]*>(.*?)</time>', block, re.DOTALL)
        description_match = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
        subject_match = re.search(r'<span[^>]+subject[^>]*>(.*?)</span>', block, re.DOTALL)
        if not title_match or not date_match:
            continue
        subject = html_text(subject_match.group(1)) if subject_match else ""
        papers.append(official_publication_page_paper({
            "title": html_text(title_match.group(1)),
            "url": link,
            "published": parse_publication_date(html_text(date_match.group(1))),
            "authors": [org["name"]],
            "abstract": html_text(description_match.group(1)) if description_match else subject,
            "matched_keywords": [subject] if subject else [],
            "company_match_source": f"official Anthropic research page {url}",
        }, org))
    return papers


def fetch_anthropic_system_cards_page(org: dict, source: dict) -> list[dict]:
    url = source.get("url", "")
    try:
        html = fetch_text_url(url)
    except requests.RequestException as e:
        print(f"Anthropic 공식 system cards 페이지 수집 실패 ({org['name']} / {url}): {e}", file=sys.stderr)
        return []

    papers = []
    seen_urls = set()
    row_pattern = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL)
    cell_pattern = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.DOTALL)
    link_pattern = re.compile(r'<a[^>]+href="([^"]+)"', re.DOTALL)
    for row in row_pattern.findall(html):
        cells = cell_pattern.findall(row)
        if len(cells) < 3:
            continue
        model = html_text(cells[0])
        date_text = html_text(cells[1])
        link_match = link_pattern.search(cells[2])
        if not model or model.lower() == "model" or not date_text or not link_match:
            continue
        link = urljoin(url, html_lib.unescape(link_match.group(1))).replace("http://anthropic.com/", "https://www.anthropic.com/")
        if link in seen_urls or not url_allowed_for_source(link, source):
            continue
        seen_urls.add(link)
        papers.append(official_publication_page_paper({
            "title": f"{model} System Card",
            "url": link,
            "published": parse_publication_date(date_text),
            "authors": [org["name"]],
            "abstract": f"Official Anthropic system card for {model}.",
            "work_type": "model_card",
            "matched_keywords": ["system card", "Claude", model],
            "company_match_source": f"official Anthropic system cards page {url}",
        }, org))
    return papers


def seed_article_text(article: dict, field: str) -> str:
    english = article.get("ArticleSubContentEn", {}) or {}
    chinese = article.get("ArticleSubContentZh", {}) or {}
    return english.get(field) or chinese.get(field) or ""


def seed_article_detail_url(source: dict, title_key: str) -> str:
    template = source.get("url_template", "https://seed.bytedance.com/en/research/{title_key}")
    return template.format(title_key=title_key)


def fetch_bytedance_seed_publication_api(
    org: dict,
    source: dict,
    start_date: str = "",
    end_date: str = "",
) -> list[dict]:
    endpoint = source.get("url", "https://seed.bytedance.com/api/get_article_list_v2")
    count = int(source.get("count", 50))
    max_pages = int(source.get("max_pages", 20))
    params_base = {
        "article_type": int(source.get("article_type", 1)),
        "count": count,
        "order_desc": str(source.get("order_desc", True)).lower(),
    }
    headers = {
        "User-Agent": USER_AGENT,
        "x-tt-locale": source.get("locale", "US"),
    }
    page_token = str(source.get("page_token", "0"))
    papers_by_url = {}

    for _ in range(max_pages):
        try:
            response = requests.get(
                endpoint,
                params={**params_base, "page_token": page_token},
                timeout=30,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as e:
            print(f"ByteDance Seed 공식 publication API 수집 실패 ({org['name']} / {endpoint}): {e}", file=sys.stderr)
            break

        articles = data.get("sub_article_list", []) or []
        if not articles:
            break

        page_dates = []
        for article in articles:
            meta = article.get("ArticleMeta", {}) or {}
            title = clean_markdown_text(seed_article_text(article, "Title"))
            title_key = seed_article_text(article, "TitleKey")
            if not title or not title_key:
                continue
            link = seed_article_detail_url(source, title_key)
            if not url_allowed_for_source(link, source):
                continue
            published = timestamp_millis_to_date(meta.get("PublishDate"))
            if published:
                page_dates.append(published)
            if start_date and end_date and not date_in_range(published, start_date, end_date):
                continue
            areas = [
                area.get("ResearchAreaName") or area.get("ResearchAreaNameZh") or ""
                for area in meta.get("ResearchArea", []) or []
            ]
            teams = [
                team.get("Name") or team.get("NameZh") or ""
                for team in meta.get("WorkingTeam", []) or []
            ]
            external_links = [
                item.get("Link", "")
                for item in meta.get("ExternalLinks", []) or []
                if item.get("Link")
            ]
            abstract = clean_markdown_text(seed_article_text(article, "Abstract"))
            if external_links:
                abstract = f"{abstract} External paper link: {external_links[0]}".strip()
            paper = official_publication_page_paper({
                "id": f"bytedance-seed:{meta.get('ID') or meta.get('ArticleID') or title_key}",
                "title": title,
                "url": link,
                "published": published,
                "authors": split_author_text(meta.get("Author", "")) or [org["name"]],
                "abstract": abstract,
                "work_type": source.get("work_type", "publication"),
                "matched_keywords": list(dict.fromkeys([value for value in areas + teams + [meta.get("Journal", "")] if value])),
                "company_match_source": f"official ByteDance Seed publication API {endpoint}",
            }, org)
            papers_by_url[link] = paper

        if start_date and page_dates and max(page_dates) < start_date:
            break
        if not data.get("has_more"):
            break
        next_page_token = data.get("next_page_token")
        if next_page_token is None or str(next_page_token) == page_token:
            break
        page_token = str(next_page_token)

    return list(papers_by_url.values())


def fetch_huawei_noah_wagtail_publications(
    org: dict,
    source: dict,
    start_date: str = "",
    end_date: str = "",
) -> list[dict]:
    endpoint = source.get("url", "https://www.noahlab.com.hk/wt_app/api/v2/pages/")
    limit = min(int(source.get("limit", 20)), 20)
    max_pages = int(source.get("max_pages", 10))
    locale = source.get("locale", "en")
    url_template = source.get("url_template", "https://www.noahlab.com.hk/en/scientific_research/{slug}")
    fields = source.get(
        "fields",
        "year_type,resources_type,publication_date,resources_title,meeting,link",
    )
    papers_by_url = {}

    for page in range(max_pages):
        offset = page * limit
        try:
            response = requests.get(
                endpoint,
                params={
                    "type": source.get("page_type", "research.ResourcesPage"),
                    "locale": locale,
                    "limit": limit,
                    "offset": offset,
                    "fields": fields,
                },
                timeout=30,
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as e:
            print(f"Huawei Noah 공식 publication API 수집 실패 ({org['name']} / {endpoint}): {e}", file=sys.stderr)
            break

        items = data.get("items", []) or []
        if not items:
            break

        for item in items:
            meta = item.get("meta", {}) or {}
            slug = meta.get("slug", "")
            title = clean_markdown_text(item.get("resources_title") or item.get("title", ""))
            published = parse_publication_date(item.get("publication_date", "")) or parse_publication_date(meta.get("first_published_at", ""))
            if not slug or not title:
                continue
            link = url_template.format(slug=slug)
            if not url_allowed_for_source(link, source):
                continue
            if start_date and end_date and not date_in_range(published, start_date, end_date):
                continue
            external_link = item.get("link", "")
            meeting = clean_markdown_text(item.get("meeting", ""))
            resources_type = clean_markdown_text(item.get("resources_type", ""))
            abstract_parts = ["Official Huawei Noah's Ark Lab publication entry."]
            if meeting:
                abstract_parts.append(f"Venue: {meeting}.")
            if external_link:
                abstract_parts.append(f"External paper link: {external_link}")
            papers_by_url[link] = official_publication_page_paper({
                "id": f"huawei-noah:{item.get('id') or slug}",
                "title": title,
                "url": link,
                "published": published,
                "authors": [org["name"]],
                "abstract": " ".join(abstract_parts),
                "work_type": source.get("work_type", "publication"),
                "matched_keywords": [value for value in (resources_type, meeting, item.get("year_type", "")) if value],
                "company_match_source": f"official Huawei Noah Wagtail publication API {endpoint}",
            }, org)

        total = int(data.get("meta", {}).get("total_count", 0) or 0)
        if total and offset + limit >= total:
            break

    return list(papers_by_url.values())


def microsoft_meta_value(post_data: dict, field: str) -> str:
    values = post_data.get("meta", {}).get(field, [])
    if not values:
        return ""
    first = values[0]
    return first.get("date") or first.get("value") or first.get("raw") or ""


def microsoft_term_names(post_data: dict, field: str) -> list[str]:
    return [
        term.get("name", "")
        for term in post_data.get("terms", {}).get(field, [])
        if term.get("name")
    ]


def microsoft_publication_authors(post: dict) -> list[str]:
    markup = post.get("markup", "")
    match = re.search(r'content-excerpt__people[^>]*>\s*(.*?)\s*</p>', markup, re.DOTALL)
    if not match:
        return []
    return split_author_text(html_text(match.group(1)))


def microsoft_post_to_paper(post: dict, org: dict, endpoint: str) -> dict | None:
    post_data = post.get("data", {})
    link = post_data.get("permalink", "")
    if not link:
        return None

    title = post_data.get("post_title", "")
    published = parse_publication_date(microsoft_meta_value(post_data, "msr_published_date"))
    venue = microsoft_meta_value(post_data, "msr_conference_name") or microsoft_meta_value(post_data, "msr_journal")
    publication_types = microsoft_term_names(post_data, "msr-publication-type")
    areas = microsoft_term_names(post_data, "msr-research-area")
    field_names = microsoft_term_names(post_data, "msr-field-of-study")
    matched_keywords = list(dict.fromkeys(publication_types + areas + field_names + ([venue] if venue else [])))
    abstract = html_text(post_data.get("post_excerpt") or post_data.get("post_content", ""))
    if venue:
        abstract = f"{abstract} Venue: {venue}".strip()

    return official_publication_page_paper({
        "id": f"microsoft:{get_url_key({'url': link}) or link}",
        "title": title,
        "url": link,
        "published": published,
        "authors": microsoft_publication_authors(post) or [org["name"]],
        "abstract": abstract,
        "matched_keywords": matched_keywords,
        "company_match_source": f"official Microsoft Research publications API {endpoint}",
    }, org)


def date_range_windows(start_date: str, end_date: str, split_by_year: bool = False) -> list[tuple[str, str]]:
    if not split_by_year or not start_date or not end_date:
        return [(start_date, end_date)]

    start_year = int(start_date[:4])
    end_year = int(end_date[:4])
    windows = []
    for year in range(start_year, end_year + 1):
        window_start = max(start_date, f"{year}-01-01")
        window_end = min(end_date, f"{year}-12-31")
        windows.append((window_start, window_end))
    return windows


def fetch_microsoft_publication_list(
    org: dict,
    source: dict,
    start_date: str = "",
    end_date: str = "",
) -> list[dict]:
    endpoint = source.get("endpoint", "https://www.microsoft.com/en-us/research/wp-json/microsoft-research/v1/faceted-search")
    post_id = str(source.get("post_id", "687471"))
    max_pages = int(source.get("max_pages", 500))
    retries = int(source.get("retries", 3))
    timeout_seconds = int(source.get("timeout_seconds", 60))
    max_workers = int(source.get("max_workers", 8))
    papers_by_url = {}

    def page_params(page: int, window_start: str, window_end: str) -> dict:
        params = {
            "post_id": post_id,
            "sort_by": "most-recent",
            "page": page,
        }
        if window_start:
            params["facet[date][range][from]"] = window_start
        if window_end:
            params["facet[date][range][to]"] = window_end
        return params

    def fetch_page(page: int, window_start: str, window_end: str) -> tuple[int, dict | None, Exception | None]:
        params = page_params(page, window_start, window_end)
        last_error = None
        for attempt in range(1, retries + 1):
            try:
                response = requests.get(
                    endpoint,
                    params=params,
                    timeout=timeout_seconds,
                    headers={"User-Agent": "awesome-frontier-ai-papers/0.2"},
                )
                response.raise_for_status()
                return page, response.json(), None
            except (requests.RequestException, ValueError) as e:
                last_error = e
                if attempt < retries:
                    time.sleep(min(2 * attempt, 10))
        return page, None, last_error

    def add_posts(data: dict | None) -> int:
        if not data:
            return 0
        page_posts = data.get("posts", []) or []
        for post in page_posts:
            paper = microsoft_post_to_paper(post, org, endpoint)
            if not paper or not url_allowed_for_source(paper.get("url", ""), source):
                continue
            papers_by_url[paper["url"]] = paper
        return len(page_posts)

    for window_start, window_end in date_range_windows(start_date, end_date, bool(source.get("split_by_year", False))):
        page, first_data, first_error = fetch_page(1, window_start, window_end)
        if first_data is None:
            print(
                f"Microsoft 공식 publication API 수집 실패 "
                f"({org['name']} / {window_start}..{window_end} page {page}): {first_error}",
                file=sys.stderr,
            )
            continue

        if not add_posts(first_data):
            continue

        try:
            max_num_pages = int(first_data.get("max_num_pages", 1) or 1)
        except (TypeError, ValueError):
            max_num_pages = 1
        last_page = min(max_num_pages, max_pages)
        if last_page <= 1:
            continue

        worker_count = max(1, min(max_workers, last_page - 1))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(fetch_page, page, window_start, window_end)
                for page in range(2, last_page + 1)
            ]
            for future in as_completed(futures):
                page, data, error = future.result()
                if data is None:
                    print(
                        f"Microsoft 공식 publication API 수집 실패 "
                        f"({org['name']} / {window_start}..{window_end} page {page}): {error}",
                        file=sys.stderr,
                    )
                    continue

                add_posts(data)

                try:
                    page_max_num_pages = int(data.get("max_num_pages", last_page) or last_page)
                except (TypeError, ValueError):
                    page_max_num_pages = last_page
                if page_max_num_pages > last_page:
                    print(
                        f"Microsoft 공식 publication API page count 증가 감지 "
                        f"({org['name']} / {window_start}..{window_end}: {last_page} -> {page_max_num_pages}); "
                        f"이번 실행은 최초 page 1 기준으로 {last_page} page까지만 수집",
                        file=sys.stderr,
                    )
                    last_page = page_max_num_pages

    return list(papers_by_url.values())


def nvidia_publication_date(url: str, fallback_year: int) -> str:
    match = re.search(r"/publication/(\d{4})-(\d{2})_", url)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    return str(fallback_year)


def fetch_nvidia_publication_list(
    org: dict,
    source: dict,
    start_date: str = "",
    end_date: str = "",
) -> list[dict]:
    url = source.get("url", "")
    start_year = int((start_date or "2024")[:4])
    end_year = int((end_date or datetime.now().strftime("%Y"))[:4])
    max_pages_per_year = int(source.get("max_pages_per_year", 10))
    papers = []

    for year in range(end_year, start_year - 1, -1):
        for page in range(max_pages_per_year):
            query = urlencode({"f[0]": f"publication_date:{year}", "page": page})
            page_url = f"{url}?{query}"
            try:
                html = fetch_text_url(page_url)
            except requests.RequestException as e:
                print(f"NVIDIA 공식 publication 페이지 수집 실패 ({org['name']} / {page_url}): {e}", file=sys.stderr)
                break

            page_papers = []
            for block in re.findall(r'<div class="views-row">(.*?)</div>\s*</div>', html, re.DOTALL):
                title_match = re.search(
                    r'views-field-title.*?<a href="([^"]+)"[^>]*>(.*?)</a>',
                    block,
                    re.DOTALL,
                )
                if not title_match:
                    continue
                authors_match = re.search(
                    r'views-field-field-authors.*?<span class="field-content">(.*?)</span>',
                    block,
                    re.DOTALL,
                )
                venue_match = re.search(
                    r'views-field-field-published-in.*?<span class="field-content">(.*?)</span>',
                    block,
                    re.DOTALL,
                )
                link = urljoin(url, title_match.group(1))
                published = nvidia_publication_date(link, year)
                venue = html_text(venue_match.group(1)) if venue_match else ""
                page_papers.append(official_publication_page_paper({
                    "title": html_text(title_match.group(2)),
                    "url": link,
                    "published": published,
                    "authors": split_author_text(html_text(authors_match.group(1))) if authors_match else [org["name"]],
                    "abstract": f"Official NVIDIA Research publication. {venue}".strip(),
                    "matched_keywords": [venue] if venue else [],
                    "company_match_source": f"official NVIDIA Research publications page {page_url}",
                }, org))

            if not page_papers:
                break
            papers.extend(page_papers)

    return papers


def fetch_official_publication_page(
    org: dict,
    source: dict,
    start_date: str = "",
    end_date: str = "",
) -> list[dict]:
    source_type = source.get("type", "rss")
    if source_type == "rss":
        return fetch_rss_publication_page(org, source)
    if source_type == "apple_next_data":
        return fetch_apple_next_data_publications(org, source)
    if source_type == "deepmind_publication_list":
        return fetch_deepmind_publication_list(org, source)
    if source_type == "deepmind_model_cards":
        return fetch_deepmind_model_cards(org, source)
    if source_type == "huggingface_org_papers":
        return fetch_huggingface_org_papers(org, source, start_date, end_date)
    if source_type == "baidu_ernie_publication_page":
        return fetch_baidu_ernie_publication_page(org, source, start_date, end_date)
    if source_type == "amazon_publication_list":
        return fetch_amazon_publication_list(org, source, start_date, end_date)
    if source_type == "anthropic_research_page":
        return fetch_anthropic_research_page(org, source)
    if source_type == "anthropic_system_cards_page":
        return fetch_anthropic_system_cards_page(org, source)
    if source_type == "bytedance_seed_publication_api":
        return fetch_bytedance_seed_publication_api(org, source, start_date, end_date)
    if source_type == "huawei_noah_wagtail_publications":
        return fetch_huawei_noah_wagtail_publications(org, source, start_date, end_date)
    if source_type == "meta_publication_search":
        return fetch_meta_publication_search(org, source, start_date, end_date)
    if source_type == "microsoft_publication_list":
        return fetch_microsoft_publication_list(org, source, start_date, end_date)
    if source_type == "nvidia_publication_list":
        return fetch_nvidia_publication_list(org, source, start_date, end_date)
    print(f"지원하지 않는 공식 publication source ({org['name']} / {source_type})", file=sys.stderr)
    return []


def github_headers() -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    return headers


def github_get_json(url: str, params: dict | None = None):
    response = requests.get(url, params=params, timeout=30, headers=github_headers())
    response.raise_for_status()
    return response.json()


def github_unauth_rate_limited(error: requests.RequestException) -> bool:
    response = getattr(error, "response", None)
    return (
        not os.environ.get("GITHUB_TOKEN")
        and response is not None
        and response.status_code in {403, 429}
    )


def fetch_huggingface_repo_reports(org: dict, repo_id: str) -> list[dict]:
    try:
        metadata = huggingface_request(
            "GET",
            f"https://huggingface.co/api/models/{repo_id}",
        )
        model = metadata.json()
    except requests.RequestException as e:
        print(f"HuggingFace 공식 repo 메타데이터 검색 실패 ({org['name']} / {repo_id}): {e}", file=sys.stderr)
        return []

    try:
        readme = fetch_text_url(f"https://huggingface.co/{repo_id}/raw/main/README.md")
    except requests.RequestException:
        readme = ""

    fallback_title = markdown_heading(readme) or repo_id.split("/")[-1]
    abstract = markdown_abstract(readme)
    published = (model.get("createdAt") or model.get("lastModified") or "")[:10]
    reports = []
    for sibling in model.get("siblings", []):
        filename = sibling.get("rfilename", "")
        if not pdf_report_allowed(filename, readme):
            continue
        title = markdown_pdf_link_title(readme, filename) or fallback_title
        reports.append(official_report_to_paper({
            "title": title,
            "url": f"https://huggingface.co/{repo_id}/blob/main/{filename}",
            "published": published,
            "authors": [org["name"]],
            "abstract": abstract,
            "work_type": "technical-report",
            "company_match_source": f"official HuggingFace repo {repo_id}",
        }, org, source="official_repository_scan"))
    return reports


def fetch_huggingface_org_repo_reports(org: dict, author: str, max_repos: int) -> list[dict]:
    if max_repos <= 0:
        return []

    try:
        response = huggingface_request(
            "GET",
            "https://huggingface.co/api/models",
            params={
                "author": author,
                "limit": max_repos,
                "sort": "lastModified",
                "direction": -1,
            },
        )
        models = response.json()
    except requests.RequestException as e:
        print(f"HuggingFace 공식 org repo 검색 실패 ({org['name']} / {author}): {e}", file=sys.stderr)
        return []

    reports = []
    for model in models[:max_repos]:
        repo_id = model.get("id")
        if repo_id:
            reports.extend(fetch_huggingface_repo_reports(org, repo_id))
    return reports


def fetch_github_repo_reports(org: dict, repo: str) -> list[dict]:
    try:
        repo_data = github_get_json(f"https://api.github.com/repos/{repo}")
        branch = repo_data.get("default_branch", "main")
        tree = github_get_json(
            f"https://api.github.com/repos/{repo}/git/trees/{branch}",
            params={"recursive": "1"},
        )
        readme = fetch_text_url(f"https://raw.githubusercontent.com/{repo}/{branch}/README.md")
    except requests.RequestException as e:
        if github_unauth_rate_limited(e):
            return []
        print(f"GitHub 공식 repo 보고서 검색 실패 ({org['name']} / {repo}): {e}", file=sys.stderr)
        return []

    fallback_title = markdown_heading(readme) or repo.split("/")[-1]
    abstract = markdown_abstract(readme) or clean_markdown_text(repo_data.get("description", ""))
    published = (repo_data.get("created_at") or repo_data.get("pushed_at") or "")[:10]
    reports = []
    for item in tree.get("tree", []):
        path = item.get("path", "")
        if item.get("type") != "blob" or not github_pdf_path_allowed(path):
            continue
        title = markdown_pdf_link_title(readme, path) or fallback_title
        reports.append(official_report_to_paper({
            "title": title,
            "url": f"https://github.com/{repo}/blob/{branch}/{path}",
            "published": published,
            "authors": [org["name"]],
            "abstract": abstract,
            "work_type": "technical-report",
            "company_match_source": f"official GitHub repo {repo}",
        }, org, source="official_repository_scan"))
    return reports


def github_pdf_path_allowed(path: str) -> bool:
    return pdf_report_allowed(path)


def fetch_github_owner_repos(owner: str, max_repos: int) -> list[dict]:
    try:
        return github_get_json(
            f"https://api.github.com/orgs/{owner}/repos",
            params={
                "type": "public",
                "sort": "pushed",
                "direction": "desc",
                "per_page": max_repos,
            },
        )
    except requests.RequestException as e:
        if github_unauth_rate_limited(e):
            return []
        try:
            return github_get_json(
                f"https://api.github.com/users/{owner}/repos",
                params={
                    "type": "public",
                    "sort": "pushed",
                    "direction": "desc",
                    "per_page": max_repos,
                },
            )
        except requests.RequestException as e:
            if github_unauth_rate_limited(e):
                return []
            print(f"GitHub 공식 org repo 검색 실패 ({owner}): {e}", file=sys.stderr)
            return []


def fetch_github_org_repo_reports(org: dict, owner: str, max_repos: int) -> list[dict]:
    reports = []
    for repo in fetch_github_owner_repos(owner, max_repos)[:max_repos]:
        full_name = repo.get("full_name")
        if full_name:
            reports.extend(fetch_github_repo_reports(org, full_name))
    return reports


def fetch_official_report_papers(
    config: dict,
    org_registry: list[dict],
    days_back: int,
    since: str | None = None,
) -> list[dict]:
    """회사 공식 repo/manifest에 있는 논문·기술보고서 수집"""
    report_config = config.get("company_tracking", {}).get("official_reports", {})
    if not report_config.get("enabled", True):
        return []

    start_date = since or (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    end_date = datetime.now().strftime("%Y-%m-%d")
    max_hf_org_repos = report_config.get("max_huggingface_org_repos", 50)
    max_github_org_repos = report_config.get("max_github_org_repos", 20)
    max_workers = max(1, int(report_config.get("max_workers", 6)))
    if not os.environ.get("GITHUB_TOKEN"):
        max_github_org_repos = min(
            max_github_org_repos,
            report_config.get("max_github_org_repos_without_token", 2),
        )

    tracked_orgs = [
        org for org in org_registry
        if org.get("group_id", "").startswith("company_")
    ]
    if not tracked_orgs:
        return []

    def collect_org_reports(org: dict) -> list[dict]:
        org_papers = []
        for report in org.get("official_reports", []):
            paper = official_report_to_paper(report, org)
            if date_in_range(paper.get("published", ""), start_date, end_date):
                org_papers.append(paper)

        for source in org.get("official_publication_pages", []):
            for paper in fetch_official_publication_page(org, source, start_date, end_date):
                if date_in_range(paper.get("published", ""), start_date, end_date):
                    org_papers.append(paper)

        repositories = org.get("official_repositories", {})
        for author in repositories.get("huggingface_orgs", []):
            for paper in fetch_huggingface_org_repo_reports(org, author, max_hf_org_repos):
                if date_in_range(paper.get("published", ""), start_date, end_date):
                    org_papers.append(paper)
        for repo_id in repositories.get("huggingface", []):
            for paper in fetch_huggingface_repo_reports(org, repo_id):
                if date_in_range(paper.get("published", ""), start_date, end_date):
                    org_papers.append(paper)
        for repo in repositories.get("github", []):
            for paper in fetch_github_repo_reports(org, repo):
                if date_in_range(paper.get("published", ""), start_date, end_date):
                    org_papers.append(paper)
        for owner in repositories.get("github_orgs", []):
            for paper in fetch_github_org_repo_reports(org, owner, max_github_org_repos):
                if date_in_range(paper.get("published", ""), start_date, end_date):
                    org_papers.append(paper)

        return org_papers

    papers = []
    worker_count = min(max_workers, len(tracked_orgs))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(collect_org_reports, org) for org in tracked_orgs]
        for future in as_completed(futures):
            papers.extend(future.result())

    return papers


def fetch_huggingface_company_papers(
    config: dict,
    org_registry: list[dict],
    days_back: int,
    since: str | None = None,
) -> list[dict]:
    """HuggingFace Papers 검색으로 회사/모델명 기반 논문 보강"""
    hf_config = config.get("company_tracking", {}).get("huggingface_search", {})
    if not hf_config.get("enabled", True):
        return []

    start_date = since or (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    end_date = datetime.now().strftime("%Y-%m-%d")
    max_results_per_query = hf_config.get("max_results_per_query", 100)
    papers = []

    for org in org_registry:
        if not org.get("group_id", "").startswith("company_"):
            continue

        terms = org.get("huggingface_search_terms") or org.get("arxiv_search_terms") or [org["name"]]
        terms = list(dict.fromkeys([term for term in terms if term and len(term) > 2]))

        for term in terms:
            try:
                response = requests.get(
                    "https://huggingface.co/api/papers/search",
                    params={"q": term},
                    timeout=30,
                )
                response.raise_for_status()
                data = response.json()
            except requests.RequestException as e:
                print(f"HuggingFace Papers 검색 실패 ({org['name']} / {term}): {e}", file=sys.stderr)
                continue

            for item in data[:max_results_per_query]:
                paper_data = item.get("paper", {})
                arxiv_id = paper_data.get("id", "")
                published = (paper_data.get("publishedAt") or item.get("publishedAt") or "")[:10]
                if published and (published < start_date or published > end_date):
                    continue

                title = (paper_data.get("title") or item.get("title") or "").replace("\n", " ").strip()
                abstract = (paper_data.get("summary") or item.get("summary") or "").replace("\n", " ").strip()
                authors = [
                    author.get("name", "")
                    for author in paper_data.get("authors", [])
                    if author.get("name")
                ]
                if not match_huggingface_company_evidence(item, paper_data, org):
                    continue

                url = f"https://huggingface.co/papers/{arxiv_id}" if arxiv_id else ""
                paper = {
                    "id": f"arxiv:{get_arxiv_id(arxiv_id)}" if arxiv_id else f"hf:{term}:{title}",
                    "source": "huggingface_search",
                    "title": title,
                    "authors": authors,
                    "abstract": abstract,
                    "categories": [],
                    "url": url,
                    "published": published,
                    "affiliations": [org["name"]],
                    "author_affiliations": [],
                    "matched_keywords": [],
                    "matched_orgs": [org["name"]],
                    "company_groups": [org.get("group_id", "company")],
                    "company_regions": [org.get("region", "")],
                    "upvotes": paper_data.get("upvotes", 0),
                    "quality_signals": {
                        "company_match_source": "HuggingFace Papers organization/author metadata",
                    },
                }
                if title and url and is_ai_relevant_paper(paper, config):
                    papers.append(paper)

    return papers


def fetch_arxiv_query(
    query: str,
    days_back: int = 2,
    max_results: int = 500,
    since: str | None = None,
    request_delay_seconds: float = 0.0,
) -> list[dict]:
    """arXiv API에서 검색 쿼리로 논문 수집"""
    start_date = (
        datetime.strptime(since, "%Y-%m-%d")
        if since
        else datetime.now() - timedelta(days=days_back)
    )
    base_url = "http://export.arxiv.org/api/query?"
    papers = []
    start = 0
    batch_size = min(100, max_results)

    while start < max_results:
        params = {
            "search_query": query,
            "start": start,
            "max_results": min(batch_size, max_results - start),
            "sortBy": "submittedDate",
            "sortOrder": "descending"
        }

        url = base_url + urlencode(params)

        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
        except requests.RequestException:
            raise

        feed = feedparser.parse(response.content)
        if not feed.entries:
            break

        reached_older_than_start = False
        for entry in feed.entries:
            published = entry.get("published", "")
            if published:
                try:
                    pub_date = datetime.strptime(published, "%Y-%m-%dT%H:%M:%SZ")
                    if pub_date < start_date:
                        reached_older_than_start = True
                        continue
                except ValueError:
                    pass

            categories_list = [tag.get("term", "") for tag in entry.get("tags", [])]
            arxiv_id = entry.get("id", "").split("/abs/")[-1]
            authors = []
            author_affiliations = []
            for author in entry.get("authors", []):
                authors.append(author.get("name", ""))
                affiliation = (
                    author.get("arxiv_affiliation")
                    or author.get("affiliation")
                    or author.get("affiliations")
                )
                if isinstance(affiliation, list):
                    author_affiliations.extend([str(a) for a in affiliation if a])
                elif affiliation:
                    author_affiliations.append(str(affiliation))

            paper = {
                "id": f"arxiv:{get_arxiv_id(arxiv_id)}",
                "source": "arxiv",
                "title": entry.get("title", "").replace("\n", " ").strip(),
                "authors": authors,
                "abstract": entry.get("summary", "").replace("\n", " ").strip(),
                "categories": categories_list,
                "url": f"https://arxiv.org/abs/{arxiv_id}",
                "published": published[:10] if published else "",
                "affiliations": [],
                "author_affiliations": author_affiliations,
                "matched_keywords": [],
                "matched_orgs": []
            }
            papers.append(paper)

        if reached_older_than_start or len(feed.entries) < batch_size:
            break

        start += batch_size
        if request_delay_seconds > 0 and start < max_results:
            time.sleep(request_delay_seconds)

    return papers


def load_openalex_cache() -> dict:
    if OPENALEX_CACHE_FILE.exists():
        try:
            with open(OPENALEX_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_openalex_cache(cache: dict):
    OUTPUT_DIR.mkdir(exist_ok=True)
    with open(OPENALEX_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, ensure_ascii=False, indent=2, fp=f)


def openalex_get(path: str, params: dict, config: dict) -> dict:
    """OpenAlex API 호출"""
    openalex_config = config.get("company_tracking", {}).get("openalex", {})
    mailto = openalex_config.get("mailto")
    if mailto:
        params = {**params, "mailto": mailto}

    response = requests.get(
        f"{OPENALEX_API_BASE}{path}",
        params=params,
        timeout=30,
        headers={"User-Agent": "awesome-frontier-ai-papers/0.2"},
    )
    response.raise_for_status()
    return response.json()


def resolve_openalex_institutions(org: dict, config: dict, cache: dict) -> list[str]:
    """회사명을 OpenAlex institution ID로 해석"""
    configured_ids = org.get("openalex_ids", [])
    if configured_ids:
        return configured_ids

    openalex_config = config.get("company_tracking", {}).get("openalex", {})
    if not openalex_config.get("resolve_institutions", True):
        return []

    search_terms = org.get("openalex_search_terms") or [org["name"]]
    country = org.get("country_code")
    cache_key = "|".join([org["name"], ",".join(search_terms), country or "any"])
    cached = cache.get(cache_key)
    if cached and cached.get("ids"):
        return cached["ids"]

    max_ids = openalex_config.get("max_institutions_per_org", 3)
    resolved = []

    for term in search_terms:
        try:
            data = openalex_get("/institutions", {
                "search": term,
                "per-page": 5,
                "select": "id,display_name,country_code,type,ror",
            }, config)
        except requests.RequestException as e:
            print(f"OpenAlex 기관 검색 실패 ({org['name']}): {e}", file=sys.stderr)
            continue

        for result in data.get("results", []):
            if country and result.get("country_code") and result.get("country_code") != country:
                continue
            display_name = result.get("display_name", "")
            searchable = " ".join([display_name] + org.get("aliases", []))
            if any(re.search(term_pattern(alias), searchable, re.IGNORECASE) for alias in [org["name"], term]):
                resolved.append(result["id"])
                break

        if len(resolved) >= max_ids:
            break

    resolved = list(dict.fromkeys(resolved))[:max_ids]
    cache[cache_key] = {
        "ids": resolved,
        "resolved_at": datetime.now().strftime("%Y-%m-%d"),
    }
    return resolved


def reconstruct_openalex_abstract(inverted_index: dict | None) -> str:
    """OpenAlex abstract_inverted_index를 일반 문자열로 복원"""
    if not inverted_index:
        return ""

    words_by_pos = {}
    for word, positions in inverted_index.items():
        for pos in positions:
            words_by_pos[pos] = word

    return " ".join(words_by_pos[pos] for pos in sorted(words_by_pos))


def best_openalex_url(work: dict) -> str:
    """arXiv 링크가 있으면 우선 사용하고, 없으면 DOI/landing page 사용"""
    locations = []
    if work.get("primary_location"):
        locations.append(work["primary_location"])
    locations.extend(work.get("locations") or [])

    for location in locations:
        for key in ("landing_page_url", "pdf_url"):
            url = location.get(key)
            if url and "arxiv.org" in url:
                return url.replace("/pdf/", "/abs/").removesuffix(".pdf")

    primary = work.get("primary_location") or {}
    return (
        primary.get("landing_page_url")
        or work.get("ids", {}).get("doi")
        or work.get("id")
        or ""
    )


def openalex_work_to_paper(work: dict, org: dict) -> dict:
    authors = []
    institutions = set()

    for authorship in work.get("authorships", []):
        author_name = authorship.get("author", {}).get("display_name")
        if author_name:
            authors.append(author_name)
        for institution in authorship.get("institutions", []):
            display_name = institution.get("display_name")
            if display_name:
                institutions.add(display_name)

    url = best_openalex_url(work)
    openalex_id = work.get("id", "")
    doi = work.get("ids", {}).get("doi", "")
    paper_id = f"openalex:{openalex_id.split('/')[-1]}" if openalex_id else get_paper_key({"url": url, "doi": doi})
    if "arxiv.org/abs/" in url:
        paper_id = f"arxiv:{get_arxiv_id(url)}"

    return {
        "id": paper_id,
        "source": "openalex",
        "work_type": work.get("type", ""),
        "title": work.get("display_name", "").replace("\n", " ").strip(),
        "authors": authors,
        "abstract": reconstruct_openalex_abstract(work.get("abstract_inverted_index")),
        "categories": [],
        "url": url,
        "published": work.get("publication_date", ""),
        "doi": doi,
        "openalex_id": openalex_id,
        "affiliations": [org["name"]],
        "author_affiliations": sorted(institutions),
        "matched_keywords": [],
        "matched_orgs": [org["name"]],
        "company_groups": [org.get("group_id", "company")],
        "company_regions": [org.get("region", "")],
        "cited_by_count": work.get("cited_by_count", 0),
        "concepts": [
            {
                "id": concept.get("id", ""),
                "display_name": concept.get("display_name", ""),
                "score": concept.get("score", 0),
            }
            for concept in work.get("concepts", [])
        ],
        "quality_signals": {
            "affiliation_source": "OpenAlex",
            "company_match_source": "OpenAlex authorship institution metadata",
            "cited_by_count": work.get("cited_by_count", 0),
        },
    }


def fetch_openalex_company_papers(
    config: dict,
    org_registry: list[dict],
    days_back: int,
    since: str | None = None,
) -> list[dict]:
    """OpenAlex affiliation metadata로 회사/연구소 논문 수집"""
    tracking = config.get("company_tracking", {})
    openalex_config = tracking.get("openalex", {})
    if not openalex_config.get("enabled", True):
        return []

    start_date = since or (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    end_date = datetime.now().strftime("%Y-%m-%d")
    max_per_org = openalex_config.get("max_results_per_org", 30)
    ai_concept_ids = get_ai_concept_ids(config)
    cache = load_openalex_cache()
    papers = []

    for org in org_registry:
        if not org.get("group_id", "").startswith("company_"):
            continue

        institution_ids = resolve_openalex_institutions(org, config, cache)
        if not institution_ids:
            continue

        filters = [
            "authorships.institutions.id:" + "|".join(institution_ids[:5]),
            f"from_publication_date:{start_date}",
            f"to_publication_date:{end_date}",
            "is_retracted:false",
        ]
        if ai_concept_ids:
            filters.append("concepts.id:" + "|".join(ai_concept_ids))
        params = {
            "filter": ",".join(filters),
            "sort": "publication_date:desc",
            "select": "id,display_name,publication_date,authorships,primary_location,locations,ids,abstract_inverted_index,cited_by_count,type,concepts",
        }

        org_count = 0
        cursor = "*"
        while org_count < max_per_org:
            page_size = min(200, max_per_org - org_count)
            page_params = {
                **params,
                "per-page": page_size,
                "cursor": cursor,
            }

            try:
                data = openalex_get("/works", page_params, config)
            except requests.RequestException as e:
                print(f"OpenAlex 논문 수집 실패 ({org['name']}): {e}", file=sys.stderr)
                break

            results = data.get("results", [])
            if not results:
                break

            for work in results:
                paper = openalex_work_to_paper(work, org)
                if (
                    paper["title"]
                    and is_ai_relevant_paper(paper, config)
                    and not is_excluded_company_paper(paper, config)
                ):
                    papers.append(paper)
                org_count += 1

            next_cursor = data.get("meta", {}).get("next_cursor")
            if not next_cursor or len(results) < page_size:
                break
            cursor = next_cursor

    save_openalex_cache(cache)
    return papers


def arxiv_quote(term: str) -> str:
    return f'all:"{term}"' if " " in term or "." in term else f"all:{term}"


def chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def fetch_arxiv_company_papers(
    config: dict,
    org_registry: list[dict],
    days_back: int,
    since: str | None = None,
) -> list[dict]:
    """arXiv에서 회사명/연구소명 기반 보강 검색"""
    tracking = config.get("company_tracking", {})
    arxiv_config = tracking.get("arxiv_company_search", {})
    if not arxiv_config.get("enabled", True):
        return []

    categories = config.get("categories", ["cs.CL", "cs.AI", "cs.LG"])
    cat_query = " OR ".join([f"cat:{cat}" for cat in categories])
    max_results_per_query = arxiv_config.get("max_results_per_query", 200)
    query_chunk_size = arxiv_config.get("query_chunk_size", 20)
    request_delay = arxiv_config.get("request_delay_seconds", 3)
    papers = []
    grouped_orgs: dict[str, list[dict]] = {}

    for org in org_registry:
        if not org.get("group_id", "").startswith("company_"):
            continue
        grouped_orgs.setdefault(org["group_id"], []).append(org)

    first_query = True

    for orgs in grouped_orgs.values():
        terms = []
        for org in orgs:
            terms.extend(org.get("arxiv_search_terms") or [org["name"]])
        terms = list(dict.fromkeys([term for term in terms if term and len(term) > 2]))

        for terms_chunk in chunks(terms, query_chunk_size):
            org_query = " OR ".join(arxiv_quote(term) for term in terms_chunk)
            query = f"({cat_query}) AND ({org_query})"

            if not first_query and request_delay > 0:
                time.sleep(request_delay)
            first_query = False

            try:
                org_papers = fetch_arxiv_query(
                    query,
                    days_back,
                    max_results_per_query,
                    since=since,
                    request_delay_seconds=request_delay,
                )
            except requests.RequestException as e:
                print(f"arXiv 회사 검색 실패 ({terms_chunk[0]}...): {e}", file=sys.stderr)
                continue

            for paper in org_papers:
                text = paper_match_text(paper)
                matched_orgs = match_organizations(text, orgs)
                if matched_orgs:
                    company_groups = []
                    company_regions = []
                    for org_name in matched_orgs:
                        metadata = get_org_metadata(org_name, org_registry)
                        company_groups.append(metadata.get("group_id", "company"))
                        company_regions.append(metadata.get("region", ""))

                    paper["matched_orgs"] = list(dict.fromkeys(paper.get("matched_orgs", []) + matched_orgs))
                    paper["affiliations"] = paper["matched_orgs"]
                    paper["company_groups"] = list(dict.fromkeys(paper.get("company_groups", []) + company_groups))
                    paper["company_regions"] = list(dict.fromkeys(paper.get("company_regions", []) + company_regions))
                    paper["quality_signals"] = {
                        **paper.get("quality_signals", {}),
                        "company_match_source": "arXiv metadata/text",
                    }
                    papers.append(paper)

    return papers


def match_keywords(text: str, keywords: list[str]):
    """텍스트에서 키워드 매칭"""
    matched = []
    text_lower = text.lower()

    for keyword in keywords:
        pattern = r"\b" + re.escape(keyword.lower()) + r"\b"
        if re.search(pattern, text_lower):
            matched.append(keyword)

    return matched


def compute_quality_score(paper: dict) -> int:
    """간단한 정렬용 품질/관련도 점수"""
    score = 0
    score += min(int(paper.get("upvotes", 0)), 50)
    score += min(int(paper.get("cited_by_count", 0)), 30)
    score += 12 * len(paper.get("matched_orgs", []))
    score += 4 * len(paper.get("matched_keywords", []))
    if paper.get("source") == "openalex":
        score += 10
    if paper.get("official_report"):
        score += 25
    if paper.get("company_groups"):
        score += 15
    return score


def enrich_papers(
    papers: list[dict],
    config: dict,
    org_registry: list[dict],
    allow_text_org_matches: bool = True,
) -> list[dict]:
    """키워드/기관 매칭과 정렬 점수 보강"""
    for paper in papers:
        text = paper_match_text(paper)
        matched_keywords = list(dict.fromkeys(paper.get("matched_keywords", [])))
        matched_orgs = list(dict.fromkeys(paper.get("matched_orgs", [])))
        if allow_text_org_matches:
            matched_orgs = list(dict.fromkeys(matched_orgs + match_organizations(text, org_registry)))
        company_groups = list(paper.get("company_groups", []))
        company_regions = list(paper.get("company_regions", []))

        for org_name in matched_orgs:
            metadata = get_org_metadata(org_name, org_registry)
            group_id = metadata.get("group_id")
            region = metadata.get("region")
            if group_id and group_id.startswith("company_"):
                company_groups.append(group_id)
            if region:
                company_regions.append(region)

        paper["matched_keywords"] = matched_keywords
        paper["matched_orgs"] = matched_orgs
        paper["affiliations"] = matched_orgs
        paper["company_groups"] = list(dict.fromkeys(company_groups))
        paper["company_regions"] = list(dict.fromkeys(company_regions))
        paper["quality_score"] = compute_quality_score(paper)

    return papers


def merge_paper_lists(*paper_lists: list[dict]) -> list[dict]:
    """여러 소스의 논문을 중복 제거하며 병합"""
    merged: dict[str, dict] = {}
    key_index: dict[str, str] = {}
    source_priority = {
        "official_report": 50,
        "official_repository_scan": 45,
        "official_publication_page": 40,
        "huggingface_search": 30,
        "openalex": 20,
        "arxiv": 10,
    }

    for papers in paper_lists:
        for paper in papers:
            keys = [key for key in (get_paper_key(paper), get_url_key(paper), get_title_key(paper)) if key]
            if not keys:
                continue

            existing_key = next((key_index[key] for key in keys if key in key_index), "")
            if not existing_key:
                key = keys[0]
                paper["sources"] = list(dict.fromkeys([paper.get("source", "unknown")]))
                merged[key] = paper
                for candidate_key in keys:
                    key_index[candidate_key] = key
                continue

            existing = merged[existing_key]
            existing["sources"] = list(dict.fromkeys(existing.get("sources", []) + [paper.get("source", "unknown")]))
            for field in ("matched_keywords", "matched_orgs", "affiliations", "author_affiliations", "company_groups", "company_regions"):
                existing[field] = list(dict.fromkeys(existing.get(field, []) + paper.get(field, [])))
            for field in ("upvotes", "cited_by_count", "quality_score"):
                existing[field] = max(existing.get(field, 0), paper.get(field, 0))
            existing["official_report"] = bool(existing.get("official_report") or paper.get("official_report"))
            if not existing.get("abstract") and paper.get("abstract"):
                existing["abstract"] = paper["abstract"]
            if not existing.get("authors") and paper.get("authors"):
                existing["authors"] = paper["authors"]
            if source_priority.get(paper.get("source", ""), 0) > source_priority.get(existing.get("source", ""), 0):
                existing["source"] = paper.get("source", existing.get("source", "unknown"))
            if existing.get("source") != "openalex" and paper.get("source") == "openalex":
                existing["openalex_id"] = paper.get("openalex_id", existing.get("openalex_id"))
                existing["doi"] = paper.get("doi", existing.get("doi"))
            for candidate_key in keys:
                key_index[candidate_key] = existing_key

    return list(merged.values())
