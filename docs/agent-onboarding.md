# Agent 接手指南

本指南面向首次进入仓库、或负责每日产出检查的 agent。先读根目录
[AGENTS.md](../AGENTS.md)，再按本指南执行；遇到具体问题再查
[架构](architecture.md)、[开发规范](development.md)和[运维手册](operations.md)。

## 当前能力与任务边界

项目从 Reddit/RSS 等来源采集 F1 新闻，用 DeepSeek 生成中文摘要、Pillow
生成卡片，可投递到 Telegram。仓库名称包含小红书，但当前没有小红书自动发布入口。

Docker 的 `scheduler.py` 负责每日生成和投递；当前没有独立的“每日 agent
巡检、自动改代码、自动部署”服务。下面是可交给 agent 执行的工作流程，
不是已经启用的自动化。Claude 可以作为开发工具，运行时模型仍由
`config.yaml` 中的 DeepSeek 配置决定，Claude 订阅不替代项目 API 凭据。

提交、推送、生产部署、手动发布是不同的操作，按本次任务授权执行。
只要求检查或修复时，先准备修改和验证结果；不要顺带部署或补发。

## 首次接手

1. 在仓库根目录确认 `git status --short`、`git branch --show-current` 和
   `git log -5 --oneline`，保留已有改动。不要将本地版本当作生产版本。
2. 确认 `.venv/bin/python --version`。新环境按 README 使用 Python 3.11 和
   `requirements.lock`；已有环境不要为了接手直接覆盖重建。
3. 阅读 `config.yaml` 和 `.env.example`，了解来源、质量关卡和调度。
   `.env` 是机器本地凭据，不输出其内容，不从生产复制全部环境。
4. 运行离线回归和编译检查，记录实际 Python 版本和失败项：

   ```bash
   .venv/bin/python -m unittest discover -s tests
   .venv/bin/python -m compileall -q analyzer generator collectors publisher run.py scheduler.py
   git diff --check
   ```

5. 涉及生产时按运维手册核对远端 commit、容器和调度日志。本地 `output/`
   不在 Git 中；新 clone 不会带来历史产出、缓存或投递状态。

首次创建 `.env` 才复制 `.env.example`；已有文件不要覆盖。下文命令中的
`<run>` 必须替换为选定的实际运行目录名，不能原样执行。

### 命令的实际影响

| 命令 | 网络 / 模型 | 产物与状态 | Telegram |
| --- | --- | --- | --- |
| `python -m unittest discover -s tests` | 模拟客户端，无真实模型调用 | 临时测试目录 | 不发送 |
| `python run.py --dry-run --hours 24` | 真实采集，可刷新赛历/积分榜；无模型调用 | 写运行产物，不更新共享历史/缓存 | 不发送 |
| `python run.py --mock --hours 24` | 真实采集，可抓正文；模拟话题和文案 | 写运行产物，不更新共享历史/缓存 | 不发送 |
| `python run.py --hours 24` | 真实采集与付费模型调用 | 写缓存、候选与短期编辑历史，可清理旧产物 | 不发送 |
| `python run.py --telegram-only output/<run> --telegram-dry-run` | 本地校验，无模型调用 | 校验既有稿件、图片与质量标记 | 不发送 |
| `python run.py --hours 24 --push-telegram` | 真实采集与模型调用 | 更新运行及发布状态，先尝试补偿队列 | 真实发送 |
| `python run.py --telegram-only output/<run>` | 无模型调用 | 按投递检查点发送并完成发布历史 | 真实发送 |

表中 `python` 使用仓库 `.venv/bin/python`。`--resume` 会继续尚未完成的
运行；找不到可恢复运行或超过期限时，会开始新运行，并非只读检查。
`--telegram-dry-run` 单独用于完整生成时仍会调用模型；检查已有产物时应
与 `--telegram-only` 配对。完整生成不发送也会改变本地编辑状态，真实
模型验证应使用独立测试 checkout 的 `output/`，不要挂载生产 output。

## Daily Output Review

### 先确认检查的是哪次运行

以 Asia/Hong_Kong 当天为准，先看生产调度日志确认任务是否启动、结束或超时，
再检查对应运行目录。不要只取 `output/` 最新目录：它可能是 mock、手动运行，
也可能尚未完成。目录名不是判断新闻日期的依据；使用
`run_state.json.generated_at` / `meta.json.run_context.generated_at` 转换为 HKT。
尚未到调度时间、仍在执行和执行失败需要分别记录。

获得授权的产物副本可放在本地已忽略的 `output/inspection/` 下。检查生产时
只读取相关运行、调度日志和待投递队列；不要把生产状态覆盖到本地运行根目录。
运维手册的服务器地址和路径是连接入口，生产版本和状态每次现场核对。

### 检查顺序与通过标准

