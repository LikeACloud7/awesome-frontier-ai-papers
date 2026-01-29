# Daily AI Papers Configuration

View and modify paper collection settings.

## Config File Location
```
{PROJECT_PATH}/config/interests.json
```

## View Current Settings

Read and display the config file:

```bash
cat {PROJECT_PATH}/config/interests.json
```

**Display format:**
```
## 📋 Current Settings

### 🤖 Review Provider
- gemini (Gemini CLI로 병렬 리뷰)
- claude (Claude가 직접 리뷰)

### 🎯 Tracking Keywords
- political, politics, election, democracy
- personalized, personalization, preference, memory, long-term
- efficient, compression, quantization, distillation
- ...

### 🏢 Tracking Organizations
- Google, DeepMind, Meta, Microsoft, OpenAI, Anthropic, ...

### 📁 arXiv Categories
- cs.CL, cs.AI, cs.LG

### ⚙️ Other Settings
- Search period: 2 days
- Papers per category: 10

---
To modify, just ask:
- "Change review provider to claude" or "Change review provider to gemini"
- "Add 'reasoning' to keywords"
- "Add 'xAI' to organizations"
- "Change papers per category to 15"
- "Change search period to 3 days"
```

## Modifying Settings

When user requests changes, edit the config file directly.

### Review Provider
- "Change review provider to claude" → Set review_provider to "claude"
- "Change review provider to gemini" → Set review_provider to "gemini"

### Add/Remove Keywords
- "Add 'XXX' to keywords" → Add to keywords array
- "Remove 'XXX' from keywords" → Remove from keywords array

### Add/Remove Organizations
- "Add 'XXX' to organizations" → Add to organizations array
- "Remove 'XXX' from organizations" → Remove from organizations array

### Papers Per Category
- "Change papers per category to N" → Modify max_per_category

### Search Period
- "Change search period to N days" → Modify days_back

## Modifying Categories

Categories are defined in Python code:
```
{PROJECT_PATH}/scripts/fetch_papers.py
```

Edit the CATEGORIES dictionary when user requests category changes.

## Notes

- Changes take effect on next `/papers` run
- Watch for JSON syntax errors (quotes, commas)
- Summarize changes after modification

---
**IMPORTANT**: Replace `{PROJECT_PATH}` with your actual installation path.
