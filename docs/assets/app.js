const PAGE_SIZE = 10;
const HOME_LIST_SIZE = 8;
const CATEGORIES = ["IT", "경제", "취업"];
const PERIODS = [
  { key: "daily", label: "일간" },
  { key: "weekly", label: "주간" },
  { key: "monthly", label: "월간" },
];

let allPosts = [];
let trendsData = null;
let filteredPosts = [];
let page = 1;
let currentPostId = null;
let selectedCategory = "IT";
let selectedPeriod = "weekly";
let selectedKeyword = "";
let detailBackPath = null;
let currentExplanationLevel = "middle_school";

const el = {
  homeTitle: document.getElementById("homeTitle"),
  themeToggle: document.getElementById("themeToggle"),
  homeView: document.getElementById("homeView"),
  helperTabs: document.getElementById("helperTabs"),
  popularMeta: document.getElementById("popularMeta"),
  popularGrid: document.getElementById("popularGrid"),
  periodTabs: document.getElementById("periodTabs"),
  trendMeta: document.getElementById("trendMeta"),
  trendList: document.getElementById("trendList"),
  wordCloud: document.getElementById("wordCloud"),
  keywordResultSection: document.getElementById("keywordResultSection"),
  keywordResultTitle: document.getElementById("keywordResultTitle"),
  keywordResultList: document.getElementById("keywordResultList"),
  keywordClearBtn: document.getElementById("keywordClearBtn"),
  openNewsListBtn: document.getElementById("openNewsListBtn"),
  homeListMeta: document.getElementById("homeListMeta"),
  homeNewsList: document.getElementById("homeNewsList"),
  newsView: document.getElementById("newsView"),
  backHomeBtn: document.getElementById("backHomeBtn"),
  searchInput: document.getElementById("searchInput"),
  sortSelect: document.getElementById("sortSelect"),
  categorySelect: document.getElementById("categorySelect"),
  metaLine: document.getElementById("metaLine"),
  listContainer: document.getElementById("listContainer"),
  pageInfo: document.getElementById("pageInfo"),
  firstBtn: document.getElementById("firstBtn"),
  prevBtn: document.getElementById("prevBtn"),
  nextBtn: document.getElementById("nextBtn"),
  lastBtn: document.getElementById("lastBtn"),
  listView: document.getElementById("listView"),
  detailView: document.getElementById("detailView"),
  backBtn: document.getElementById("backBtn"),
  detailTitle: document.getElementById("detailTitle"),
  detailMeta: document.getElementById("detailMeta"),
  detailKeywords: document.getElementById("detailKeywords"),
  detailExplanationTabs: document.getElementById("detailExplanationTabs"),
  detailAiSummary: document.getElementById("detailAiSummary"),
  detailThumbnail: document.getElementById("detailThumbnail"),
  detailBody: document.getElementById("detailBody"),
};

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function bulletTextToHtml(text) {
  const lines = String(text || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length === 0) return "<ul><li>요약 데이터가 없습니다.</li></ul>";

  const items = [];
  for (const line of lines) {
    const normalized = line.replace(/^\-\s*/, "").trim();
    if (!normalized) continue;
    items.push(`<li>${escapeHtml(normalized)}</li>`);
  }
  if (items.length === 0) return "<ul><li>요약 데이터가 없습니다.</li></ul>";
  return `<ul>${items.join("")}</ul>`;
}

