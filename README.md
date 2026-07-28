# News Archive Pages

취업·경제·IT 분야의 뉴스를 누구나 쉽고 빠르게 볼 수 있는 AI 요약 기사 아카이빙 사이트

<img width="1293" height="909" alt="image" src="https://github.com/user-attachments/assets/185edef0-0583-4237-85d8-a4139c382107" />

## 주요 기능

### 일/주/월 간 기사 데이터를 기반으로 한 트렌딩 키워드 및 워드 클라우드 

<img width="976" height="756" alt="{1771604D-1390-492B-8D6A-DCDF6C65120B}" src="https://github.com/user-attachments/assets/47be7d8c-7865-41d8-b033-172a0ad636ee" />

### 누구나 쉽게 이해할 수 있도록 AI 기반 수준별 기사 요약 기능 제공

<img width="1003" height="488" alt="image" src="https://github.com/user-attachments/assets/2fb03e79-37a1-4b16-97bb-34394be774ed" />

### 2,000 건이 넘는 아카이빙 기사 데이터 제공

<img width="1144" height="914" alt="{79380F95-5354-4775-B81E-DFA58CA7E1A9}" src="https://github.com/user-attachments/assets/d1542fad-c00e-46f8-9dd4-412408f1e7c6" />

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

## 3시간 자동 수집 + 배포 (GitHub Actions)
- 워크플로: `.github/workflows/deploy-pages.yml`
- 주기: `0 */3 * * *` (UTC 기준 3시간 간격)
- 수행 순서:
  1) NewsAPI 수집(`scripts/update_archive_only.py`)
  2) `data/news_archive.jsonl` 및 `data/news_archive.001.jsonl` 형태의 분할 아카이브 누적 업데이트
  3) `docs/data/news_archive.manifest.json` + 분할 JSON 생성
  4) 변경분 commit/push
  5) GitHub Pages 배포

필수 Secrets (Repository Settings > Secrets and variables > Actions):
- `NEWSAPI_KEY` (필수)
- `OPENAI_API_KEY` (선택, 요약 품질 개선용)

## Security Notes
- 외부 CDN/외부 JS 의존성 없이 self-hosted 정적 파일만 사용
- CSP, Referrer Policy, X-Frame-Options(meta) 적용
- DOM 렌더링 시 `textContent` 사용으로 XSS 위험 최소화
