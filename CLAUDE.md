# CLAUDE.md

## Commit Convention

- Format: `type: concise message`
- Language: English
- Keep it short and clear

### Types
- `feat:` - New feature
- `fix:` - Bug fix
- `docs:` - Documentation
- `refactor:` - Code refactoring
- `chore:` - Maintenance tasks

### Examples
```
feat: add HuggingFace daily papers integration
fix: handle empty API response
docs: update installation guide
refactor: extract HTML generation to separate function
chore: update dependencies
```

## Project Structure

- `scripts/` - Python scripts for paper collection and review
- `config/` - User configuration files
- `prompts/` - LLM prompt templates
- `.claude/commands/` - Claude Code slash commands

## Development Notes

- Use `{PROJECT_PATH}` placeholder in command files
- `install.sh` replaces placeholders with actual paths
- Scripts use relative paths via `__file__`
