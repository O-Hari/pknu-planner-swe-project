# 부경대 강의 시간표 및 개인 일정 관리 웹앱

부경대학교 학생이 개설 강의를 검색하고 시간표를 구성하며, 강의계획서와 개인 일정을 함께 확인하는 웹 애플리케이션을 목표로 합니다.

현재 저장소에는 **FastAPI 백엔드, Supabase 데이터베이스 스키마, 공개 강의계획서 PDF 크롤러, 교과목 정보 추출 및 Supabase 동기화 스크립트**가 구현되어 있습니다. API 요청과 응답은 FastAPI의 Swagger UI에서 확인할 수 있습니다.

이 문서는 데이터베이스 설정, API 실행, 강의계획서 수집·검증·적재 방법을 안내합니다. 명령은 모두 **프로젝트 루트 디렉터리**에서 실행합니다.

## 저장소 구조

```text
.
├── .env.example                     # Supabase 환경 변수 예시
├── api/                             # FastAPI 및 API 전용 의존성
├── database/supabase/migrations/
│   ├── 001_core_schema.sql          # 강의·시간표·개인 일정 스키마 및 RLS
│   ├── 002_syllabus_files.sql       # PDF 저장 버킷 및 수집 메타데이터 테이블
│   └── 003_planner_api.sql          # 강의 카탈로그 연동 및 시간표 충돌 검사
├── scripts/
│   ├── requirements.txt            # Python 의존성
│   ├── syllabus/                   # 공개 목록 조회·PDF 수집·정보 추출
│   └── sync_crawled_syllabi.py      # 로컬 수집 결과 → Supabase 적재
└── crawled_syllabus/                # 실행 시 생성되는 수집 결과 (Git 제외)
```

## 1. Python 실행 환경 준비

- **Python 3.11 이상**: 파일 검증에 `hashlib.file_digest`를 사용합니다.
- **Supabase 프로젝트**: 데이터베이스·Storage 적재에 필요합니다. 로컬 수집·추출·`--dry-run`에는 Supabase 연결 정보가 필요하지 않습니다.
- 학교 공개 조회 시스템과 Supabase에 접근할 수 있는 네트워크 환경이 필요합니다.

Windows PowerShell 기준:

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r scripts/requirements.txt
```

PowerShell 실행 정책으로 가상환경 활성화가 차단되면, 활성화 없이 `.\.venv\Scripts\python.exe -m pip install -r scripts/requirements.txt`를 실행하고 이후 명령의 `python`도 `.\.venv\Scripts\python.exe`로 바꾸면 됩니다.

macOS / Linux에서는 다음과 같이 준비합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r scripts/requirements.txt
```

주요 의존성은 HTTP 요청용 `requests`와 PDF 검증·표 추출용 `PyMuPDF`입니다. Supabase 동기화와 `.env` 로딩은 Python 표준 라이브러리를 사용합니다. 브라우저 자동화 도구나 별도 크롤러 저장소는 필요하지 않습니다.

## 2. Supabase 데이터베이스 설정

### 2-1. 프로젝트 생성 및 SQL 적용

Supabase 프로젝트를 준비한 뒤, 해당 프로젝트의 **SQL Editor**에서 아래 파일 내용을 **순서대로** 실행합니다. 현재 저장소에는 로컬 Supabase 실행 설정이 없으므로 SQL Editor를 이용하는 절차를 기준으로 합니다.

| 순서 | SQL 파일 | 생성·설정 내용 |
|---|---|---|
| 1 | [001_core_schema.sql](database/supabase/migrations/001_core_schema.sql) | 강의·분반·수업 시간·강의계획서·시간표·개인 일정 테이블, 인덱스, 갱신 시각 트리거, RLS 정책 |
| 2 | [002_syllabus_files.sql](database/supabase/migrations/002_syllabus_files.sql) | 비공개 `course-syllabi` Storage 버킷, `public.syllabus_files` 테이블과 `course_metadata` 컬럼, 접근 권한 및 인덱스 |
| 3 | [003_planner_api.sql](database/supabase/migrations/003_planner_api.sql) | 기존·신규 PDF에서 학기·강의·분반·수업 시간 연결, 강의 검색 함수, 시간표 중복 검사 |

주요 테이블의 역할은 다음과 같습니다.

