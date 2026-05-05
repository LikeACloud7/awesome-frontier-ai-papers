# Coverage Notes

This project is built for lab-level frontier AI monitoring. The goal is to list papers and reports where a tracked company or lab is visible in a source-level signal such as an official publication page, official report source, organization metadata, or author affiliation metadata.

## Included By Default

- Official publication pages and feeds configured in `config/frontier_labs.json`
- Official technical reports, model cards, system cards, and dataset cards configured per lab
- Company-owned HuggingFace and GitHub repositories when `--comprehensive` is used
- HuggingFace Papers results when organization or author metadata matches the tracked lab
- OpenAlex works whose authorship institution metadata resolves to a tracked lab when `--comprehensive` is used

## Excluded By Default

- Papers that only mention a company model or product name without authorship or affiliation evidence
- Broad arXiv text matches for model-family terms, unless `--include-arxiv` is explicitly used
- General company research unrelated to configured frontier-AI terms, unless it is an explicit report/card source
- Common archive or release-noise URLs configured in `excluded_url_patterns`

## Why arXiv Text Search Is Optional

arXiv's public API does not reliably expose full PDF affiliation text. Searching for terms such as `Qwen`, `DeepSeek`, `Claude`, or `GPT` can retrieve many third-party papers that use those models but were not authored by the labs. That is useful for a broader ecosystem scan, but it is too noisy for the default "papers from frontier labs" archive.

Use:

```bash
venv/bin/python scripts/update_company_papers.py --since 2024-01-01 --comprehensive --include-arxiv --max-papers 50000
```

only when you intentionally want the broader sweep.

## Adding Coverage

The preferred order for new sources is:

1. official lab publication page or API
2. official RSS/feed endpoint
3. official HuggingFace organization paper or model repo
4. official GitHub organization or repo with report PDFs
5. OpenAlex institution IDs
6. manually curated official report entries
7. optional arXiv text fallback

Every added source should preserve a `quality_signals.company_match_source` value so users can see why an item was included.
