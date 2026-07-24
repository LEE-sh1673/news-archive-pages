#!/usr/bin/env python3
import base64
import datetime as dt
import html
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError
from html.parser import HTMLParser

from build_data import (
    build_explanation_variants_from_blueprint,
    build_explanation_variants_from_summary,
    build_summary_blueprint_from_ai_summary,
    extract_keywords,
    filter_lines_by_title_relevance,
)

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except Exception:
    requests = None
    HTTPAdapter = None
    Retry = None

NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
PREFERRED_LLM_BACKEND = os.environ.get("PREFERRED_LLM_BACKEND", "codex").strip().lower()
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Seoul")
MAX_SUMMARY_LINES = max(1, int(os.environ.get("MAX_SUMMARY_LINES", "15")))
ARCHIVE_PATH = os.environ.get("ARCHIVE_PATH", "data/news_archive.jsonl")
ARCHIVE_SPLIT_MAX_BYTES = max(
    1024 * 1024,
    int(os.environ.get("ARCHIVE_SPLIT_MAX_BYTES", str(47 * 1024 * 1024))),
)
EXPLANATION_BACKFILL_LIMIT = max(0, int(os.environ.get("EXPLANATION_BACKFILL_LIMIT", "5")))
EXPLANATION_REFRESH_LIMIT = max(0, int(os.environ.get("EXPLANATION_REFRESH_LIMIT", "30")))
ITEM_LIMIT_PER_CATEGORY = max(1, int(os.environ.get("ITEM_LIMIT_PER_CATEGORY", "40")))
NEWS_PAGE_SIZE = max(20, min(100, int(os.environ.get("NEWS_PAGE_SIZE", "100"))))
MAX_NEWS_PAGES = max(1, int(os.environ.get("MAX_NEWS_PAGES", "3")))
MOJIBAKE_MARKERS = ("Ã", "Â", "â€™", "â€œ", "â€", "ï¿½", "\ufffd")
MAX_SOURCE_CHARS = max(1000, int(os.environ.get("MAX_SOURCE_CHARS", "16000")))
ROOT_DIR = Path(__file__).resolve().parents[1]
THUMBNAIL_DIR = Path(os.environ.get("THUMBNAIL_DIR", str(ROOT_DIR / "docs" / "assets" / "thumbs"))).expanduser()
THUMBNAIL_MODEL = os.environ.get("THUMBNAIL_MODEL", "gpt-image-1")
THUMBNAIL_SIZE = os.environ.get("THUMBNAIL_SIZE", "1024x1024")
PLAYWRIGHT_TIMEOUT_MS = max(5000, int(os.environ.get("PLAYWRIGHT_TIMEOUT_MS", "60000")))
CODEX_TIMEOUT_SECONDS = max(15, int(os.environ.get("CODEX_TIMEOUT_SECONDS", "45")))
MIDDLE_SCHOOL_SYSTEM_PROMPT_PATH = ROOT_DIR / "prompts" / "middle_school_system_prompt.md"
MIDDLE_SCHOOL_FEWSHOT_PATH = ROOT_DIR / "prompts" / "middle_school_fewshot.md"
NEWSAPI_ENDPOINT = "https://newsapi.org/v2/everything"
_NEWSAPI_SESSION = None


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


def _simple_ui_noise_filter(lines):
    out = []
    for ln in lines:
        s = clean_text(ln)
        if not s:
            continue
        if any(rx.search(s) for rx in UI_NOISE_PATTERNS_SIMPLE):
            continue
        out.append(s)
    return out


def _codex_filter_unrelated_lines(title: str, text: str) -> str:
    if not title or not text:
        return ""
    prompt = (
        "아래 기사 본문 라인 목록에서 제목과 관련 없는 UI/광고/메뉴 문구만 제거해줘.\n"
        "- 원문 문장은 절대 재작성하지 말고, 관련 있는 라인만 그대로 남겨줘.\n"
        "- 출력은 원문 라인만 줄바꿈으로 반환.\n\n"
        f"제목: {title}\n"
        f"본문 라인들:\n{text}\n"
    )
    return run_codex_cli_summary(prompt)


def filter_scraped_body_text(title: str, body_text: str, url: str = "") -> str:
    source = normalize_inner_text(body_text)
    if not source:
        return ""
    lines = [ln for ln in source.splitlines() if ln.strip()]
    lines = _simple_ui_noise_filter(lines)
    lines = filter_lines_by_title_relevance(title, lines, url=url) or lines
    baseline = "\n".join(lines)[:MAX_SOURCE_CHARS]
    if not baseline:
        return ""

    # If Codex CLI is available, try title-context filtering first.
    codex_filtered = _codex_filter_unrelated_lines(title, baseline)
    if codex_filtered:
        final_lines = _simple_ui_noise_filter(codex_filtered.splitlines())
        final_lines = filter_lines_by_title_relevance(title, final_lines, url=url) or final_lines
        if final_lines:
            return "\n".join(final_lines)[:MAX_SOURCE_CHARS]
    return baseline


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

    prompt = (
        "아래 원문을 한국어 불릿 리스트로 다시 정리해줘.\n"
        "- 과장/추측 없이 사실 중심으로 정리\n"
        "- 출력은 불릿만 작성 (서론/결론 문장 금지)\n\n"
        f"원문: {merged}\n"
    )
    codex_out = run_codex_cli_summary(prompt)
    bullets = normalize_bullet_output(codex_out)
    if bullets:
        return enforce_line_limit(bullets, MAX_SUMMARY_LINES)

    sentences = re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s+|(?<=요\.)\s+", merged)
    sentences = [s.strip(" -") for s in sentences if s and s.strip(" -")]
    if not sentences:
        sentences = [merged]

    fallback = []
    for s in sentences:
        if len(fallback) >= MAX_SUMMARY_LINES:
            break
        sentence = clean_text(s)
        if not sentence:
            continue
        if sentence[-1] not in ".!?。다":
            sentence = sentence + "."
        fallback.append(f"- {sentence}")
    return enforce_line_limit("\n".join(fallback), MAX_SUMMARY_LINES)


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
    if PREFERRED_LLM_BACKEND == "codex":
        txt = run_codex_cli_summary(prompt)
        if txt:
            return txt
    if not OPENAI_API_KEY:
        return ""
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
    if PREFERRED_LLM_BACKEND == "codex":
        txt = run_codex_cli_summary(prompt)
        if txt:
            return normalize_bullet_output(txt)
    if not OPENAI_API_KEY:
        return ""
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


