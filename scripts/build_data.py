#!/usr/bin/env python3
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

try:
    from kiwipiepy import Kiwi
except Exception:
    Kiwi = None
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
except Exception:
    TfidfVectorizer = None
    LogisticRegression = None

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT / "data" / "news_archive.jsonl"
DEFAULT_OUT = ROOT / "docs" / "data" / "news_archive.json"
DEFAULT_OUT_MANIFEST = ROOT / "docs" / "data" / "news_archive.manifest.json"
DEFAULT_TRENDS_OUT = ROOT / "docs" / "data" / "trends.json"
DEFAULT_UI_NOISE_REPORT_OUT = ROOT / "docs" / "data" / "ui_noise_report.json"
ARCHIVE_SPLIT_MAX_BYTES = max(
    1024 * 1024,
    int(os.environ.get("ARCHIVE_SPLIT_MAX_BYTES", str(47 * 1024 * 1024))),
)
PUBLIC_JSON_SPLIT_MAX_BYTES = max(
    1024 * 1024,
    int(os.environ.get("PUBLIC_JSON_SPLIT_MAX_BYTES", str(ARCHIVE_SPLIT_MAX_BYTES))),
)
UI_NOISE_COMMON_PATH = ROOT / "config" / "ui_noise" / "common.json"
UI_NOISE_PUBLISHERS_PATH = ROOT / "config" / "ui_noise" / "publishers.json"

MOJIBAKE_MARKERS = ("Ã", "Â", "â€™", "â€œ", "â€", "ï¿½", "\ufffd")
CATEGORIES = ("IT", "경제", "취업")
PERIODS = {
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
    "monthly": timedelta(days=30),
}
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9.+-]*|[0-9]{2,}|[가-힣]{2,}")
ALLOWED_KIWI_TAGS = {"NNG", "NNP", "NP", "NR", "SL", "SH"}
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
    "기업",
    "기술",
    "억원",
    "분기",
    "매출",
    "성장",
    "대비",
    "동기",
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
_KIWI = None


@lru_cache(maxsize=1)
def load_ui_noise_config():
    common = {"menu_terms": [], "noise_patterns": []}
    publishers = {}
    if UI_NOISE_COMMON_PATH.exists():
        common = json.loads(UI_NOISE_COMMON_PATH.read_text(encoding="utf-8"))
    if UI_NOISE_PUBLISHERS_PATH.exists():
        publishers = json.loads(UI_NOISE_PUBLISHERS_PATH.read_text(encoding="utf-8"))
    compiled_common = [re.compile(pattern) for pattern in common.get("noise_patterns", [])]
    compiled_publishers = {}
    for key, item in publishers.items():
        compiled_publishers[key] = {
            "domains": item.get("domains", []),
            "brand_terms": item.get("brand_terms", []),
            "menu_terms": set(item.get("menu_terms", [])),
            "noise_patterns": [re.compile(pattern) for pattern in item.get("noise_patterns", [])],
        }
    return {
        "common_menu_terms": set(common.get("menu_terms", [])),
        "common_patterns": compiled_common,
        "publishers": compiled_publishers,
    }


def detect_publisher(url: str = "", lines=None):
    config = load_ui_noise_config()
    host = (urlparse(url).hostname or "").lower()
    lines = lines or []
    joined = "\n".join(lines)
    for publisher, item in config["publishers"].items():
        if any(domain in host for domain in item["domains"]):
            return publisher
        if any(term in joined for term in item["brand_terms"]):
            return publisher
    return "generic"


def get_publisher_noise_terms(publisher: str):
    config = load_ui_noise_config()
    common_terms = set(config["common_menu_terms"])
    common_patterns = list(config["common_patterns"])
    publisher_conf = config["publishers"].get(publisher, {})
    publisher_terms = set(publisher_conf.get("menu_terms", set()))
    publisher_patterns = list(publisher_conf.get("noise_patterns", []))
    for term in publisher_conf.get("brand_terms", []):
        publisher_terms.add(term)
    return common_terms.union(publisher_terms), common_patterns + publisher_patterns


