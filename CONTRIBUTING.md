# Contributing

Contributions are welcome, especially new official lab sources, parser fixes, and coverage improvements.

## Source Quality

Prefer high-confidence sources:

- official company or lab publication pages
- official model card, system card, and technical-report pages
- official HuggingFace or GitHub organizations
- OpenAlex institution metadata

Avoid adding broad keyword searches as default behavior when they can match papers that only mention a model or product. If a source is noisy, gate it behind an explicit option or document the caveat.

## Adding A New Lab

Update `config/frontier_labs.json`:

1. add the organization to the correct US or China group
2. add aliases conservatively
3. add OpenAlex IDs or search terms
4. add official publication pages, feeds, HuggingFace orgs, GitHub orgs, or manual official reports
5. run a backfill and inspect sample results

Useful command:

```bash
.venv/bin/python scripts/update_company_papers.py --since 2024-01-01 --comprehensive --output output/preview/company_papers.json
```

## Validation

Before opening a PR, run:

```bash
.venv/bin/python -m unittest discover -s tests -v
npm run typecheck
npm run build
```

## README and Generated Lists

The README and `docs/labs/*.md` are generated from the dataset. Change the layout in `scripts/generate_markdown_index.py`, then regenerate it. Keep the latest-paper tables and links to every lab's full archive.

Keep personal reports, audit exports, sample outputs, and verification screenshots under the ignored `output/` directory. Commit only project documentation, assets used by the README or site, and data required by the collector or site.

## Security

Treat titles, URLs, HTML, PDFs, and API responses as untrusted. Keep public-address checks, response limits, Markdown escaping, and artifact validation intact when adding a source. Use pinned dependencies and never commit credentials. See [SECURITY.md](SECURITY.md) for reporting guidance and the project’s security boundaries.

For data changes, also check:

```bash
jq '.collection, .totals' public/data/company_papers.json
jq '[.papers[] | select((.companies|length)==0 or .title=="" or .url=="")] | length' public/data/company_papers.json
```
