# DART 자본 금리민감도 분석 대시보드

주요 생명보험사(삼성생명, 한화생명, 교보생명)의 자본총계와 시장 금리(미국 10년물, 한국 국고채 10년물) 추이를 분석하여 금리 민감도(듀레이션)를 산출하는 웹 애플리케이션입니다.

## 주요 기능

- **자본총계 추이**: DART API를 통한 분기별 자본총계(별도 재무제표 기준) 조회
- **금리 데이터**: FRED(미국), ECOS(한국) API를 통한 시장 금리 조회
- **듀레이션 분석**: 금리 변동에 대한 자본 민감도(듀레이션) 계산
- **시각화**: Chart.js 기반 트렌드 차트 및 Scatter Plot

## 기술 스택

### Backend
- Python 3.11+
- Flask 3.0.0
- Gunicorn 21.2.0
- Pandas 2.1.4, NumPy 1.26.2
- cachetools 5.3.2

### Frontend
- HTML5 / CSS3 (Google Material Design)
- JavaScript (ES6+)
- Chart.js 4.4.1

## 로컬 실행

### 1. 가상환경 생성 및 활성화

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS/Linux
python3 -m venv venv
source venv/bin/activate
```

### 2. 의존성 설치

```bash
pip install -r requirements.txt
```

### 3. 환경변수 설정

`.env.example`을 복사하여 `.env` 파일을 생성하고, API 키를 설정합니다.

```bash
cp .env.example .env
```

`.env` 파일 편집:
```
DART_API_KEY=your_dart_api_key
ECOS_API_KEY=your_ecos_api_key
FRED_API_KEY=your_fred_api_key
```

### 4. 애플리케이션 실행

```bash
# 개발 모드
python app.py

# 또는 Flask CLI
flask run --host=0.0.0.0 --port=5000
```

브라우저에서 `http://localhost:5000` 접속

## 프로덕션 실행 (Gunicorn)

```bash
gunicorn app:app --bind 0.0.0.0:8000 --workers 2 --threads 4 --timeout 120
```

## Render 배포

### 1. GitHub 리포지토리 생성 및 푸시

```bash
git init
git add .
git commit -m "Initial commit: DART equity vs rates duration dashboard"
git branch -M main
git remote add origin https://github.com/<YOUR_GITHUB_ID>/DART_v2.git
git push -u origin main
```

### 2. Render 대시보드 설정

1. [Render](https://render.com) 로그인
2. **New +** → **Web Service** 선택
3. GitHub 연결 후 `DART_v2` 리포지토리 선택
4. 설정:
   - **Branch**: `main`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120`
5. **Environment Variables** 추가:
   - `DART_API_KEY`
   - `ECOS_API_KEY`
   - `FRED_API_KEY`
6. **Create Web Service** 클릭

### 3. 배포 확인

배포 완료 후 생성된 URL (예: `https://dart-v2.onrender.com`)에서:
- `GET /` - 메인 페이지 로드 확인
- `GET /api/companies` - API 정상 동작 확인
- 회사 선택 → 분석 실행 → 차트 표시 확인

## API 엔드포인트

### GET /api/companies
분석 대상 회사 목록 반환

```json
[
  {"id": "samsung", "name": "삼성생명"},
  {"id": "hanwha", "name": "한화생명"},
  {"id": "kyobo", "name": "교보생명"}
]
```

### POST /api/analyze
자본-금리 민감도 분석 실행

**Request:**
```json
{
  "company_id": "samsung",
  "year_count": 3
}
```

**Response:**
```json
{
  "quarters": ["2022-12-31", "2023-03-31", ...],
  "equity_level": [123456, 125000, ...],
  "us10y_level": [3.8, 4.1, ...],
  "kr10y_level": [3.2, 3.3, ...],
  "equity_qoq": [null, 0.016, ...],
  "us10y_change": [null, 0.003, ...],
  "kr10y_change": [null, 0.001, ...],
  "duration": {
    "us10y": {"series": [...], "summary": 12.3},
    "kr10y": {"series": [...], "summary": 9.8}
  }
}
```

### GET /api/health
헬스 체크

```json
{
  "status": "healthy",
  "dart_api": true,
  "ecos_api": true,
  "fred_api": true
}
```

## API 키 발급 방법

- **DART**: https://opendart.fss.or.kr (회원가입 후 API 키 발급)
- **ECOS**: https://ecos.bok.or.kr/api (회원가입 후 인증키 발급)
- **FRED**: https://fred.stlouisfed.org/docs/api/api_key.html (계정 생성 후 API 키 발급)

## 프로젝트 구조

```
DART_v2/
├── app.py              # Flask 백엔드
├── templates/
│   └── index.html      # 프론트엔드
├── requirements.txt    # Python 의존성
├── runtime.txt         # Python 버전 지정
├── render.yaml         # Render 배포 설정
├── .env.example        # 환경변수 템플릿
└── README.md           # 문서
```

## 라이선스

MIT License