def get_kiwi():
    global _KIWI
    if Kiwi is None:
        return None
    if _KIWI is not None:
        return _KIWI
    try:
        _KIWI = Kiwi()
    except Exception:
        _KIWI = None
    return _KIWI


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


def parse_ai_summary_block(text: str):
    title = ""
    takeaway = ""
    points = []
    for raw in str(text or "").splitlines():
        line = sanitize(raw, "summary")
        if not line:
            continue
        if line.startswith("제목:"):
            title = line.replace("제목:", "", 1).strip()
            continue
        if line.startswith("핵심 요약:"):
            takeaway = line.replace("핵심 요약:", "", 1).strip()
            continue
        if line.startswith("- 주요 포인트:"):
            point = line.replace("- 주요 포인트:", "", 1).strip()
            if point:
                points.append(point)
            continue
        if line.startswith("주요 포인트:"):
            point = line.replace("주요 포인트:", "", 1).strip()
            if point:
                points.append(point)
    return {
        "title": title,
        "takeaway": takeaway,
        "points": points[:3],
    }


def ensure_sentence(text: str) -> str:
    clean = sanitize(text, "summary").strip(" -")
    if not clean:
        return ""
    if clean[-1] not in ".!?。요다":
        clean = clean + "."
    return clean


def explanation_title(base_title: str, suffix: str) -> str:
    clean = sanitize(base_title, "title").strip()
    if not clean:
        clean = "기사 핵심 내용"
    clean = clean.rstrip(" .!?")
    return f"{clean}, {suffix}"


def explanation_takeaway(base: str, extra: str) -> str:
    core = ensure_sentence(base) or "기사의 핵심 내용을 정리해 드릴게요."
    extra = sanitize(extra, "summary").strip()
    if not extra:
        return core
    if extra[-1] not in ".!?。요다":
        extra = extra + "."
    return f"{core} {extra}"


def explanation_point(base: str, extra: str) -> str:
    core = ensure_sentence(base) or "핵심 흐름을 함께 살펴보시면 이해에 도움이 돼요."
    core = core.rstrip()
    extra = sanitize(extra, "summary").strip()
    if not extra:
        return core
    if extra[-1] not in ".!?。요다":
        extra = extra + "."
    return f"{core} {extra}"


