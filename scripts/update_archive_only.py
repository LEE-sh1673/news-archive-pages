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
from urllib.error import HTTPError, URLError
from html.parser import HTMLParser

NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Seoul")
MAX_SUMMARY_LINES = max(1, int(os.environ.get("MAX_SUMMARY_LINES", "15")))
ARCHIVE_PATH = os.environ.get("ARCHIVE_PATH", "data/news_archive.jsonl")
ITEM_LIMIT_PER_CATEGORY = max(1, int(os.environ.get("ITEM_LIMIT_PER_CATEGORY", "40")))
NEWS_PAGE_SIZE = max(20, min(100, int(os.environ.get("NEWS_PAGE_SIZE", "100"))))
MAX_NEWS_PAGES = max(1, int(os.environ.get("MAX_NEWS_PAGES", "3")))
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


def normalize_inner_text(s: str) -> str:
    if not s:
        return ""
    s = html.unescape(s).replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    s = re.sub(r"[ \t\f\v]+", " ", s)
    lines = [ln.strip() for ln in s.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def _context_terms(title: str, description: str):
    base = f"{title} {description}".lower()
    terms = re.findall(r"[0-9a-zA-Z가-힣]{2,}", base)
    stop = {
        "속보",
        "단독",
        "기자",
        "뉴스",
        "관련",
        "업데이트",
        "today",
        "news",
        "the",
        "and",
    }
    return {t for t in terms if t not in stop}


def minimal_context_filter(title: str, description: str, body_text: str) -> str:
    if not body_text:
        return ""
    source = normalize_inner_text(body_text)
    if not source:
        return ""

    terms = _context_terms(title, description)
    # Only remove obviously unrelated UI/utility lines when they don't match title context.
    junk_hint = re.compile(
        r"(?i)(login|sign in|sign up|newsletter|subscribe|cookie|privacy|terms|menu|home|copyright|all rights reserved)"
        r"|(\b댓글\b|\b공유\b|\b좋아요\b|\b본문보기\b|\b기사원문\b)"
    )
    kept = []
    for ln in source.splitlines():
        line = clean_text(ln)
        if not line:
            continue
        if len(line) <= 2:
            continue
        if not junk_hint.search(line):
            kept.append(line)
            continue
        line_terms = set(re.findall(r"[0-9a-zA-Z가-힣]{2,}", line.lower()))
        if terms and line_terms.intersection(terms):
            kept.append(line)

    if not kept:
        kept = [clean_text(ln) for ln in source.splitlines() if clean_text(ln)]
    return "\n".join(kept)


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
    merged = " ".join(part for part in [clean_text(title), clean_text(description), clean_text(content)] if part)
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
        sentence = clean_text(s)
        if not sentence:
            continue
        if sentence[-1] not in ".!?。다":
            sentence = sentence + "."
        bullets.append(f"- {sentence}")
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
            stripped = clean_text(ln.lstrip("- ").strip())
            if not stripped:
                continue
            if stripped[-1] not in ".!?。다":
                stripped = stripped + "."
            fixed.append(f"- {stripped}")
        lines = fixed
    return enforce_line_limit("\n".join(lines), MAX_SUMMARY_LINES)


def llm_core_summary(title: str, description: str, content: str) -> str:
    prompt = (
        "아래 뉴스 본문(document.body.innerText 원문 기반, 최소 필터링 적용)을 한국어로 충실히 요약해줘.\n"
        "- 기사 핵심 사실/배경/영향 중심\n"
        "- 제목/설명 맥락과 관련이 약한 UI/유틸 텍스트는 무시\n"
        "- 6~10문장 내로 작성\n\n"
        f"제목: {title}\n"
        f"설명: {description}\n"
        f"본문: {content}\n"
    )
    payload = {"model": OPENAI_MODEL, "input": prompt, "temperature": 0.2}
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=40) as resp:
        out = json.loads(resp.read().decode("utf-8", errors="replace"))
    txt = clean_text(out.get("output_text", "").strip())
    return txt


