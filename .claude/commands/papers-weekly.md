# Weekly AI Paper Collection

Collect and review AI/ML papers from the past week.

## How to Use

1. Run the paper collection script with 7-day period:

```bash
{PROJECT_PATH}/venv/bin/python {PROJECT_PATH}/scripts/fetch_papers.py --days 7
```

2. After the browser opens with the paper list:

```
Paper list opened in browser!

Usage:
- Select papers with checkboxes
- "선택한 번호 복사" → Paste here for AI review
- "선택한 링크 복사" → Copy links for NotebookLM
```

## When User Provides Paper Numbers

When user says something like "1, 3, 7 요약해줘" (review 1, 3, 7):

**Run the review script:**

```bash
{PROJECT_PATH}/venv/bin/python {PROJECT_PATH}/scripts/review_papers.py "1,3,7"
```

(Replace numbers with user's input)

### Review Provider Modes

The script behavior depends on `review_provider` setting in `config/interests.json`:

#### Gemini Mode (`"review_provider": "gemini"`)
- Script processes each paper with Gemini CLI in parallel
- Generates HTML review page automatically
- Opens results in browser

#### Claude Mode (`"review_provider": "claude"`)
- Script outputs paper info as JSON (stdout)
- **You (Claude) must review each paper directly**
- For each paper in the output:
  1. Use WebFetch to read the arXiv page URL
  2. Apply the prompt template from the JSON output
  3. Generate detailed review in Korean
- After reviewing all papers, summarize the results

## After Review Completes

```
Review complete! Check your browser.

Each paper includes:
- TL;DR summary
- Core contribution
- Methodology
- Key results
- Limitations
- Takeaways

Use "전체 링크 복사" to copy links for deeper reading in NotebookLM.
```

## Notes

- Weekly mode collects papers from the past 7 days
- More papers may be found compared to daily mode
- Use /papers for daily (2-day) collection
- Review time depends on number of papers (~30sec-1min per paper)

---
**IMPORTANT**: Replace `{PROJECT_PATH}` with your actual installation path.
