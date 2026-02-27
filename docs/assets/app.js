const PAGE_SIZE = 10;
let allPosts = [];
let filteredPosts = [];
let page = 1;

const el = {
  homeTitle: document.getElementById("homeTitle"),
  themeToggle: document.getElementById("themeToggle"),
  toolbar: document.querySelector(".toolbar"),
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
  detailAiSummary: document.getElementById("detailAiSummary"),
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
    .map((x) => x.trim())
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

function parseDate(value) {
  const d = new Date(value || 0);
  return Number.isNaN(d.getTime()) ? new Date(0) : d;
}

function relativeTime(value) {
  const now = Date.now();
  const ts = parseDate(value).getTime();
  const diffSec = Math.max(Math.floor((now - ts) / 1000), 0);
  if (diffSec < 60) return `${diffSec}초 전`;
  const m = Math.floor(diffSec / 60);
  if (m < 60) return `${m}분 전`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}시간 전`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}일 전`;
  const mo = Math.floor(d / 30);
  if (mo < 12) return `${mo}개월 전`;
  return `${Math.floor(mo / 12)}년 전`;
}

function sortPosts(posts, sortKey) {
  const sorted = [...posts];
  const fetchedOf = (x) => x.fetched_at || x.archived_at || x.article_published_at || x.published_at;
  if (sortKey === "latest") {
    sorted.sort((a, b) => parseDate(fetchedOf(b)) - parseDate(fetchedOf(a)));
  } else if (sortKey === "oldest") {
    sorted.sort((a, b) => parseDate(fetchedOf(a)) - parseDate(fetchedOf(b)));
  } else if (sortKey === "title") {
    sorted.sort((a, b) => (a.title || "").localeCompare(b.title || "", "ko"));
  } else if (sortKey === "category") {
    sorted.sort((a, b) => (a.category || "").localeCompare(b.category || "", "ko"));
  }
  return sorted;
}

function applyFilters() {
  const q = el.searchInput.value.trim().toLowerCase();
  const category = el.categorySelect.value;
  const base = allPosts.filter((p) => {
    if (category !== "all" && p.category !== category) return false;
    if (!q) return true;
    const hay = `${p.title || ""} ${p.summary || ""} ${p.body || ""}`.toLowerCase();
    return hay.includes(q);
  });
  filteredPosts = sortPosts(base, el.sortSelect.value);
  page = 1;
  renderList();
}

function goHome(resetAll = false) {
  if (resetAll) {
    el.searchInput.value = "";
    el.sortSelect.value = "latest";
    el.categorySelect.value = "all";
    filteredPosts = sortPosts([...allPosts], "latest");
    page = 1;
  }
  el.detailView.classList.add("hidden");
  el.listView.classList.remove("hidden");
  el.toolbar.classList.remove("hidden");
  renderList();
}

function openDetail(id) {
  const post = allPosts.find((p) => String(p.id) === String(id));
  if (!post) return;
  el.toolbar.classList.add("hidden");
  el.listView.classList.add("hidden");
  el.detailView.classList.remove("hidden");

  const a = document.createElement("a");
  a.href = post.url || "#";
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  a.textContent = post.title || "제목 없음";

  el.detailTitle.textContent = "";
  el.detailTitle.appendChild(a);
  const articlePublishedAt = post.article_published_at || post.published_at || "-";
  const fetchedAt = post.fetched_at || post.archived_at || "-";
  el.detailMeta.textContent = `카테고리: ${post.category || "-"} | 기사 생성일: ${articlePublishedAt} | 수집일: ${fetchedAt}`;
  el.detailAiSummary.textContent = post.ai_summary || "요약할 수 없는 내용입니다";
  el.detailBody.innerHTML = bulletTextToHtml(post.body || post.summary || "");
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
  rows.forEach((p, idx) => {
    const item = document.createElement("div");
    item.className = "item";

    const head = document.createElement("div");
    head.className = "item-head";

    const rank = document.createElement("div");
    rank.className = "rank";
    rank.textContent = `${start + idx + 1}.`;

    const body = document.createElement("div");
    const title = document.createElement("a");
    title.className = "title";
    title.textContent = p.title || "제목 없음";
    title.href = "#";
    title.addEventListener("click", (e) => {
      e.preventDefault();
      openDetail(p.id);
    });

    const summary = document.createElement("div");
    summary.className = "summary";
    summary.textContent = p.summary || "";

    const meta = document.createElement("div");
    meta.className = "meta";
    const articlePublishedAt = p.article_published_at || p.published_at || "-";
    const fetchedAt = p.fetched_at || p.archived_at || "-";
    meta.textContent = `수집 ${relativeTime(fetchedAt)} | ${p.category || "-"} | 기사 생성일 ${articlePublishedAt}`;

    body.appendChild(title);
    body.appendChild(summary);
    body.appendChild(meta);
    head.appendChild(rank);
    head.appendChild(body);
    item.appendChild(head);
    el.listContainer.appendChild(item);
  });
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

async function loadData() {
  const res = await fetch("./data/news_archive.json", { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to load data: ${res.status}`);
  allPosts = await res.json();
  filteredPosts = sortPosts([...allPosts], "latest");
  renderList();
}

function bindEvents() {
  el.homeTitle.addEventListener("click", (e) => {
    e.preventDefault();
    goHome(true);
  });
  el.themeToggle.addEventListener("click", toggleTheme);
  el.searchInput.addEventListener("input", applyFilters);
  el.sortSelect.addEventListener("change", applyFilters);
  el.categorySelect.addEventListener("change", applyFilters);
  el.firstBtn.addEventListener("click", () => {
    page = 1;
    renderList();
  });
  el.prevBtn.addEventListener("click", () => {
    page -= 1;
    renderList();
  });
  el.nextBtn.addEventListener("click", () => {
    page += 1;
    renderList();
  });
  el.lastBtn.addEventListener("click", () => {
    page = Math.max(Math.ceil(filteredPosts.length / PAGE_SIZE), 1);
    renderList();
  });
  el.backBtn.addEventListener("click", () => {
    goHome(false);
  });
}

applyTheme(localStorage.getItem("theme") || "light");
bindEvents();
loadData().catch((err) => {
  el.metaLine.textContent = `데이터 로드 실패: ${err.message}`;
});