def llm_format_bullets(title: str, core_summary: str) -> str:
    prompt = (
        "아래 요약문을 한국어 불릿 리스트로 다시 정리해줘.\n"
        f"- 전체 출력은 최대 {MAX_SUMMARY_LINES}줄\n"
        "- 각 불릿은 반드시 1줄만 사용\n"
        "- 각 불릿은 서로 독립된 완전한 요약 문장이어야 함\n"
        "- 제목과 직접 연관된 핵심 내용만 포함\n"
        "- 과장/추측 없이 사실 중심으로 정리\n"
        "- 출력은 불릿만 작성 (서론/결론 문장 금지)\n\n"
        f"제목: {title}\n"
        f"요약문: {core_summary}\n"
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
    return normalize_bullet_output(txt)


def summarize(title: str, description: str, content: str) -> str:
    # Pipeline: document.body.innerText -> minimal context filter -> first summary -> second bullet formatting
    filtered_source = minimal_context_filter(title, description, content)
    if not filtered_source:
        return local_summary(title, description, content)
    if OPENAI_API_KEY:
        try:
            core = llm_core_summary(title, description, filtered_source)
            if not core:
                return local_summary(title, description, filtered_source)
            bullets = llm_format_bullets(title, core)
            if bullets:
                return bullets
            return local_summary(title, description, core)
        except Exception:
            return local_summary(title, description, filtered_source)
    return local_summary(title, description, filtered_source)


def format_crawled_body(title: str, description: str, body_text: str) -> str:
    filtered = minimal_context_filter(title, description, body_text)
    if not filtered:
        return ""
    lines = [clean_text(ln) for ln in filtered.splitlines() if clean_text(ln)]
    dedup = []
    seen = set()
    for ln in lines:
        key = ln.lower()
        if key in seen:
            continue
        seen.add(key)
        dedup.append(ln)
    return "\n".join(f"- {ln}" for ln in dedup)


def _has_readable_content(text: str) -> bool:
    if not text:
        return False
    return bool(re.search(r"[A-Za-z0-9가-힣]", text))


def _fallback_ai_summary(title: str, text: str) -> str:
    clean = clean_text(text)
    if not _has_readable_content(clean):
        return "요약할 수 없는 내용입니다"
    sents = re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s+|(?<=요\.)\s+", clean)
    sents = [clean_text(s) for s in sents if clean_text(s)]
    key = sents[0] if sents else clean
    more = sents[1] if len(sents) > 1 else key
    t = clean_text(title)[:5] if clean_text(title) else "기사요약"
    return (
        f"제목: {t}\n"
        f"핵심 요약: {key}\n"
        f"- 주요 포인트: {key}\n"
        f"- 주요 포인트: {more}\n"
        f"- 주요 포인트: {sents[2] if len(sents) > 2 else clean}"
    )


def _normalize_ai_summary_text(text: str) -> str:
    if not text:
        return ""
    lines = [clean_text(ln) for ln in str(text).splitlines() if clean_text(ln)]
    return "\n".join(lines)


def build_ai_summary(title: str, description: str, body_text: str) -> str:
    source = minimal_context_filter(title, description, body_text)
    if not _has_readable_content(source):
        return "요약할 수 없는 내용입니다"

    if OPENAI_API_KEY:
        try:
            prompt = (
                "제공된 기사 원문 내용을 바탕으로 핵심만 요약해줘. "
                "이때 `핵심 사실이 정리되었습니다.`와 같은 내용 말고, "
                "기사의 원문 내용을 대상으로 핵심 내용만 정리해줘.\n\n"
                "요약 기준:\n"
                "제목: 기사 내용을 포괄하는 제목 (5글자 내외)\n"
                "핵심 요약 (Key Takeaway): 1~2문장으로 전체 내용 정리\n"
                "주요 포인트 (Bullet points): 가장 중요한 내용 3가지\n\n"
                "출력 형식:\n"
                "제목: ...\n"
                "핵심 요약: ...\n"
                "- 주요 포인트: ...\n"
                "- 주요 포인트: ...\n"
                "- 주요 포인트: ...\n\n"
                f"기사 제목: {title}\n"
                f"기사 설명: {description}\n"
                f"기사 원문: {source}\n"
            )
            payload = {"model": OPENAI_MODEL, "input": prompt, "temperature": 0.2}
            req = urllib.request.Request(
                "https://api.openai.com/v1/responses",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                out = json.loads(resp.read().decode("utf-8", errors="replace"))
            summary = _normalize_ai_summary_text(out.get("output_text", ""))
            return summary if _has_readable_content(summary) else "요약할 수 없는 내용입니다"
        except Exception:
            pass

    fallback = _fallback_ai_summary(title, source)
    return fallback if _has_readable_content(fallback) else "요약할 수 없는 내용입니다"


def http_json(url: str, timeout: int = 20):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "X-Api-Key": NEWSAPI_KEY,
        },
    )
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
        charset = resp.headers.get_content_charset()

    candidates = []
    if charset:
        candidates.append(charset)
    candidates.extend(["utf-8", "cp949", "euc-kr"])

    for enc in candidates:
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")


