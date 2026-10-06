#!/usr/bin/env python3
"""Rerun digest writing, fact check and final review on an existing run.

Used to measure a prompt change against the exact topics and article evidence of
a real run, without collecting again. The rerun uses the run's original time
and season snapshot, writes to its own directory and never touches topic
history, story DB, caches, the season state or the pending delivery queue.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from analyzer.context import RunContext
from analyzer.season_context import build_season_context_prompt
from generator.deepseek_client import DeepSeekClient
from generator.digest_writer import generate_digest, save_digest
from generator.images import generate_images_for_digest
from publisher.telegram import _digest_title_for_telegram, push_digest_to_telegram
from run import DEFAULT_CONFIG, _collect_usage, _load_image_measurements, _provenance, load_config

REPLAY_TITLE_PREFIX = "【重跑对比】"
REVIEW_PREFIX = "终审提示："
GUARD_PREFIX = "质量检查提示："


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_source(run_dir: Path) -> dict[str, Any]:
    """Read what the original digest stage consumed.

    ``meta.json`` holds the exact topics given to the writer (already capped to
    ``digest.max_items``) and the original run time; ``season_snapshot.json``
    holds the calendar and standings the run used.
    """
    meta_path = run_dir / "drafts" / "digest" / "meta.json"
    season_path = run_dir / "season_snapshot.json"
    for path in (meta_path, season_path):
        if not path.exists():
            raise FileNotFoundError(f"replay needs {path.relative_to(run_dir)} from the source run")
    meta = _read_json(meta_path)
    topics = meta.get("topics")
    if not isinstance(topics, list) or not topics:
        raise ValueError(f"{meta_path} has no digest topics")
    run_context = meta.get("run_context") or {}
    generated_at = datetime.fromisoformat(str(run_context["generated_at"]).replace("Z", "+00:00"))
    draft_path = run_dir / "drafts" / "digest" / "draft.json"
    return {
        "meta": meta,
        "topics": topics,
        "season_config": _read_json(season_path)["config"],
        "generated_at": generated_at,
        "window_hours": int(run_context.get("window_hours") or 24),
        "draft": _read_json(draft_path) if draft_path.exists() else None,
    }


def _note_counts(notes: list[str]) -> dict[str, int]:
    review = sum(1 for note in notes if note.startswith(REVIEW_PREFIX))
    guard = sum(1 for note in notes if note.startswith(GUARD_PREFIX))
    return {"fact_check": len(notes) - review - guard, "final_review": review, "quality_guard": guard}


def build_comparison(source_name: str, old_draft: dict | None, old_notes: list[str],
                     new_draft: dict, new_notes: list[str], new_blocking: list[str]) -> str:
    old_counts, new_counts = _note_counts(old_notes), _note_counts(new_notes)
    lines = [
        f"# 重跑对比：{source_name}",
        "",
        "| 修改次数 | 原稿 | 重跑 |",
        "| --- | --- | --- |",
    ]
    for key, label in (("fact_check", "事实核查"), ("final_review", "终审"), ("quality_guard", "质量检查提示")):
        lines.append(f"| {label} | {old_counts[key]} | {new_counts[key]} |")
    lines += ["", f"重跑质量关卡：{'拦截 ' + ', '.join(new_blocking) if new_blocking else '通过'}", ""]

    old_items = (old_draft or {}).get("items") or []
    new_items = new_draft.get("items") or []
    for index in range(max(len(old_items), len(new_items))):
        lines.append(f"## 条目 {index + 1}")
        for label, items in (("原稿", old_items), ("重跑", new_items)):
            item = items[index] if index < len(items) else None
            lines.append(f"### {label}")
            if item is None:
                lines.append("（无）")
            else:
                lines.append(f"**{item.get('headline', '')}**")
                lines.append("")
                lines.append(str(item.get("content", "")))
            lines.append("")

    for label, notes in (("原稿核查记录", old_notes), ("重跑核查记录", new_notes)):
        lines.append(f"## {label}")
        lines.extend([f"- {note}" for note in notes] or ["（无）"])
        lines.append("")
    return "\n".join(lines)


def replay(source_dir: Path, out_dir: Path, config: dict[str, Any], client: Any, *,
           render_images: bool = False) -> dict[str, Any]:
    source = load_source(source_dir)
    # Same calendar and standings as the original run, and nothing persisted.
    config["season_context"] = source["season_config"]
    config["_runtime"] = {"root": str(ROOT), "persist": False}
    generated_at = source["generated_at"]
    run_context = RunContext(
        generated_at=generated_at,
        window_hours=source["window_hours"],
        f1_season=generated_at.year,
    ).with_season_context(build_season_context_prompt(config, generated_at))

    digest_cfg = config.get("digest", {})
    topics = source["topics"]
    digest_title = source["meta"].get("digest_title") or digest_cfg.get("title", "围场过去24H新闻")
    draft, notes, blocking_codes = generate_digest(
        client,
        topics,
        run_context,
        digest_title=digest_title,
        min_items=min(int(digest_cfg.get("min_items", 3)), len(topics)),
        max_items=int(digest_cfg.get("max_items", 5)),
        item_min_chars=int(digest_cfg.get("item_min_chars", 0)),
        item_target_chars=int(digest_cfg.get("item_target_chars", 0)),
        item_max_chars=int(digest_cfg.get("item_max_chars", 380)),
        fact_check_enabled=bool(config.get("deepseek", {}).get("fact_check_enabled", True)),
        final_review_enabled=bool(config.get("deepseek", {}).get("final_review_enabled", True)),
    )

    draft_dir = out_dir / "drafts" / "digest"
    save_digest(draft_dir, draft, notes or None)
    meta = {
        "digest_title": digest_title,
        "replay_of": str(source_dir),
        "provenance": _provenance(config),
        "topics": topics,
        "images": [],
        "image_measurements": {},
        "fact_check_notes": notes,
        "guard_blocking_codes": blocking_codes,
        "guard_blocked": bool(blocking_codes),
        "model_usage": _collect_usage(client),
        "run_context": {
            "generated_at": generated_at.isoformat(),
            "f1_season": run_context.f1_season,
            "window_hours": run_context.window_hours,
        },
    }
    _write_json(draft_dir / "meta.json", meta)

    if render_images:
        meta["images"] = generate_images_for_digest(draft, topics, draft_dir, config, generated_at=generated_at)
        meta["image_measurements"] = _load_image_measurements(draft_dir)
        if meta["image_measurements"].get("truncated_count", 0):
            meta["guard_blocking_codes"] = sorted(set(blocking_codes) | {"image_truncated"})
            meta["guard_blocked"] = True
        _write_json(draft_dir / "meta.json", meta)

    comparison = build_comparison(
        source_dir.name,
        source["draft"],
        list(source["meta"].get("fact_check_notes") or []),
        draft,
        notes,
        meta["guard_blocking_codes"],
    )
    (out_dir / "comparison.md").write_text(comparison, encoding="utf-8")
    return {"draft_dir": draft_dir, "meta": meta, "comparison": out_dir / "comparison.md"}


def push_replay(draft_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Send the rerun with a title that cannot be mistaken for the daily digest."""
    draft_path = draft_dir / "draft.json"
    draft = _read_json(draft_path)
    draft["telegram_title"] = REPLAY_TITLE_PREFIX + _digest_title_for_telegram(draft_dir, draft.get("title"))
    _write_json(draft_path, draft)
    return push_digest_to_telegram(draft_dir, config)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="Run directory to replay, e.g. output/inspection/2026-10-05_120004")
    parser.add_argument("--out", type=Path, default=None, help="Output directory (default: output/replay/<run>_<time>)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Path to config.yaml")
    parser.add_argument("--images", action="store_true", help="Render cover and item cards")
    parser.add_argument("--push-telegram", action="store_true",
                        help=f"Render images and send to TELEGRAM_CHAT_ID, titled {REPLAY_TITLE_PREFIX}")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_dotenv(ROOT / ".env")
    config = load_config(args.config)
    config["_config_sha256"] = hashlib.sha256(json.dumps(
        {key: value for key, value in config.items() if not key.startswith("_")},
        sort_keys=True, ensure_ascii=False,
    ).encode()).hexdigest()
    source_dir = args.source.resolve()
    out_dir = args.out or ROOT / "output" / "replay" / f"{source_dir.name}_{datetime.now():%Y%m%d-%H%M%S}"

    result = replay(source_dir, out_dir, config, DeepSeekClient(config),
                    render_images=args.images or args.push_telegram)
    logging.info("Replay written to %s", out_dir)
    logging.info("Comparison: %s", result["comparison"])
    if result["meta"]["guard_blocked"]:
        logging.error("Replay draft is blocked: %s", ", ".join(result["meta"]["guard_blocking_codes"]))
        return 2 if args.push_telegram else 0
    if args.push_telegram:
        push_replay(result["draft_dir"], config)
        logging.info("Replay sent to Telegram")
    return 0


if __name__ == "__main__":
    sys.exit(main())