| 테이블 | 역할 |
|---|---|
| `academic_terms` | 학년도·학기 |
| `courses`, `course_sections`, `class_meetings` | 교과목·학기별 분반·수업 시간 |
| `syllabi` | 분반에 연결하는 강의계획서 및 파싱 상태 |
| `timetables`, `timetable_sections` | 사용자 시간표와 선택한 분반 |
| `personal_events` | 사용자 개인 일정 |
| `syllabus_files` | 현재 동기화 스크립트가 기록하는 PDF 경로·해시·교과목 추출 정보 |

`003`을 적용하면 이미 적재한 PDF의 메타데이터로 강의 카탈로그를 채우고 이후 동기화에도 자동으로 반영합니다. PDF는 `syllabus_files.course_section_id`로 분반에 연결되며, API는 해당 문서와 `syllabi`에 등록된 문서를 함께 제공합니다.

`001`은 강의 카탈로그 테이블에 `anon`·`authenticated` 조회 정책을, 사용자 데이터 테이블에는 `user_id = auth.uid()::text` 기준의 소유자 정책을 설정합니다. `syllabus_files`는 일반 앱 사용자의 접근 권한을 제거하고 `service_role`에 권한을 부여합니다.

### 2-2. 환경 변수 설정

새 환경에서는 루트의 [`.env.example`](.env.example)을 복사합니다. 기존 `.env`가 있다면 그 파일에 빠진 항목만 추가합니다.

```powershell
Copy-Item .env.example .env
```

macOS / Linux에서는 `cp .env.example .env`를 사용합니다. `.env.example`의 주석을 참고해 `.env`에 **같은 Supabase 프로젝트**의 URL과 키를 입력합니다.

FastAPI와 동기화 스크립트는 루트의 `.env`를 자동으로 읽습니다. 같은 이름의 변수가 프로세스에 이미 설정되어 있으면 해당 값이 우선합니다. `.env`는 Git에서 제외되며, 서버용 키는 서버 환경에 보관합니다.

### 2-3. 설정 확인

SQL Editor에서 다음 쿼리로 테이블과 버킷을 확인할 수 있습니다.

```sql
select
  to_regclass('public.academic_terms') as academic_terms,
  to_regclass('public.courses') as courses,
  to_regclass('public.syllabus_files') as syllabus_files;

select id, public, file_size_limit
from storage.buckets
where id = 'course-syllabi';
```

테이블 조회 결과가 `null`이 아니고, 버킷의 `public`이 `false`인지 확인합니다. 새로 생성된 버킷의 파일 크기 제한은 **52,428,800바이트(50 MiB)**입니다. 기존 버킷이 있으면 `002`가 설정을 덮어쓰지 않으므로 현재 비공개 여부와 용량 제한도 확인합니다.

이전 버전의 `002`를 적용해 `course_metadata`가 없다면 최신 `002_syllabus_files.sql`을 다시 실행하면 됩니다.

### 2-4. FastAPI 실행

`001`~`003`을 적용하고 `.env`에 프로젝트 연결 정보를 설정한 뒤 실행합니다.

```powershell
python -m pip install -r api/requirements.txt
python -m uvicorn api.main:app --reload
```