NOISE_PATTERNS = [
    re.compile(r"(?i)\b(login|sign in|sign up|newsletter|subscribe|cookie|privacy|terms|copyright)\b"),
    re.compile(r"(?i)\b(all rights reserved|advertisement|sponsored)\b"),
    re.compile(r"(?i)\b(facebook|twitter|instagram|linkedin|youtube|kakaotalk|share)\b"),
    re.compile(r"(?im)^\s*(기자|리포터|Reporter|By)\s*[:：]"),
    re.compile(r"(?im)\b[\w\.-]+@[\w\.-]+\.\w+\b"),
    re.compile(r"(?im)\b\d{2,4}[-.\s]?\d{3,4}[-.\s]?\d{4}\b"),
    re.compile(r"(?i)\b(댓글|공유|좋아요|기사원문|원문보기|관련 기사|관련기사)\b"),
]
JP_RE = re.compile(r"[\u3040-\u30ff\uff66-\uff9f]")


def filter_article_paragraphs(paragraphs):
    kept = []
    for p in paragraphs:
        line = clean_text(p)
        if not line:
            continue
        if JP_RE.search(line):
            continue
        if len(line) < 10:
            continue
        if any(rx.search(line) for rx in NOISE_PATTERNS):
            continue
        kept.append(line)
    if not kept:
        kept = [clean_text(p) for p in paragraphs if clean_text(p)]
    return kept


class PriorityPExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip_depth = 0
        self.skip_tags = {"script", "style", "noscript", "iframe", "svg"}
        self.pending_article_comment = False
        self.container_stack = []
        self.current_p_parts = None
        self.candidates_step1 = []
        self.candidates_step2 = []

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in self.skip_tags:
            self.skip_depth += 1
        if self.skip_depth > 0:
            return

        attrs_map = {k.lower(): (v or "") for k, v in attrs}
        step = 0
        if self._is_step1_target(t, attrs_map):
            step = 1
        elif t in {"section", "div"} and self.pending_article_comment:
            step = 2

        if step:
            self.container_stack.append({"tag": t, "step": step, "paragraphs": [], "text_parts": []})
            if step == 2:
                self.pending_article_comment = False
        else:
            self.container_stack.append({"tag": t, "step": 0, "paragraphs": None, "text_parts": None})

        if t == "p" and self._current_target() is not None:
            self.current_p_parts = []

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in self.skip_tags and self.skip_depth > 0:
            self.skip_depth -= 1
            return
        if self.skip_depth > 0:
            return

        if t == "p" and self.current_p_parts is not None:
            para = normalize_inner_text(" ".join(self.current_p_parts))
            target = self._current_target()
            if para and target is not None:
                target["paragraphs"].append(para)
            self.current_p_parts = None

        if not self.container_stack:
            return
        node = self.container_stack.pop()
        if node["step"] in {1, 2}:
            raw_lines = [clean_text(x) for x in normalize_inner_text(" ".join(node["text_parts"])).splitlines() if clean_text(x)]
            base = node["paragraphs"] if node["paragraphs"] else raw_lines
            paragraphs = filter_article_paragraphs(base)
            if paragraphs:
                text = "\n".join(paragraphs)[:MAX_SOURCE_CHARS]
                candidate = (len(text), text)
                if node["step"] == 1:
                    self.candidates_step1.append(candidate)
                else:
                    self.candidates_step2.append(candidate)

    def handle_data(self, data):
        if self.skip_depth > 0:
            return
        target = self._current_target()
        if target is not None:
            raw = normalize_inner_text(data)
            if raw:
                target["text_parts"].append(raw)
        if self.current_p_parts is None:
            return
        txt = clean_text(data)
        if txt:
            self.current_p_parts.append(txt)

    def handle_comment(self, data):
        txt = clean_text(data).replace(" ", "")
        if "기사본문" in txt:
            self.pending_article_comment = True

    def _current_target(self):
        for node in reversed(self.container_stack):
            if node["step"] in {1, 2}:
                return node
        return None

    @staticmethod
    def _is_step1_target(tag, attrs):
        if tag not in {"div", "article"}:
            return False
        itemprop = attrs.get("itemprop", "").strip().lower()
        idv = attrs.get("id", "").strip().lower()
        return itemprop == "articlebody" or idv == "articlebody"


