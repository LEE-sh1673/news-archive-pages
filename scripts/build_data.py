#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

SRC = Path("/home/lsh/news_archive/data/news_archive.jsonl")
OUT = Path("/home/lsh/news_archive_pages/docs/data/news_archive.json")


def sanitize(text):
    if text is None:
        return ""
    return str(text).replace("\x00", "").strip()


def main():
    if not SRC.exists():
        print(f"ERROR: source not found: {SRC}", file=sys.stderr)
        return 1

    rows = []
    with SRC.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            rows.append(
                {
                    "id": sanitize(row.get("id")),
                    "title": sanitize(row.get("title")),
                    "summary": sanitize(row.get("summary")),
                    "body": sanitize(row.get("body")),
                    "url": sanitize(row.get("url")),
                    "category": sanitize(row.get("category")),
                    "published_at": sanitize(row.get("published_at")),
                    "archived_at": sanitize(row.get("archived_at")),
                }
            )

    rows.sort(key=lambda x: x.get("published_at") or x.get("archived_at"), reverse=True)
    os.makedirs(OUT.parent, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
    print(f"OK: wrote {len(rows)} rows -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
