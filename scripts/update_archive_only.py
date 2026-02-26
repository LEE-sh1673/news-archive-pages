#!/usr/bin/env python3
import datetime as dt
import html
import hashlib
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from html.parser import HTMLParser

NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Seoul")
MAX_SUMMARY_LINES = max(1, int(os.environ.get("MAX_SUMMARY_LINES", "15")))
ARCHIVE_PATH = os.environ.get("ARCHIVE_PATH", "data/news_archive.jsonl")
ITEM_LIMIT_PER_CATEGORY = max(1, int(os.environ.get("ITEM_LIMIT_PER_CATEGORY", "8")))
MOJIBAKE_MARKERS = ("Ã", "Â", "â€™", "â€œ", "â€", "ï¿½", "\ufffd")
MAX_SOURCE_CHARS = max(1000, int(os.environ.get("MAX_SOURCE_CHARS", "16000")))


def fail(msg: str) -> int:
    print(f"ERROR: {msg}", file=sys.stderr)
    return 1


def clean_text(s: str) -> str:
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"\+\d+\s*chars", "", s, flags=re.IGNORECASE)
    if any(m in s for m in MOJIBAKE_MARKERS):
        try:
            s = s.encode("latin-1", errors="ignore").decode("utf-8", errors="ignore")
        except Exception:
            pass
    s = re.sub(r"(?is)Related\s+[A-Za-z].*$", "", s).strip()
    s = re.sub(r"(?is)\bFacebook\s+Twitter\s+LinkedIn.*$", "", s).strip()
    s = re.sub(r"(?is)\bLike this:\s*Like Loading\.\.\..*$", "", s).strip()
    s = re.sub(r"(?is)관련 기사 더 보기.*$", "", s).strip()
    s = re.sub(r"(?is)Loading Comments\.\.\..*$", "", s).strip()
    s = re.sub(r"(?is)You must be logged in to post a comment\..*$", "", s).strip()
    s = re.sub(r"(?is)%d bloggers like this:.*$", "", s).strip()
    s = re.sub(r"(?is)←.*$", "", s).strip()
    s = re.sub(r"(?is)→.*$", "", s).strip()
    return s.strip(" -")


def enforce_line_limit(text: str, limit: int) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines[:limit])


def split_long_text(text: str, chunk_size: int = 140):
    text = clean_text(text)
    if len(text) <= chunk_size:
        return [text]
    out = []
    s = text
    while len(s) > chunk_size:
        cut = s[:chunk_size]
        pos = cut.rfind(" ")
        if pos < 50:
            pos = chunk_size
        out.append(s[:pos].strip())
        s = s[pos:].strip()
    if s:
        out.append(s)
    return out


def bulletize_lines(lines):
    out = []
    for ln in lines:
        ln = clean_text(ln)
        if not ln:
            continue
        out.append(f"- {ln}")
    return out


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

    bullets = []
    for s in sentences:
        if len(bullets) >= MAX_SUMMARY_LINES:
            break
        chunks = split_long_text(s, chunk_size=140)
        if len(chunks) == 1:
            bullets.append(f"- {chunks[0]}")
        else:
            bullets.append(f"- {chunks[0]}")
            for sub in chunks[1:]:
                if len(bullets) >= MAX_SUMMARY_LINES:
                    break
                bullets.append(f"  - {sub}")
    return enforce_line_limit("\n".join(bullets), MAX_SUMMARY_LINES)


def normalize_bullet_output(text: str) -> str:
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    # If model returned plain paragraphs, convert to bullet lines.
    if not any(ln.lstrip().startswith("-") for ln in lines):
        lines = bulletize_lines(lines)
    else:
        fixed = []
        for ln in lines:
            stripped = ln.lstrip()
            indent = "  " if ln.startswith(("  -", "\t-")) else ""
            if stripped.startswith("-"):
                fixed.append(f"{indent}- {stripped.lstrip('-').strip()}")
            else:
                fixed.append(f"{indent}- {stripped}")
        lines = fixed
    return enforce_line_limit("\n".join(lines), MAX_SUMMARY_LINES)


def llm_summary(title: str, description: str, content: str) -> str:
    prompt = (
        f"다음 뉴스 본문을 한국어 불릿 리스트로 요약해줘.\n"
        f"- 전체 출력은 최대 {MAX_SUMMARY_LINES}줄\n"
        "- 각 불릿은 1~2줄 내로 유지\n"
        "- 본문이 길면 하위 불릿(두 칸 들여쓰기 + '-') 사용\n"
        "- 과장/추측 없이 사실 중심, 전체 흐름이 드러나게 정리\n"
        "- 출력은 불릿만 작성 (서론/결론 문장 금지)\n\n"
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
    return normalize_bullet_output(txt) if txt else local_summary(title, description, content)


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


def http_text(url: str, timeout: int = 20):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
            )
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except Exception:
        return raw.decode("utf-8", errors="replace")


class MainBodyExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.skip_depth = 0
        self.candidates = []
        self.skip_tags = {"script", "style", "noscript", "iframe", "svg"}

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        attrs_map = {k.lower(): (v or "") for k, v in attrs}
        node = {"tag": t, "attrs": attrs_map, "parts": []}
        self.stack.append(node)
        if t in self.skip_tags:
            self.skip_depth += 1

    def handle_endtag(self, tag):
        t = tag.lower()
        if not self.stack:
            return
        node = self.stack.pop()
        text = clean_text(" ".join(node["parts"]))
        if text:
            priority = self._priority(node["attrs"])
            if priority is not None:
                self.candidates.append((priority, len(text), text))
            if self.stack:
                self.stack[-1]["parts"].append(text)
        if t in self.skip_tags and self.skip_depth > 0:
            self.skip_depth -= 1

    def handle_data(self, data):
        if self.skip_depth > 0 or not self.stack:
            return
        txt = clean_text(data)
        if txt:
            self.stack[-1]["parts"].append(txt)

    @staticmethod
    def _priority(attrs):
        itemprop = attrs.get("itemprop", "").strip().lower()
        if itemprop == "articlebody":
            return 0
        idv = attrs.get("id", "").lower()
        cls = attrs.get("class", "").lower()
        merged = f"{idv} {cls}"
        if re.search(r"(contents?|body|article[-_ ]?body|post[-_ ]?body|entry[-_ ]?content)", merged):
            return 1
        return None


def extract_main_body(html_doc: str) -> str:
    parser = MainBodyExtractor()
    parser.feed(html_doc)
    if not parser.candidates:
        return ""
    parser.candidates.sort(key=lambda x: (x[0], -x[1]))
    body = parser.candidates[0][2]
    return clean_text(body)[:MAX_SOURCE_CHARS]


def fetch_article_body(url: str) -> str:
    if not url:
        return ""
    try:
        html_doc = http_text(url, timeout=20)
        return extract_main_body(html_doc)
    except Exception:
        return ""


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
            extracted = fetch_article_body(url)
            published = clean_text(a.get("publishedAt", ""))
            body = extracted or content or desc
            summary = summarize(title, desc, body)
            entries.append(
                {
                    "id": make_id(url, title, published),
                    "title": title,
                    "summary": summary,
                    "body": body,
                    "url": url,
                    "category": category,
                    "article_published_at": published,
                    "fetched_at": now.isoformat(),
                    # legacy fields for backward compatibility
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