def _fallback_format_crawled_body(title: str, description: str, body_text: str) -> str:
    filtered = minimal_context_filter(title, description, body_text)
    if not filtered:
        return ""
    lines = [clean_text(ln) for ln in filtered.splitlines() if clean_text(ln)]
    return "\n".join(f"- {ln}" for ln in lines)


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


def _normalize_summary_sentence(text: str) -> str:
    s = clean_text(text).strip(" -")
    if not s:
        return ""
    if s[-1] not in ".!?。다":
        s = s + "."
    return s


def _normalize_compare_text(text: str) -> str:
    return re.sub(r"\s+", " ", clean_text(text)).strip().lower()


def _split_summary_sentences(text: str):
    chunks = re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s+|(?<=요\.)\s+", clean_text(text))
    out = []
    seen = set()
    for chunk in chunks:
        sentence = _normalize_summary_sentence(chunk)
        if not sentence:
            continue
        key = _normalize_compare_text(sentence)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(sentence)
    return out


def _parse_ai_summary(text: str) -> dict:
    title = ""
    takeaway = ""
    points = []
    for raw in str(text or "").splitlines():
        line = clean_text(raw)
        if not line:
            continue
        if line.startswith("제목:"):
            title = clean_text(line.replace("제목:", "", 1))
            continue
        if line.startswith("핵심 요약:"):
            takeaway = clean_text(line.replace("핵심 요약:", "", 1))
            continue
        if line.startswith("- 주요 포인트:"):
            point = clean_text(line.replace("- 주요 포인트:", "", 1))
            if point:
                points.append(point)
            continue
        if line.startswith("주요 포인트:"):
            point = clean_text(line.replace("주요 포인트:", "", 1))
            if point:
                points.append(point)
    deduped = []
    seen = set()
    for point in points:
        norm = _normalize_compare_text(point)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        deduped.append(_normalize_summary_sentence(point))
    return {
        "title": clean_text(title),
        "takeaway": _normalize_summary_sentence(takeaway),
        "points": deduped[:3],
    }


def _load_prompt_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _validate_article_type(category: str, article_type: str) -> str:
    broad = clean_text(category) or "일반"
    allowed = {"기업/실적", "정책/행정", "시장/금융", "기술/제품", "사회/일반"}
    sub = clean_text(article_type)
    if sub not in allowed:
        sub = "사회/일반"
    return f"{broad}/{sub}"


def _validate_flow_order(values) -> list[str]:
    flow = [clean_text(value) for value in (values or []) if clean_text(value)]
    while len(flow) < 3:
        flow.append(["배경/원인", "변화/대응", "영향/전망"][len(flow)])
    return flow[:3]


def _validate_summary_blueprint(payload, title: str, category: str) -> dict:
    if not isinstance(payload, dict):
        return {}
    clean_title = clean_text(payload.get("title", "")) or clean_text(title)
    takeaway = _normalize_summary_sentence(payload.get("takeaway", ""))
    key_points = [_normalize_summary_sentence(point) for point in payload.get("key_points", []) if _normalize_summary_sentence(point)]
    if not clean_title or not takeaway or len(key_points) < 3:
        return {}
    return {
        "title": clean_title,
        "takeaway": takeaway,
        "key_points": key_points[:3],
        "article_type": _validate_article_type(category, payload.get("article_type", "")),
        "flow_order": _validate_flow_order(payload.get("flow_order", [])),
    }


def _structured_summary_fallback(title: str, ai_summary: str, category: str) -> dict:
    return build_summary_blueprint_from_ai_summary(title, ai_summary, category=category)


def _looks_like_leading_source_lines(points, source: str) -> bool:
    source_lines = []
    for raw in str(source or "").splitlines():
        line = _normalize_compare_text(raw)
        if line:
            source_lines.append(line)
        if len(source_lines) >= 5:
            break
    if len(source_lines) < 3 or len(points) < 3:
        return False

    matches = 0
    for idx, point in enumerate(points[:3]):
        norm_point = _normalize_compare_text(point)
        if not norm_point:
            continue
        if idx < len(source_lines) and (
            norm_point == source_lines[idx]
            or norm_point in source_lines[idx]
            or source_lines[idx] in norm_point
        ):
            matches += 1
    return matches >= 2


def _is_valid_ai_summary(text: str, source: str) -> bool:
    parsed = _parse_ai_summary(text)
    if not parsed["title"] or not parsed["takeaway"] or len(parsed["points"]) < 3:
        return False
    if _looks_like_leading_source_lines(parsed["points"], source):
        return False
    return True


def _compose_ai_summary(title: str, takeaway: str, points) -> str:
    clean_title = clean_text(title) or "기사요약"
    clean_takeaway = _normalize_summary_sentence(takeaway)
    clean_points = []
    for point in points:
        sentence = _normalize_summary_sentence(point)
        if sentence:
            clean_points.append(sentence)
        if len(clean_points) >= 3:
            break
    if not clean_takeaway or len(clean_points) < 3:
        return ""
    return "\n".join(
        [
            f"제목: {clean_title[:12]}",
            f"핵심 요약: {clean_takeaway}",
            f"- 주요 포인트: {clean_points[0]}",
            f"- 주요 포인트: {clean_points[1]}",
            f"- 주요 포인트: {clean_points[2]}",
        ]
    )


def compose_ai_summary_from_blueprint(blueprint: dict) -> str:
    return _compose_ai_summary(
        blueprint.get("title", ""),
        blueprint.get("takeaway", ""),
        blueprint.get("key_points", []),
    )


def build_core_summary(title: str, description: str, body_text: str) -> str:
    source = minimal_context_filter(title, description, body_text)
    if not source:
        return ""
    if OPENAI_API_KEY:
        try:
            return llm_core_summary(title, description, source)
        except Exception:
            pass

    prompt = (
        "아래 기사 원문을 한국어로 4~6문장으로 요약해줘.\n"
        "- 원문 첫 문장을 그대로 복사하지 말고 핵심 사실을 재구성해서 작성\n"
        "- 기사 핵심 사실, 배경, 영향 중심으로만 정리\n"
        "- 군더더기 없이 평서문만 출력\n\n"
        f"기사 제목: {title}\n"
        f"기사 설명: {description}\n"
        f"기사 원문: {source}\n"
    )
    summary = run_codex_cli_summary(prompt)
    if _has_readable_content(summary):
        return clean_text(summary)

    bullet_summary = summarize(title, description, source)
    if _has_readable_content(bullet_summary):
        lines = [clean_text(ln.lstrip("- ").strip()) for ln in bullet_summary.splitlines() if clean_text(ln)]
        return " ".join(lines[:4]).strip()
    return ""