[Swagger UI](http://127.0.0.1:8000/docs)에서 요청을 보낼 수 있습니다. `GET /health`는 API 프로세스의 상태를 반환합니다. 시간표·이벤트·캘린더 요청은 Supabase Auth 로그인에서 받은 사용자 access token을 `Authorization: Bearer <access_token>` 헤더로 전달합니다. Swagger UI에서는 **Authorize**에 토큰을 입력합니다. Supabase의 소유자 정책(RLS)이 사용자 데이터에 적용됩니다.

서버를 실행한 뒤 `python scripts/api_test.py`로 공개 및 인증 API의 기본 응답을 확인할 수 있습니다. 테스트 계정 설정과 기능별 요청 예제는 [API 테스트 가이드](docs/API_TEST_GUIDE.md)를 참고하세요.

| 경로 | 메서드와 기능 |
|---|---|
| `/api/v1/terms` | GET 학기 목록 |
| `/api/v1/sections` | GET 강의명·교수명·학수번호 검색(`q`), 학기·요일·학수번호·학과·이수구분 필터 |
| `/api/v1/sections/{section_id}` | GET 분반·수업 시간 상세 |
| `/api/v1/sections/{section_id}/syllabi` | GET 국문·영문·공학인증 계획서와 등록된 계획서 목록 |
| `/api/v1/sections/{section_id}/syllabi/{syllabus_id}/download` | GET 5분 동안 유효한 비공개 PDF 열람 URL |
| `/api/v1/timetables`, `/api/v1/timetables/{timetable_id}` | GET 목록·상세, POST 생성, PUT 수정, DELETE 삭제 |
| `/api/v1/timetables/{timetable_id}/sections/{section_id}` | PUT 분반 추가, DELETE 분반 삭제 |
| `/api/v1/timetables/{timetable_id}/sections/{section_id}/conflicts` | GET 추가 여부와 겹치는 분반 ID 조회 |
| `/api/v1/events`, `/api/v1/events/{event_id}` | GET 목록·상세, POST 생성, PUT 수정, DELETE 삭제 |
| `/api/v1/calendar` | GET 날짜 범위의 개인 일정과 선택 시간표 강의 일정 |

- 목록 조회는 `limit`과 `offset`을 받습니다. 강의 검색의 `weekday`는 월요일 1부터 일요일 7까지입니다.
- 분반 추가 시 다른 학기나 시간이 겹치는 강의는 `409`를 반환합니다. 수업 시간이 같은 교시이거나 두 시각 구간이 겹칠 때 충돌로 판단합니다. 교시와 실제 시각 사이의 비교에는 학교 교시표가 필요합니다.
- 개인 일정의 `starts_at`, `ends_at` 및 목록의 `start`, `end`에는 `2026-11-23T09:00:00+09:00`처럼 시간대가 포함된 값을 사용합니다.
- 캘린더는 서울 기준 `start_date`부터 `end_date` 직전까지 최대 62일을 조회합니다. `timetable_id`가 있으면 시간표와 수업 패턴을 반환합니다. `academic_terms.starts_on`과 `ends_on`을 채우면 학기 범위 안의 요일별 강의 날짜도 반환합니다.

## 3. 공개 강의계획서 크롤링

부경대학교 공개 강의계획서 조회 시스템에서 개설 교과목과 분반 목록을 읽고, 게시된 계획서를 보고서 서버에서 PDF로 내려받습니다. 학교 계정 로그인 없이 공개된 학부·대학원 목록을 대상으로 하며, 미작성 계획서는 목록에 별도 기록합니다.

### 3-1. PDF 1건으로 먼저 확인

```powershell
python -m scripts.syllabus crawl --year 2026 --term 2 --limit 1
```

처음 실행하면 해당 학기의 목록을 `crawled_syllabus/_state/catalog-2026-2.json`에 저장하고, 미수집 PDF를 최대 1건 내려받습니다. `--limit`은 PDF 다운로드 수를 제한하며 목록 조회 범위를 줄이지는 않습니다.

수집 완료 여부는 생성된 목록 경로를 지정해 확인합니다.

```powershell
python -m scripts.syllabus status --manifest crawled_syllabus/_state/catalog-2026-2.json
```

`status`는 수집 이력과 실제 파일의 SHA-256을 확인해 `저장 완료 건수/목록 건수`를 표시합니다.

### 3-2. 학기 전체 수집 및 재개

```powershell
python -m scripts.syllabus crawl --year 2026 --term 2
```

같은 명령을 다시 실행하면 기존 목록과 완료 이력을 재사용하고 미수집 PDF부터 이어서 처리합니다. `Ctrl+C`로 중단해도 완료한 파일과 이력은 유지됩니다. 학교에서 계획서를 추가 게시했다면 목록을 갱신합니다.

```powershell
python -m scripts.syllabus crawl --year 2026 --term 2 --refresh-catalog
```

### 3-3. 수집 범위와 주요 옵션

```powershell
# 특정 학수번호·분반의 국문·영문 계획서 수집 (번호는 대상 강의에 맞게 변경)
python -m scripts.syllabus crawl --year 2026 --term 2 --course-no 104605 --section 101 --variants kor eng

# PDF 사이 대기 시간을 2초로 설정
python -m scripts.syllabus crawl --year 2026 --term 2 --delay 2
```

| 옵션 | 의미 |
|---|---|
| `--year` | 학년도. `crawl`에서는 필수이며 2021년 이후를 지원합니다. |
| `--term` | 학기. `1`(1학기), `summer`(여름학기), `2`(2학기), `winter`(겨울학기) 중 선택합니다. |
| `--variants` | 계획서 유형. `kor`(국문), `eng`(영문), `tech`(공학인증). 기본값은 전체 유형입니다. |
| `--course-no`, `--section` | 학수번호·분반으로 다운로드 대상을 제한합니다. |
| `--limit` | 이번 실행에서 내려받을 새 PDF의 최대 개수. 생략하면 전체입니다. |
| `--refresh-catalog` | 저장된 목록 대신 학교 공개 목록을 다시 조회합니다. |
| `--delay` | PDF 다운로드 사이 대기 시간(초). 기본값은 1초입니다. |
| `--timeout` | 다운로드 요청 타임아웃(초). 기본값은 120초입니다. |

예시의 `2026`, `2`는 원하는 학년도·학기로 바꿉니다. CLI와 추출 메타데이터는 계절학기에 소문자 `summer`·`winter`를 사용하며, `academic_terms.term_code` 스키마는 대문자 `SUMMER`·`WINTER`를 사용합니다.

## 4. 검증 및 Supabase 적재

### 4-1. 원격 업로드 없이 로컬 검증

```powershell
python scripts/sync_crawled_syllabi.py --year 2026 --term 2 --dry-run
```

성공 이력에 등록된 PDF의 경로·교과목 식별자·해시를 확인하고, PDF에서 교과목 정보를 추출해 적재 대상을 검증합니다. 이 단계는 Supabase에 접속하거나 업로드하지 않으며 연결용 환경 변수도 필요하지 않습니다. `--local-only`도 현재 동일하게 원격 적재 없이 종료합니다.

### 4-2. 소량 업로드 후 전체 적재

```powershell
# DB·버킷·키 설정 확인 후 1건 적재
python scripts/sync_crawled_syllabi.py --year 2026 --term 2 --limit 1

# 선택 학기의 수집 완료 PDF 전체 적재
python scripts/sync_crawled_syllabi.py --year 2026 --term 2
```

연도·학기를 생략하면 전체 수집 완료 이력을 대상으로 합니다. 동기화의 `--limit`은 선택된 파일 수를 제한하며 `0` 또는 생략 시 전체입니다. 수집 명령의 `--limit`과 달리 이미 적재한 파일도 선택 건수에 포함합니다.

PDF는 비공개 **`course-syllabi`** 버킷의 `crawler/<source_id>/syllabus.pdf`에 저장하고, 원본 파일명·출처·해시·크기·Storage 경로·교과목 정보는 **`public.syllabus_files`**에 기록합니다. `source_id`를 기준으로 upsert합니다.

- 원격 DB의 해시·메타데이터가 같고 Storage 파일 크기도 일치하면 건너뜁니다.
- 파일은 같고 추출 메타데이터만 달라졌다면 DB 정보만 갱신합니다.
- 동일한 파일도 다시 업로드하려면 `--force`를 추가합니다.
- 일시적인 연결 오류와 일부 HTTP 오류(429·5xx 등)는 요청당 최대 3회 시도합니다. 실패 후 같은 명령으로 재실행할 수 있습니다.

적재 결과는 터미널에 업로드·정보 갱신·건너뜀·실패 건수로 표시하며, 파일별 결과는 `crawled_syllabus/_state/supabase-sync.jsonl`에 누적 기록합니다. 실패 건이 있으면 종료 코드는 `1`입니다.

SQL Editor에서 최근 적재 결과를 확인합니다.

```sql
select
  source_id,
  course_title,
  course_metadata ->> 'staff_name' as instructor,
  course_metadata ->> 'year' as academic_year,
  course_metadata ->> 'term' as term,
  storage_path,
  synced_at
from public.syllabus_files
order by synced_at desc
limit 10;
```

### 4-3. 추출과 업로드를 분리해서 실행

교과목 정보를 먼저 JSON으로 확인한 뒤, 저장된 추출 결과로 적재할 수도 있습니다.

```powershell
# 로컬 PDF → 교과목 정보 JSON (Supabase 연결 불필요)
python -m scripts.syllabus extract --year 2026 --term 2

# 저장된 JSON을 사용한 로컬 검증
python scripts/sync_crawled_syllabi.py --year 2026 --term 2 --upload-only --dry-run

# PDF 표를 다시 추출하지 않고 적재
python scripts/sync_crawled_syllabi.py --year 2026 --term 2 --upload-only
```

`extract`는 `crawled_syllabus/course-data.json`에 **이번 실행에서 선택한 범위의 스냅샷**을 저장합니다. 다시 실행하면 이 파일을 새 결과로 갱신합니다. `--upload-only`는 선택한 모든 대상의 추출 결과가 있어야 하며, 경로·해시·교과목 식별자가 맞지 않으면 재추출을 안내하고 중단합니다. 추출 이후 PDF를 추가 수집했다면 같은 범위로 `extract`를 다시 실행합니다.

일반 동기화 명령은 PDF에서 직접 추출하므로 `course-data.json`을 미리 생성할 필요가 없습니다.

## 5. 수집 결과와 데이터 해석

```text
crawled_syllabus/
├── 2026/2/<학수번호>_<분반>_<교과목>_<유형>_<작업ID>.pdf
├── _state/
│   ├── catalog-2026-2.json    # 공개 교과목 목록, 작업 목록, 미작성 계획서
│   ├── downloads.json         # 수집 성공 이력, 식별정보, SHA-256, 오류
│   └── supabase-sync.jsonl    # 파일별 원격 적재 결과
├── _incoming/                 # PDF 저장 중 사용하는 임시 파일
└── course-data.json            # extract 명령으로 생성하는 추출 결과
```

`course_metadata`에는 강의명, 교수명, 학수번호, 분반, 학년도·학기, 학과, 학점, 수업 시간 원문(`class_time_raw`), 강의실 원문(`classroom_raw`), 요일·교시(`meetings`) 등을 저장합니다.

- 공개 목록의 한글 이름을 우선 사용하고 PDF 원문 표기는 `pdf_fields`에 보존합니다.
- 원문에 시간·강의실이 비어 있으면 `null`로 두고 `missing_fields`에 기록합니다. 정상적인 빈 값은 추출 실패로 처리하지 않습니다.
- `월5,6 목5`처럼 교시만 있으면 요일·교시를 보존하며, 시작·종료 시각은 원문에 명시된 경우에만 저장합니다.
- 표를 찾지 못하거나 시간 표기를 해석하지 못하면 `warnings`에 기록하고 `extraction_status`를 `partial`로 표시합니다.
- 국문·영문·공학인증 계획서는 각각의 문서로 유지합니다.

PDF만 수집 폴더에 복사한 파일은 적재 대상이 아닙니다. 크롤러의 검증된 성공 이력이 필요하므로 PDF와 `downloads.json`을 함께 보존합니다.

## 6. 문제 해결

| 증상 | 확인 및 조치 |
|---|---|
| `No module named requests` 또는 `fitz` | 현재 사용하는 Python으로 `python -m pip install -r scripts/requirements.txt`를 실행합니다. |
| `No module named scripts` | 프로젝트 루트에서 `python -m scripts.syllabus ...` 형식으로 실행합니다. |
| 정규 교과목 수집 이력이 없음 | 먼저 `crawl --year 2026 --term 2 --limit 1`을 실행합니다. |
| 저장된 목록의 범위가 요청과 다름 | 요청한 연도·학기·유형을 확인하고 `--refresh-catalog`로 목록을 갱신합니다. |
| Supabase URL·키가 없거나 예시 값임 / 401·403 | 루트 `.env`에 같은 프로젝트의 URL과 서버용 키를 설정합니다. 기존 프로세스 환경 변수가 우선하는지도 확인합니다. |
| `syllabus_files` 또는 `course_metadata`가 없음 | SQL Editor에서 `001` 적용 여부를 확인하고 최신 `002`를 실행합니다. |
| 버킷이 없거나 공개로 설정됨 | `002` 적용 여부와 `course-syllabi`의 비공개 설정을 확인합니다. |
| `course-data.json`이 없거나 파일과 추출 결과가 다름 | 같은 연도·학기로 `extract`를 다시 실행한 뒤 `--upload-only`를 재시도합니다. |
| 기존 성공 파일이 없거나 변경됨 | `downloads.json`에 대응하는 원본 PDF를 복원하고 경로·해시를 확인합니다. |
| 학교 서버의 접근 거부·로그인 리디렉션·PDF 식별 오류 | 수집을 중단하고 `downloads.json`의 오류와 학교 공개 조회 시스템 상태를 확인합니다. |

상세한 수집 동작은 [크롤러 README](scripts/syllabus/README.md), 전체 옵션은 다음 도움말에서 확인할 수 있습니다.

```powershell
python -m scripts.syllabus --help
python -m scripts.syllabus crawl --help
python -m scripts.syllabus extract --help
python scripts/sync_crawled_syllabi.py --help
```
