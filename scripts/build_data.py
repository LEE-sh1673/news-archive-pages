#!/usr/bin/env python3
import html
import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from khaiii import KhaiiiApi
except Exception:
    KhaiiiApi = None

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT / "data" / "news_archive.jsonl"
DEFAULT_OUT = ROOT / "docs" / "data" / "news_archive.json"
DEFAULT_TRENDS_OUT = ROOT / "docs" / "data" / "trends.json"

MOJIBAKE_MARKERS = ("Ã", "Â", "â€™", "â€œ", "â€", "ï¿½", "\ufffd")
CATEGORIES = ("IT", "경제", "취업")
PERIODS = {
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
    "monthly": timedelta(days=30),
}
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9.+-]*|[0-9]{2,}|[가-힣]{2,}")
ALLOWED_KHAIII_TAGS = {"NNG", "NNP", "NP", "NR", "SL", "SH"}
EXCLUDED_KHAIII_TAGS = {
    "VV",
    "VA",
    "VX",
    "VCP",
    "VCN",
    "MM",
    "MAG",
    "MAJ",
    "JKS",
    "JKC",
    "JKG",
    "JKO",
    "JKB",
    "JKV",
    "JKQ",
    "JX",
    "JC",
    "EP",
    "EF",
    "EC",
    "ETN",
    "ETM",
    "XSN",
    "XSV",
    "XSA",
    "XR",
    "SF",
    "SP",
    "SS",
    "SE",
    "SO",
    "SW",
}
KOREAN_SUFFIXES = (
    "으로부터",
    "에서부터",
    "에게서",
    "이라도",
    "이라고",
    "에서는",
    "으로는",
    "으로도",
    "까지는",
    "부터는",
    "이다",
    "였다",
    "했다",
    "한다",
    "하며",
    "에게",
    "에서",
    "으로",
    "까지",
    "부터",
    "처럼",
    "보다",
    "마저",
    "조차",
    "라도",
    "이라",
    "인데",
    "이며",
    "이나",
    "나마",
    "만큼",
    "하고",
    "대한",
    "관련",
    "대해",
    "에는",
    "에는",
    "에는",
    "에서",
    "이다",
    "있는",
    "이번",
    "지난",
    "오는",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "에",
    "의",
    "도",
    "와",
    "과",
    "로",
    "만",
)
STOPWORDS = {
    "뉴스",
    "기사",
    "기자",
    "사진",
    "영상",
    "속보",
    "단독",
    "오피니언",
    "칼럼",
    "전문가",
    "브리핑",
    "기준",
    "관련",
    "통해",
    "대해",
    "대한",
    "위해",
    "경우",
    "이후",
    "이날",
    "이번",
    "지난",
    "올해",
    "내년",
    "오늘",
    "내일",
    "있다",
    "없다",
    "했다",
    "한다",
    "된다",
    "됐다",
    "됐다",
    "위한",
    "통한",
    "따른",
    "나선",
    "기록",
    "발표",
    "예정",
    "추진",
    "강조",
    "제공",
    "확대",
    "강화",
    "운영",
    "출시",
    "오전",
    "오후",
    "현재",
    "최근",
    "당시",
    "분야",
    "업계",
    "시장",
    "사업",
    "대표",
    "관계자",
    "함께",
    "기준으",
    "news",
    "photo",
    "video",
    "reporter",
    "copyright",
    "all",
    "rights",
    "reserved",
    "facebook",
    "twitter",
    "linkedin",
    "youtube",
    "instagram",
    "cookie",
    "privacy",
    "login",
    "logout",
    "comment",
    "close",
    "alert",
    "loading",
}
DEPENDENT_NOUN_STOPWORDS = {
    "것",
    "수",
    "등",
    "점",
    "명",
    "건",
    "분",
    "층",
    "곳",
    "쪽",
    "차",
    "안",
    "밖",
    "후",
    "전",
}
FALLBACK_NON_NOUN_SUFFIXES = (
    "하는",
    "하며",
    "하고",
    "하여",
    "해서",
    "했다",
    "했던",
    "되는",
    "되며",
    "되고",
    "됐다",
    "된다",
    "따른",
    "위한",
    "맞춘",
    "겪는",
    "나선",
    "보인",
    "제시한",
    "추진한",
    "참여한",
    "기반한",
    "확대한",
    "강화한",
    "발표한",
    "출시한",
    "있도록",
    "하도록",
    "되도록",
    "될",
)
_KHAIII_API = None