def build_summary_blueprint(title: str, description: str, body_text: str, category: str) -> dict:
    source = minimal_context_filter(title, description, body_text)
    if not _has_readable_content(source):
        return {}

    prompt = (
        "아래 기사 원문을 읽고 구조화 요약 JSON을 작성해줘.\n"
        "- 출력 키는 title, takeaway, key_points, article_type, flow_order 를 사용\n"
        "- article_type 은 반드시 기업/실적, 정책/행정, 시장/금융, 기술/제품, 사회/일반 중 하나만 선택\n"
        "- 기사의 대분류 카테고리는 참고 정보이며, article_type 은 본문을 읽고 판단\n"
        "- takeaway 는 원인과 결과가 보이는 1~2문장\n"
        "- key_points 는 정확히 3개, 서로 다른 핵심 사실\n"
        "- flow_order 도 정확히 3개이며 key_points 의 흐름을 짧게 설명\n"
        "- key_points 와 flow_order 는 기사 유형에 맞게 자연스럽게 잡되, 억지로 동일 패턴을 강요하지 말 것\n"
        "- 원문 첫 3문장을 그대로 베끼지 말고 재구성\n"
        "- 없는 숫자나 전망을 새로 만들지 말 것\n"
        "- JSON만 반환\n\n"
        f"기사 대분류 카테고리: {category}\n"
        f"기사 제목: {title}\n"
        f"기사 설명: {description}\n"
        f"기사 원문: {source}\n"
    )

    if PREFERRED_LLM_BACKEND == "codex":
        payload = _extract_json_object(run_codex_cli_summary(prompt))
        valid = _validate_summary_blueprint(payload, title, category)
        if valid:
            return valid
    if OPENAI_API_KEY:
        payload = _openai_json_response(prompt)
        valid = _validate_summary_blueprint(payload, title, category)
        if valid:
            return valid

    core_summary = build_core_summary(title, description, source)
    if _has_readable_content(core_summary):
        structured = format_ai_summary_from_core_summary(title, core_summary)
        if _is_valid_ai_summary(structured, source):
            return _structured_summary_fallback(title, structured, category)
        fallback_from_summary = build_ai_summary_from_core_summary(title, core_summary)
        if _is_valid_ai_summary(fallback_from_summary, source):
            return _structured_summary_fallback(title, fallback_from_summary, category)

    fallback = _fallback_ai_summary(title, source)
    if _is_valid_ai_summary(fallback, source):
        return _structured_summary_fallback(title, fallback, category)
    return {}


def format_ai_summary_from_core_summary(title: str, core_summary: str) -> str:
    clean_core = clean_text(core_summary)
    if not _has_readable_content(clean_core):
        return ""
    prompt = (
        "아래 기사 요약문을 지정 형식으로 다시 작성해줘.\n"
        "- `주요 포인트`는 원문이나 요약문 첫 3문장을 그대로 복사하지 말고 핵심을 다시 요약한 3문장으로 작성\n"
        "- 각 `주요 포인트`는 서로 다른 핵심 사실을 담은 1문장이어야 함\n"
        "- 전체 출력은 아래 형식을 정확히 지킬 것\n\n"
        "제목: ...\n"
        "핵심 요약: ...\n"
        "- 주요 포인트: ...\n"
        "- 주요 포인트: ...\n"
        "- 주요 포인트: ...\n\n"
        f"기사 제목: {title}\n"
        f"요약문: {clean_core}\n"
    )
    structured = run_codex_cli_summary(prompt)
    return structured if _has_readable_content(structured) else ""


def build_ai_summary_from_core_summary(title: str, core_summary: str) -> str:
    sentences = _split_summary_sentences(core_summary)
    if len(sentences) < 3:
        return ""
    takeaway = " ".join(sentences[:2]).strip()
    points = sentences[:3]
    return _compose_ai_summary(title, takeaway, points)


def _normalize_ai_summary_text(text: str) -> str:
    if not text:
        return ""
    lines = [clean_text(ln) for ln in str(text).splitlines() if clean_text(ln)]
    return "\n".join(lines)


def run_codex_cli_summary(prompt: str) -> str:
    codex_bin = "codex"
    with tempfile.NamedTemporaryFile(prefix="codex_summary_", suffix=".txt", delete=False) as tmp:
        out_path = tmp.name
    cmd = [
        codex_bin,
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "-o",
        out_path,
        "-",
    ]
    try:
        subprocess.run(
            cmd,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=CODEX_TIMEOUT_SECONDS,
            check=True,
        )
        with open(out_path, "r", encoding="utf-8", errors="replace") as f:
            return _normalize_ai_summary_text(f.read())
    except Exception:
        return ""
    finally:
        try:
            os.remove(out_path)
        except Exception:
            pass


def build_ai_summary(title: str, description: str, body_text: str, category: str = "") -> tuple[str, dict]:
    source = minimal_context_filter(title, description, body_text)
    if not _has_readable_content(source):
        return "요약할 수 없는 내용입니다", {}
    blueprint = build_summary_blueprint(title, description, source, category)
    if blueprint:
        summary = compose_ai_summary_from_blueprint(blueprint)
        if _is_valid_ai_summary(summary, source):
            return summary, blueprint
    fallback = _fallback_ai_summary(title, source)
    if _is_valid_ai_summary(fallback, source):
        return fallback, _structured_summary_fallback(title, fallback, category)
    return "요약할 수 없는 내용입니다", {}


def _extract_json_object(text: str):
    raw = str(text or "").strip()
    if not raw:
        return {}
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        return json.loads(raw[start : end + 1])
    except Exception:
        return {}


def _openai_json_response(prompt: str):
    if not OPENAI_API_KEY:
        return {}
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
    with urllib.request.urlopen(req, timeout=40) as resp:
        out = json.loads(resp.read().decode("utf-8", errors="replace"))
    return _extract_json_object(out.get("output_text", ""))


