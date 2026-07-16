# Operations

This document covers local checks, Docker deployment, server operation, manual
triggers, and common troubleshooting.

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Required `.env` values:

```bash
DEEPSEEK_API_KEY=sk-...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Twitter is optional and requires both:

```bash
TWITTER_AUTH_TOKEN=...
TWITTER_CT0=...
```

## Local Commands

```bash
# Run all tests
.venv/bin/python -m unittest discover -s tests

# Compile check
.venv/bin/python -m compileall analyzer generator collectors publisher run.py scheduler.py

# Collect and score only
.venv/bin/python run.py --dry-run --hours 24

# Full local run
.venv/bin/python run.py --hours 24

# Mock full path without DeepSeek
.venv/bin/python run.py --mock --hours 24

# Full run and push to Telegram
.venv/bin/python run.py --hours 24 --push-telegram

# Push an existing run to Telegram
.venv/bin/python run.py --telegram-only output/<timestamp>

# Validate Telegram payload without sending
.venv/bin/python run.py --telegram-only output/<timestamp> --telegram-dry-run
```

## Docker Deployment

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail=60 f1-tg-pipeline
```

The compose service runs `scheduler.py`. Generated artifacts and persistent
memory are kept in `./output` via a bind mount.

Default schedule:

```bash
SCHEDULE_TIMEZONE=Asia/Hong_Kong
SCHEDULE_DAILY_AT=12:00
SCHEDULE_HOURS=24
SCHEDULE_PUSH_TELEGRAM=true
SCHEDULE_RUN_ON_START=false
```

Set `SCHEDULE_RUN_ON_START=true` only when you intentionally want one immediate
run after container startup.

## VPS Deployment

Expected server path:

```bash
/opt/f1-tg-pipeline
```

Deploy the latest `main`:

```bash
ssh root@206.237.27.231 'cd /opt/f1-tg-pipeline && git fetch origin main && git reset --hard origin/main && docker compose up -d --build'
```

Check status:

```bash
ssh root@206.237.27.231 'cd /opt/f1-tg-pipeline && git rev-parse --short HEAD && docker compose ps && docker compose logs --tail=40 f1-tg-pipeline'
```

## Manual Server Trigger

Run a full 24-hour digest and push to Telegram in the background:

```bash
ssh root@206.237.27.231 'cd /opt/f1-tg-pipeline && nohup docker compose run --rm f1-tg-pipeline python run.py --hours 24 --push-telegram > output/manual-run-$(date +%Y%m%d-%H%M%S).log 2>&1 & echo triggered'
```

Confirm a manual run is active:

```bash
ssh root@206.237.27.231 'pgrep -af "python run.py --hours 24 --push-telegram|docker compose run --rm f1-tg-pipeline" || true'
```

Read the latest manual log:

```bash
ssh root@206.237.27.231 'cd /opt/f1-tg-pipeline && ls -lt output/manual-run-*.log | head -1 && tail -120 $(ls -t output/manual-run-*.log | head -1)'
```

## Output Inspection

Find latest run:

```bash
ls -lt output | head
```

Inspect important files:

```bash
python - <<'PY'
import json
from pathlib import Path
base = Path("output/<timestamp>")
for name in ["shortlisted_posts.json", "topics.json", "drafts/digest/meta.json", "drafts/digest/draft.json"]:
    p = base / name
    print("---", name, p.exists())
    if p.exists():
        data = json.loads(p.read_text())
        print(json.dumps(data, ensure_ascii=False, indent=2)[:4000])
PY
```

In Docker:

```bash
docker compose exec -T f1-tg-pipeline python - <<'PY'
import json
from pathlib import Path
base = Path("/app/output/<timestamp>")
meta = json.loads((base / "drafts/digest/meta.json").read_text())
print(json.dumps(meta.get("skipped_recent_topics"), ensure_ascii=False, indent=2))
PY
```

## Common Issues

### Only one or two topics are sent

Check `drafts/digest/meta.json`:

- `social_only_without_article_content` means the evidence gate filtered thin social-only topics.
- `topic_signature:<key>` means `topic_cooldowns` filtered a repeated broad theme
  even if the URL changed, such as `verstappen_future`, `goodwood_festival`, or
  `belgian_gp_preview`.
- `shared_url:` or `text_similarity:` means JSON topic history filtered a recent duplicate.
- `story_db:` means SQLite story memory filtered a recent duplicate.
- `backfilled_recent_duplicate_for_min_items` means a very recent URL/text
  duplicate was reused to satisfy `digest.min_items`; topic-signature duplicates
  are not backfilled.

If the output is still too small, inspect `shortlisted_posts.json` and consider:

- increasing RSS sources
- lowering `evidence_gate.min_article_backed_topics`
- increasing `shortlist.max_social_only_posts`
- shortening `topic_history.dedupe_days`
- adjusting or removing a specific `topic_cooldowns.rules` entry if a theme
  should be allowed again sooner

The pipeline also has article-backed fallback fill: when filtered topics are
below `digest.min_items`, unused article URLs from `shortlisted_posts.json` are
converted into fallback topics and then checked by the same evidence/history
filters. If fallback still does not fill the digest, the remaining candidates are
probably social-only, cooled down by `topic_cooldowns`, or already present in
topic history.

Do not disable evidence gating unless the goal is explicitly to include rumor or
social-only content.

### Chinese text renders as boxes or garbled characters

Docker must install `fonts-noto-cjk`, and `generator/images.py` must use Noto CJK
font paths. Rebuild the image after Dockerfile changes:

```bash
docker compose up -d --build
```

### Reddit is blocked

The server may fail `rdt-cli` access. The collector falls back to Reddit RSS and
skips Reddit search when CLI subreddit access is unavailable. This is expected
on the current VPS.

### Telegram push fails

Check:

- bot token and chat ID in `.env`
- user has sent `/start` to the bot
- images exist under `drafts/digest/images/`
- Bot API response in run log

Dry-run validation:

```bash
python run.py --telegram-only output/<timestamp> --telegram-dry-run
```

### Scheduled run does not happen

Check scheduler logs:

```bash
docker compose logs --tail=80 f1-tg-pipeline
```

Expected line:

```text
Scheduler configured: daily_at=12:00 timezone=Asia/Hong_Kong hours=24 push_telegram=True run_on_start=False
Next scheduled run: ...
```

### Docker Compose buildx warning

The VPS may print:

```text
Docker Compose requires buildx plugin to be installed
```

The current setup still builds with the classic builder. It is a warning, not a
pipeline failure.

## Maintenance

Update `config.yaml` manually when the season context changes:

- completed race count
- next race
- cancelled or rescheduled races
- big-four team performance and car baseline
- newly confirmed technical terminology

After config changes:

```bash
.venv/bin/python -m unittest discover -s tests
git add config.yaml
git commit -m "Update season context"
git push
```

Then deploy with the VPS command above.
