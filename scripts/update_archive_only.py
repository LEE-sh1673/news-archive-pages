#!/usr/bin/env python3
import datetime as dt
import hashlib
import json
import os
import re
import sys
import urllib.parse
import urllib.request

NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Seoul")
MAX_SUMMARY_LINES = max(1, int(os.environ.get("MAX_SUMMARY_LINES", "15")))
ARCHIVE_PATH = os.environ.get("ARCHIVE_PATH", "data/news_archive.jsonl")
ITEM_LIMIT_PER_CATEGORY = max(1, int(os.environ.get("ITEM_LIMIT_PER_CATEGORY", "8")))


def fail(msg: str) -> int:
    print(f"ERROR: {msg}", file=sys.stderr)
    return 1


def clean_text(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"\+\d+\s*chars", "", s, flags=re.IGNORECASE)
    return s.strip(" -")


def enforce_line_limit(text: str, limit: int) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines[:limit])


def local_summary(title: str, description: str, content: str) -> str:
    merged = " ".join(
        part for part in [clean_text(title), clean_text(description), clean_text(content)] if part
    )
    if not merged:
        return "요약할 본문이 부족합니다."
    sentences = re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s+|(?<=요\.)\s+", merged)
    sentences = [s.strip(" -") for s in sentences if s and s.strip(" -")]
    if not sentences:
        sentences = [merged]
    return enforce_line_limit("\n".join(sentences), MAX_SUMMARY_LINES)


def llm_summary(title: str, description: str, content: str) -> str:
    prompt = (
        f"다음 뉴스 내용을 한국어로 문맥 보존 요약해줘. 최대 {MAX_SUMMARY_LINES}줄.\n"
        "핵심 배경/영향/시사점을 사실 중심으로 포함.\n\n"
        f"제목: {title}\n"
        f"설명: {description}\n"
        f"본문: {content}\n"
    )
    payload = {
        "model": OPENAI_MODEL,
        "input": prompt,
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        out = json.loads(resp.read().decode("utf-8", errors="replace"))
    txt = out.get("output_text", "").strip()
    return enforce_line_limit(txt, MAX_SUMMARY_LINES) if txt else local_summary(title, description, content)


def summarize(title: str, description: str, content: str) -> str:
    if OPENAI_API_KEY:
        try:
            return llm_summary(title, description, content)
        except Exception:
            return local_summary(title, description, content)
    return local_summary(title, description, content)


def http_json(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": "news-archive-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read().decode("utf-8", errors="replace")
    return json.loads(data)


def fetch_news(from_date: str, to_date: str, query: str, page_size: int = 12):
    params = {
        "q": query,
        "language": "ko",
        "sortBy": "publishedAt",
        "from": from_date,
        "to": to_date,
        "pageSize": page_size,
        "apiKey": NEWSAPI_KEY,
    }
    url = "https://newsapi.org/v2/everything?" + urllib.parse.urlencode(params)
    data = http_json(url)
    if data.get("status") != "ok":
        raise RuntimeError(f"NewsAPI error: {data}")
    return data.get("articles", [])


def make_id(url: str, title: str, published_at: str) -> str:
    raw = f"{url}|{title}|{published_at}".encode("utf-8", errors="ignore")
    return hashlib.sha1(raw).hexdigest()[:16]


def load_existing_ids(path: str):
    ids = set()
    if not os.path.exists(path):
        return ids
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            rid = str(row.get("id", "")).strip()
            if rid:
                ids.add(rid)
    return ids


def append_entries(path: str, entries):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = load_existing_ids(path)
    added = 0
    with open(path, "a", encoding="utf-8") as f:
        for row in entries:
            rid = row.get("id", "")
            if not rid or rid in existing:
                continue
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            existing.add(rid)
            added += 1
    return added


def unique_articles(articles, limit: int):
    out = []
    seen = set()
    for a in articles:
        title = clean_text(a.get("title", ""))
        url = (a.get("url") or "").strip()
        if not title or not url:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
        if len(out) >= limit:
            break
    return out


def main() -> int:
    if not NEWSAPI_KEY:
        return fail("NEWSAPI_KEY is required")

    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(TIMEZONE)
    except Exception:
        tz = dt.timezone(dt.timedelta(hours=9))

    now = dt.datetime.now(tz)
    from_date = (now - dt.timedelta(days=1)).date().isoformat()
    to_date = now.date().isoformat()

    it_query = "(AI OR 반도체 OR 클라우드 OR 빅테크 OR IT OR 소프트웨어)"
    job_query = "(취업 OR 채용 OR 고용 OR 노동시장 OR 실업)"

    try:
        it_articles = unique_articles(fetch_news(from_date, to_date, it_query), ITEM_LIMIT_PER_CATEGORY)
        job_articles = unique_articles(fetch_news(from_date, to_date, job_query), ITEM_LIMIT_PER_CATEGORY)
    except Exception as e:
        return fail(str(e))

    entries = []
    for category, arr in [("IT", it_articles), ("취업", job_articles)]:
        for a in arr:
            title = clean_text(a.get("title", ""))
            url = (a.get("url") or "").strip()
            desc = clean_text(a.get("description", ""))
            content = clean_text(a.get("content", ""))
            published = clean_text(a.get("publishedAt", ""))
            body = content or desc
            summary = summarize(title, desc, body)
            entries.append(
                {
                    "id": make_id(url, title, published),
                    "title": title,
                    "summary": summary,
                    "body": body,
                    "url": url,
                    "category": category,
                    "published_at": published,
                    "archived_at": now.isoformat(),
                    "source": "NewsAPI",
                }
            )

    added = append_entries(ARCHIVE_PATH, entries)
    print(
        f"OK: archive={ARCHIVE_PATH}, added={added}, "
        f"it_candidates={len(it_articles)}, job_candidates={len(job_articles)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
