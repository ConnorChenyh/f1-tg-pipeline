# Agent Guide — F1 个人项目

This repository builds a scheduled F1 news digest pipeline. Keep changes narrow,
evidence-grounded, and compatible with the existing Docker/VPS deployment.

## 项目边界与规则作用域

- 本仓库是独立的个人 F1 内容项目，不属于公司 TRON 工作区。
- 本文件适用于本仓库及其子目录。针对本项目的工作规范以本文件和所链接的项目文档为准；不套用其他仓库的 Java、业务系统或公司汇报流程。
- 本项目工作不读取、不创建、不更新 `/Users/connor/Desktop/tron/personal_work_log`，不纳入公司日报、周报或工时汇总；除非用户明确另行要求。
- 不因本项目任务修改公司仓库、复用公司数据库、凭据或部署配置，也不把公司数据复制进本仓库。
- 项目长期知识维护在本仓库 `docs/`；不自动创建个人工作日志或按日期堆积进度记录。运行诊断数据保留在已忽略的 `output/`，它们不是工作日志。
- 这些规则用于约束协作行为，不构成操作系统级权限隔离。跨项目操作必须有明确的任务依据。

## 文档入口

- [文档索引](docs/README.md)：按任务找到对应规范。
- [开发规范](docs/development.md)：环境、改动边界、测试和交付要求。
- [架构文档](docs/architecture.md)：模块职责、数据流和持久化状态。
- [运维手册](docs/operations.md)：部署、定时任务和故障排查。

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

Production runs from `/opt/f1-tg-pipeline` through Docker Compose, independently
of company services. Follow [docs/operations.md](docs/operations.md#vps-deployment)
for deployment checks and commands. Deployment and manual Telegram publication
are separate actions; do not trigger a digest just to verify deployment.
