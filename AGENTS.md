# Agent Guide

This repository builds a scheduled F1 news digest pipeline. Keep changes narrow,
evidence-grounded, and compatible with the existing Docker/VPS deployment.

## Repository Map

- `README.md` - user-facing setup, local run, Docker deployment, and common commands.
- `docs/architecture.md` - pipeline architecture, module responsibilities, data flow, and quality gates.
- `docs/operations.md` - VPS deployment, scheduling, manual runs, logs, and troubleshooting.
- `config.yaml` - source settings, digest limits, source tiers, story governance, season context, and image options.
- `run.py` - one-shot pipeline orchestrator.
- `scheduler.py` - daily scheduler used by Docker.
- `collectors/` - Reddit, RSS, and optional Twitter source collection.
- `analyzer/` - normalization, scoring, shortlist, evidence enrichment, history, SQLite story DB, and season context.
- `generator/` - DeepSeek prompts, writing, fact-check, final review, quality guard, and image generation.
- `publisher/` - Telegram delivery.
- `tests/` - focused unit tests for scheduler, Telegram, image layout, topic history, story governance, and quality flow.

## Working Rules

- Do not write personal work logs for this project.
- Do not print or commit secrets from `.env`.
- Preserve the existing input/output contract unless the user explicitly asks for a redesign.
- Prefer deterministic gates before LLM calls: source tiering, article evidence, topic history, and story DB state.
- Treat article body text as stronger evidence than RSS snippets, Reddit titles, or model summaries.
- For uncertain F1 technical names, keep the English term instead of inventing a Chinese translation.
- If repeated manual runs are requested, remember that topic history and topic-level cooldowns may suppress recently published stories; minimum-item backfill exists only for very recent URL/text duplicates and must not reintroduce low-evidence or cooled-down themes.

## Common Commands

```bash
# Local validation
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall analyzer generator collectors publisher run.py scheduler.py

# Local collection only
.venv/bin/python run.py --dry-run --hours 24

# Local full run
.venv/bin/python run.py --hours 24

# Local mock full path
.venv/bin/python run.py --mock --hours 24
```

## Server Notes

Production is expected to run from `/opt/f1-tg-pipeline` on the VPS through
Docker Compose. The normal deployment sequence is:

```bash
ssh root@206.237.27.231 'cd /opt/f1-tg-pipeline && git fetch origin main && git reset --hard origin/main && docker compose up -d --build'
```

Use `docs/operations.md` for manual trigger and log commands.
