from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _format_items_for_copy(draft: dict[str, Any]) -> str:
    lines = [draft.get("hook", "")]
    for item in draft.get("items", []):
        ordinal = item.get("ordinal", "")
        headline = item.get("headline", "")
        content = item.get("content", "")
        lines.append(f"{ordinal}、{headline}")
        lines.append(content)
        lines.append("")
    return "\n".join(lines).strip()


def generate_preview(output_dir: Path) -> Path:
    topics_path = output_dir / "topics.json"
    topics = json.loads(topics_path.read_text(encoding="utf-8")) if topics_path.exists() else []

    digest_dir = output_dir / "drafts" / "digest"
    legacy_dirs = sorted((output_dir / "drafts").glob("post_*")) if (output_dir / "drafts").exists() else []
    draft_dirs = [digest_dir] if digest_dir.exists() else legacy_dirs

    draft_blocks: list[str] = []

    for draft_dir in draft_dirs:
        draft_json_path = draft_dir / "draft.json"
        if not draft_json_path.exists():
            continue
        draft = _read_json(draft_json_path)
        images = sorted((draft_dir / "images").glob("*.png")) if (draft_dir / "images").exists() else []
        rel_images = [str(img.relative_to(output_dir)).replace("\\", "/") for img in images]

        carousel_items = "".join(
            f'<img src="{html.escape(rel)}" alt="slide" class="slide" />' for rel in rel_images
        )

        body_text = _format_items_for_copy(draft)
        hashtags = " ".join(draft.get("hashtags", []))
        copy_payload = {
            "title": draft.get("title", ""),
            "body": body_text,
            "hashtags": hashtags,
        }

        item_blocks = "".join(
            f"""
            <div class="digest-item">
              <h3>{html.escape(item.get('ordinal', ''))}、{html.escape(item.get('headline', ''))}</h3>
              <p>{html.escape(item.get('content', ''))}</p>
            </div>
            """
            for item in draft.get("items", [])
        )

        if not item_blocks and draft.get("body"):
            item_blocks = "".join(f"<p>{html.escape(p)}</p>" for p in draft.get("body", []))

        draft_blocks.append(
            f"""
            <section class="draft-card">
              <h2>{html.escape(draft.get('title', draft_dir.name))}</h2>
              <p class="hook">{html.escape(draft.get('hook', ''))}</p>
              <div class="carousel">{carousel_items}</div>
              <div class="body">{item_blocks}</div>
              <p class="tags">{html.escape(hashtags)}</p>
              <p class="risk"><strong>Risk:</strong> {html.escape(draft.get('risk_note', '无'))}</p>
              <ul class="sources">{''.join(f'<li><a href="{html.escape(s)}" target="_blank">{html.escape(s)}</a></li>' for s in draft.get('sources', []))}</ul>
              <button onclick='copyDraft({json.dumps(copy_payload, ensure_ascii=False)})'>复制标题+正文+标签</button>
            </section>
            """
        )

    topic_blocks = "".join(
        f"""
        <article class="topic-card">
          <h3>{html.escape(topic.get('title_zh', ''))}</h3>
          <p>{html.escape(topic.get('summary', ''))}</p>
          <p><strong>Heat:</strong> {html.escape(str(topic.get('heat_score', '')))}</p>
        </article>
        """
        for topic in topics
    )

    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>F1 XHS Digest Preview</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: #111; color: #f2f2f2; }}
    main {{ max-width: 980px; margin: 0 auto; padding: 24px; }}
    h1 {{ margin-bottom: 8px; }}
    .topic-grid, .draft-grid {{ display: grid; gap: 16px; }}
    .topic-card, .draft-card {{ background: #1d1d27; border: 1px solid #333; border-radius: 12px; padding: 16px; }}
    .digest-item {{ margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid #333; }}
    .digest-item h3 {{ color: #ff6a62; margin: 0 0 8px; }}
    .carousel {{ display: flex; gap: 12px; overflow-x: auto; padding: 8px 0; }}
    .slide {{ height: 320px; border-radius: 8px; border: 1px solid #444; }}
    .hook {{ color: #ff6a62; }}
    .tags {{ color: #9ad1ff; }}
    .risk {{ color: #ffd27f; }}
    button {{ margin-top: 12px; background: #e10600; color: white; border: 0; border-radius: 8px; padding: 10px 14px; cursor: pointer; }}
    a {{ color: #9ad1ff; }}
  </style>
</head>
<body>
  <main>
    <h1>围场24H新闻 · 预览</h1>
    <p>输出目录: {html.escape(str(output_dir))}</p>

    <h2>纳入话题</h2>
    <div class="topic-grid">{topic_blocks or '<p>暂无话题</p>'}</div>

    <h2>整合草稿</h2>
    <div class="draft-grid">{''.join(draft_blocks) or '<p>暂无草稿</p>'}</div>
  </main>
  <script>
    function copyDraft(payload) {{
      const text = `${{payload.title}}\\n\\n${{payload.body}}\\n\\n${{payload.hashtags}}`;
      navigator.clipboard.writeText(text).then(() => alert('已复制到剪贴板'));
    }}
  </script>
</body>
</html>
"""

    preview_path = output_dir / "preview.html"
    preview_path.write_text(page, encoding="utf-8")
    return preview_path