function aiSummaryToHtml(text) {
  const rawLines = String(text || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (rawLines.length === 0) return "<p>요약할 수 없는 내용입니다.</p>";

  let title = "";
  let takeaway = "";
  const points = [];

  for (const line of rawLines) {
    if (line.startsWith("제목:")) {
      title = line.replace(/^제목:\s*/, "").trim();
      continue;
    }
    if (line.startsWith("핵심 요약:")) {
      takeaway = line.replace(/^핵심 요약:\s*/, "").trim();
      continue;
    }
    if (line.startsWith("- 주요 포인트:")) {
      const point = line.replace(/^\-\s*주요 포인트:\s*/, "").trim();
      if (point) points.push(point);
      continue;
    }
    if (line.startsWith("주요 포인트:")) {
      const point = line.replace(/^주요 포인트:\s*/, "").trim();
      if (point) points.push(point);
    }
  }

  const parts = [];
  if (title) parts.push(`<p><strong>제목:</strong> ${escapeHtml(title)}</p>`);
  if (takeaway) parts.push(`<p><strong>핵심 요약:</strong> ${escapeHtml(takeaway)}</p>`);
  if (points.length > 0) {
    const items = points.map((point) => `<li>${escapeHtml(point)}</li>`).join("");
    parts.push(`<p><strong>주요 포인트</strong></p><ul>${items}</ul>`);
  }
  if (parts.length === 0) return `<p>${escapeHtml(rawLines.join(" "))}</p>`;
  return parts.join("");
}

function explanationLevelToHtml(levelData) {
  if (!levelData) return "<p>설명 데이터를 준비하지 못했습니다.</p>";
  const points = Array.isArray(levelData.points) ? levelData.points : [];
  const items = points.map((point) => `<li>${escapeHtml(point)}</li>`).join("");
  return [
    `<p><strong>제목:</strong> ${escapeHtml(levelData.title || "")}</p>`,
    `<p><strong>핵심 요약:</strong> ${escapeHtml(levelData.takeaway || "")}</p>`,
    items ? `<p><strong>주요 포인트</strong></p><ul>${items}</ul>` : "",
  ].join("");
}

function getExplanationLevels(post) {
  if (post?.explanation_levels && typeof post.explanation_levels === "object") {
    return post.explanation_levels;
  }
  return {};
}

function renderExplanationTabs(post) {
  const explanationLevels = getExplanationLevels(post);
  const keys = ["middle_school", "high_school", "university", "expert"];
  const availableKeys = keys.filter((key) => explanationLevels[key]);
  if (availableKeys.length === 0) {
    el.detailExplanationTabs.textContent = "";
    el.detailAiSummary.innerHTML = aiSummaryToHtml(post.ai_summary || "요약할 수 없는 내용입니다");
    return;
  }
  if (!availableKeys.includes(currentExplanationLevel)) {
    currentExplanationLevel = availableKeys[0];
  }

  el.detailExplanationTabs.textContent = "";
  availableKeys.forEach((key) => {
    const item = explanationLevels[key];
    const button = document.createElement("button");
    button.type = "button";
    button.className = `explanation-tab${currentExplanationLevel === key ? " active" : ""}`;
    button.textContent = item.label || key;
    button.addEventListener("click", () => {
      currentExplanationLevel = key;
      renderExplanationTabs(post);
    });
    el.detailExplanationTabs.appendChild(button);
  });
  el.detailAiSummary.innerHTML = explanationLevelToHtml(explanationLevels[currentExplanationLevel]);
}

function parseDate(value) {
  const date = new Date(value || 0);
  return Number.isNaN(date.getTime()) ? new Date(0) : date;
}

function relativeTime(value) {
  const now = Date.now();
  const ts = parseDate(value).getTime();
  const diffSec = Math.max(Math.floor((now - ts) / 1000), 0);
  if (diffSec < 60) return `${diffSec}초 전`;
  const minutes = Math.floor(diffSec / 60);
  if (minutes < 60) return `${minutes}분 전`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}시간 전`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}일 전`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}개월 전`;
  return `${Math.floor(months / 12)}년 전`;
}

function normalizePath(path) {
  if (!path) return "/";
  let out = String(path).replace(/\/+/g, "/");
  if (!out.startsWith("/")) out = `/${out}`;
  if (out.length > 1 && out.endsWith("/")) out = out.slice(0, -1);
  return out || "/";
}

function getRepoBasePath() {
  const path = normalizePath(window.location.pathname);
  if (path === "/" || path === "/index.html") return "";
  if (path.includes("/articles/")) return path.split("/articles/")[0];
  if (path.endsWith("/404.html")) return path.slice(0, -"/404.html".length);
  if (path.endsWith("/news")) return path.slice(0, -"/news".length);
  return path;
}

function getCurrentUrl() {
  return new URL(window.location.href);
}

function getRoutedLocation() {
  const url = getCurrentUrl();
  const routeParam = url.searchParams.get("route");
  const routeUrl = routeParam ? new URL(routeParam, window.location.origin) : url;
  return {
    path: normalizePath(routeUrl.pathname),
    params: routeUrl.searchParams,
    hash: routeUrl.hash || "",
  };
}

function buildUrl(path, params = {}) {
  const url = new URL(window.location.origin);
  url.pathname = normalizePath(path);
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    url.searchParams.set(key, String(value));
  });
  return `${url.pathname}${url.search}${url.hash}`;
}

function homePath(params = {}) {
  return buildUrl(`${getRepoBasePath() || ""}/`, params);
}

function newsPath(params = {}) {
  return buildUrl(`${getRepoBasePath() || ""}/news`, params);
}

function articlePath(id, params = {}) {
  return buildUrl(`${getRepoBasePath() || ""}/articles/${encodeURIComponent(id)}`, params);
}

function replaceRouteState(path) {
  window.history.replaceState({}, "", path);
}

function pushRouteState(path) {
  window.history.pushState({}, "", path);
}

function postTimestamp(post) {
  return parseDate(post.fetched_at || post.archived_at || post.article_published_at || post.published_at).getTime();
}

function sortPosts(posts, sortKey) {
  const sorted = [...posts];
  if (sortKey === "latest") {
    sorted.sort((a, b) => postTimestamp(b) - postTimestamp(a));
  } else if (sortKey === "oldest") {
    sorted.sort((a, b) => postTimestamp(a) - postTimestamp(b));
  } else if (sortKey === "title") {
    sorted.sort((a, b) => (a.title || "").localeCompare(b.title || "", "ko"));
  } else if (sortKey === "category") {
    sorted.sort((a, b) => (a.category || "").localeCompare(b.category || "", "ko"));
  }
  return sorted;
}

function getPostById(id) {
  return allPosts.find((post) => String(post.id) === String(id)) || null;
}

function getTrendBucket() {
  return trendsData?.categories?.[selectedCategory]?.[selectedPeriod] || null;
}

function getTrendPostIds() {
  return getTrendBucket()?.popular_post_ids || [];
}

function getKeywordArticles(keyword) {
  const bucket = getTrendBucket();
  if (!bucket || !keyword) return [];
  const ranked = bucket.trending_keywords || [];
  const found = ranked.find((item) => item.keyword === keyword);
  const ids = found?.article_ids || [];
  const posts = ids.map((id) => getPostById(id)).filter(Boolean);
  if (posts.length > 0) return posts;

  const start = parseDate(bucket.range_start).getTime();
  const end = parseDate(bucket.range_end).getTime();
  return allPosts.filter((post) => {
    const ts = postTimestamp(post);
    if (ts < start || ts > end) return false;
    return (post.keywords || []).includes(keyword);
  });
}

function rankDeltaLabel(delta) {
  if (delta === 999) return { text: "NEW", className: "rank-badge new" };
  if (delta > 0) return { text: `▲ ${delta}`, className: "rank-badge up" };
  if (delta < 0) return { text: `▼ ${Math.abs(delta)}`, className: "rank-badge down" };
  return { text: "유지", className: "rank-badge same" };
}

function createArticleLink(post, context) {
  const link = document.createElement("a");
  link.href = articlePath(post.id, context);
  link.className = "title";
  link.textContent = post.title || "제목 없음";
  link.addEventListener("click", (event) => {
    event.preventDefault();
    openDetail(post.id, context);
  });
  return link;
}

function articleContext(view) {
  const context = {
    from: view,
    category: selectedCategory,
    period: selectedPeriod,
  };
  if (selectedKeyword) context.keyword = selectedKeyword;
  if (view === "news") {
    if (el.searchInput.value.trim()) context.search = el.searchInput.value.trim();
    if (el.sortSelect.value !== "latest") context.sort = el.sortSelect.value;
    if (el.categorySelect.value !== "all") context.listCategory = el.categorySelect.value;
    if (page > 1) context.page = page;
  }
  return context;
}

function syncNewsRoute() {
  replaceRouteState(
    newsPath({
      category: el.categorySelect.value !== "all" ? el.categorySelect.value : "",
      search: el.searchInput.value.trim(),
      sort: el.sortSelect.value !== "latest" ? el.sortSelect.value : "",
      period: selectedPeriod,
      page: page > 1 ? page : "",
    }),
  );
}

function renderHelperTabs() {
  el.helperTabs.textContent = "";
  CATEGORIES.forEach((category) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `tab-btn${selectedCategory === category ? " active" : ""}`;
    button.textContent = category;
    button.addEventListener("click", () => {
      selectedCategory = category;
      selectedKeyword = "";
      renderHome();
      replaceRouteState(homePath({ category: selectedCategory, period: selectedPeriod }));
    });
    el.helperTabs.appendChild(button);
  });
}

function renderPeriodTabs() {
  el.periodTabs.textContent = "";
  PERIODS.forEach((period) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `period-btn${selectedPeriod === period.key ? " active" : ""}`;
    button.textContent = period.label;
    button.addEventListener("click", () => {
      selectedPeriod = period.key;
      selectedKeyword = "";
      renderHome();
      replaceRouteState(homePath({ category: selectedCategory, period: selectedPeriod }));
    });
    el.periodTabs.appendChild(button);
  });
}

function renderPopularPosts() {
  const bucket = getTrendBucket();
  const ids = bucket?.popular_post_ids || [];
  const posts = ids.map((id) => getPostById(id)).filter(Boolean);
  el.popularGrid.textContent = "";
  el.popularMeta.textContent = `${selectedCategory} · ${PERIODS.find((item) => item.key === selectedPeriod)?.label || ""} 기준`;

  posts.forEach((post, index) => {
    const card = document.createElement("article");
    card.className = `popular-card ${index === 0 ? "hero" : "side"}`;
    card.addEventListener("click", () => openDetail(post.id, articleContext("home")));

    const badge = document.createElement("span");
    badge.className = "popular-rank";
    badge.textContent = `TOP ${index + 1}`;

    const title = document.createElement("h3");
    title.className = "popular-title";
    title.textContent = post.title || "제목 없음";

    const summary = document.createElement("p");
    summary.className = "popular-summary";
    summary.textContent = String(post.summary || "").replace(/^\-\s*/gm, " ").replace(/\s+/g, " ").trim();

    const meta = document.createElement("p");
    meta.className = "meta";
    meta.textContent = `${post.category || "-"} · 수집 ${relativeTime(post.fetched_at || post.archived_at)}`;

    card.appendChild(badge);
    card.appendChild(title);
    card.appendChild(summary);
    card.appendChild(meta);
    el.popularGrid.appendChild(card);
  });
}

function renderTrendList() {
  const bucket = getTrendBucket();
  const trends = bucket?.trending_keywords || [];
  el.trendList.textContent = "";
  el.trendMeta.textContent = bucket ? `${bucket.range_start.slice(0, 10)} ~ ${bucket.range_end.slice(0, 10)}` : "";

  trends.forEach((item) => {
    const li = document.createElement("li");
    li.className = `trend-item${selectedKeyword === item.keyword ? " active" : ""}`;

    const button = document.createElement("button");
    button.type = "button";
    button.className = "trend-btn";
    button.addEventListener("click", () => {
      selectedKeyword = selectedKeyword === item.keyword ? "" : item.keyword;
      renderHome();
      replaceRouteState(homePath({ category: selectedCategory, period: selectedPeriod, keyword: selectedKeyword }));
    });

    const rank = document.createElement("span");
    rank.className = "trend-rank";
    rank.textContent = String(item.rank);

    const keyword = document.createElement("span");
    keyword.className = "trend-keyword";
    keyword.textContent = item.keyword;

    const count = document.createElement("span");
    count.className = "trend-count";
    count.textContent = `${item.count}건`;

    const delta = rankDeltaLabel(item.delta);
    const badge = document.createElement("span");
    badge.className = delta.className;
    badge.textContent = delta.text;

    button.appendChild(rank);
    button.appendChild(keyword);
    button.appendChild(count);
    button.appendChild(badge);
    li.appendChild(button);
    el.trendList.appendChild(li);
  });
}

function renderWordCloud() {
  const cloud = getTrendBucket()?.word_cloud || [];
  el.wordCloud.textContent = "";
  cloud.forEach((item, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `cloud-word${selectedKeyword === item.keyword ? " active" : ""}`;
    button.style.setProperty("--weight", String(item.weight || 0.2));
    button.style.setProperty("--index", String(index));
    button.textContent = item.keyword;
    button.addEventListener("click", () => {
      selectedKeyword = selectedKeyword === item.keyword ? "" : item.keyword;
      renderHome();
      replaceRouteState(homePath({ category: selectedCategory, period: selectedPeriod, keyword: selectedKeyword }));
    });
    el.wordCloud.appendChild(button);
  });
}

function renderKeywordResults() {
  const keyword = selectedKeyword;
  if (!keyword) {
    el.keywordResultSection.classList.add("hidden");
    el.keywordResultList.textContent = "";
    return;
  }

  const posts = sortPosts(getKeywordArticles(keyword), "latest");
  el.keywordResultSection.classList.remove("hidden");
  el.keywordResultTitle.textContent = `"${keyword}" 키워드 기사`;
  el.keywordResultList.textContent = "";

  posts.slice(0, 10).forEach((post) => {
    const item = document.createElement("article");
    item.className = "mini-item";

    const title = createArticleLink(post, articleContext("home"));
    const summary = document.createElement("p");
    summary.className = "mini-summary";
    summary.textContent = String(post.summary || "").replace(/^\-\s*/gm, " ").replace(/\s+/g, " ").trim();

    const meta = document.createElement("p");
    meta.className = "meta";
    meta.textContent = `${post.category || "-"} · 기사 생성일 ${post.article_published_at || post.published_at || "-"}`;

    item.appendChild(title);
    item.appendChild(summary);
    item.appendChild(meta);
    el.keywordResultList.appendChild(item);
  });
}

function renderHomeNewsList() {
  const posts = sortPosts(
    allPosts.filter((post) => {
      if (post.category !== selectedCategory) return false;
      if (!selectedKeyword) return true;
      return (post.keywords || []).includes(selectedKeyword);
    }),
    "latest",
  );
  el.homeListMeta.textContent = selectedKeyword
    ? `${selectedCategory} · "${selectedKeyword}" 포함 기사 ${posts.length}건`
    : `${selectedCategory} 최신 기사 ${posts.length}건`;
  el.homeNewsList.textContent = "";

  posts.slice(0, HOME_LIST_SIZE).forEach((post, idx) => {
    const item = document.createElement("div");
    item.className = "item";

    const head = document.createElement("div");
    head.className = "item-head";

    const rank = document.createElement("div");
    rank.className = "rank";
    rank.textContent = `${idx + 1}.`;

    const body = document.createElement("div");
    body.appendChild(createArticleLink(post, articleContext("home")));

    const summary = document.createElement("div");
    summary.className = "summary";
    summary.textContent = post.summary || "";

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `수집 ${relativeTime(post.fetched_at || post.archived_at)} | ${post.category || "-"} | 키워드 ${(post.keywords || []).slice(0, 4).join(", ")}`;

    body.appendChild(summary);
    body.appendChild(meta);
    head.appendChild(rank);
    head.appendChild(body);
    item.appendChild(head);
    el.homeNewsList.appendChild(item);
  });
}

function renderHome() {
  renderHelperTabs();
  renderPeriodTabs();
  renderPopularPosts();
  renderTrendList();
  renderWordCloud();
  renderKeywordResults();
  renderHomeNewsList();
}

function applyFilters(updateHistory = false, resetPage = true) {
  const query = el.searchInput.value.trim().toLowerCase();
  const category = el.categorySelect.value;
  const base = allPosts.filter((post) => {
    if (category !== "all" && post.category !== category) return false;
    const keywords = (post.keywords || []).join(" ");
    const haystack = `${post.title || ""} ${post.summary || ""} ${post.body || ""} ${keywords}`.toLowerCase();
    if (!query) return true;
    return haystack.includes(query);
  });
  filteredPosts = sortPosts(base, el.sortSelect.value);
  if (resetPage) page = 1;
  renderList();
  if (updateHistory) {
    syncNewsRoute();
  }
}

function renderList() {
  const total = filteredPosts.length;
  const totalPages = Math.max(Math.ceil(total / PAGE_SIZE), 1);
  page = Math.min(Math.max(page, 1), totalPages);

  const start = (page - 1) * PAGE_SIZE;
  const rows = filteredPosts.slice(start, start + PAGE_SIZE);

  el.metaLine.textContent = `총 ${total}건 · 페이지 ${page}/${totalPages}`;
  el.pageInfo.textContent = `${page} / ${totalPages}`;
  el.firstBtn.disabled = page <= 1;
  el.prevBtn.disabled = page <= 1;
  el.nextBtn.disabled = page >= totalPages;
  el.lastBtn.disabled = page >= totalPages;

  el.listContainer.textContent = "";
  rows.forEach((post, idx) => {
    const item = document.createElement("div");
    item.className = "item";

    const head = document.createElement("div");
    head.className = "item-head";

    const rank = document.createElement("div");
    rank.className = "rank";
    rank.textContent = `${start + idx + 1}.`;

    const body = document.createElement("div");
    body.appendChild(createArticleLink(post, articleContext("news")));

    const summary = document.createElement("div");
    summary.className = "summary";
    summary.textContent = post.summary || "";

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `수집 ${relativeTime(post.fetched_at || post.archived_at)} | ${post.category || "-"} | 기사 생성일 ${post.article_published_at || post.published_at || "-"} | 키워드 ${(post.keywords || []).slice(0, 5).join(", ")}`;

    body.appendChild(summary);
    body.appendChild(meta);
    head.appendChild(rank);
    head.appendChild(body);
    item.appendChild(head);
    el.listContainer.appendChild(item);
  });
}

function openHome(updateHistory = true) {
  currentPostId = null;
  el.homeView.classList.remove("hidden");
  el.newsView.classList.add("hidden");
  el.detailView.classList.add("hidden");
  renderHome();
  if (updateHistory) {
    pushRouteState(homePath({ category: selectedCategory, period: selectedPeriod, keyword: selectedKeyword }));
  }
  document.title = "news archive";
}

function openNewsList(updateHistory = true) {
  currentPostId = null;
  el.homeView.classList.add("hidden");
  el.newsView.classList.remove("hidden");
  el.detailView.classList.add("hidden");
  applyFilters(false, false);
  if (updateHistory) {
    pushRouteState(
      newsPath({
        category: el.categorySelect.value !== "all" ? el.categorySelect.value : "",
        search: el.searchInput.value.trim(),
        sort: el.sortSelect.value !== "latest" ? el.sortSelect.value : "",
        period: selectedPeriod,
        page: page > 1 ? page : "",
      }),
    );
  }
  document.title = "news archive | list";
}

function openDetail(id, context = {}, updateHistory = true) {
  const post = getPostById(id);
  if (!post) return;

  currentPostId = String(post.id);
  currentExplanationLevel = "middle_school";
  detailBackPath = context.from === "news"
    ? newsPath({
        category: context.listCategory || "",
        search: context.search || context.keyword || "",
        sort: context.sort || "",
        period: context.period || selectedPeriod,
      })
    : homePath({
        category: context.category || selectedCategory,
        period: context.period || selectedPeriod,
        keyword: context.keyword || "",
      });

  el.homeView.classList.add("hidden");
  el.newsView.classList.add("hidden");
  el.detailView.classList.remove("hidden");

  const anchor = document.createElement("a");
  anchor.href = post.url || "#";
  anchor.target = "_blank";
  anchor.rel = "noopener noreferrer";
  anchor.textContent = post.title || "제목 없음";

  el.detailTitle.textContent = "";
  el.detailTitle.appendChild(anchor);
  el.detailMeta.textContent = `카테고리: ${post.category || "-"} | 기사 생성일: ${post.article_published_at || post.published_at || "-"} | 수집일: ${post.fetched_at || post.archived_at || "-"}`;
  el.detailKeywords.textContent = "";
  (post.keywords || []).slice(0, 8).forEach((keyword) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "keyword-chip";
    chip.textContent = keyword;
    chip.addEventListener("click", () => {
      selectedCategory = post.category || selectedCategory;
      selectedKeyword = keyword;
      openHome(true);
    });
    el.detailKeywords.appendChild(chip);
  });
  renderExplanationTabs(post);
  if (post.thumbnail) {
    el.detailThumbnail.src = post.thumbnail;
    el.detailThumbnail.classList.remove("hidden");
  } else {
    el.detailThumbnail.removeAttribute("src");
    el.detailThumbnail.classList.add("hidden");
  }
  el.detailBody.innerHTML = bulletTextToHtml(post.body || post.summary || "");
  if (updateHistory) {
    pushRouteState(articlePath(post.id, context));
  }
  document.title = `${post.title || "news archive"} | news archive`;
}

function routeFromLocation() {
  const routed = getRoutedLocation();
  const path = routed.path;
  const base = getRepoBasePath();
  const articlePrefix = `${base}/articles/`;
  const newsRoute = normalizePath(`${base}/news`);

  const categoryParam = routed.params.get("category");
  const periodParam = routed.params.get("period");
  const keywordParam = routed.params.get("keyword");
  if (CATEGORIES.includes(categoryParam)) selectedCategory = categoryParam;
  if (PERIODS.some((period) => period.key === periodParam)) selectedPeriod = periodParam;
  selectedKeyword = keywordParam || "";

  if (path.startsWith(articlePrefix)) {
    const id = decodeURIComponent(path.slice(articlePrefix.length));
    const exists = getPostById(id);
    if (exists) {
      openDetail(id, {
        from: routed.params.get("from") || "home",
        category: routed.params.get("category") || selectedCategory,
        period: routed.params.get("period") || selectedPeriod,
        keyword: routed.params.get("keyword") || "",
        search: routed.params.get("search") || "",
        sort: routed.params.get("sort") || "",
        listCategory: routed.params.get("listCategory") || routed.params.get("category") || "",
      }, false);
      replaceRouteState(articlePath(id, Object.fromEntries(routed.params.entries())));
      return;
    }
  }

  if (path === newsRoute) {
    el.categorySelect.value = CATEGORIES.includes(routed.params.get("category")) ? routed.params.get("category") : "all";
    el.searchInput.value = routed.params.get("search") || routed.params.get("keyword") || "";
    el.sortSelect.value = ["latest", "oldest", "title", "category"].includes(routed.params.get("sort"))
      ? routed.params.get("sort")
      : "latest";
    const pageParam = Number(routed.params.get("page") || "1");
    page = Number.isFinite(pageParam) && pageParam > 0 ? pageParam : 1;
    openNewsList(false);
    replaceRouteState(newsPath(Object.fromEntries(routed.params.entries())));
    return;
  }

  openHome(false);
  replaceRouteState(homePath({ category: selectedCategory, period: selectedPeriod, keyword: selectedKeyword }));
}

async function loadData() {
  const [archiveRows, trendsRes] = await Promise.all([
    loadArchiveRows(),
    fetch("./data/trends.json", { cache: "no-store" }),
  ]);
  if (!trendsRes.ok) throw new Error(`Failed to load trends: ${trendsRes.status}`);

  allPosts = sortPosts(archiveRows, "latest");
  trendsData = await trendsRes.json();
  selectedCategory = trendsData.default_category || selectedCategory;
  selectedPeriod = trendsData.default_period || selectedPeriod;
  filteredPosts = sortPosts([...allPosts], "latest");
  routeFromLocation();
}

async function loadArchiveRows() {
  const manifestRes = await fetch("./data/news_archive.manifest.json", { cache: "no-store" });
  if (manifestRes.ok) {
    const manifest = await manifestRes.json();
    const partNames = Array.isArray(manifest.parts) ? manifest.parts : [];
    const partResponses = await Promise.all(
      partNames.map((partName) => fetch(`./data/${partName}`, { cache: "no-store" }))
    );
    for (const response of partResponses) {
      if (!response.ok) throw new Error(`Failed to load archive part: ${response.status}`);
    }
    const partRows = await Promise.all(partResponses.map((response) => response.json()));
    return partRows.flat();
  }
  if (manifestRes.status !== 404) {
    throw new Error(`Failed to load archive manifest: ${manifestRes.status}`);
  }

  const archiveRes = await fetch("./data/news_archive.json", { cache: "no-store" });
  if (!archiveRes.ok) throw new Error(`Failed to load data: ${archiveRes.status}`);
  return archiveRes.json();
}

function applyTheme(mode) {
  const theme = mode === "dark" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", theme);
  el.themeToggle.textContent = theme === "dark" ? "☀️ Light" : "🌙 Dark";
  localStorage.setItem("theme", theme);
}

function toggleTheme() {
  const now = document.documentElement.getAttribute("data-theme") || "light";
  applyTheme(now === "dark" ? "light" : "dark");
}

function bindEvents() {
  el.homeTitle.addEventListener("click", (event) => {
    event.preventDefault();
    openHome(true);
  });
  el.themeToggle.addEventListener("click", toggleTheme);
  el.openNewsListBtn.addEventListener("click", () => {
    el.categorySelect.value = selectedCategory;
    el.searchInput.value = selectedKeyword;
    el.sortSelect.value = "latest";
    openNewsList(true);
  });
  el.backHomeBtn.addEventListener("click", () => openHome(true));
  el.keywordClearBtn.addEventListener("click", () => {
    selectedKeyword = "";
    renderHome();
    replaceRouteState(homePath({ category: selectedCategory, period: selectedPeriod }));
  });
  el.searchInput.addEventListener("input", () => applyFilters(true));
  el.sortSelect.addEventListener("change", () => applyFilters(true));
  el.categorySelect.addEventListener("change", () => applyFilters(true));
  el.firstBtn.addEventListener("click", () => {
    page = 1;
    renderList();
    syncNewsRoute();
  });
  el.prevBtn.addEventListener("click", () => {
    page -= 1;
    renderList();
    syncNewsRoute();
  });
  el.nextBtn.addEventListener("click", () => {
    page += 1;
    renderList();
    syncNewsRoute();
  });
  el.lastBtn.addEventListener("click", () => {
    page = Math.max(Math.ceil(filteredPosts.length / PAGE_SIZE), 1);
    renderList();
    syncNewsRoute();
  });
  el.backBtn.addEventListener("click", () => {
    if (detailBackPath) {
      pushRouteState(detailBackPath);
      routeFromLocation();
      return;
    }
    openHome(true);
  });
  window.addEventListener("popstate", () => {
    routeFromLocation();
  });
}

applyTheme(localStorage.getItem("theme") || "light");
bindEvents();
loadData().catch((error) => {
  el.homeView.classList.remove("hidden");
  el.homeView.innerHTML = `<p class="meta">데이터 로드 실패: ${escapeHtml(error.message)}</p>`;
});