def _contains_generic_explanation_template(text: str) -> bool:
    raw = clean_text(text)
    if not raw:
        return False
    generic_markers = (
        "쉽고 또렷하게 풀어드릴게요",
        "핵심 원인과 흐름을 함께 살펴볼게요",
        "구조와 메커니즘 중심으로 정리해 드릴게요",
        "실무 메커니즘과 시장 영향까지 압축해 드릴게요",
        "어려운 말은 줄이고 어떤 일이 왜 중요한지부터 차근차근 짚어드릴게요",
        "어려운 말 대신 쉬운 표현으로 원인과 결과가 보이게 설명해 드릴게요",
        "개념과 원인을 연결해서 보면 기사 구조가 훨씬 분명하게 보인답니다",
        "배경과 작동 원리, 그리고 후속 파급 효과까지 함께 해석해 보시면 좋겠습니다",
        "실무적으로는 제도 설계와 집행 방식, 시장 임팩트까지 함께 보셔야 판단이 정교해집니다",
        "이 부분을 보면 왜 이런 일이 시작됐는지 쉽게 이해할 수 있어요",
        "이 부분을 보면 지금 어떤 새로운 움직임이 있는지 떠올리기 쉬워요",
        "이 부분을 보면 앞으로 어떤 모습이 기대되는지 함께 생각해 볼 수 있어요",
        "먼저 어떤 일이 있었는지 편하게 이해하시면 돼요",
        "왜 이런 변화가 나왔는지도 함께 보시면 좋아요",
        "앞으로 어떤 영향이 이어질지도 같이 살펴보시면 돼요",
    )
    return any(marker in raw for marker in generic_markers)


def _validate_explanation_levels(payload):
    required_keys = ["middle_school", "high_school", "university", "expert"]
    if not isinstance(payload, dict):
        return {}
    out = {}
    for key in required_keys:
        item = payload.get(key)
        if not isinstance(item, dict):
            return {}
        title = clean_text(item.get("title", ""))
        takeaway = clean_text(item.get("takeaway", ""))
        points = [clean_text(point) for point in item.get("points", []) if clean_text(point)]
        if not title or not takeaway or len(points) != 3:
            return {}
        if _contains_generic_explanation_template(title) or _contains_generic_explanation_template(takeaway):
            return {}
        if any(_contains_generic_explanation_template(point) for point in points):
            return {}
        out[key] = {
            "label": {
                "middle_school": "중학생 수준",
                "high_school": "고등학생 수준",
                "university": "대학생 수준",
                "expert": "전문가 수준",
            }[key],
            "title": title,
            "takeaway": takeaway,
            "points": points[:3],
        }
    return out


def build_explanation_levels(title: str, ai_summary: str, body_text: str, structured_summary: dict | None = None):
    blueprint = structured_summary or {}
    if not blueprint:
        parsed = _parse_ai_summary(ai_summary)
        if parsed["takeaway"] and len(parsed["points"]) >= 3:
            blueprint = build_summary_blueprint_from_ai_summary(title, ai_summary)
        else:
            blueprint = {}
    if blueprint.get("takeaway") and len(blueprint.get("key_points", [])) >= 3:
        middle_prompt_doc = _load_prompt_text(MIDDLE_SCHOOL_SYSTEM_PROMPT_PATH)
        middle_fewshot_doc = _load_prompt_text(MIDDLE_SCHOOL_FEWSHOT_PATH)
        prompt = (
            "아래 기사 요약을 바탕으로 다정한 존댓말 어투의 4단계 설명 데이터를 JSON으로 작성해줘.\n"
            "- 단계 키는 middle_school, high_school, university, expert 를 사용\n"
            "- 각 단계는 title, takeaway, points(길이 3 배열)를 가져야 함\n"
            "- 네 단계는 모두 같은 기사 흐름을 설명해야 하며, 서로 다른 사실이나 새로운 쟁점을 추가하면 안 됨\n"
            "- 네 단계 모두 동일한 3개 핵심 포인트 순서를 유지하고, 난이도와 표현만 바꿔야 함\n"
            "- flow_order 에 제시된 흐름 순서를 그대로 따라야 함\n"
            "- title 은 해당 수준에 맞게 요약과 어투가 반영된 자연스러운 문장형 제목으로 작성\n"
            "- takeaway 는 2문장 이내의 핵심 요약으로 작성\n"
            "- points 는 반드시 3개만 작성하고, 각 항목은 한 문장으로 짧고 또렷하게 작성\n"
            "- 원문 사실을 벗어나지 말고, 없는 숫자나 전망을 새로 만들지 말 것\n"
            "- middle_school: 중학생 눈높이, 쉬운 비유와 쉬운 단어 사용\n"
            "- middle_school 은 제공된 시스템 프롬프트와 few-shot 정책을 반드시 우선 적용\n"
            "- middle_school 은 기사 원문 사실 범위 안에서만 쉬운 말로 재표현하고, 비유는 허용하지만 새로운 사실 추가 금지, 연도/수치/기업명은 가능한 유지\n"
            "- high_school: 고등학생 눈높이, 개념과 원인을 연결\n"
            "- university: 대학생 눈높이, 구조와 메커니즘 설명\n"
            "- expert: 실무 전문가 눈높이, 제도/시장/메커니즘 중심\n"
            "- 네 단계 모두 '제목 / 핵심 요약 / 주요 포인트 3개' 구조를 떠올리되 JSON만 반환\n"
            "- 같은 사건을 다른 난이도로 풀어쓴다는 점이 핵심이며, 단계별로 포인트 방향이 달라지면 안 됨\n"
            "- 출력은 JSON만 반환\n\n"
            f"기사 제목: {title}\n"
            f"구조화 요약 제목: {blueprint['title']}\n"
            f"구조화 핵심 요약: {blueprint['takeaway']}\n"
            f"구조화 주요 포인트: {json.dumps(blueprint['key_points'], ensure_ascii=False)}\n"
            f"세부 기사 유형: {blueprint.get('article_type', '')}\n"
            f"포인트 흐름 순서: {json.dumps(blueprint.get('flow_order', []), ensure_ascii=False)}\n"
            f"본문 참고: {clean_text(body_text)[:1800]}\n"
        )
        if middle_prompt_doc:
            prompt += f"\n[중학생 시스템 프롬프트]\n{middle_prompt_doc}\n"
        if middle_fewshot_doc:
            prompt += f"\n[중학생 Few-shot 예시]\n{middle_fewshot_doc}\n"
        if PREFERRED_LLM_BACKEND == "codex":
            payload = _extract_json_object(run_codex_cli_summary(prompt))
            valid = _validate_explanation_levels(payload)
            if valid:
                return valid
        if OPENAI_API_KEY:
            payload = _openai_json_response(prompt)
            valid = _validate_explanation_levels(payload)
            if valid:
                return valid
    if blueprint:
        return build_explanation_variants_from_blueprint(blueprint, article_title=title)
    return build_explanation_variants_from_summary(title, ai_summary)


def format_crawled_body(title: str, description: str, body_text: str) -> str:
    # Keep display format (bulleted lines), but do not summarize article body.
    # Use full filtered source text (noise removed) as-is.
    return _fallback_format_crawled_body(title, description, body_text)


