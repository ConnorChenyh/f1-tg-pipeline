# Operations

This document covers local checks, Docker deployment, server operation, manual
triggers, and common troubleshooting.

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock   # exact pinned versions, as used in Docker
cp .env.example .env
```

`requirements.txt` is the human-edited direct-dependency list.
`requirements.lock` pins every resolved package and is what the Dockerfile
installs. After an intentional upgrade, refresh the lock:

```bash
.venv/bin/python -m pip install -U -r requirements.txt
.venv/bin/python -m pip freeze > requirements.lock
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

Telegram network requests retry three times with incremental five-second
backoff. If all attempts fail, the generated output directory is added to
`output/pending_telegram_deliveries.json`; the next Telegram-enabled scheduled
run tries those pending digests before producing the new one. Successful
compensation removes the entry. Existing outputs are never added retroactively.

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

### Guard rejected the draft

The log line is:

```text
Quality guard rejected the draft; saved for human review and skipped delivery: too_few_items
```

The run still succeeds and the draft is on disk. Inspect:

```bash
python - <<'PY'
import json
from pathlib import Path
meta = json.loads(Path("output/<timestamp>/drafts/digest/meta.json").read_text())
print("guard_blocked:", meta["guard_blocked"])
print("blocking codes:", meta["guard_blocking_codes"])
print("notes:", meta.get("fact_check_notes"))
PY
```

Nothing was delivered to Telegram and the season snapshot was not advanced, so the
next Telegram-enabled run still carries the same context. Topics from a rejected
run are recorded in topic history, so the same story is not re-picked and
re-rejected tomorrow. Fix the underlying cause in `generator/digest_writer.py`
or `generator/quality_guard.py` prompts/patterns, or push the saved draft
manually:

```bash
python run.py --telegram-only output/<timestamp>
```

### Item text is truncated on an image

`generate_images_for_digest` logs:

```text
Image layout truncated 1 item(s) that did not fit one card: slide_02.png (191/2400 chars)
```

Check `drafts/digest/render_measurements.json` (also copied into `meta.json` as
`image_measurements`). `rendered_chars` below `source_chars` means content was
dropped to fit; `dropped_lines` counts the lines the draw loop skipped.
`generator/images.py` searches font sizes from large to small (34 down to 22).

To reduce truncation, either shorten the item (`digest.item_max_chars`), give the
body more room, or **lower** the smallest searched size. Raising the floor makes
the minimum font larger and therefore truncates more, not less.

### LLM returned a malformed response

Expected log:

```text
Model response failed schema validation (attempt 1/2): 'items' must be a non-empty JSON array
```

The client re-prompts with the validation error included. If all attempts fail
the run aborts with a `RuntimeError` naming the error kind (`schema` or
`transport`). A `transport` error is retried; a caller error such as a bad
request is not retried.

### A run failed part way through

Every run records its progress in `output/<timestamp>/run_state.json` and points
at itself from `output/active_run.json`. Continue instead of starting over:

```bash
.venv/bin/python run.py --resume
```

The resumed run reuses the saved shortlist, topics and draft, so no new DeepSeek
calls are made for work that already succeeded. Expected log lines:

```text
Resuming run 2026-09-11_113002 (completed stages: collect, topics, digest)
Resume: reusing the written draft from 2026-09-11_113002
```

Runs older than `run_state.max_resume_age_hours` (default 6) are refused, and a
run that already reached `delivered` has nothing to resume. `--resume` is ignored
with `--mock`/`--dry-run`, which persist no state.

### Standings look stale or the cache is wrong

Fetched standings are stored in `output/standings_cache.json` for
`standings_refresh.cache_max_age_sec` seconds (default 3600) so repeated manual
runs do not re-scrape. Inspect it:

```bash
python - <<'PY'
import json
from pathlib import Path
print(json.dumps(json.loads(Path("output/standings_cache.json").read_text()), ensure_ascii=False, indent=2))
PY
```

If the numbers are wrong or the page layout changed, the run falls back to the
`config.yaml` snapshot and logs:

```text
Standings refresh failed; using configured team_baseline snapshot: ...
```

Set `cache_max_age_sec: 0` to always fetch, or delete the cache file to force a
fresh read.

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

Normal race-to-race season context changes are automatic. The configured race
dates determine break, race-build-up, active-weekend, and completed-race phases;
live Formula1 standings refresh the points baseline. A separate Telegram message
reports phase, completed-round, and standings changes.

Update `config.yaml` manually only when the authoritative calendar itself changes:

- cancelled or rescheduled races
- a new season calendar
- changed team/car identities
- newly confirmed technical terminology

After config changes:

```bash
.venv/bin/python -m unittest discover -s tests
git add config.yaml
git commit -m "Update season context"
git push
```

Then deploy with the VPS command above.
