# F1 Hot Topics to Xiaohongshu Draft Pipeline

Local pipeline that collects F1 discussions from Reddit and RSS (Twitter optional), clusters hot topics with DeepSeek, generates image-based Chinese digests, and can push them to Telegram.

## Documentation

- `AGENTS.md` - agent navigation, collaboration rules, and common validation commands
- `docs/architecture.md` - pipeline architecture, module responsibilities, data flow, and quality gates
- `docs/operations.md` - Docker/VPS deployment, manual triggers, log inspection, and troubleshooting

## Prerequisites

- Python 3.10+
- `rdt-cli` for Reddit collection (installed via `pip install -r requirements.txt`)

```bash
pipx install rdt-cli   # alternative if you use pipx globally
```

- Optional Twitter support:

```bash
pipx install twitter-cli
```

Set `TWITTER_AUTH_TOKEN` and `TWITTER_CT0` in `.env` if you want Twitter as a source.

## Setup

```bash
cd f1-xhs-pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set:

```
DEEPSEEK_API_KEY=sk-...
TELEGRAM_BOT_TOKEN=123456:...
TELEGRAM_CHAT_ID=123456789
```

## Run

```bash
# Full run (default: last 24 hours, one digest post)
python run.py

# Custom window
python run.py --hours 24

# Collect only (no DeepSeek calls)
python run.py --dry-run

# Offline end-to-end without API key
python run.py --mock

# Push generated digest to Telegram after a full run
python run.py --push-telegram

# Push an existing output directory to Telegram without rerunning DeepSeek
python run.py --telegram-only output/<timestamp>

# Validate Telegram payload without sending
python run.py --telegram-only output/<timestamp> --telegram-dry-run
```

`--mock` only validates the local pipeline path. It does not evaluate final
Chinese copy quality because it intentionally skips DeepSeek and uses simple
placeholder text.

## Output

Each run creates `output/<timestamp>/` with:

- `raw_posts.json` - normalized scored posts (24h window)
- `shortlisted_posts.json` - deterministic candidate shortlist sent to topic extraction
- `topics.json` - hot topics used in the digest
- `drafts/digest/` - single roundup: `draft.md`, `draft.json`, `images/*.png`
- `preview.html` - local review page with copy button

The digest title is fixed as **围场过去24H新闻**, with body items numbered 一、二、三...

## Scheduled Docker Deployment

Copy `.env.example` to `.env`, fill in `DEEPSEEK_API_KEY`, `TELEGRAM_BOT_TOKEN`,
and `TELEGRAM_CHAT_ID`, then run:

```bash
docker compose up -d --build
```

The container runs `scheduler.py` and executes one full 24-hour digest at
`SCHEDULE_DAILY_AT` every day, using `SCHEDULE_TIMEZONE`. By default it pushes
to Telegram and keeps generated artifacts in `./output`.

Useful overrides in `.env`:

```bash
SCHEDULE_TIMEZONE=Asia/Hong_Kong
TZ=Asia/Hong_Kong
SCHEDULE_DAILY_AT=12:00
SCHEDULE_HOURS=24
SCHEDULE_PUSH_TELEGRAM=true
SCHEDULE_RUN_ON_START=false
```

Set `SCHEDULE_RUN_ON_START=true` for a one-off immediate run when the container
starts, then it will continue with the daily schedule.

## Review workflow

1. Run `python run.py`
2. Open `output/<latest>/preview.html` in a browser
3. Check `risk_note` and source links
4. Push with `python run.py --telegram-only output/<latest>` if you want to send it

## Notes

- DeepSeek is text-only; images are generated with Pillow text-card templates.
- If Reddit or a single RSS feed fails, other sources continue.
- Twitter collector is optional and skipped when credentials are missing.
- Candidate governance runs before DeepSeek: source tiers, article bonus,
  cross-source bonus, social-only caps, time decay, and batch dedup.
- Published topic memory is stored in `output/topic_history.json` and
  `output/story_memory.sqlite3`; repeated manual runs may backfill recent
  evidence-backed topics to satisfy the minimum digest size.
- Drafts are grounded on collected evidence with timestamps. A fact-check pass
  runs by default (`deepseek.fact_check_enabled`), followed by a separate final
  review pass (`deepseek.final_review_enabled`) for punctuation, grammar,
  semantic clarity, terminology, and last-mile fact confirmation.
- Digest generation uses a compact grounding packet and a deterministic quality
  guard before saving. Blocking issues include source links outside evidence and
  obvious semantic compression such as turning separate hillclimb/balcony actions
  into “driving onto the balcony”.
- Source articles are fetched and read before summarization when URLs are article pages (`article_fetch.enabled`).
- Telegram push is optional. Create a bot with BotFather, send `/start` to the
  bot, set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, then use
  `--push-telegram` or `--telegram-only`.
