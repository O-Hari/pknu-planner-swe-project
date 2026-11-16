# 정규 교과목 강의계획서 수집

부경대학교 공개 강의계획서 조회 시스템에서 학년도·학기별 개설 교과목과
분반을 조회한 뒤, 보고서 서버에서 게시된 강의계획서를 PDF로 내려받습니다.
실행에는 이 프로젝트의 코드만 사용합니다.

```powershell
python -m pip install -r scripts/requirements.txt

# 2026년 2학기 공개 계획서 중 미수집 PDF 1건부터 확인
python -m scripts.syllabus crawl --year 2026 --term 2 --limit 1

# 같은 학기 전체 수집 / 중단 후 같은 명령으로 재개
python -m scripts.syllabus crawl --year 2026 --term 2

# 특정 교과목·분반, 국문과 영문
python -m scripts.syllabus crawl --year 2026 --term 2 --course-no 104605 --section 101 --variants kor eng

# 수집 파일 검증 후 Supabase 동기화
python scripts/sync_crawled_syllabi.py --year 2026 --term 2 --dry-run
python scripts/sync_crawled_syllabi.py --year 2026 --term 2

# PDF를 업로드하지 않고 교과목 데이터만 로컬 JSON으로 추출
python -m scripts.syllabus extract --year 2026 --term 2

# 이미 추출한 course-data.json을 사용해 적재만 실행
python scripts/sync_crawled_syllabi.py --year 2026 --term 2 --upload-only
```

학기는 `1`, `summer`, `2`, `winter`, 계획서 유형은 `kor`, `eng`, `tech`를
지원합니다. 기본 대상은 학부·대학원의 공개 목록에 게시된 모든 유형이며,
미작성 계획서는 별도 기록합니다. `--refresh-catalog`로 목록을 갱신할 수 있습니다.

```text
crawled_syllabus/
  2026/2/<학수번호>_<분반>_<교과목>_<유형>_<작업ID>.pdf
  _state/catalog-2026-2.json  # 교과목 목록
  _state/downloads.json      # 성공 이력, 교과목 정보, SHA-256
  _incoming/                # 저장 중 임시 PDF
  course-data.json           # 선택 범위의 교과목 정보 추출 결과
  _state/supabase-sync.jsonl # 파일별 Supabase 적재 결과
```

PDF 헤더의 학수번호·분반·학년도·학기·유형과 파일 해시를 검증합니다.
이력에 등록된 PDF만 Supabase 동기화 대상으로 사용합니다. 기존 공지 첨부 ZIP은
동기화 대상에 포함하지 않습니다. Supabase SQL Editor에서 migrations의 `001`,
`002`를 순서대로 적용하고 루트 `.env`에 프로젝트 URL과 서버용 키를 설정하세요.
이전 버전의 `002`를 이미 실행했다면 최신 `002_syllabus_files.sql`을 다시 실행하면 됩니다.
파일은 비공개 `course-syllabi` 버킷, 교과목 식별정보는 `syllabus_files.course_metadata`에 저장합니다.

`course_metadata`에는 강의명, 교수명, 학수번호, 분반, 학년도·학기, 학과, 학점,
수업 시간 원문(`class_time_raw`), 장소 원문(`classroom_raw`), 요일·교시(`meetings`)를
함께 저장합니다. 공개 목록의 한글 이름을 기준으로 하며 PDF의 영문·국문 표기는
`pdf_fields`에 보존합니다. 표에서 빈 값은 `null`로 남기고 `missing_fields`에 기록합니다.
시간·강의실이 원문에 없는 것은 정상적인 누락으로 처리하며 추출 성공 건수에 포함합니다.
`일부 미확인`은 표를 찾지 못했거나 원문 시간 표기를 해석하지 못해 경고가 있는 경우에만 표시합니다.
연구실·상담시간과 수업의 장소·시간을 혼동하지 않도록 첫 페이지의 교과목 표만 읽습니다.
국문·영문·공학인증 PDF는 각각의 문서로 유지하며 동일한 학수번호·분반으로 연결할 수 있습니다.

`월5,6 목5`처럼 교시만 기재된 경우 요일과 `[5, 6]` 등의 교시를 보존하고
시작·종료 시각은 `null`로 둡니다. 원문에 시각이 명시된 경우에만 `starts_at`,
`ends_at`에 저장합니다. 해석하지 못한 시간은 `warnings`에 남기며 추정하지 않습니다.
기존에 다운로드한 PDF도 재수집 없이 `extract` 또는 동기화 명령으로 처리할 수 있습니다.
`course-data.json`은 명령 실행 시 선택한 범위와 완료 이력의 스냅샷입니다.
`--upload-only`를 지정하면 이 JSON의 추출 데이터를 그대로 사용하며 PDF 표를 다시 읽지 않습니다.
수집 이력과 로컬 파일의 해시·경로·교과목 식별자가 일치하는지 확인한 뒤 적재합니다.
선택한 범위의 추출 결과가 없거나 파일이 변경됐다면 중단하고 재추출을 안내합니다.

추출과 적재 명령은 터미널에 진행 막대, 완료/전체 건수, 진행률, 현재 강의·분반,
처리 단계, 경과 시간과 예상 잔여 시간을 표시합니다. 추출 시 성공·일부 미확인·실패,
적재 시 업로드·정보 갱신·건너뜀·실패 건수를 구분합니다. 적재 전 PDF 검증·추출
과정에도 진행 표시가 나옵니다. PDF 업로드, Storage 검증, DB 기록 단계와 네트워크
재시도를 확인할 수 있습니다. 예상 시간은 완료 건의 평균 처리 속도로 계산하며,
터미널에서는 한 줄을 갱신하고 출력 리디렉션 시 일반 로그 행으로 남깁니다.

적재 전 테이블과 비공개 버킷의 접근·용량 설정을 확인합니다. 원격 DB의 해시와
메타데이터가 같고 Storage 파일 크기도 일치하면 건너뜁니다. 파일은 같고 추출 정보만
변경됐다면 DB만 갱신합니다. Storage 확인 후 DB를 upsert하고 서버가 반환한 행을
확인한 뒤 성공 이력을 기록합니다. 일시적인 네트워크 오류·429·5xx 응답은 최대 3번
시도하며, 같은 명령으로 재실행해 실패 항목을 다시 처리할 수 있습니다.
`--force`는 같은 파일도 재업로드하고, `--limit 1`은 선택한 범위에서 1건만 처리합니다.
크롤링 중에도 완료된 PDF의 스냅샷을 적재할 수 있으며, 새로 수집된 파일은 다음 실행에서
처리합니다. 수집 이력과 적재 이력의 파일·잠금을 분리합니다.

PDF 사이에 기본 1초를 기다립니다. 접근 거부나 로그인 리디렉션, PDF 식별정보
오류가 나면 해당 단계에서 중단하고 `_state/downloads.json`에 오류를 기록합니다.

이식 출처: 기존 프로젝트의 정규 교과목 크롤러 모듈
(`origin/main`, `e39bf4a7f18aa317612d7510800058e041835165`).
소스를 복사하고 경로·TLS 보조 코드를 이 프로젝트에 맞게 정리했습니다.
