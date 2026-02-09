# Infinity Lab - Build Automation

**Purpose:** GitHub Actions workflow automation for algorithmic trading strategy development.

## Architecture

This repository contains only the GitHub Actions workflow definitions. All proprietary code, strategies, and data remain in a private repository.

**Public repo role:** Workflow trigger (enables unlimited GitHub Actions minutes on free tier)

**Private repo role:** All code, scripts, strategies, and outputs

## Workflow

1. Create issue in this repo with `vm-command` label
2. Workflow triggers automatically
3. Clones private repository
4. Executes build using private repo code
5. Commits results back to private repo

## Privacy

- ✅ All API keys stored as GitHub Secrets (encrypted)
- ✅ All proprietary code in private repository
- ✅ Strategy specifications never exposed
- ✅ Build outputs remain private
- ✅ Only workflow definitions are public

## Technology Stack

- **AI:** Claude (Anthropic), GPT-4o (OpenAI)
- **Backtesting:** QuantConnect
- **Code Generation:** Aider + MCP servers
- **Orchestration:** GitHub Actions + Linear
- **Interface:** Perplexity AI

---

**License:** MIT (workflow definitions only)

**Note:** This is a minimal public repository. All implementation details are private.