def get_khaiii_api():
    global _KHAIII_API
    if KhaiiiApi is None:
        return None
    if _KHAIII_API is not None:
        return _KHAIII_API
    try:
        _KHAIII_API = KhaiiiApi()
    except Exception:
        _KHAIII_API = None
    return _KHAIII_API


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
    for pattern in patterns:
        out = re.sub(pattern, "", out).strip()
    return out


def sanitize(text, field=""):
    if text is None:
        return ""
    out = str(text).replace("\x00", "").strip()
    out = html.unescape(out)
    out = _fix_mojibake(out)
    if field in ("summary", "body"):
        out = _strip_feed_noise(out)
    return out


def looks_like_bullets(text: str) -> bool:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return bool(lines and lines[0].startswith("-"))


def make_bullet_summary(text: str, max_lines: int = 24) -> str:
    text = sanitize(text, "body")
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s+|(?<=요\.)\s+", text)
    sentences = [sentence.strip(" -") for sentence in sentences if sentence and sentence.strip(" -")]
    if not sentences:
        sentences = [text]

    out = []
    for sentence in sentences:
        if len(out) >= max_lines:
            break
        clean_sentence = sanitize(sentence, "body").strip()
        if not clean_sentence:
            continue
        if clean_sentence[-1] not in ".!?。다":
            clean_sentence = clean_sentence + "."
        out.append(f"- {clean_sentence}")
    return "\n".join(out[:max_lines])


def parse_dt(value: str):
    raw = sanitize(value)
    if not raw:
        return None
    raw = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def choose_timestamp(row: dict):
    for key in ("fetched_at", "archived_at", "article_published_at", "published_at"):
        parsed = parse_dt(row.get(key, ""))
        if parsed:
            return parsed
    return datetime.fromtimestamp(0, tz=timezone.utc)


def normalize_token(token: str) -> str:
    out = sanitize(token).lower()
    if not out:
        return ""
    if re.fullmatch(r"[가-힣]{2,}", out):
        for suffix in KOREAN_SUFFIXES:
            if out.endswith(suffix) and len(out) - len(suffix) >= 2:
                out = out[: -len(suffix)]
                break
    out = out.strip(".:,()[]{}'\"")
    if len(out) < 2:
        return ""
    if out.isdigit() and len(out) < 4:
        return ""
    if out in STOPWORDS:
        return ""
    return out


def extract_tokens_with_khaiii(text: str):
    api = get_khaiii_api()
    if api is None:
        return []

    tokens = []
    source = sanitize(text, "body")
    if not source:
        return tokens

    try:
        for word in api.analyze(source):
            for morph in word.morphs:
                lex = getattr(morph, "lex", "")
                tag = getattr(morph, "tag", "")
                if tag not in ALLOWED_KHAIII_TAGS or tag in EXCLUDED_KHAIII_TAGS:
                    continue
                token = normalize_token(lex)
                if not token:
                    continue
                if token in DEPENDENT_NOUN_STOPWORDS:
                    continue
                if tag == "NR" and len(token) < 2:
                    continue
                tokens.append(token)
    except Exception:
        return []
    return tokens


def extract_tokens_with_fallback(text: str):
    tokens = []
    for raw in TOKEN_RE.findall(sanitize(text)):
        token = normalize_token(raw)
        if not token:
            continue
        if re.fullmatch(r"[가-힣]{3,}", token) and token.endswith(FALLBACK_NON_NOUN_SUFFIXES):
            continue
        if token in DEPENDENT_NOUN_STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def extract_tokens(text: str):
    tokens = extract_tokens_with_khaiii(text)
    if tokens:
        return tokens
    return extract_tokens_with_fallback(text)


def split_context_units(text: str):
    source = sanitize(text, "body")
    if not source:
        return []
    return [unit.strip() for unit in re.split(r"[\n\r]+|(?<=[.!?。])\s+", source) if unit.strip()]