def extract_priority_p_text(html_doc: str) -> str:
    p = PriorityPExtractor()
    p.feed(html_doc)
    if p.candidates_step1:
        p.candidates_step1.sort(key=lambda x: -x[0])
        return p.candidates_step1[0][1]
    if p.candidates_step2:
        p.candidates_step2.sort(key=lambda x: -x[0])
        return p.candidates_step2[0][1]
    return ""


class BodyPExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_body = False
        self.skip_depth = 0
        self.skip_tags = {"script", "style", "noscript", "iframe", "svg"}
        self.in_p = False
        self.current_p_parts = []
        self.paragraphs = []
        self.body_text_parts = []

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t == "body":
            self.in_body = True
            return
        if not self.in_body:
            return
        if t in self.skip_tags:
            self.skip_depth += 1
            return
        if self.skip_depth > 0:
            return
        if t == "p":
            self.in_p = True
            self.current_p_parts = []

    def handle_endtag(self, tag):
        t = tag.lower()
        if t == "body":
            self.in_body = False
            return
        if not self.in_body:
            return
        if t in self.skip_tags and self.skip_depth > 0:
            self.skip_depth -= 1
            return
        if self.skip_depth > 0:
            return
        if t == "p" and self.in_p:
            para = normalize_inner_text(" ".join(self.current_p_parts))
            if para:
                self.paragraphs.append(para)
            self.current_p_parts = []
            self.in_p = False

    def handle_data(self, data):
        if not self.in_body or self.skip_depth > 0:
            return
        raw = normalize_inner_text(data)
        if raw:
            self.body_text_parts.append(raw)
        if not self.in_p:
            return
        txt = clean_text(data)
        if txt:
            self.current_p_parts.append(txt)


def extract_body_p_text(html_doc: str) -> str:
    p = BodyPExtractor()
    p.feed(html_doc)
    lines = filter_article_paragraphs(p.paragraphs)
    return "\n".join(lines)[:MAX_SOURCE_CHARS]


class ArticleBodyInnerTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip_depth = 0
        self.skip_tags = {"script", "style", "noscript", "iframe", "svg", "img"}
        self.stack = []
        self.candidates = []

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        attrs_map = {k.lower(): (v or "") for k, v in attrs}
        if t in self.skip_tags:
            self.skip_depth += 1
        is_target = False
        if t in {"div", "article"}:
            itemprop = attrs_map.get("itemprop", "").strip().lower()
            idv = attrs_map.get("id", "").strip().lower()
            is_target = itemprop == "articlebody" or idv == "articlebody"
        self.stack.append({"tag": t, "is_target": is_target, "parts": []})

    def handle_endtag(self, tag):
        t = tag.lower()
        if not self.stack:
            return
        node = self.stack.pop()
        text = normalize_inner_text(" ".join(node["parts"]))
        if node["is_target"] and text:
            lines = filter_article_paragraphs([clean_text(x) for x in text.splitlines()])
            if lines:
                val = "\n".join(lines)[:MAX_SOURCE_CHARS]
                self.candidates.append((len(val), val))
        if self.stack and text:
            self.stack[-1]["parts"].append(text)
        if t in self.skip_tags and self.skip_depth > 0:
            self.skip_depth -= 1

    def handle_data(self, data):
        if self.skip_depth > 0 or not self.stack:
            return
        txt = normalize_inner_text(data)
        if txt:
            self.stack[-1]["parts"].append(txt)


