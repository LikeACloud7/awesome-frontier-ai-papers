#!/usr/bin/env python3
"""Generate the README and per-lab paper indexes from the static dataset."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


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
    text = str(value or "").replace("\n", " ").strip()
    text = text.replace("|", "\\|")
    return re.sub(r"\s+", " ", text)


def link(title: str, url: str) -> str:
    title = md_escape(title)
    if not url:
        return title
    safe_url = str(url).replace(")", "%29")
    return f"[{title}]({safe_url})"


def source_label(source: str) -> str:
    labels = {
        "official_publication_page": "Official page",
        "official_report": "Official report",
        "official_repository_scan": "Official repo",
        "huggingface_search": "HuggingFace",
        "openalex": "OpenAlex",
        "arxiv": "arXiv",
    }
    return labels.get(source, source or "source")


def source_labels(paper: dict) -> str:
    return ", ".join(source_label(source) for source in paper.get("sources") or [paper.get("source", "")])


def lab_anchor(name: str) -> str:
    anchor = name.lower().replace("/", "").replace(".", "")
    anchor = re.sub(r"[^a-z0-9가-힣 -]", "", anchor)
    return anchor.replace(" ", "-")


def lab_doc_path(company: dict) -> str:
    return f"docs/labs/{company['id']}.md"


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
        date = md_escape(paper.get("published") or "n.d.")
        title = link(paper.get("title", ""), paper.get("url", ""))
        work_type = md_escape(paper.get("work_type") or "paper")
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
            f"# {company['name']} Papers",
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

        (LAB_DOCS_DIR / f"{company['id']}.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def region_icon(region: str) -> str:
    return "🇺🇸" if region == "US" else "🇨🇳" if region == "China" else "🌐"


def build_readme(dataset: dict) -> str:
    companies = dataset["companies"]
    papers = dataset["papers"]
    totals = dataset["totals"]
    generated_at = dataset.get("generated_at", "")

    recent_all = sorted(papers, key=paper_sort_key, reverse=True)[:20]

    lines = [
        "# Awesome Frontier AI Papers",
        "",
        "> An Awesome-style GitHub repository for frontier AI lab papers, model cards, system cards, dataset cards, and technical reports, with a clean web view for search and filtering.",
        "",
        "[![Update papers and deploy site](https://github.com/LikeACloud7/awesome-frontier-ai-papers/actions/workflows/update-papers.yml/badge.svg)](https://github.com/LikeACloud7/awesome-frontier-ai-papers/actions/workflows/update-papers.yml)",
        f"[![GitHub Pages](https://img.shields.io/badge/site-GitHub%20Pages-222?logo=github)]({PAGES_URL})",
        "![Papers](https://img.shields.io/badge/papers-{papers}-0f766e)".format(papers=totals["papers"]),
        "",
        "**Keywords:** frontier AI papers, LLM papers, AI research papers, model cards, system cards, technical reports, OpenAI papers, Anthropic papers, Google DeepMind papers, Meta FAIR papers, Qwen papers, DeepSeek papers.",
        "",
        f"- Website: {PAGES_URL}",
        f"- Dataset: [`public/data/company_papers.json`](public/data/company_papers.json)",
        f"- AI/LLM crawler summary: [`public/llms.txt`](public/llms.txt)",
        f"- Last generated: `{generated_at}`",
        f"- Coverage: `{totals['papers']}` papers across `{totals['companies']}` labs since 2024",
        "",
        "## Labs",
        "",
        "| Region | Lab | Papers | Latest | Full list |",
        "|---|---:|---:|---|---|",
    ]

    for company in companies:
        lines.append(
            f"| {region_icon(company.get('region', ''))} {md_escape(company.get('region', ''))} "
            f"| [{md_escape(company['name'])}](#{lab_anchor(company['name'])}) "
            f"| {company['paper_count']} "
            f"| {company.get('latest_paper_date') or 'n.d.'} "
            f"| [all papers]({lab_doc_path(company)}) |"
        )

    lines.extend([
        "",
        "## Latest Across Labs",
        "",
    ])
    lines.extend(table_rows(recent_all, include_lab=True))
    lines.extend([
        "",
        "## Papers By Lab",
        "",
        "Each section shows the newest papers for quick scanning. Open the per-lab page for the complete list.",
        "",
    ])

    for company in companies:
        company_papers = papers_for_company(papers, company["name"])
        visible = company_papers[:MAX_README_PAPERS_PER_LAB]
        lines.extend([
            f"### {region_icon(company.get('region', ''))} {company['name']}",
            "",
            f"`{company['paper_count']}` papers · latest `{company.get('latest_paper_date') or 'n.d.'}` · [full list]({lab_doc_path(company)})",
            "",
        ])
        lines.extend(table_rows(visible))
        if len(company_papers) > len(visible):
            lines.append(f"\nMore: [{len(company_papers) - len(visible)} additional papers]({lab_doc_path(company)})")
        lines.append("")

    lines.extend([
        "## Collection Policy",
        "",
        "Included by default:",
        "",
        "- official company publication pages and feeds",
        "- official technical reports, model cards, system cards, and dataset cards",
        "- company-owned HuggingFace and GitHub repositories",
        "- HuggingFace Papers entries with matching organization or author metadata",
        "- OpenAlex authorship institution metadata via `--comprehensive`",
        "",
        "The broad arXiv company-name text sweep is disabled by default because model names can over-match third-party papers. Use `--include-arxiv` only when that noisy layer is wanted.",
        "",
        "See [docs/COVERAGE.md](docs/COVERAGE.md) for source and caveat details.",
        "",
        "## Update Locally",
        "",
        "```bash",
        "python3 -m venv venv",
        "venv/bin/pip install -r requirements.txt",
        "npm install",
        "venv/bin/python scripts/update_company_papers.py --since 2024-01-01 --comprehensive --max-papers 50000",
        "venv/bin/python scripts/generate_markdown_index.py",
        "npm run dev",
        "```",
        "",
        "## License",
        "",
        "MIT. See [LICENSE](LICENSE).",
    ])

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    dataset = load_dataset()
    write_lab_docs(dataset)
    README_FILE.write_text(build_readme(dataset), encoding="utf-8")


if __name__ == "__main__":
    main()