def build_explanation_variants_from_summary(article_title: str, ai_summary: str):
    parsed = parse_ai_summary_block(ai_summary)
    base_title = parsed["title"] or sanitize(article_title, "title") or "기사 요약"
    takeaway = ensure_sentence(parsed["takeaway"] or "기사의 핵심 내용을 정리했어요")
    points = [ensure_sentence(point) for point in parsed["points"] if ensure_sentence(point)]
    while len(points) < 3:
        points.append("핵심 내용을 추가로 정리할 수 있도록 본문 정보를 더 보완하고 있어요.")

    variants = {
        "middle_school": {
            "label": "중학생 수준",
            "title": explanation_title(base_title, "쉽고 또렷하게 풀어드릴게요"),
            "takeaway": explanation_takeaway(
                takeaway,
                "어려운 말은 줄이고 어떤 일이 왜 중요한지부터 차근차근 짚어드릴게요",
            ),
            "points": [
                explanation_point(points[0], "먼저 어떤 일이 있었는지 편하게 이해하시면 돼요"),
                explanation_point(points[1], "왜 이런 변화가 나왔는지도 함께 보시면 좋아요"),
                explanation_point(points[2], "앞으로 어떤 영향이 이어질지도 같이 살펴보시면 돼요"),
            ],
        },
        "high_school": {
            "label": "고등학생 수준",
            "title": explanation_title(base_title, "핵심 원인과 흐름을 함께 살펴볼게요"),
            "takeaway": explanation_takeaway(
                takeaway,
                "개념과 원인을 연결해서 보면 기사 구조가 훨씬 분명하게 보인답니다",
            ),
            "points": [
                explanation_point(points[0], "직접적인 배경과 핵심 변수를 같이 보시면 이해가 빨라져요"),
                explanation_point(points[1], "제도나 구조가 어떻게 움직이는지도 함께 연결해 보시면 좋아요"),
                explanation_point(points[2], "이 변화가 시장이나 산업에 남길 파장도 같이 생각해 볼 수 있어요"),
            ],
        },
        "university": {
            "label": "대학생 수준",
            "title": explanation_title(base_title, "구조와 메커니즘 중심으로 정리해 드릴게요"),
            "takeaway": explanation_takeaway(
                takeaway,
                "배경과 작동 원리, 그리고 후속 파급 효과까지 함께 해석해 보시면 좋겠습니다",
            ),
            "points": [
                explanation_point(points[0], "현상 자체보다 그 배경 메커니즘까지 함께 보시는 것이 중요해요"),
                explanation_point(points[1], "제도 변화나 시장 구조가 어떤 행동 변화를 유도하는지도 읽어볼 수 있어요"),
                explanation_point(points[2], "정책과 산업, 시장 관점의 후속 효과까지 이어서 해석해 볼 수 있어요"),
            ],
        },
        "expert": {
            "label": "전문가 수준",
            "title": explanation_title(base_title, "실무 메커니즘과 시장 영향까지 압축해 드릴게요"),
            "takeaway": explanation_takeaway(
                takeaway,
                "실무적으로는 제도 설계와 집행 방식, 시장 임팩트까지 함께 보셔야 판단이 정교해집니다",
            ),
            "points": [
                explanation_point(points[0], "실무에서는 이 지점이 어떤 실행 리스크를 만드는지까지 같이 보셔야 해요"),
                explanation_point(points[1], "규제와 상품 구조, 유동성 메커니즘을 함께 봐야 판단 정확도가 높아져요"),
                explanation_point(points[2], "후속 대응이 시장 구조와 참여자 행동을 어떻게 바꿀지도 점검하셔야 합니다"),
            ],
        },
    }
    return variants


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


def extract_tokens_with_kiwi(text: str):
    kiwi = get_kiwi()
    if kiwi is None:
        return []

    tokens = []
    source = sanitize(text, "body")
    if not source:
        return tokens

    try:
        for morph in kiwi.tokenize(source):
            if morph.tag not in ALLOWED_KIWI_TAGS:
                continue
            token = normalize_token(morph.form)
            if not token:
                continue
            if token in DEPENDENT_NOUN_STOPWORDS:
                continue
            if morph.tag == "NR" and len(token) < 2:
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
    tokens = extract_tokens_with_kiwi(text)
    if tokens:
        return tokens
    return extract_tokens_with_fallback(text)


def extract_token_set(text: str):
    return {token for token in extract_tokens(text) if token}


def build_relevance_context(title: str, lines):
    title_terms = extract_token_set(title)
    if not title_terms:
        title_terms = set()

    line_terms = []
    seed_counts = Counter()
    for line in lines:
        terms = extract_token_set(line)
        line_terms.append((line, terms))
        if title_terms and title_terms.intersection(terms):
            for term in terms:
                seed_counts[term] += 1

    if not title_terms and line_terms:
        for _, terms in line_terms[:8]:
            for term in terms:
                seed_counts[term] += 1

    context_terms = set(title_terms)
    for term, _ in seed_counts.most_common(24):
        context_terms.add(term)
    return title_terms, context_terms, line_terms


def build_line_feature_text(line: str, publisher: str, title_terms, context_terms, terms, line_idx: int, total_lines: int):
    overlap_title = sorted(title_terms.intersection(terms))
    overlap_context = sorted(context_terms.intersection(terms))
    position = "top" if line_idx < 8 else "bottom" if total_lines and line_idx >= max(total_lines - 8, 0) else "mid"
    return " ".join(
        [
            f"publisher={publisher}",
            f"position={position}",
            f"title_overlap={1 if overlap_title else 0}",
            f"context_overlap={1 if overlap_context else 0}",
            f"token_count={len(terms)}",
            "terms=" + " ".join(sorted(terms)),
            "title_terms=" + " ".join(overlap_title),
            "context_terms=" + " ".join(overlap_context),
            "raw=" + sanitize(line, "body").lower(),
        ]
    ).strip()