def extract_keywords(title: str, body_text: str, summary: str = "", limit: int = 12):
    title_tokens = [token for token in extract_tokens(title) if token]
    title_set = set(title_tokens)
    if not title_set:
        title_set = set(extract_tokens(summary)[:6])

    body_counts = Counter(token for token in extract_tokens(body_text) if token)
    summary_counts = Counter(token for token in extract_tokens(summary) if token)
    co_counts = Counter()
    for unit in split_context_units(body_text or summary):
        unit_tokens = [token for token in extract_tokens(unit) if token]
        if not unit_tokens:
            continue
        if title_set and not title_set.intersection(unit_tokens):
            continue
        for token in unit_tokens:
            co_counts[token] += 1

    scores = Counter()
    for token, count in body_counts.items():
        if count >= 2:
            scores[token] += count
    for token, count in summary_counts.items():
        scores[token] += count * 2
    for token, count in co_counts.items():
        scores[token] += count * 3
    for idx, token in enumerate(title_tokens):
        scores[token] += max(6 - idx, 2)

    ranked = []
    for token, score in scores.items():
        if token in STOPWORDS:
            continue
        if title_set and token not in title_set and co_counts[token] == 0 and summary_counts[token] == 0:
            continue
        ranked.append((token, score, body_counts[token], summary_counts[token]))

    ranked.sort(key=lambda item: (-item[1], -item[2], -item[3], item[0]))
    return [token for token, _, _, _ in ranked[:limit]]


def compute_rank_delta(current_rank_map: dict, previous_rank_map: dict, keyword: str):
    current_rank = current_rank_map.get(keyword)
    previous_rank = previous_rank_map.get(keyword)
    if current_rank is None:
        return 0
    if previous_rank is None:
        return 999
    return previous_rank - current_rank


def build_period_summary(rows, category: str, now_dt: datetime, period_name: str, delta: timedelta):
    category_rows = [row for row in rows if row.get("category") == category]
    window_start = now_dt - delta
    previous_start = window_start - delta

    current_rows = [row for row in category_rows if choose_timestamp(row) >= window_start]
    previous_rows = [
        row for row in category_rows if previous_start <= choose_timestamp(row) < window_start
    ]
    if not current_rows:
        current_rows = category_rows[:]

    keyword_counts = Counter()
    previous_counts = Counter()
    keyword_articles = {}
    for row in current_rows:
        for keyword in row.get("keywords", [])[:12]:
            keyword_counts[keyword] += 1
            keyword_articles.setdefault(keyword, []).append(row["id"])
    for row in previous_rows:
        for keyword in row.get("keywords", [])[:12]:
            previous_counts[keyword] += 1

    current_rank_map = {
        keyword: idx + 1
        for idx, (keyword, _) in enumerate(keyword_counts.most_common(10))
    }
    previous_rank_map = {
        keyword: idx + 1
        for idx, (keyword, _) in enumerate(previous_counts.most_common(10))
    }

    ranking = []
    for keyword, count in keyword_counts.most_common(10):
        ranking.append(
            {
                "keyword": keyword,
                "count": count,
                "rank": current_rank_map[keyword],
                "delta": compute_rank_delta(current_rank_map, previous_rank_map, keyword),
                "article_ids": keyword_articles.get(keyword, [])[:20],
            }
        )

    cloud = []
    max_count = keyword_counts.most_common(1)[0][1] if keyword_counts else 1
    for keyword, count in keyword_counts.most_common(30):
        cloud.append(
            {
                "keyword": keyword,
                "count": count,
                "weight": round(count / max_count, 4) if max_count else 0,
            }
        )

    keyword_score_map = {keyword: count for keyword, count in keyword_counts.items()}
    scored_posts = []
    for row in current_rows:
        age_days = max((now_dt - choose_timestamp(row)).total_seconds() / 86400, 0)
        freshness_bonus = max(delta.days - age_days, 0)
        score = sum(keyword_score_map.get(keyword, 0) for keyword in row.get("keywords", [])[:8]) + freshness_bonus
        scored_posts.append((score, choose_timestamp(row), row["id"]))
    scored_posts.sort(key=lambda item: (-item[0], -item[1].timestamp(), item[2]))
    popular_ids = [article_id for _, _, article_id in scored_posts[:3]]
    if len(popular_ids) < 3:
        latest_fallback = sorted(category_rows, key=choose_timestamp, reverse=True)
        for row in latest_fallback:
            if row["id"] in popular_ids:
                continue
            popular_ids.append(row["id"])
            if len(popular_ids) >= 3:
                break

    return {
        "range_start": window_start.isoformat(),
        "range_end": now_dt.isoformat(),
        "popular_post_ids": popular_ids[:3],
        "trending_keywords": ranking,
        "word_cloud": cloud,
    }