def extract_articlebody_inner_text(html_doc: str) -> str:
    p = ArticleBodyInnerTextExtractor()
    p.feed(html_doc)
    if not p.candidates:
        return ""
    p.candidates.sort(key=lambda x: -x[0])
    return p.candidates[0][1]


def fetch_article_body(url: str) -> str:
    if not url:
        return ""
    try:
        html_doc = http_text(url, timeout=20)
        # 1) <div|article itemprop="articleBody" or id="articleBody">
        # 2) section|div preceded by <!-- 기사 본문 -->
        # 3) fallback to document.body innerText p-only, then articleBody innerText
        priority = extract_priority_p_text(html_doc)
        if priority:
            return priority
        body_p = extract_body_p_text(html_doc)
        if body_p:
            return body_p
        return extract_articlebody_inner_text(html_doc)
    except Exception:
        return ""


def fetch_news(from_date: str, to_date: str, query: str):
    all_articles = []
    for page in range(1, MAX_NEWS_PAGES + 1):
        params = {
            "q": query,
            "language": "ko",
            "sortBy": "publishedAt",
            "from": from_date,
            "to": to_date,
            "pageSize": NEWS_PAGE_SIZE,
            "page": page,
            # Keep query param for compatibility with endpoints that don't honor X-Api-Key header.
            "apiKey": NEWSAPI_KEY,
        }
        url = "https://newsapi.org/v2/everything?" + urllib.parse.urlencode(params)
        data = http_json(url)
        if data.get("status") != "ok":
            raise RuntimeError(f"NewsAPI error: {data}")
        articles = data.get("articles", [])
        if not articles:
            break
        all_articles.extend(articles)
        if len(articles) < NEWS_PAGE_SIZE:
            break
    return all_articles


def safe_fetch_news(from_date: str, to_date: str, query: str, label: str):
    last_err = None
    for attempt in range(1, 4):
        try:
            return fetch_news(from_date, to_date, query)
        except HTTPError as e:
            last_err = e
            # 426 often indicates policy/transport constraints from provider edge.
            # Retry with backoff; if still failing, degrade gracefully.
            print(f"WARN: {label} fetch HTTPError {e.code} on attempt {attempt}/3: {e}", file=sys.stderr)
        except URLError as e:
            last_err = e
            print(f"WARN: {label} fetch URLError on attempt {attempt}/3: {e}", file=sys.stderr)
        except Exception as e:
            last_err = e
            print(f"WARN: {label} fetch error on attempt {attempt}/3: {e}", file=sys.stderr)

    print(f"WARN: {label} fetch failed after retries: {last_err}", file=sys.stderr)
    return []


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

    it_articles = unique_articles(safe_fetch_news(from_date, to_date, it_query, "IT"), ITEM_LIMIT_PER_CATEGORY)
    job_articles = unique_articles(safe_fetch_news(from_date, to_date, job_query, "JOB"), ITEM_LIMIT_PER_CATEGORY)

    if not it_articles and not job_articles:
        # Do not fail the workflow on transient upstream issues.
        print("WARN: no articles fetched for both categories; keeping existing archive.", file=sys.stderr)
        return 0

    entries = []
    for category, arr in [("IT", it_articles), ("취업", job_articles)]:
        for a in arr:
            title = clean_text(a.get("title", ""))
            url = (a.get("url") or "").strip()
            desc = clean_text(a.get("description", ""))
            content = clean_text(a.get("content", ""))
            extracted = fetch_article_body(url)
            published = clean_text(a.get("publishedAt", ""))
            body_raw = extracted or content or desc
            # AS-IS: crawl -> noise removal -> summarize -> formatting
            # summary = summarize(title, desc, body_raw)
            # TO-BE: crawl -> noise removal -> formatting
            formatted_body = format_crawled_body(title, desc, body_raw)
            ai_summary = build_ai_summary(title, desc, body_raw)
            summary = formatted_body or clean_text(desc or body_raw)
            entries.append(
                {
                    "id": make_id(url, title, published),
                    "title": title,
                    "summary": summary,
                    # Detail view body should show formatted crawled content.
                    "body": formatted_body or summary,
                    "ai_summary": ai_summary,
                    "scraped_body": body_raw,
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
