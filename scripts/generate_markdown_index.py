#!/usr/bin/env python3
"""Generate the README and per-lab paper indexes from the static dataset."""

from __future__ import annotations

import json
import html
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from security import safe_link, safe_slug


REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = REPO_ROOT / "public" / "data" / "company_papers.json"
README_FILE = REPO_ROOT / "README.md"
LAB_DOCS_DIR = REPO_ROOT / "docs" / "labs"
MAX_README_PAPERS_PER_LAB = 8
REPO_URL = "https://github.com/LikeACloud7/awesome-frontier-ai-papers"
PAGES_URL = "https://likeacloud7.github.io/awesome-frontier-ai-papers/"


def load_dataset() -> dict:
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def md_escape(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = html.escape(text, quote=False)
    return re.sub(r"([\\`*_\[\]|:.@])", r"\\\1", text)


def link(title: str, url: str) -> str:
    title = md_escape(title)
    if not safe_link(url):
        return title
    safe_url = quote(url, safe=":/?#[]@!$&'*,;=%+")
    return f"[{title}](<{safe_url}>)"


def source_label(source: str) -> str:
    labels = {
        "official_publication_page": "Official page",
        "official_report": "Official report",
        "official_repository_scan": "Official repo",
        "huggingface_search": "Hugging Face",
        "openalex": "OpenAlex",
        "arxiv": "arXiv",
        "arxiv_affiliation": "Verified affiliation",
    }
    return labels.get(source, source or "source")


def source_labels(paper: dict) -> str:
    return ", ".join(source_label(source) for source in paper.get("sources") or [paper.get("source", "")])


def lab_anchor(name: str) -> str:
    anchor = name.lower().replace("/", "").replace(".", "")
    anchor = re.sub(r"[^a-z0-9가-힣 -]", "", anchor)
    return anchor.replace(" ", "-")


def lab_doc_path(company: dict) -> str:
    return f"docs/labs/{safe_slug(company['id'])}.md"


def paper_sort_key(paper: dict) -> tuple[str, int, str]:
    return (
        paper.get("published", ""),
        int(paper.get("quality_score", 0) or 0),
        paper.get("title", ""),
    )


def papers_for_company(papers: list[dict], company_name: str) -> list[dict]:
    selected = [paper for paper in papers if company_name in paper.get("companies", [])]
    return sorted(selected, key=paper_sort_key, reverse=True)


def table_rows(papers: list[dict], include_lab: bool = False) -> list[str]:
    header = "| Date | Paper | Type | Source |"
    divider = "|---|---|---|---|"
    if include_lab:
        header = "| Date | Lab | Paper | Type | Source |"
        divider = "|---|---|---|---|---|"

    rows = [header, divider]
    for paper in papers:
        date = md_escape(paper.get("published") or "n.d.").replace("-", "‑")
        title = link(paper.get("title", ""), paper.get("url", ""))
        work_type = md_escape((paper.get("work_type") or "paper").replace("_", " ").replace("-", " ").capitalize())
        sources = md_escape(source_labels(paper))
        if include_lab:
            labs = md_escape(", ".join(paper.get("companies", [])))
            rows.append(f"| {date} | {labs} | {title} | {work_type} | {sources} |")
        else:
            rows.append(f"| {date} | {title} | {work_type} | {sources} |")
    return rows


def year_for_paper(paper: dict) -> str:
    published = paper.get("published", "")
    return published[:4] if re.match(r"^\d{4}", published) else "No date"


def write_lab_docs(dataset: dict) -> None:
    LAB_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    papers = dataset["papers"]

    for company in dataset["companies"]:
        company_papers = papers_for_company(papers, company["name"])
        by_year: dict[str, list[dict]] = defaultdict(list)
        for paper in company_papers:
            by_year[year_for_paper(paper)].append(paper)

        lines = [
            f"# {md_escape(company['name'])} Papers",
            "",
            f"- Region: `{company.get('region', '')}`",
            f"- Papers: `{len(company_papers)}`",
            f"- Latest: `{company.get('latest_paper_date') or 'n.d.'}`",
            f"- [Back to README](../../README.md#{lab_anchor(company['name'])})",
            "",
        ]

        for year in sorted(by_year.keys(), reverse=True):
            lines.extend([f"## {year}", ""])
            lines.extend(table_rows(by_year[year]))
            lines.append("")

        (LAB_DOCS_DIR / f"{safe_slug(company['id'])}.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def region_icon(region: str) -> str:
    return "🇺🇸" if region == "US" else "🇨🇳" if region == "China" else "🌐"


def build_readme(dataset: dict) -> str:
    companies = sorted(dataset["companies"], key=lambda c: (0 if c.get("region") == "US" else 1, c["name"].casefold()))
    papers = dataset["papers"]
    totals = dataset["totals"]
    generated_at = dataset.get("generated_at", "")
    snapshot = generated_at[:10] or "Not generated"
    health = dataset.get("collection", {})
    region_counts = {region: sum(c.get("region") == region for c in companies) for region in ("US", "China")}
    recent_all = sorted(papers, key=paper_sort_key, reverse=True)[:20]
    lines = [
        '<p align="center">',
        '  <img src=".github/assets/research-map.svg" width="100%" alt="Frontier research, connected — AI papers from the United States and China">',
        '</p>',
        '',
        '<h1 align="center">Awesome Frontier AI Papers</h1>',
        '',
        '<p align="center">',
        '  <strong>An open research index for the frontier of AI.</strong><br>',
        '  Follow papers, technical reports, and model cards from leading US and Chinese AI labs.',
        '</p>',
        '',
        '<p align="center">',
        '  <a href="https://awesome.re"><img src="https://awesome.re/badge.svg" alt="Awesome"></a>',
        f'  <a href="{REPO_URL}/actions/workflows/update-papers.yml"><img src="{REPO_URL}/actions/workflows/update-papers.yml/badge.svg" alt="Collection and deployment workflow"></a>',
        f'  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT license"></a>',
        '  <a href="public/data/company_papers.json"><img src="https://img.shields.io/badge/data-open_JSON-10b981.svg" alt="Open JSON dataset"></a>',
        '</p>',
        '',
        '<p align="center">',
        f'  <a href="{PAGES_URL}"><strong>Explore the website ↗</strong></a> ·',
        '  <a href="#latest-across-labs">Latest papers</a> ·',
        '  <a href="#labs">Browse labs</a> ·',
        '  <a href="docs/COVERAGE.md">Coverage</a> ·',
        '  <a href="CONTRIBUTING.md">Contribute</a>',
        '</p>',
        '',
        '| Publications in the index | Tracked labs | Archive window | Latest snapshot |',
        '|:---:|:---:|:---:|:---:|',
        f"| **{totals['papers']:,}** | **{totals['tracked_companies']}** · {region_counts['US']} US / {region_counts['China']} China | **2024 → present** | **{snapshot}** |",
        '',
        'AI research by the tracked labs, with links back to the source. Publication counts include papers, research posts, technical reports, and model or system cards.',
        '',
    ]
    if health.get("status") == "partial":
        errors = health.get("error_sources", health.get("failed_sources", 0))
        pending = health.get("pending_metadata", 0)
        source_status = f"{errors} source needs attention" if errors == 1 else f"{errors} sources need attention"
        pending_status = f"{pending} record awaits metadata" if pending == 1 else f"{pending} records await metadata"
        lines.extend(['> [!NOTE]',
            f"> **Collection is still catching up.** {source_status} and {pending_status}. [View collection status](public/data/collection_health.json).", ''])
    lines.extend([
        '## Latest Across Labs', '',
        'The 20 newest entries. Use the [web explorer](' + PAGES_URL + ') to search by lab, date, author, or source.', '',
    ])
    lines.extend(table_rows(recent_all, include_lab=True))
    lines.extend(['', '## Labs', '', 'Jump to a lab’s recent work, or open its complete archive.', ''])
    for region, title in [('US', '🇺🇸 United States'), ('China', '🇨🇳 China')]:
        lines.extend([f'### {title}', '', '| Lab | Publications | Latest | Archive |', '|:---|---:|:---|:---|'])
        for company in companies:
            if company.get('region') != region:
                continue
            lines.append(f"| [{md_escape(company['name'])}](#{lab_anchor(company['name'])}) | **{company['paper_count']:,}** | {md_escape(company.get('latest_paper_date') or 'n.d.')} | [Full list →]({lab_doc_path(company)}) |")
        lines.append('')
    lines.extend(['## Papers by Lab', '', 'The latest eight entries for every lab. Full archives are organized by year.', ''])
    for company in companies:
        company_papers = papers_for_company(papers, company['name'])
        visible = company_papers[:MAX_README_PAPERS_PER_LAB]
        lines.extend([f"### {md_escape(company['name'])}", '',
            f"{region_icon(company.get('region', ''))} **{company['paper_count']:,} publications** · Latest `{company.get('latest_paper_date') or 'n.d.'}` · [Full archive →]({lab_doc_path(company)})", ''])
        lines.extend(table_rows(visible))
        lines.extend(['', f"[All {company['paper_count']:,} entries →]({lab_doc_path(company)}) · [Back to labs ↑](#labs)", ''])
    lines.extend([
        '## How the Index Works', '',
        '| Discover | Verify | Keep up |',
        '|:---|:---|:---|',
        '| Official lab catalogues, feeds, repositories, OpenAlex, and Hugging Face. | AI topic evidence and lab attribution; arXiv author affiliations when needed. | Daily recent collection, rotating historical reconciliation, and persistent retry queues. |',
        '',
        'The scope is **AI research from the configured frontier labs**. Model mentions alone do not establish authorship. Public sources have gaps, so this index does not claim exhaustive coverage.',
        '',
        '[Collection policy & limitations](docs/COVERAGE.md) · [Open dataset](public/data/company_papers.json) · [Collection health](public/data/collection_health.json)',
        '',
        '## Contributing', '',
        'Found a missing source, broken link, or incorrect lab attribution? Contributions are welcome.', '',
        '- Add or repair a source in [`config/frontier_labs.json`](config/frontier_labs.json).',
        '- Include an official publication link or author-affiliation evidence.',
        '- Follow the [contribution guide](CONTRIBUTING.md). Report security issues through the [security policy](SECURITY.md).',
        '',
        '## Run Locally', '',
        '<details>', '<summary><strong>Set up the collector and web explorer</strong></summary>', '',
        'Requires Python 3.10+ and Node.js 22+.', '',
        '```bash',
        'python3 -m venv .venv',
        '.venv/bin/python -m pip install --require-hashes -r requirements.txt',
        'npm ci --ignore-scripts',
        '',
        '.venv/bin/python scripts/update_company_papers.py --days 30 --comprehensive --reconcile',
        '.venv/bin/python scripts/generate_markdown_index.py',
        'npm run dev',
        '```', '',
        'The README and lab lists are generated. Edit `scripts/generate_markdown_index.py` to change their layout.', '',
        '</details>', '',
        '## License', '',
        '[MIT](LICENSE) for this project. Linked papers and reports retain their original licenses and copyrights.', '',
        '---', '',
        '<p align="center"><sub>Built for reading, exploring, and keeping up with frontier AI research.</sub></p>',
    ])
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    dataset = load_dataset()
    write_lab_docs(dataset)
    README_FILE.write_text(build_readme(dataset), encoding="utf-8")


if __name__ == "__main__":
    main()