def http_json(url: str, timeout: int = 20, headers: dict | None = None):
    req_headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read().decode("utf-8", errors="replace")
    return json.loads(data)


def simplify_news_query(query: str) -> str:
    # Keep only the first 2-3 meaningful terms for degraded fallback.
    terms = [t.strip() for t in re.split(r"\bOR\b|\(|\)", query, flags=re.IGNORECASE) if t.strip()]
    if not terms:
        return query
    return " OR ".join(terms[:3])


def split_news_query(query: str):
    terms = [t.strip() for t in re.split(r"\bOR\b|\(|\)", query, flags=re.IGNORECASE) if t.strip()]
    # Keep only meaningful unique terms.
    out = []
    seen = set()
    for t in terms:
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def get_newsapi_session():
    global _NEWSAPI_SESSION
    if _NEWSAPI_SESSION is not None:
        return _NEWSAPI_SESSION
    if not requests:
        return None

    s = requests.Session()
    if Retry and HTTPAdapter:
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.8,
            status_forcelist=[426, 429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
    _NEWSAPI_SESSION = s
    return _NEWSAPI_SESSION


def request_newsapi(params: dict, auth_mode: str = "header"):
    p = dict(params)
    headers = {}
    if auth_mode == "header":
        headers["X-Api-Key"] = NEWSAPI_KEY
    elif auth_mode == "query":
        p["apiKey"] = NEWSAPI_KEY
    else:
        raise ValueError(f"unsupported auth_mode: {auth_mode}")

    url = NEWSAPI_ENDPOINT + "?" + urllib.parse.urlencode(p)
    session = get_newsapi_session()
    if session:
        req_headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }
        req_headers.update(headers)
        resp = session.get(url, headers=req_headers, timeout=25)
        if resp.status_code >= 400:
            raise HTTPError(url, resp.status_code, resp.text[:200], resp.headers, None)
        try:
            return resp.json()
        except Exception:
            return json.loads(resp.text)
    return http_json(url, timeout=25, headers=headers)


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


def http_text_playwright(url: str, timeout_ms: int = PLAYWRIGHT_TIMEOUT_MS) -> str:
    if not sync_playwright:
        return ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
                    ),
                )
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                # Give JS-rendered article blocks a short settle window.
                page.wait_for_timeout(1500)
                return page.content() or ""
            finally:
                browser.close()
    except Exception:
        return ""


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
UI_NOISE_PATTERNS_SIMPLE = [
    re.compile(r"뉴스$", re.IGNORECASE),
    re.compile(r"전체보기"),
    re.compile(r"댓글보기"),
    re.compile(r"제보하기"),
    re.compile(r"관련사진보기"),
    re.compile(r"구독"),
    re.compile(r"로그인"),
    re.compile(r"회원가입"),
]


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
        self.found_step1_target = False
        self.found_step2_target = False

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
            self.container_stack.append({"tag": t, "step": step, "paragraphs": []})
            if step == 1:
                self.found_step1_target = True
            else:
                self.found_step2_target = True
            if step == 2:
                self.pending_article_comment = False
        else:
            self.container_stack.append({"tag": t, "step": 0, "paragraphs": None})

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
            paragraphs = filter_article_paragraphs(node["paragraphs"])
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


def extract_priority_p_result(html_doc: str):
    p = PriorityPExtractor()
    p.feed(html_doc)
    if p.candidates_step1:
        p.candidates_step1.sort(key=lambda x: -x[0])
        return p.candidates_step1[0][1], p.found_step1_target, p.found_step2_target
    if not p.found_step1_target and p.candidates_step2:
        p.candidates_step2.sort(key=lambda x: -x[0])
        return p.candidates_step2[0][1], p.found_step1_target, p.found_step2_target
    return "", p.found_step1_target, p.found_step2_target


class BodyPExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_body = False
        self.skip_depth = 0
        self.skip_tags = {"script", "style", "noscript", "iframe", "svg"}
        self.in_p = False
        self.current_p_parts = []
        self.paragraphs = []

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


class ItempropArticleBodyExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip_tags = {"script", "style", "noscript", "iframe", "svg"}
        self.skip_depth = 0
        self.stack = []
        self.current_target_depth = 0
        self.current_p_parts = None
        self.current_target_p = []
        self.candidates = []
        self.inner_candidates = []

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        attrs_map = {k.lower(): (v or "") for k, v in attrs}
        if t in self.skip_tags:
            self.skip_depth += 1
        is_target = (
            t in {"div", "section"}
            and attrs_map.get("itemprop", "").strip().lower() == "articlebody"
        )
        if is_target:
            self.current_target_depth += 1
        if self.current_target_depth > 0 and t == "p" and self.skip_depth == 0:
            self.current_p_parts = []
        self.stack.append({"is_target": is_target, "parts": []})

    def handle_endtag(self, tag):
        t = tag.lower()
        if t == "p" and self.current_p_parts is not None:
            ptxt = normalize_inner_text(" ".join(self.current_p_parts))
            if ptxt:
                self.current_target_p.append(ptxt)
            self.current_p_parts = None
        if not self.stack:
            return
        node = self.stack.pop()
        txt = normalize_inner_text(" ".join(node["parts"]))
        if node["is_target"] and txt:
            p_lines = filter_article_paragraphs(self.current_target_p)
            if p_lines:
                ptxt = "\n".join(p_lines)[:MAX_SOURCE_CHARS]
                self.candidates.append((len(ptxt), ptxt))
            self.current_target_p = []
            inner_lines = filter_article_paragraphs([x for x in txt.splitlines() if x.strip()])
            if inner_lines:
                inner_txt = "\n".join(inner_lines)[:MAX_SOURCE_CHARS]
                self.inner_candidates.append((len(inner_txt), inner_txt))
            self.current_target_depth = max(0, self.current_target_depth - 1)
        if self.stack and txt:
            self.stack[-1]["parts"].append(txt)
        if t in self.skip_tags and self.skip_depth > 0:
            self.skip_depth -= 1

    def handle_data(self, data):
        if self.skip_depth > 0 or not self.stack:
            return
        txt = normalize_inner_text(data)
        if txt:
            self.stack[-1]["parts"].append(txt)
            if self.current_p_parts is not None:
                self.current_p_parts.append(txt)


class BodyInnerTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_body = False
        self.skip_tags = {"script", "style", "noscript", "iframe", "svg"}
        self.skip_depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t == "body":
            self.in_body = True
            return
        if not self.in_body:
            return
        if t in self.skip_tags:
            self.skip_depth += 1

    def handle_endtag(self, tag):
        t = tag.lower()
        if t == "body":
            self.in_body = False
            return
        if self.in_body and t in self.skip_tags and self.skip_depth > 0:
            self.skip_depth -= 1

    def handle_data(self, data):
        if not self.in_body or self.skip_depth > 0:
            return
        txt = normalize_inner_text(data)
        if txt:
            self.parts.append(txt)


def extract_itemprop_articlebody_text(html_doc: str) -> str:
    p = ItempropArticleBodyExtractor()
    p.feed(html_doc)
    if p.candidates:
        p.candidates.sort(key=lambda x: -x[0])
        return p.candidates[0][1]
    if p.inner_candidates:
        p.inner_candidates.sort(key=lambda x: -x[0])
        return p.inner_candidates[0][1]
    return ""


def extract_document_body_inner_text(html_doc: str) -> str:
    p = BodyInnerTextExtractor()
    p.feed(html_doc)
    return normalize_inner_text("\n".join(p.parts))[:MAX_SOURCE_CHARS]


def extract_article_body_from_html(html_doc: str) -> str:
    if not html_doc:
        return ""
    # 1) div|section[itemprop=articleBody] innerText
    # 2) fallback to document.body innerText
    article_body = extract_itemprop_articlebody_text(html_doc)
    if article_body:
        return article_body
    return extract_document_body_inner_text(html_doc)


def fetch_article_body(url: str, title: str = "") -> str:
    if not url:
        return ""
    try:
        html_doc = http_text(url, timeout=60)
        body = filter_scraped_body_text(title, extract_article_body_from_html(html_doc), url=url)
        if body and len(body.strip()) >= 120:
            return body

        # Fallback for JS-rendered pages where urllib HTML misses article body.
        rendered_html = http_text_playwright(url, timeout_ms=PLAYWRIGHT_TIMEOUT_MS)
        rendered_body = filter_scraped_body_text(title, extract_article_body_from_html(rendered_html), url=url)
        if rendered_body:
            return rendered_body

        return body
    except Exception:
        return ""


def fetch_news(from_date: str, to_date: str, query: str):
    def fetch_news_pages(q: str):
        out = []
        for page in range(1, MAX_NEWS_PAGES + 1):
            params = {
                "q": q,
                "language": "ko",
                "sortBy": "publishedAt",
                "from": from_date,
                "to": to_date,
                "pageSize": NEWS_PAGE_SIZE,
                "page": page,
            }
            try:
                data = request_newsapi(params, auth_mode="header")
            except HTTPError as e:
                if e.code != 426:
                    raise
                data = request_newsapi(params, auth_mode="query")
            except URLError:
                data = request_newsapi(params, auth_mode="query")

            if data.get("status") != "ok":
                raise RuntimeError(f"NewsAPI error: {data}")
            articles = data.get("articles", [])
            if not articles:
                break
            out.extend(articles)
            if len(articles) < NEWS_PAGE_SIZE:
                break
        return out

    try:
        return fetch_news_pages(query)
    except HTTPError as e:
        if e.code != 426:
            raise

    # 426 fallback #2: simplify complex OR query once.
    q_simple = simplify_news_query(query)
    if q_simple and q_simple != query:
        try:
            return fetch_news_pages(q_simple)
        except HTTPError as e:
            if e.code != 426:
                raise
        except Exception:
            pass

    # 426 fallback #3: split OR query and merge partial results.
    split_terms = split_news_query(query)
    merged = []
    seen = set()
    for term in split_terms[:6]:
        try:
            part = fetch_news_pages(term)
        except HTTPError as e:
            if e.code == 426:
                continue
            raise
        except Exception:
            continue

        for a in part:
            title = clean_text(a.get("title", "")).lower()
            url = (a.get("url") or "").strip()
            if not title or not url:
                continue
            key = (title, url)
            if key in seen:
                continue
            seen.add(key)
            merged.append(a)

    if merged:
        return merged
    raise HTTPError(NEWSAPI_ENDPOINT, 426, "Upgrade Required", None, None)


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


def make_thumbnail_prompt(title: str, body_text: str) -> str:
    source = minimal_context_filter(title, "", body_text)[:1200]
    return (
        "다음 기사 내용 기반으로, 사실 중심의 뉴스 썸네일 일러스트를 생성해줘. "
        "텍스트/로고/워터마크/브랜드명/얼굴 클로즈업 없이 상징적인 장면으로 구성해줘. "
        "고해상도, 16:10 비율 느낌의 심플한 편집 스타일.\n\n"
        f"기사 제목: {title}\n"
        f"기사 핵심 내용: {source}"
    )


def thumbnail_rel_path(article_id: str) -> str:
    return f"./assets/thumbs/{article_id}.png"


