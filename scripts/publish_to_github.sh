#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${1:-}"
if [[ -z "$REPO_URL" ]]; then
  echo "Usage: $0 <git_repo_url>"
  echo "Example: $0 git@github.com:<user>/news-archive-pages.git"
  exit 1
fi

cd /home/lsh/news_archive_pages

python3 scripts/build_data.py

if [[ ! -d .git ]]; then
  git init
  git branch -M main
fi

git add .
git commit -m "Deploy news archive pages" || true

if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REPO_URL"
else
  git remote add origin "$REPO_URL"
fi

git push -u origin main
