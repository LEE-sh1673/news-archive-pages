#!/usr/bin/env python3
import html
import json
import os
import sys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT / "data" / "news_archive.jsonl"
DEFAULT_OUT = ROOT / "docs" / "data" / "news_archive.json"


MOJIBAKE_MARKERS = ("Ã", "Â", "â€™", "â€œ", "â€", "ï¿½", "\ufffd")


def _fix_mojibake(text: str) -> str:
    if not any(m in text for m in MOJIBAKE_MARKERS):
        return text
    try:
        return text.encode("latin-1", errors="ignore").decode("utf-8", errors="ignore")
    except Exception:
        return text


def _strip_feed_noise(text: str) -> str:
    patterns = [
        r"(?is)Related\s+[A-Za-z].*$",
        r"(?is)\bFacebook\s+Twitter\s+LinkedIn.*$",
        r"(?is)\bLike this:\s*Like Loading\.\.\..*$",
        r"(?is)관련 기사 더 보기.*$",
        r"(?is)Loading Comments\.\.\..*$",
        r"(?is)You must be logged in to post a comment\..*$",
        r"(?is)%d bloggers like this:.*$",
        r"(?is)←.*$",
        r"(?is)→.*$",
    ]
    out = text
    for p in patterns:
        out = re.sub(p, "", out).strip()
    return out


def sanitize(text, field=""):
    if text is None:
        return ""
    s = str(text).replace("\x00", "").strip()
    s = html.unescape(s)
    s = _fix_mojibake(s)
    if field in ("summary", "body"):
        s = _strip_feed_noise(s)
    return s


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
                        "id": sanitize(row.get("id"), "id"),
                        "title": sanitize(row.get("title"), "title"),
                        "summary": sanitize(row.get("summary"), "summary"),
                        "body": sanitize(row.get("body"), "body"),
                        "url": sanitize(row.get("url"), "url"),
                        "category": sanitize(row.get("category"), "category"),
                        "published_at": sanitize(row.get("published_at"), "published_at"),
                        "archived_at": sanitize(row.get("archived_at"), "archived_at"),
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