def generate_thumbnail(article_id: str, title: str, body_text: str) -> str:
    rel = thumbnail_rel_path(article_id)
    out_path = THUMBNAIL_DIR / f"{article_id}.png"
    if out_path.exists():
        return rel
    if not OPENAI_API_KEY:
        return ""
    if not _has_readable_content(body_text):
        return ""

    THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
    prompt = make_thumbnail_prompt(title, body_text)
    payload = {
        "model": THUMBNAIL_MODEL,
        "prompt": prompt,
        "size": THUMBNAIL_SIZE,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/images/generations",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            out = json.loads(resp.read().decode("utf-8", errors="replace"))
        data = out.get("data") or []
        if not data:
            return ""
        first = data[0]
        b64 = first.get("b64_json")
        if b64:
            out_path.write_bytes(base64.b64decode(b64))
            return rel
        url = first.get("url")
        if url:
            with urllib.request.urlopen(url, timeout=30) as resp:
                out_path.write_bytes(resp.read())
            return rel
    except Exception:
        return ""
    return ""


def load_existing_ids(path: str):
    ids = set()
    for archive_path in iter_archive_part_paths(path):
        with open(archive_path, "r", encoding="utf-8") as f:
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


def archive_part_path(path: str, index: int) -> str:
    base = Path(path)
    if index <= 1:
        return str(base)
    return str(base.with_name(f"{base.stem}.{index:03d}{base.suffix}"))


def iter_archive_part_paths(path: str):
    base = Path(path)
    candidates = []
    if base.exists():
        candidates.append(base)
    pattern = f"{base.stem}.*{base.suffix}"
    for candidate in sorted(base.parent.glob(pattern)):
        if candidate == base:
            continue
        if re.fullmatch(rf"{re.escape(base.stem)}\.\d{{3}}{re.escape(base.suffix)}", candidate.name):
            candidates.append(candidate)
    return [str(candidate) for candidate in candidates]


def split_archive_lines(path: str):
    seen = set()
    lines = []
    for archive_path in iter_archive_part_paths(path):
        with open(archive_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                rid = str(row.get("id", "")).strip()
                if rid and rid in seen:
                    continue
                if rid:
                    seen.add(rid)
                lines.append(json.dumps(row, ensure_ascii=False) + "\n")
    return lines


def load_archive_rows(path: str):
    seen = set()
    rows = []
    for archive_path in iter_archive_part_paths(path):
        with open(archive_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                rid = str(row.get("id", "")).strip()
                if rid and rid in seen:
                    continue
                if rid:
                    seen.add(rid)
                rows.append(row)
    return rows


def _parse_row_timestamp(value: str):
    raw = clean_text(value)
    if not raw:
        return dt.datetime.fromtimestamp(0, tz=dt.timezone.utc)
    raw = raw.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except Exception:
        return dt.datetime.fromtimestamp(0, tz=dt.timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _row_timestamp(row: dict):
    for key in ("fetched_at", "archived_at", "article_published_at", "published_at"):
        value = row.get(key)
        if value:
            return _parse_row_timestamp(value)
    return dt.datetime.fromtimestamp(0, tz=dt.timezone.utc)


def _is_template_explanation_levels(levels):
    if not isinstance(levels, dict):
        return True
    for key in ("middle_school", "high_school", "university", "expert"):
        item = levels.get(key)
        if not isinstance(item, dict):
            return True
        title = clean_text(item.get("title", ""))
        takeaway = clean_text(item.get("takeaway", ""))
        points = item.get("points", [])
        if not title or not takeaway or not isinstance(points, list) or len(points) != 3:
            return True
        if _contains_generic_explanation_template(title) or _contains_generic_explanation_template(takeaway):
            return True
        if any(_contains_generic_explanation_template(point) for point in points):
            return True
    return False


def refresh_existing_explanations(path: str, limit: int):
    if limit <= 0:
        return 0
    rows = load_archive_rows(path)
    candidates = []
    for idx, row in enumerate(rows):
        source = clean_text(row.get("scraped_body") or row.get("body") or row.get("summary"))
        if not source:
            continue
        if not _is_template_explanation_levels(row.get("explanation_levels")):
            continue
        candidates.append((_row_timestamp(row), idx))
    candidates.sort(key=lambda item: item[0], reverse=True)

    updated = 0
    for _, idx in candidates[:limit]:
        row = rows[idx]
        source = clean_text(row.get("scraped_body") or row.get("body") or row.get("summary"))
        title = clean_text(row.get("title", ""))
        ai_summary = str(row.get("ai_summary", "") or "")
        blueprint = row.get("summary_blueprint") if isinstance(row.get("summary_blueprint"), dict) else {}
        if not _is_valid_ai_summary(ai_summary, source):
            ai_summary, blueprint = build_ai_summary(
                title,
                clean_text(row.get("summary", "")),
                source,
                category=clean_text(row.get("category", "")),
            )
            if _is_valid_ai_summary(ai_summary, source):
                row["ai_summary"] = ai_summary
        if blueprint:
            row["summary_blueprint"] = blueprint
        row["explanation_levels"] = build_explanation_levels(
            title,
            row.get("ai_summary", ai_summary),
            source,
            structured_summary=blueprint,
        )
        updated += 1
        if updated % 5 == 0:
            rewrite_archive_parts(path, [json.dumps(item, ensure_ascii=False) + "\n" for item in rows])

    if updated and updated % 5:
        rewrite_archive_parts(path, [json.dumps(row, ensure_ascii=False) + "\n" for row in rows])
    return updated


def rebuild_summary_assets(row: dict, force: bool = False):
    title = clean_text(row.get("title", ""))
    category = clean_text(row.get("category", ""))
    summary_text = clean_text(row.get("summary", ""))
    source = clean_text(row.get("scraped_body") or row.get("body") or summary_text)
    ai_summary = str(row.get("ai_summary", "") or "")
    blueprint = row.get("summary_blueprint") if isinstance(row.get("summary_blueprint"), dict) else {}

    if force or not blueprint:
        regenerated_summary, regenerated_blueprint = build_ai_summary(
            title,
            summary_text,
            source,
            category=category,
        )
        if _is_valid_ai_summary(regenerated_summary, source):
            ai_summary = regenerated_summary
            row["ai_summary"] = regenerated_summary
        if regenerated_blueprint:
            blueprint = regenerated_blueprint
            row["summary_blueprint"] = regenerated_blueprint
    elif not _is_valid_ai_summary(ai_summary, source):
        regenerated_summary, regenerated_blueprint = build_ai_summary(
            title,
            summary_text,
            source,
            category=category,
        )
        if _is_valid_ai_summary(regenerated_summary, source):
            ai_summary = regenerated_summary
            row["ai_summary"] = regenerated_summary
        if regenerated_blueprint:
            blueprint = regenerated_blueprint
            row["summary_blueprint"] = regenerated_blueprint

    row["explanation_levels"] = build_explanation_levels(
        title,
        row.get("ai_summary", ai_summary),
        source,
        structured_summary=blueprint,
    )
    return row


def refresh_latest_explanations(path: str, limit: int):
    if limit <= 0:
        return 0
    rows = load_archive_rows(path)
    ranked = [(_row_timestamp(row), idx) for idx, row in enumerate(rows)]
    ranked.sort(key=lambda item: item[0], reverse=True)

    updated = 0
    for _, idx in ranked[:limit]:
        row = rows[idx]
        source = clean_text(row.get("scraped_body") or row.get("body") or row.get("summary"))
        if not source:
            continue
        blueprint = row.get("summary_blueprint") if isinstance(row.get("summary_blueprint"), dict) else {}
        levels = row.get("explanation_levels") if isinstance(row.get("explanation_levels"), dict) else {}
        has_complete_levels = all(isinstance(levels.get(key), dict) for key in ("middle_school", "high_school", "university", "expert"))
        if blueprint.get("key_points") and has_complete_levels and not _is_template_explanation_levels(levels):
            continue
        rows[idx] = rebuild_summary_assets(row, force=not blueprint)
        updated += 1
        if updated % 5 == 0:
            rewrite_archive_parts(path, [json.dumps(item, ensure_ascii=False) + "\n" for item in rows])

    if updated and updated % 5:
        rewrite_archive_parts(path, [json.dumps(row, ensure_ascii=False) + "\n" for row in rows])
    return updated


def rewrite_archive_parts(path: str, lines):
    base = Path(path)
    os.makedirs(base.parent, exist_ok=True)
    tmp_paths = []
    current_index = 1
    current_size = 0
    current_path = Path(archive_part_path(path, current_index))
    current_file = open(current_path.with_suffix(current_path.suffix + ".tmp"), "w", encoding="utf-8")
    tmp_paths.append((current_path, Path(current_file.name)))

    try:
        for line in lines:
            encoded_size = len(line.encode("utf-8"))
            if current_size > 0 and current_size + encoded_size > ARCHIVE_SPLIT_MAX_BYTES:
                current_file.close()
                current_index += 1
                current_size = 0
                current_path = Path(archive_part_path(path, current_index))
                current_file = open(current_path.with_suffix(current_path.suffix + ".tmp"), "w", encoding="utf-8")
                tmp_paths.append((current_path, Path(current_file.name)))
            current_file.write(line)
            current_size += encoded_size
        current_file.close()

        expected_paths = {str(target) for target, _ in tmp_paths}
        for old_path in iter_archive_part_paths(path):
            if old_path not in expected_paths:
                os.remove(old_path)
        for target, tmp in tmp_paths:
            os.replace(tmp, target)
    finally:
        try:
            current_file.close()
        except Exception:
            pass
        for _, tmp in tmp_paths:
            if tmp.exists():
                try:
                    tmp.unlink()
                except Exception:
                    pass


def rebalance_archive_parts(path: str):
    lines = split_archive_lines(path)
    rewrite_archive_parts(path, lines)
    return len(lines), iter_archive_part_paths(path)


def append_entries(path: str, entries):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rebalance_archive_parts(path)
    existing = load_existing_ids(path)
    added = 0
    part_paths = iter_archive_part_paths(path)
    current_path = part_paths[-1] if part_paths else path
    current_size = os.path.getsize(current_path) if os.path.exists(current_path) else 0
    current_index = len(part_paths) if part_paths else 1
    f = open(current_path, "a", encoding="utf-8")
    try:
        for row in entries:
            rid = row.get("id", "")
            if not rid or rid in existing:
                continue
            line = json.dumps(row, ensure_ascii=False) + "\n"
            encoded_size = len(line.encode("utf-8"))
            if current_size > 0 and current_size + encoded_size > ARCHIVE_SPLIT_MAX_BYTES:
                f.close()
                current_index += 1
                current_path = archive_part_path(path, current_index)
                f = open(current_path, "a", encoding="utf-8")
                current_size = 0
            f.write(line)
            current_size += encoded_size
            existing.add(rid)
            added += 1
    finally:
        f.close()
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
    if len(sys.argv) > 1 and sys.argv[1] == "--rebalance-only":
        count, paths = rebalance_archive_parts(ARCHIVE_PATH)
        print(f"OK: rebalanced archive rows={count}, parts={','.join(paths)}")
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "--refresh-explanations":
        updated = refresh_existing_explanations(ARCHIVE_PATH, EXPLANATION_BACKFILL_LIMIT)
        print(f"OK: refreshed_explanations={updated}, archive={ARCHIVE_PATH}")
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "--refresh-latest-explanations":
        updated = refresh_latest_explanations(ARCHIVE_PATH, EXPLANATION_REFRESH_LIMIT)
        print(f"OK: refreshed_latest_explanations={updated}, archive={ARCHIVE_PATH}")
        return 0

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
    eco_query = "(경제 OR 물가 OR 금리 OR 환율 OR 증시 OR 금융 OR 산업)"

    it_articles = unique_articles(safe_fetch_news(from_date, to_date, it_query, "IT"), ITEM_LIMIT_PER_CATEGORY)
    job_articles = unique_articles(safe_fetch_news(from_date, to_date, job_query, "JOB"), ITEM_LIMIT_PER_CATEGORY)
    eco_articles = unique_articles(safe_fetch_news(from_date, to_date, eco_query, "ECO"), ITEM_LIMIT_PER_CATEGORY)

    if not it_articles and not job_articles and not eco_articles:
        # Do not fail the workflow on transient upstream issues.
        print("WARN: no articles fetched for all categories; keeping existing archive.", file=sys.stderr)
        return 0

    entries = []
    for category, arr in [("IT", it_articles), ("취업", job_articles), ("경제", eco_articles)]:
        for a in arr:
            title = clean_text(a.get("title", ""))
            url = (a.get("url") or "").strip()
            desc = clean_text(a.get("description", ""))
            extracted = fetch_article_body(url, title=title)
            published = clean_text(a.get("publishedAt", ""))
            rid = make_id(url, title, published)
            # Keep post original body sourced from fetch_article_body(url).
            body_raw = extracted
            # AS-IS: crawl -> noise removal -> summarize -> formatting
            # summary = summarize(title, desc, body_raw)
            # TO-BE: crawl -> noise removal -> formatting
            formatted_body = format_crawled_body(title, desc, body_raw)
            ai_summary, summary_blueprint = build_ai_summary(title, desc, body_raw, category=category)
            explanation_levels = build_explanation_levels(
                title,
                ai_summary,
                body_raw or formatted_body or desc,
                structured_summary=summary_blueprint,
            )
            keywords = extract_keywords(
                title,
                body_raw or formatted_body or desc,
                ai_summary or formatted_body or desc,
                url=url,
            )
            # Thumbnail generation is temporarily disabled.
            # thumb = generate_thumbnail(rid, title, body_raw)
            thumb = ""
            summary = formatted_body or clean_text(desc or body_raw)
            entries.append(
                {
                    "id": rid,
                    "title": title,
                    "summary": summary,
                    # Detail view body should show formatted crawled content.
                    "body": formatted_body or summary,
                    "ai_summary": ai_summary,
                    "summary_blueprint": summary_blueprint,
                    "explanation_levels": explanation_levels,
                    "thumbnail": thumb,
                    "scraped_body": body_raw,
                    "url": url,
                    "category": category,
                    "article_published_at": published,
                    "fetched_at": now.isoformat(),
                    # legacy fields for backward compatibility
                    "published_at": published,
                    "archived_at": now.isoformat(),
                    "source": "NewsAPI",
                    "keywords": keywords,
                }
            )

    added = append_entries(ARCHIVE_PATH, entries)
    refreshed = refresh_existing_explanations(ARCHIVE_PATH, EXPLANATION_BACKFILL_LIMIT)
    print(
        f"OK: archive={ARCHIVE_PATH}, added={added}, "
        f"it_candidates={len(it_articles)}, job_candidates={len(job_articles)}, "
        f"refreshed_explanations={refreshed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
