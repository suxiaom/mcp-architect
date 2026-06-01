# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MCP Architect is a Model Context Protocol (MCP) tool that provides Claude with long-term business memory and architectural assistance for projects. It maintains a local JSON-based business logic index (`business_index.json`) that enables Claude to understand project business logic, module dependencies, and technical decision history without consuming large context windows.

## Architecture

The project is structured as a Python package with two main components:

1. **CLI Tool** (`main.py`): Click-based command-line interface for the `mcp-arch` command that integrates the MCP server into projects
2. **MCP Server** (`server.py`): FastMCP-based server that provides 6 core tools for business intelligence
3. **Templates** (`templates/CLAUDE.md`): Architecture prompt template injected into user projects

### Core MCP Tools (server.py)

| Tool | Purpose |
|------|---------|
| `search_business_index(keyword)` | Fuzzy search across business logic with weighted scoring (name matches 2x weight) |
| `check_stale_indexes()` | Detects outdated index entries by comparing file mtime vs index timestamp |
| `generate_architecture_diagram()` | Creates Mermaid diagrams showing module/workflow dependencies |
| `update_business_index(updates)` | Updates JSON index with auto-backup to `.bak` file |
| `get_business_index()` | Retrieves complete business index |
| `validate_index()` | Removes entries for deleted/missing files |

### Business Index Schema

The `business_index.json` contains three top-level arrays:
- **modules**: Business logic units with `id`, `name`, `path`, `summary`, `key_functions`, `last_updated`
- **workflows**: Business processes with `dependencies` array linking to module IDs
- **decisions**: Technical decision history with `content`, `file_ref`, `date`

## Development Commands

### Installation
```bash
# Using pipx (recommended, isolated environment)
pipx install git+https://github.com/your-username/mcp-architect.git

# Using pip
pip install git+https://github.com/your-username/mcp-architect.git
```

### Running the MCP Server Directly
```bash
# Using uv (recommended, auto-manages dependencies)
uv run business_index_mcp.py

# Using python (requires fastmcp installed)
python business_index_mcp.py
```

### CLI Usage
```bash
# Integrate architect capabilities into current project
mcp-arch setup
```

## Key Implementation Details

### Search Algorithm (`calculate_match_score`)
- Exact substring match returns 1.0
- Fuzzy match via `difflib.SequenceMatcher.ratio()` returns similarity * 0.8 (threshold > 0.6)
- Field weights: name (x2), summary, id, content

### Cross-Platform Configuration
`get_claude_config_path()` handles config paths for:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

### Setup Workflow
The `mcp-arch setup` command:
1. Verifies `CLAUDE.md` exists (requires prior `/init` from Claude CLI)
2. Copies `server.py` to project as `business_index_mcp.py`
3. Appends architecture prompts to `CLAUDE.md`
4. Registers MCP server in Claude config (prefers `uv run`, falls back to `python`)

### Anti-Hallucination Strategy
`check_stale_indexes()` compares `os.path.getmtime(path)` against `last_updated` timestamp. If file is newer than index by > 24 hours, module is flagged for re-indexing.

## Dependencies

- **click**: CLI framework
- **fastmcp**: MCP protocol implementation
- **uv**: Recommended for running the generated MCP server (auto-detected during setup)