def run_codex_cli_text(prompt: str) -> str:
    codex_bin = shutil.which("codex")
    if not codex_bin:
        return ""
    with tempfile.NamedTemporaryFile(prefix="codex_ui_noise_", suffix=".txt", delete=False) as tmp:
        out_path = tmp.name
    cmd = [
        codex_bin,
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
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
            timeout=90,
            check=True,
        )
        return Path(out_path).read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return ""
    finally:
        try:
            os.remove(out_path)
        except Exception:
            pass


def llm_label_noise_lines(title: str, publisher: str, candidate_lines):
    if os.environ.get("ENABLE_LLM_NOISE_ASSIST", "").strip().lower() not in {"1", "true", "yes"}:
        return {}
    if not candidate_lines:
        return {}
    prompt_lines = []
    for idx, line in enumerate(candidate_lines, start=1):
        prompt_lines.append(f"{idx}. {line}")
    prompt = (
        "아래 기사 라인들을 content 또는 noise 로 분류해줘.\n"
        "- noise: 메뉴, 공유, 푸터, 회사정보, 약관, 광고, 다른 기사 링크, 고객센터, SNS 등\n"
        "- content: 기사 제목과 직접 관련된 본문 문장\n"
        "- 출력은 `번호: label` 형식만 사용\n\n"
        f"언론사: {publisher}\n"
        f"기사 제목: {title}\n"
        "라인 목록:\n"
        + "\n".join(prompt_lines)
    )
    raw = run_codex_cli_text(prompt)
    labels = {}
    for line in raw.splitlines():
        match = re.match(r"^\s*(\d+)\s*:\s*(content|noise)\s*$", line.strip(), re.IGNORECASE)
        if not match:
            continue
        idx = int(match.group(1)) - 1
        if 0 <= idx < len(candidate_lines):
            labels[candidate_lines[idx]] = match.group(2).lower()
    return labels


def train_line_noise_classifier(title: str, lines, publisher: str = "generic"):
    if not lines or TfidfVectorizer is None or LogisticRegression is None:
        return None, {}

    title_terms, context_terms, line_terms = build_relevance_context(title, lines)
    menu_terms, noise_patterns = get_publisher_noise_terms(publisher)
    train_texts = []
    labels = []
    undecided = []
    debug = {}

    for idx, (line, terms) in enumerate(line_terms):
        feature_text = build_line_feature_text(line, publisher, title_terms, context_terms, terms, idx, len(line_terms))
        lower_line = sanitize(line, "body").lower()
        explicit_noise = any(term.lower() in lower_line for term in menu_terms) or any(
            pattern.search(sanitize(line, "body")) for pattern in noise_patterns
        )
        explicit_content = bool(title_terms.intersection(terms)) or (
            bool(context_terms.intersection(terms)) and len(terms) >= 2 and len(line) >= 24
        )
        if explicit_noise and not explicit_content:
            train_texts.append(feature_text)
            labels.append(1)
            debug[line] = "seed_noise"
            continue
        if explicit_content and not explicit_noise:
            train_texts.append(feature_text)
            labels.append(0)
            debug[line] = "seed_content"
            continue
        undecided.append((line, feature_text))

    llm_labels = llm_label_noise_lines(title, publisher, [line for line, _ in undecided[:8]])
    for line, feature_text in undecided:
        if line in llm_labels:
            train_texts.append(feature_text)
            labels.append(1 if llm_labels[line] == "noise" else 0)
            debug[line] = f"llm_{llm_labels[line]}"

    if len(set(labels)) < 2 or len(train_texts) < 6:
        return None, debug

    vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=1)
    x = vectorizer.fit_transform(train_texts)
    classifier = LogisticRegression(max_iter=300, class_weight="balanced")
    classifier.fit(x, labels)
    return (vectorizer, classifier, title_terms, context_terms), debug


