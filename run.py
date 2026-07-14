#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

from analyzer.article_fetcher import enrich_evidence_with_articles
from analyzer.context import RunContext
from analyzer.evidence import enrich_topics_with_evidence
from analyzer.evidence_gate import filter_topics_by_evidence_quality
from analyzer.normalize import normalize_posts
from analyzer.score import score_posts
from analyzer.season_context import build_season_context_prompt
from analyzer.shortlist import shortlist_posts
from analyzer.story_db import (
    filter_topics_seen_in_story_db,
    init_story_db,
    prune_story_db,
    record_candidates,
    record_published_topics,
)
from analyzer.topic_history import append_topic_history, filter_recent_topics
from analyzer.topics import extract_topics
from collectors.reddit import collect_reddit
from collectors.rss import collect_rss
from collectors.twitter import collect_twitter
from generator.deepseek_client import DeepSeekClient
from generator.digest_writer import generate_digest, save_digest
from generator.images import generate_images_for_digest
from generator.preview import generate_preview
from publisher.telegram import TelegramConfigError, push_digest_to_telegram

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config.yaml"


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def mock_topics(scored_posts: list) -> list[dict]:
    if not scored_posts:
        return []
    topics = []
    ordinals = ["topic_01", "topic_02", "topic_03"]
    for i, post in enumerate(scored_posts[:3]):
        topics.append(
            {
                "id": ordinals[i],
                "title_zh": post.title or post.text[:40],
                "summary": post.text[:120],
                "heat_score": 80 - i * 5,
                "evidence_urls": [post.url] if post.url else [],
                "publish_recommendation": "publish",
                "skip_reason": None,
            }
        )
    return topics


def mock_digest(topics: list[dict]) -> dict:
    items = []
    ordinals = ["一", "二", "三", "四", "五"]
    for i, topic in enumerate(topics[:3]):
        items.append(
            {
                "ordinal": ordinals[i],
                "headline": topic.get("title_zh", f"热点{i + 1}"),
                "content": topic.get("summary", ""),
            }
        )
    return {
        "title": "围场过去24H新闻",
        "hook": "过去24小时，围场有这些值得关注的消息：",
        "items": items,
        "hashtags": ["#F1", "#Formula1", "#围场新闻"],
        "sources": [
            url
            for topic in topics[:3]
            for url in (topic.get("evidence_urls") or [])
        ][:5],
        "risk_note": "离线 mock 模式，发布前请人工核实",
    }


def save_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_output_dir(root: Path) -> Path:
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out = root / "output" / ts
    out.mkdir(parents=True, exist_ok=True)
    return out


