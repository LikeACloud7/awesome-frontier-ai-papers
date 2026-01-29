# 📚 Daily AI Papers

> A daily AI/ML paper discovery system powered by Claude Code

Stay on top of the latest research without drowning in papers. Daily AI Papers automatically collects papers from arXiv and HuggingFace, categorizes them by your interests, and generates detailed reviews using LLMs.

## ✨ Features

- **🔥 Hot Papers** - Trending papers from HuggingFace Daily Papers
- **🏢 Big-tech Tracking** - Papers from Google, Meta, OpenAI, Anthropic, etc.
- **🎯 Custom Categories** - Filter by your research interests
- **🤖 AI-Powered Reviews** - Deep paper analysis via Gemini CLI or Claude (your choice!)
- **🚫 No Duplicates** - Automatically tracks seen papers
- **🌐 Beautiful Web UI** - Tab-based interface for easy browsing

## 📋 Requirements

- Python 3.10+
- [Claude Code](https://claude.ai/claude-code) (CLI)
- [Gemini CLI](https://github.com/google-gemini/gemini-cli) (optional, for parallel reviews)

## 🚀 Installation

### 1. Clone the repository

```bash
git clone https://github.com/LikeACloud7/daily_ai_papers.git
cd daily_ai_papers
```

### 2. Run the installer

```bash
./install.sh
```

This will:
- Create Python virtual environment
- Install dependencies
- Configure Claude Code commands with correct paths

### 3. Restart Claude Code

```bash
# Exit current session if running
exit

# Start fresh
claude
```

## 📖 Usage

### Start Claude Code

```bash
claude
```

### Collect Papers

```
/papers
```

This will:
1. Fetch papers from arXiv and HuggingFace
2. Filter by your interests
3. Open a web UI in your browser

### Select & Review Papers

1. **Browse** - Click tabs to explore categories
2. **Select** - Check papers you're interested in
3. **Copy** - Click "선택한 번호 복사" (Copy selected numbers)
4. **Review** - Paste in Claude Code (e.g., `1, 3, 7 요약해줘`)

Gemini CLI will analyze each paper in parallel and generate detailed reviews.

### Configure Settings

```
/papers-config
```

Customize:
- **Keywords** - Add/remove tracking keywords
- **Organizations** - Add/remove companies to track
- **Papers per category** - How many papers to show
- **Search period** - How many days back to search

## 📁 Project Structure

```
daily_ai_papers/
├── .claude/
│   └── commands/
│       ├── papers.md          # Main command
│       └── papers-config.md   # Config command
├── scripts/
│   ├── fetch_papers.py        # Paper collection
│   └── review_papers.py       # AI review generation
├── prompts/
│   └── review_paper.md        # Review prompt template
├── config/
│   └── interests.json         # Your preferences
├── output/
│   ├── papers.html            # Paper list UI
│   ├── papers.json            # Paper data
│   ├── reviews.html           # Review results UI
│   └── seen.json              # Seen paper tracking
├── requirements.txt
└── README.md
```

## ⚙️ Configuration

Edit `config/interests.json`:

```json
{
  "review_provider": "gemini",
  "keywords": [
    "LLM", "language model",
    "efficient", "quantization",
    "agent", "multi-agent"
  ],
  "organizations": [
    "Google", "DeepMind", "Meta", "OpenAI", "Anthropic"
  ],
  "categories": ["cs.CL", "cs.AI", "cs.LG"],
  "days_back": 2,
  "max_per_category": 10
}
```

### Review Provider

Choose who generates paper reviews:

| Provider | Description |
|----------|-------------|
| `gemini` | Uses Gemini CLI for parallel processing (requires Gemini CLI installed) |
| `claude` | Claude reviews papers directly in conversation (no extra tools needed) |

### Category Definitions

Categories are defined in `scripts/fetch_papers.py`. Default categories:

| Category | Keywords |
|----------|----------|
| 🔥 Hot Papers | HuggingFace trending |
| 🏢 Big-tech | Papers mentioning tracked orgs |
| 🎯 Domain | persuasion, political, journalism |
| 🧠 Personalization | personalized, memory, preference |
| ⚡ Efficient LLM | efficient, quantization, compression |

## 🎨 Customizing the Review Prompt

Edit `prompts/review_paper.md` to change how papers are analyzed. The default prompt generates:

- TL;DR summary
- Research motivation
- Core methodology
- Key results
- Limitations
- Personal assessment
- Takeaways

## 🤝 Contributing

Contributions are welcome! Feel free to:

- Add new paper sources (Semantic Scholar, etc.)
- Improve the UI
- Add new categories
- Enhance the review prompt

## 📄 License

MIT License - feel free to use and modify!

---

**Happy paper reading!** 📖✨