下表路径相对选定的运行目录；图片、测量和投递记录位于其 `drafts/digest/`
内，`preview.html` 位于运行目录根部。待投递队列是共享 output 状态，
路径由 `telegram.pending_deliveries_path` 配置决定。

| 检查 | 证据 | 判断 |
| --- | --- | --- |
| 执行完成 | 调度日志、`run_state.json` | 当日目标运行已结束；退出码 0 还不代表发布成功 |
| 来源健康 | `collection_status.json`、`shortlisted_posts.json` | 区分请求失败、成功但无新闻、缓存命中；单一来源失败不等于整次失败 |
| 新闻依据 | `topics.json`、`drafts/digest/draft.json`、`meta.json` | 对照正文证据核查数字、时间、归属与重复主题；看 `fact_check_notes` 和跳过原因 |
| 质量关卡 | `meta.json` | `guard_blocked` 为 false，检查 `guard_blocking_codes`；缺失或损坏的文件不能视为通过 |
| 图片完整 | `render_measurements.json`、`images/`、`preview.html` | 无截断，封面和每条新闻卡齐全；实际查看图片的中文、标题和日期 |
| 投递完成 | `run_state.json`、`telegram_delivery.json`、待投递队列、日志 | 应发布的运行 outcome 为 delivered，确认批次记录完整；部分发送需继续排查 |

`generated` 表示生成但未投递，`rejected` 表示质量拒绝，`delivery_pending`
表示投递失败待恢复；未完成运行可能还没有 outcome。mock 也会生成这些文件，
因此必须结合启动命令和日志判断。模型审阅通过不等于事实已核实；正文缺失时
应记录证据不足，不把标题或另一模型的判断当作原文。

用以下命令校验选定的既有产物，不重新生成、不发送：

```bash
.venv/bin/python run.py --telegram-only output/<run> --telegram-dry-run
```

该命令检查稿件、图片和发布条件，但不能证明新闻事实准确、当天定时任务
成功或远端 Telegram 已收到消息，也不检查投递检查点的指纹和已发送批次。
当前实现即使 dry-run 也要求 token 和 chat ID 存在；纯本地验证可用命令级
占位值，不必复制生产凭据：

```bash
TELEGRAM_BOT_TOKEN=123:dry-run TELEGRAM_CHAT_ID=0 .venv/bin/python run.py --telegram-only output/<run> --telegram-dry-run
```

占位值命令必须保留 `--telegram-dry-run`。投递完成情况仍需另行读取真实
检查点、运行状态和日志。

### 发现问题后的修复路径

| 问题 | 优先调查 | 修复与验证 |
| --- | --- | --- |
| 没有启动 / 超时 | `scheduler.py`、调度环境、容器日志 | 区分主机休眠、调度与子进程故障；相关回归在 `tests/test_scheduler.py` |
| 来源失败 / 正文缺失 | `collectors/`、`analyzer/article_fetcher.py` | 核对真实来源和抽取结果；采集测试见 `test_pipeline_hardening.py`，抽取见 `test_article_extraction.py` |
| 重复、选题不足 | shortlist、evidence/history gate、meta 跳过原因 | 检查冷却和证据，不清空历史凑条数；见 `test_story_governance.py`、`test_topic_history.py` |
| 错误事实 / 表述误导 | 原文、`generator/` 写稿/核查/终审/质量关卡 | 用故障样例和正常样例验证；见 `test_quality_pipeline.py`、`test_reader_facing_style.py` |
| 图片截断 / 标点排版 | `generator/images.py` 和测量报告 | 运行 `test_images.py`，查看真实渲染结果 |
| 投递失败 / 部分发送 | `publisher/`、队列和检查点 | 保留已确认批次，按运维手册恢复；见 `test_telegram_retry.py`、`test_delivery_lifecycle.py` |

先保留问题运行与错误证据，再在本地做最小修改、跑相关回归及完整检查，
同步对应文档。不要编辑质量标记来放行，不删除投递检查点强制重发；
部分发布后需要修改正文或图片时，应产生新的运行目录。
离线测试不能证明真实模型写作或生产投递成功，报告中分别说明。

检查结果按“运行时间及版本 → 问题与证据 → 修改 → 验证 → 待执行操作”
交付。常规未发现问题时简短说明即可；检查材料留在忽略的 output，
长期规则维护在 docs，不新建按日期堆积的工作日志。

## 文件定位提示

- 调度、恢复与阶段保存：`scheduler.py`、`run.py`、`analyzer/run_state.py`。
- 共享状态与锁：`analyzer/file_state.py`；状态含义见架构文档和运维手册。
- 离线端到端测试夹具：`tests/harness.py`；不是线上运行入口。
- `scripts/create_driver_nicknames_feature.py` 是指定文章的专题卡片脚本，
  不属于每日调度或通用初始化流程。
- `docs/REVIEW_*.md` 是历史评审材料，不能当作当前待修列表或开发规范。