def backfill_recent_topics(
    fresh_topics: list[dict],
    skipped_topics: list[dict],
    original_topics: list[dict],
    min_items: int,
) -> tuple[list[dict], list[dict]]:
    if len(fresh_topics) >= min_items:
        return fresh_topics, skipped_topics

    reusable_reasons = ("shared_url:", "text_similarity:", "story_db:")
    reusable_ids = {
        item.get("id")
        for item in skipped_topics
        if str(item.get("reason") or "").startswith(reusable_reasons)
    }
    if not reusable_ids:
        return fresh_topics, skipped_topics

    selected_ids = {topic.get("id") for topic in fresh_topics}
    backfilled: list[dict] = []
    for topic in original_topics:
        topic_id = topic.get("id")
        if topic_id in selected_ids or topic_id not in reusable_ids:
            continue
        item = dict(topic)
        notes = list(item.get("evidence_quality_notes", []) or [])
        notes.append("recent duplicate backfilled to satisfy minimum digest item count")
        item["evidence_quality_notes"] = notes
        backfilled.append(item)
        selected_ids.add(topic_id)
        if len(fresh_topics) + len(backfilled) >= min_items:
            break

    if not backfilled:
        return fresh_topics, skipped_topics

    backfilled_ids = {topic.get("id") for topic in backfilled}
    remaining_skipped = [
        item
        for item in skipped_topics
        if item.get("id") not in backfilled_ids
    ]
    for topic in backfilled:
        remaining_skipped.append(
            {
                "id": topic.get("id"),
                "title_zh": topic.get("title_zh"),
                "reason": "backfilled_recent_duplicate_for_min_items",
            }
        )
    return fresh_topics + backfilled, remaining_skipped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="F1 hot topics to Xiaohongshu draft pipeline")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Path to config.yaml")
    parser.add_argument("--hours", type=int, default=None, help="Time window in hours")
    parser.add_argument("--max-drafts", type=int, default=None, help="Maximum drafts to generate")
    parser.add_argument("--dry-run", action="store_true", help="Collect and cluster only")
    parser.add_argument("--mock", action="store_true", help="Skip DeepSeek and use mock topics/drafts")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--push-telegram", action="store_true", help="Push generated digest text/images to Telegram")
    parser.add_argument(
        "--telegram-only",
        type=Path,
        default=None,
        help="Push an existing output/<timestamp> digest to Telegram without running the pipeline",
    )
    parser.add_argument("--telegram-dry-run", action="store_true", help="Validate Telegram payload without sending")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(args.verbose)
    load_dotenv(ROOT / ".env")

    config = load_config(args.config)
    if args.telegram_only is not None:
        output_dir = args.telegram_only
        draft_dir = output_dir / "drafts" / "digest"
        try:
            result = push_digest_to_telegram(draft_dir, config, dry_run=args.telegram_dry_run)
            logging.info("Telegram push result: %s", result)
            return 0
        except TelegramConfigError as exc:
            logging.error("Telegram config error: %s", exc)
            return 1
        except Exception as exc:
            logging.error("Telegram push failed: %s", exc)
            return 1

    window_hours = args.hours if args.hours is not None else int(config.get("window_hours", 12))
    max_drafts = args.max_drafts if args.max_drafts is not None else int(config.get("max_drafts", 3))
    heat_threshold = int(config.get("heat_threshold", 60))
    fact_check_enabled = bool(config.get("deepseek", {}).get("fact_check_enabled", True))
    final_review_enabled = bool(config.get("deepseek", {}).get("final_review_enabled", True))
    digest_cfg = config.get("digest", {})
    digest_title = digest_cfg.get("title", "围场过去24H新闻")
    digest_min_items = int(digest_cfg.get("min_items", 3))
    digest_max_items = int(digest_cfg.get("max_items", 5))
    digest_item_min_chars = int(digest_cfg.get("item_min_chars", 240))
    digest_item_target_chars = int(digest_cfg.get("item_target_chars", 300))
    digest_item_max_chars = int(digest_cfg.get("item_max_chars", 380))
    run_context = RunContext.now(window_hours)
    run_context = run_context.with_season_context(build_season_context_prompt(config, run_context.generated_at))
    run_id = run_context.generated_at.strftime("%Y-%m-%d_%H%M%S")
    init_story_db(ROOT, config)
    prune_story_db(ROOT, config, run_context.generated_at)

    output_dir = build_output_dir(ROOT)
    logging.info("Output directory: %s", output_dir)

    posts = []
    for collector_name, collector in (
        ("reddit", lambda: collect_reddit(config)),
        ("rss", lambda: collect_rss(config, window_hours)),
        ("twitter", lambda: collect_twitter(config)),
    ):
        try:
            posts.extend(collector())
        except Exception as exc:
            logging.warning("%s collector failed: %s", collector_name, exc)

    normalized = normalize_posts(posts, window_hours)
    scored = score_posts(normalized)
    save_json(output_dir / "raw_posts.json", [p.to_dict() for p in scored])
    shortlisted = shortlist_posts(scored, config, run_context.generated_at)
    save_json(output_dir / "shortlisted_posts.json", [p.to_dict() for p in shortlisted])
    record_candidates(shortlisted, ROOT, config, run_id, run_context.generated_at)

    if not shortlisted:
        logging.error("No posts collected in the last %s hours", window_hours)
        return 1

    if args.dry_run:
        logging.info("Dry run complete: %d posts saved", len(scored))
        return 0

    skipped_recent_topics: list[dict] = []
    if args.mock:
        topics = enrich_topics_with_evidence(mock_topics(shortlisted), shortlisted)
        topics = enrich_evidence_with_articles(topics, config)
        client = None
    else:
        client = DeepSeekClient(config)
        topics = extract_topics(
            client,
            shortlisted,
            heat_threshold,
            run_context,
            min_topics=digest_min_items,
            max_topics=digest_max_items,
        )
        topics = enrich_topics_with_evidence(topics, shortlisted)
        topics = enrich_evidence_with_articles(topics, config)
        topics, skipped_by_evidence = filter_topics_by_evidence_quality(topics, config)
        if skipped_by_evidence:
            skipped_recent_topics.extend(skipped_by_evidence)
        history_candidate_topics = topics
        topics, skipped_by_story_db = filter_topics_seen_in_story_db(
            topics,
            ROOT,
            config,
            run_context.generated_at,
        )
        if skipped_by_story_db:
            skipped_recent_topics.extend(skipped_by_story_db)
        topics, skipped_by_topic_history = filter_recent_topics(
            topics,
            ROOT,
            config,
            run_context.generated_at,
        )
        if skipped_by_topic_history:
            skipped_recent_topics.extend(skipped_by_topic_history)
        topics, skipped_recent_topics = backfill_recent_topics(
            topics,
            skipped_recent_topics,
            history_candidate_topics,
            digest_min_items,
        )

    save_json(output_dir / "topics.json", topics)
    save_json(output_dir / "run_context.json", {
        "generated_at": run_context.generated_at.isoformat(),
        "window_hours": run_context.window_hours,
        "f1_season": run_context.f1_season,
    })

    if not topics:
        logging.error("No fresh topics available after heat/history filtering")
        return 1

    # Cap topics used in digest
    digest_topics = topics[:digest_max_items]
    effective_min_items = min(digest_min_items, len(digest_topics))
    draft_dir = output_dir / "drafts" / "digest"
    fact_check_notes: list[str] = []

    try:
        if args.mock:
            draft = mock_digest(digest_topics)
        else:
            draft, fact_check_notes = generate_digest(
                client,
                digest_topics,
                run_context,
                digest_title=digest_title,
                min_items=effective_min_items,
                max_items=digest_max_items,
                item_min_chars=digest_item_min_chars,
                item_target_chars=digest_item_target_chars,
                item_max_chars=digest_item_max_chars,
                fact_check_enabled=fact_check_enabled,
                final_review_enabled=final_review_enabled,
            )
        save_digest(draft_dir, draft, fact_check_notes or None)
        image_paths = generate_images_for_digest(draft, digest_topics, draft_dir, config)
        meta = {
            "topics": digest_topics,
            "skipped_recent_topics": skipped_recent_topics,
            "images": image_paths,
            "fact_check_notes": fact_check_notes,
            "run_context": {
                "generated_at": run_context.generated_at.isoformat(),
                "f1_season": run_context.f1_season,
                "window_hours": run_context.window_hours,
            },
        }
        save_json(draft_dir / "meta.json", meta)
        logging.info("Generated digest at %s", draft_dir)
        if args.push_telegram:
            result = push_digest_to_telegram(draft_dir, config, dry_run=args.telegram_dry_run)
            logging.info("Telegram push result: %s", result)
        if not args.telegram_dry_run:
            append_topic_history(digest_topics, ROOT, config, run_context.generated_at)
            record_published_topics(digest_topics, ROOT, config, run_context.generated_at)
    except Exception as exc:
        logging.error("Failed to generate digest: %s", exc)
        return 1

    preview_path = generate_preview(output_dir)
    logging.info("Preview: %s", preview_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
