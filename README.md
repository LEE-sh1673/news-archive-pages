# News Archive Pages

GitHub Pages용 정적 사이트입니다.
데이터 원본은 `/home/lsh/news_archive/data/news_archive.jsonl` 입니다.

## Local Build
```bash
/home/lsh/news_archive_pages/scripts/build_data.sh
```

## Local Preview
```bash
cd /home/lsh/news_archive_pages/docs
python3 -m http.server 8080
```
접속: `http://localhost:8080`

## Deploy to GitHub Pages
1. 저장소 생성(예: `news-archive-pages`) 후 이 폴더 내용을 push
2. 브랜치 `main`에 push
3. GitHub repository settings > Pages > Source: `GitHub Actions`
4. `.github/workflows/deploy-pages.yml` 실행 확인

## Security Notes
- 외부 CDN/외부 JS 의존성 없이 self-hosted 정적 파일만 사용
- CSP, Referrer Policy, X-Frame-Options(meta) 적용
- DOM 렌더링 시 `textContent` 사용으로 XSS 위험 최소화
