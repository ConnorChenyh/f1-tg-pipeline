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
pip install -r requirements.lock
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

# Continue the most recent unfinished run instead of collecting again
python run.py --resume

# Push generated digest to Telegram after a full run
python run.py --push-telegram

# Push an existing output directory to Telegram without rerunning DeepSeek
python run.py --telegram-only output/<timestamp>

# Validate Telegram payload without sending
python run.py --telegram-only output/<timestamp> --telegram-dry-run
```

`--mock` only validates the local pipeline path. It does not evaluate final
Chinese copy quality because it intentionally skips DeepSeek and uses simple
placeholder text. `--mock` and `--dry-run` are read-only with respect to
persistent memory: they never write topic history, the story DB, the standings
cache, a resumable run pointer, or the season snapshot, so a throwaway run cannot
suppress real topics.

`--resume` continues the most recent unfinished run, reusing the saved shortlist,
topics and draft so no DeepSeek work is repeated. It is ignored with
`--mock`/`--dry-run`, and runs older than `run_state.max_resume_age_hours` are
refused.

Dependencies are pinned in `requirements.lock`, which is what the Dockerfile
installs; `requirements.txt` is the human-edited direct-dependency list.

`--push-telegram` delivers a single title line followed by the images. The body
is not duplicated into the message: the cover card lists every headline and each
detail card carries its own text. Images are sent in batches of 10 when a digest
has more slides than one media group allows.

## Output

Each run creates `output/<timestamp>/` with:

- `raw_posts.json` - normalized scored posts (24h window)
- `shortlisted_posts.json` - deterministic candidate shortlist sent to topic extraction
- `topics.json` - hot topics used in the digest
- `drafts/digest/` - single roundup: `draft.md`, `draft.json`, `images/*.png`
- `drafts/digest/meta.json` - includes `model_usage`: tokens, latency, retries
  and failed calls per stage, so the cost of a run is inspectable
- `drafts/digest/render_measurements.json` - per-slide `source_chars` vs
  `rendered_chars` plus a `truncated` flag, so silent text loss on a card is visible
- `preview.html` - local review page with copy button

The digest title is fixed as **围场过去24H新闻**, with body items numbered 一、二、三...
The delivered Telegram title appends the run date (`围场过去24H新闻YY.MM.DD`), derived
from the run's `meta.json` so it has a single source of truth.

## Scheduled Docker Deployment

Copy `.env.example` to `.env`, fill in `DEEPSEEK_API_KEY`, `TELEGRAM_BOT_TOKEN`,
and `TELEGRAM_CHAT_ID`, then run:

```bash
docker compose up -d --build
```

The container runs `scheduler.py` and executes one full 24-hour digest at
`SCHEDULE_DAILY_AT` every day, using `SCHEDULE_TIMEZONE`. By default it pushes
to Telegram and keeps generated artifacts in `./output`. Telegram network
requests retry three times with incremental five-second backoff. A digest that
still cannot be sent is recorded in `output/pending_telegram_deliveries.json`
and is compensated before the next scheduled digest run.

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
- Calendar phase and live standings memory is stored in
  `output/season_context_state.json`. The first successful run establishes a
  silent baseline; later race-phase, completed-round, or standings changes are
  summarized to Telegram after the daily digest is delivered.
- `topic_cooldowns` in `config.yaml` suppresses repeated themes even when new
  URLs appear, such as repeated Verstappen future/transfer, Goodwood Festival of
  Speed, or Belgian GP preview stories.
- If filtering leaves too few topics, the pipeline can add fallback topics from
  high-quality article-backed shortlist items, while still applying evidence and
  cooldown filters.
- Drafts are grounded on collected evidence with timestamps. A fact-check pass
  runs by default (`deepseek.fact_check_enabled`), followed by a separate final
  review pass (`deepseek.final_review_enabled`) for punctuation, grammar,
  semantic clarity, terminology, and last-mile fact confirmation.
- LLM responses are contract-checked, and the handling differs per field. Being
  precise matters here because "coerced" is not "repaired":
  - **Re-prompted (repair)** with the specific validation error: a missing or
    empty `items` array, a `topics` payload that is not a non-empty array, a
    topic without `title_zh`, and `hashtags`/`sources` that are present but not
    arrays of strings.
  - **Coerced in place, no re-prompt**: a numeric `content`/`headline`/`hook` is
    converted to text, and `hashtags`/`sources` that are `null` become `[]`.
    `heat_score` that cannot be read as a number becomes `0`.
  - Nothing is guessed from a value that is absent where it is required: a
    missing `headline`/`content` is refused.
- The fact-check and final-review passes rewrite the draft, so their output is
  re-validated. If a review reply is structurally unusable, the draft written
  before that pass is kept and a warning is logged, so a bad second opinion does
  not discard the digest.
- A `heat_score` that cannot be read as a number is coerced to `0` and logged
  with the offending value. Such a topic is then usually dropped by
  `heat_threshold`, although the minimum-topic backfill can still re-add it.
  `topics.json` only stores the topics that were finally selected, so a dropped
  topic will not appear there — check the log line instead.
- If the deterministic quality guard still rejects the draft after review, the
  run **saves the draft anyway** and records `guard_blocked` plus
  `guard_blocking_codes` in `meta.json`. Delivery is skipped and no season
  snapshot is advanced, but the day's work is preserved for human review instead
  of being lost. Check `guard_blocking_codes` in `meta.json` after such a run.
- Image layout reports truncation. If an item cannot fit one card,
  `render_measurements.json` records it and the log warns, so "each item fits one
  image" is verifiable rather than assumed.
- Digest generation uses a compact grounding packet and a deterministic quality
  guard before saving. Blocking issues include source links outside evidence and
  obvious semantic compression such as turning separate hillclimb/balcony actions
  into “driving onto the balcony”.
- Source articles are fetched and read before summarization when URLs are article pages (`article_fetch.enabled`).
  URLs are validated first (scheme, private/loopback/metadata addresses, DNS resolution and every
  redirect hop), so a link from Reddit or RSS content cannot reach the host's own network.
- Standings and article fetches retry with bounded, jittered backoff. The OpenAI SDK's own retry
  loop is disabled so the configured attempt budget is the real one.
- `deepseek.max_total_seconds` (default 900) caps all model calls in one run. When it is spent the
  run stops with a clear error instead of retrying further; work already checkpointed can be
  continued with `--resume`, but a run that exhausts the budget before topic extraction produces
  no digest.
- Old run directories are pruned after `output_retention.keep_days`, but never while a run is
  referenced by the pending-delivery queue or the active-run pointer.
- Telegram push is optional. Create a bot with BotFather, send `/start` to the
  bot, set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, then use
  `--push-telegram` or `--telegram-only`.
