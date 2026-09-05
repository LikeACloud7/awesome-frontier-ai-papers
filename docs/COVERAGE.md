# Coverage and automatic collection

This archive tracks **AI research authored by the configured US and Chinese frontier labs**. The lab list is in `config/frontier_labs.json`. It includes language models, agents, alignment and safety, multimodal models, vision, speech, robotics, learning methods, and AI training/inference systems. It is not a catalogue of every subject a parent company publishes, and a third-party paper using GPT, Qwen, or another model is not automatically a paper from that model's lab.

## What runs automatically

The GitHub Actions workflow runs daily. Each run:

1. Collects the last 30 days from official publication catalogues, feeds, OpenAlex affiliations, and Hugging Face metadata.
2. Follows pagination on official catalogues, including Hugging Face organization pages, DeepMind, Google Research, Meta, Microsoft, Amazon, NVIDIA, ByteDance, and Huawei. Anthropic's embedded research catalogue and Alignment Science articles are also collected.
3. Uses the explicit AI-research scope of dedicated catalogues (such as Anthropic Research or Apple Machine Learning). General company catalogues such as Microsoft, Amazon Science, NVIDIA and Google Research require AI topic, venue, or abstract evidence. Sparse listing records get detail metadata before a topic decision; unresolved records are queued for retry.
4. Scans a rotating batch of official repositories and queues arXiv links found in their READMEs. These links require author-affiliation verification before lab attribution; citations to someone else's paper are not sufficient.
5. Searches arXiv using organization terms and rotating known-author queries, then checks the author affiliation block in HTML or the first PDF page. Unfinished candidate and repository work is saved for subsequent runs.
6. Reconciles one lab's historical publications from `archive_since` (currently `2024-01-01`), then advances to the next lab. A normal 19-lab cycle takes 19 daily runs; failed sources remain visible and are revisited.
7. Merges discoveries into the existing archive, preserving older records, canonical arXiv IDs, alternate titles/URLs, and source-specific evidence.

Historical reconciliation is automatic: no one needs to maintain a list of missing paper titles. `--since` changes the acquisition window and **does not replace or truncate the archive**. `--max-papers` defaults to zero (no ceiling); a requested ceiling causes an error instead of deleting older papers.

The date range is independent of the AI topic scope. Change `company_tracking.archive_since` to cover an earlier period. The current repository's 2024 start date is preserved.

## Health and progress

- `public/data/collection_health.json`: per-source status, errors, limits, timestamps, and historical reconciliation progress.
- `public/data/collection_pending.json`: records awaiting enough metadata for an AI-topic decision; automatically retried.
- `public/data/collection_state.json`: the next lab in the historical reconciliation cycle and per-lab attempts.
- `output/repository_scans` and `output/affiliation_queue`: repository/candidate queues restored and saved by GitHub Actions cache, including when collection has partial failures. These caches are reconstructible; eviction can cause repeated discovery work.
- `output/publication_metadata`: an expiring detail-page cache to avoid repeatedly fetching unchanged abstracts.

The website reports partial collection. GitHub Actions preserves and publishes successfully collected records but ends in failure when a source has an actual error, so a green deployment is not used to hide a failed collection. Pending bounded background scans are reported as partial rather than claiming complete coverage.

OpenAlex can optionally use the `OPENALEX_API_KEY` repository secret. The code reads this only from the environment and does not save it in the dataset. GitHub repository scans use the workflow's existing `GITHUB_TOKEN`.

## Evidence and remaining limits

Accepted lab attribution comes from an official publication source, official repository report, verified author affiliation, or matching organization metadata. OpenAlex institution matching checks the returned institution name, including international subsidiaries; the lab's headquarters country is not used to exclude overseas authors.

No public index can prove that every lab-authored paper has been found. Papers with absent affiliations, papers never linked from discoverable sources, publisher access failures, API result restrictions, and queued scans can still be missing. Topic decisions use configured AI-only catalogue scope, explicit AI terminology, AI venues, subject metadata, and abstracts rather than an LLM classifier. `topic_evidence` preserves that decision before display abstracts are shortened. Uncertain sparse records remain in the retry queue. The service records these limitations and does not equate an archive total or a successful HTTP response with 100% recall.

Research posts, technical reports, and model/system cards retain their item type. A research blog post is not presented as proof of peer review. Model mentions in an abstract or PDF references are not affiliation evidence.

## Development checks

Use Python 3.10 or newer in a virtual environment (the workflow uses Python 3.13).

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/update_company_papers.py --days 30 --comprehensive --reconcile --strict
```

A separate output can be used to verify changes without replacing the website dataset:

```bash
python scripts/update_company_papers.py --since 2024-01-01 --sources official --output output/preview/company_papers.json
```

`--sources` and `--lab` are diagnostic controls. The scheduled workflow does not restrict the source families or tracked labs.