def classify_line_relevance(title: str, line: str, context_terms=None, title_terms=None, publisher="generic", noise_model=None, line_idx=0, total_lines=0):
    clean_line = sanitize(line, "body")
    if not clean_line:
        return False, {"reason": "empty", "terms": set()}

    if title_terms is None:
        title_terms = extract_token_set(title)
    terms = extract_token_set(clean_line)
    if context_terms is None:
        context_terms = set(title_terms)
    menu_terms, noise_patterns = get_publisher_noise_terms(publisher)

    overlap_title = title_terms.intersection(terms)
    overlap_context = context_terms.intersection(terms)
    menu_hits = sum(1 for term in terms if term in menu_terms)
    pattern_noise = any(pattern.search(clean_line) for pattern in noise_patterns)
    is_short = len(clean_line) < 18

    if pattern_noise:
        return False, {"reason": "ui_noise", "terms": terms}
    if menu_hits >= 1 and not overlap_title and not overlap_context:
        return False, {"reason": "menu_terms", "terms": terms}
    if overlap_title:
        return True, {"reason": "title_overlap", "terms": terms}
    if overlap_context:
        return True, {"reason": "context_overlap", "terms": terms}
    if not terms and is_short:
        return False, {"reason": "short_noncontent", "terms": terms}
    if len(terms) <= 1 and is_short:
        return False, {"reason": "low_signal", "terms": terms}
    if noise_model is not None:
        vectorizer, classifier, model_title_terms, model_context_terms = noise_model
        feature_text = build_line_feature_text(
            line,
            publisher,
            model_title_terms,
            model_context_terms,
            terms,
            line_idx,
            total_lines,
        )
        proba = classifier.predict_proba(vectorizer.transform([feature_text]))[0][1]
        if proba >= 0.70:
            return False, {"reason": "model_noise", "terms": terms, "score": round(float(proba), 4)}
        if proba <= 0.35 and len(terms) >= 2:
            return True, {"reason": "model_content", "terms": terms, "score": round(float(proba), 4)}
    if len(terms) >= 3 and len(clean_line) >= 28:
        return True, {"reason": "content_shape", "terms": terms}
    return False, {"reason": "low_relevance", "terms": terms}


def filter_lines_by_title_relevance(title: str, lines, url: str = "", return_report: bool = False):
    publisher = detect_publisher(url, lines)
    title_terms, context_terms, line_terms = build_relevance_context(title, lines)
    noise_model, _ = train_line_noise_classifier(title, lines, publisher)
    kept = []
    removed = []
    suspicious_kept = []
    for idx, (line, terms) in enumerate(line_terms):
        keep, meta = classify_line_relevance(
            title,
            line,
            context_terms=context_terms,
            title_terms=title_terms,
            publisher=publisher,
            noise_model=noise_model,
            line_idx=idx,
            total_lines=len(line_terms),
        )
        if keep:
            kept.append(sanitize(line, "body"))
            for term in terms:
                context_terms.add(term)
            if meta["reason"] in {"content_shape", "low_relevance"}:
                suspicious_kept.append(
                    {
                        "line": sanitize(line, "body"),
                        "reason": meta["reason"],
                        "terms": sorted(list(terms))[:8],
                    }
                )
        elif meta["reason"] == "low_relevance" and len(terms) >= 4 and len(line) >= 40:
            kept.append(sanitize(line, "body"))
            suspicious_kept.append(
                {
                    "line": sanitize(line, "body"),
                    "reason": "fallback_keep",
                    "terms": sorted(list(terms))[:8],
                }
            )
        else:
            removed.append(
                {
                    "line": sanitize(line, "body"),
                    "reason": meta["reason"],
                    "terms": sorted(list(terms))[:8],
                    "score": meta.get("score"),
                }
            )
    if return_report:
        return kept, {
            "publisher": publisher,
            "removed": removed,
            "suspicious_kept": suspicious_kept,
        }
    return kept


