#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT / "data" / "news_archive.jsonl"
DEFAULT_OUT = ROOT / "docs" / "data" / "news_archive.json"


def sanitize(text):
    if text is None:
        return ""
    return str(text).replace("\x00", "").strip()


def main():
    src = Path(os.environ.get("SOURCE_JSONL", str(DEFAULT_SRC))).expanduser()
    out = Path(os.environ.get("OUTPUT_JSON", str(DEFAULT_OUT))).expanduser()

    rows = []
    if src.exists():
        with src.open("r", encoding="utf-8") as f:
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
    else:
        # In GitHub Actions, local source JSONL may not exist.
        # Keep already committed docs/data file if present, and avoid hard failure.
        if out.exists():
            print(f"WARN: source not found: {src}. Keep existing output: {out}")
            return 0
        print(f"WARN: source not found: {src}. Write empty dataset to {out}")

    rows.sort(key=lambda x: x.get("published_at") or x.get("archived_at"), reverse=True)
    os.makedirs(out.parent, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
    print(f"OK: wrote {len(rows)} rows -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
