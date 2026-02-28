# GitHub Copilot Agent Instructions: Infinity Lab

## 1. Zero-Placeholder Policy (CRITICAL)
- **NO PLACEHOLDERS**: Never use `...`, `TODO`, `FIXME`, `pass`, or `// add implementation here` in your generated code.
- **COMPLETE LOGIC**: You must write 100% complete, production-ready code. Do not leave boilerplate or logic for the user to fill out.
- **PARSING**: If processing JSON or API responses, write out the full parsing logic. 

## 2. Python Architecture & Code Style
- **Language**: Python 3.10+
- **Typing**: Use strict type hints for all function arguments, return types, and class variables.
- **Error Handling**: Use explicit `try/except` blocks. Do not swallow exceptions silently. Fail loudly with clear, contextual error messages.
- **Secrets**: Load all secrets (e.g., `SLACK_BOT_TOKEN`, `LINEAR_API_KEY`) via `os.environ.get()`. Never hardcode secrets.

## 3. Testing Requirements
- **Framework**: Use `pytest`.
- **Completeness**: Always generate corresponding test files when creating or updating scripts.
- **Coverage**: Include tests for the "happy path" and explicit failure modes (invalid inputs, API timeouts). 
- **Corpus**: Do not mock complex data schemas blindly. Provide realistic, structured test fixtures/corpus data.

## 4. Context & Environment
- **Project**: You are building the ACB (Autonomous Coding Bot) infrastructure within GitHub Actions.
- **File System**: Assume execution from the repository root. Use standard Python tools (`os`, `sys`, `pathlib`) for relative path resolution.
- **Formatting**: Output valid markdown. Keep markdown responses brief and focus purely on providing the technical solution.