def build_ui_noise_report(rows):
    report = {"generated_from_rows": len(rows), "publishers": {}}
    for row in rows[:400]:
        source_text = row.get("scraped_body") or row.get("body") or row.get("summary") or ""
        lines = split_context_units(source_text)
        if not lines:
            continue
        _, line_report = filter_lines_by_title_relevance(
            row.get("title", ""),
            lines,
            url=row.get("url", ""),
            return_report=True,
        )
        publisher = line_report["publisher"]
        bucket = report["publishers"].setdefault(
            publisher,
            {
                "article_count": 0,
                "removed_counter": Counter(),
                "suspicious_counter": Counter(),
            },
        )
        bucket["article_count"] += 1
        for item in line_report["removed"]:
            if item["line"]:
                bucket["removed_counter"][item["line"]] += 1
        for item in line_report["suspicious_kept"]:
            if item["line"]:
                bucket["suspicious_counter"][item["line"]] += 1

    for publisher, bucket in report["publishers"].items():
        removed_counter = bucket.pop("removed_counter")
        suspicious_counter = bucket.pop("suspicious_counter")
        bucket["removed_examples"] = [
            {"line": line, "count": count}
            for line, count in removed_counter.most_common(15)
        ]
        bucket["suspicious_kept_examples"] = [
            {"line": line, "count": count}
            for line, count in suspicious_counter.most_common(15)
        ]
    return report


def split_context_units(text: str):
    source = sanitize(text, "body")
    if not source:
        return []
    return [unit.strip() for unit in re.split(r"[\n\r]+|(?<=[.!?。])\s+", source) if unit.strip()]


def extract_keywords(title: str, body_text: str, summary: str = "", limit: int = 12, url: str = ""):
    filtered_lines = filter_lines_by_title_relevance(title, split_context_units(body_text), url=url)
    filtered_body = "\n".join(filtered_lines) if filtered_lines else body_text
    filtered_summary_lines = filter_lines_by_title_relevance(title, split_context_units(summary), url=url)
    filtered_summary = "\n".join(filtered_summary_lines) if filtered_summary_lines else summary
    title_tokens = [token for token in extract_tokens(title) if token]
    title_set = set(title_tokens)
    if not title_set:
        title_set = set(extract_tokens(filtered_summary)[:6])

    body_counts = Counter(token for token in extract_tokens(filtered_body) if token)
    summary_counts = Counter(token for token in extract_tokens(filtered_summary) if token)
    co_counts = Counter()
    for unit in split_context_units(filtered_body or filtered_summary):
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


def iter_archive_part_paths(src: Path):
    candidates = []
    if src.exists():
        candidates.append(src)
    pattern = f"{src.stem}.*{src.suffix}"
    for candidate in sorted(src.parent.glob(pattern)):
        if candidate == src:
            continue
        if re.fullmatch(rf"{re.escape(src.stem)}\.\d{{3}}{re.escape(src.suffix)}", candidate.name):
            candidates.append(candidate)
    return candidates