def build_trends(rows):
    if not rows:
        now_dt = datetime.now(timezone.utc)
    else:
        now_dt = max(choose_timestamp(row) for row in rows)
    categories = {}
    for category in CATEGORIES:
        categories[category] = {
            period_name: build_period_summary(rows, category, now_dt, period_name, delta)
            for period_name, delta in PERIODS.items()
        }
    return {
        "generated_at": now_dt.isoformat(),
        "default_category": "IT",
        "default_period": "weekly",
        "categories": categories,
    }


def main():
    src = Path(os.environ.get("SOURCE_JSONL", str(DEFAULT_SRC))).expanduser()
    out = Path(os.environ.get("OUTPUT_JSON", str(DEFAULT_OUT))).expanduser()
    trends_out = Path(os.environ.get("OUTPUT_TRENDS_JSON", str(DEFAULT_TRENDS_OUT))).expanduser()

    rows = []
    if src.exists():
        with src.open("r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue

                summary = sanitize(row.get("summary"), "summary")
                body = sanitize(row.get("body"), "body")
                ai_summary = sanitize(row.get("ai_summary"), "summary")
                scraped_body = sanitize(row.get("scraped_body"), "body")

                if looks_like_bullets(summary):
                    body = summary
                else:
                    source_for_summary = scraped_body or body or summary
                    bullet = make_bullet_summary(source_for_summary)
                    if bullet:
                        summary = bullet
                        body = bullet

                keywords = row.get("keywords") or extract_keywords(
                    row.get("title", ""),
                    scraped_body or body or summary,
                    ai_summary or summary,
                )

                rows.append(
                    {
                        "id": sanitize(row.get("id"), "id"),
                        "title": sanitize(row.get("title"), "title"),
                        "summary": summary,
                        "body": body,
                        "ai_summary": ai_summary or "요약할 수 없는 내용입니다",
                        "thumbnail": sanitize(row.get("thumbnail"), "thumbnail"),
                        "scraped_body": scraped_body,
                        "url": sanitize(row.get("url"), "url"),
                        "category": sanitize(row.get("category"), "category"),
                        "article_published_at": sanitize(
                            row.get("article_published_at") or row.get("published_at"),
                            "article_published_at",
                        ),
                        "fetched_at": sanitize(row.get("fetched_at") or row.get("archived_at"), "fetched_at"),
                        "published_at": sanitize(row.get("published_at"), "published_at"),
                        "archived_at": sanitize(row.get("archived_at"), "archived_at"),
                        "keywords": [sanitize(keyword) for keyword in keywords if sanitize(keyword)],
                    }
                )
    else:
        if out.exists():
            print(f"WARN: source not found: {src}. Keep existing output: {out}")
            return 0
        print(f"WARN: source not found: {src}. Write empty dataset to {out}")

    rows.sort(key=choose_timestamp, reverse=True)
    trends = build_trends(rows)

    os.makedirs(out.parent, exist_ok=True)
    os.makedirs(trends_out.parent, exist_ok=True)
    with out.open("w", encoding="utf-8") as file:
        json.dump(rows, file, ensure_ascii=False, separators=(",", ":"))
    with trends_out.open("w", encoding="utf-8") as file:
        json.dump(trends, file, ensure_ascii=False, separators=(",", ":"))

    print(f"OK: wrote {len(rows)} rows -> {out}")
    print(f"OK: wrote trends -> {trends_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
