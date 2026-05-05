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
venv/bin/python scripts/update_company_papers.py --since 2024-01-01 --comprehensive --max-papers 50000
```

## Validation

Before opening a PR, run:

```bash
venv/bin/python -m compileall scripts
npm run typecheck
npm run build
```

For data changes, also check:

```bash
jq '.collection, .totals' public/data/company_papers.json
jq '[.papers[] | select((.companies|length)==0 or .title=="" or .url=="")] | length' public/data/company_papers.json
```