def load_archive_rows(src: Path):
    rows = []
    seen_ids = set()
    for part_path in iter_archive_part_paths(src):
        with part_path.open("r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue

                row_id = sanitize(row.get("id"), "id")
                if row_id and row_id in seen_ids:
                    continue
                if row_id:
                    seen_ids.add(row_id)

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

                keywords = row.get("keywords") or []
                if not keywords:
                    keywords = extract_keywords(
                        row.get("title", ""),
                        scraped_body or body or summary,
                        ai_summary or summary,
                        url=row.get("url", ""),
                    )
                explanation_levels = row.get("explanation_levels")
                if not isinstance(explanation_levels, dict):
                    explanation_levels = build_explanation_variants_from_summary(
                        row.get("title", ""),
                        ai_summary or summary,
                    )

                rows.append(
                    {
                        "id": row_id,
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
                        "explanation_levels": explanation_levels,
                    }
                )
    return rows


def split_rows_for_json_parts(rows, max_bytes: int):
    parts = []
    current_rows = []
    current_size = 2
    for row in rows:
        row_json = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        row_size = len(row_json.encode("utf-8"))
        separator_size = 1 if current_rows else 0
        if current_rows and current_size + separator_size + row_size > max_bytes:
            parts.append(current_rows)
            current_rows = [row]
            current_size = 2 + row_size
            continue
        current_rows.append(row)
        current_size += separator_size + row_size
    if current_rows or not parts:
        parts.append(current_rows)
    return parts


def write_json_parts(rows, out: Path, manifest_path: Path):
    os.makedirs(out.parent, exist_ok=True)
    parts = split_rows_for_json_parts(rows, PUBLIC_JSON_SPLIT_MAX_BYTES)
    part_names = []
    active_part_paths = set()

    for index, chunk in enumerate(parts, start=1):
        part_name = f"{out.stem}.{index:03d}{out.suffix}"
        part_path = out.with_name(part_name)
        with part_path.open("w", encoding="utf-8") as file:
            json.dump(chunk, file, ensure_ascii=False, separators=(",", ":"))
        part_names.append(part_name)
        active_part_paths.add(part_path.name)

    for candidate in out.parent.glob(f"{out.stem}.*{out.suffix}"):
        if candidate.name not in active_part_paths and re.fullmatch(
            rf"{re.escape(out.stem)}\.\d{{3}}{re.escape(out.suffix)}",
            candidate.name,
        ):
            candidate.unlink()

    if out.exists():
        out.unlink()

    manifest = {
        "version": 1,
        "base_name": out.name,
        "parts": part_names,
        "total_rows": len(rows),
    }
    with manifest_path.open("w", encoding="utf-8") as file:
        json.dump(manifest, file, ensure_ascii=False, separators=(",", ":"))


def main():
    src = Path(os.environ.get("SOURCE_JSONL", str(DEFAULT_SRC))).expanduser()
    out = Path(os.environ.get("OUTPUT_JSON", str(DEFAULT_OUT))).expanduser()
    manifest_out = Path(os.environ.get("OUTPUT_JSON_MANIFEST", str(DEFAULT_OUT_MANIFEST))).expanduser()
    trends_out = Path(os.environ.get("OUTPUT_TRENDS_JSON", str(DEFAULT_TRENDS_OUT))).expanduser()
    ui_noise_report_out = Path(
        os.environ.get("OUTPUT_UI_NOISE_REPORT_JSON", str(DEFAULT_UI_NOISE_REPORT_OUT))
    ).expanduser()

    rows = []
    if iter_archive_part_paths(src):
        rows = load_archive_rows(src)
    else:
        if out.exists():
            print(f"WARN: source not found: {src}. Keep existing output: {out}")
            return 0
        print(f"WARN: source not found: {src}. Write empty dataset to {out}")

    rows.sort(key=choose_timestamp, reverse=True)
    trends = build_trends(rows)
    ui_noise_report = build_ui_noise_report(rows)

    os.makedirs(out.parent, exist_ok=True)
    os.makedirs(trends_out.parent, exist_ok=True)
    os.makedirs(ui_noise_report_out.parent, exist_ok=True)
    write_json_parts(rows, out, manifest_out)
    with trends_out.open("w", encoding="utf-8") as file:
        json.dump(trends, file, ensure_ascii=False, separators=(",", ":"))
    with ui_noise_report_out.open("w", encoding="utf-8") as file:
        json.dump(ui_noise_report, file, ensure_ascii=False, separators=(",", ":"))

    print(f"OK: wrote {len(rows)} rows -> {manifest_out}")
    print(f"OK: wrote trends -> {trends_out}")
    print(f"OK: wrote ui noise report -> {ui_noise_report_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
