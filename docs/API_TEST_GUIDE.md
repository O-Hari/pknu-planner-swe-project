# PKNU Planner API 테스트 가이드

Swagger UI에서 강의 검색부터 시간표·개인 일정 등록까지 순서대로 테스트합니다. 각 요청의 HTTP 상태 코드와 응답 본문을 확인하고, 생성 응답의 ID를 이후 조회·수정·삭제에 사용합니다.

## 테스트 환경 준비

1. 개발용 Supabase 프로젝트에 [데이터베이스 마이그레이션](../database/supabase/migrations/) `001`~`003`을 순서대로 적용합니다.
2. 프로젝트 루트의 `.env`에 같은 프로젝트의 `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SERVICE_ROLE_KEY`를 설정합니다. 레거시 프로젝트에서는 publishable 키 항목을 비우고 `SUPABASE_ANON_KEY`를 사용할 수 있습니다. 서버용 키는 서버의 `.env`에 보관합니다.
3. [README의 적재 절차](../README.md#4-검증-및-supabase-적재)를 따라 테스트할 강의계획서 PDF를 적재합니다. 충돌 테스트에는 같은 학기의 겹치는 분반 두 개가 필요합니다. 캘린더에서 날짜별 강의까지 확인하려면 테스트 학기의 `academic_terms.starts_on`과 `ends_on`을 채웁니다.
4. 사용자 격리 테스트에 사용할 Supabase Auth 계정 A와 B를 준비합니다. 사용할 테스트 계정의 이메일과 비밀번호를 루트 `.env`의 `API_TEST_EMAIL`과 `API_TEST_PASSWORD`에 입력합니다.

프로젝트 루트에서 실행합니다.

```powershell
python -m pip install -r api/requirements.txt
python -m uvicorn api.main:app --reload
```

[Swagger UI](http://127.0.0.1:8000/docs)에서 아래 세 그룹 중 하나를 선택하고, 엔드포인트를 펼쳐 **Try it out** → **Execute**를 누릅니다. **강의계획서 관련**의 `GET /api/v1/terms`로 Supabase 연결과 데이터 조회를 확인할 수 있습니다. 서버 상태는 [`/health`](http://127.0.0.1:8000/health)에서 확인합니다.

## 기본 API 테스트

서버를 켠 상태에서 별도의 PowerShell 창을 열고 프로젝트 루트에서 실행합니다.

```powershell
.\.venv\Scripts\python.exe scripts\api_test.py
```

스크립트는 서버 상태, 학기 목록, 토큰 없는 시간표 요청의 `401`을 확인합니다. 이어서 `.env`의 테스트 계정으로 로그인하고 시간표·개인 일정 목록이 `200`을 반환하는지 검사합니다. 서버를 다른 주소에서 실행 중이면 `--base-url`에 해당 주소를 지정합니다.

## Swagger UI 인증

Swagger UI에서 인증 없이 `GET /api/v1/timetables`를 실행하면 `401`이 반환됩니다. 계정 A로 Supabase Auth에 로그인한 뒤 **Authorize**의 **Value**에 응답의 `access_token` 값만 입력합니다. Swagger UI가 `Authorization: Bearer <access_token>` 헤더를 붙입니다.

Swagger UI용 토큰만 발급받으려면 아래 명령을 실행합니다. [Supabase 비밀번호 로그인](https://supabase.com/docs/guides/auth/passwords) · [Auth 토큰 API](https://supabase.com/docs/reference/self-hosting-auth/v1/password-based-signup-with-either-email-or-phone)

```powershell
.\.venv\Scripts\python.exe scripts\api_test.py --token-only
```

출력된 토큰을 **Authorize**에 입력합니다. 토큰과 PDF 서명 URL은 테스트 결과를 공유할 때 가립니다.

## 강의계획서 관련

| 요청 | 입력 및 기대 결과 |
|---|---|
| `GET /api/v1/terms` | `200`. 사용할 학기의 `id`를 `term_id`로 기록합니다. |
| `GET /api/v1/sections` | `term_id`와 알려진 강의명 또는 교수명 일부를 `q`에 입력합니다. `200`과 일치하는 분반이 반환됩니다. `weekday`에 1(월)~7(일)을 넣어 요일 필터도 확인합니다. |
| `GET /api/v1/sections/{section_id}` | 검색 결과의 분반 `id`를 입력합니다. `200`과 교과목 정보, 수업 시간 `meetings`가 반환됩니다. |
| `GET /api/v1/sections/{section_id}/syllabi` | 문서가 적재된 분반으로 호출합니다. `200` 응답에서 `download_available: true`인 문서의 `id`를 기록합니다. |
| `GET /api/v1/sections/{section_id}/syllabi/{syllabus_id}/download` | `200`, `expires_in: 300`. 응답의 `url`을 열어 PDF가 표시되는지 확인합니다. |

검색에 `weekday=8`을 보내면 `422`가 반환됩니다. 존재하지 않는 분반 ID로 상세를 요청하면 `404`와 `detail.code: section_not_found`가 반환됩니다.

## 시간표 관련

계정 A로 `POST /api/v1/timetables`에 아래 본문을 보내고 `201` 응답의 `id`를 `timetable_id`로 기록합니다. 예제의 `term_id`는 앞에서 조회한 값으로 바꿉니다.

```json
{
  "term_id": 1,
  "title": "API 테스트 시간표",
  "is_primary": false
}
```

1. `PUT /api/v1/timetables/{timetable_id}/sections/{section_id}`로 같은 학기 분반 A를 추가합니다. `204`가 반환됩니다. 같은 요청을 반복한 뒤 `GET /api/v1/timetables/{timetable_id}`를 호출해 분반 A가 한 번만 들어 있는지 확인합니다.
2. 분반 A와 같은 요일의 같은 교시 또는 겹치는 실제 시각을 가진 분반 B를 선택합니다. `GET /api/v1/timetables/{timetable_id}/sections/{section_id}/conflicts`에 분반 B의 ID를 넣으면 `200` 응답의 `conflicting_section_ids`에 분반 A의 ID가 포함됩니다.
3. 분반 B 추가를 요청하면 `409`와 `detail.code: class_time_conflict`가 반환됩니다. 다른 학기의 분반을 추가하면 `409`와 `detail.code: different_term`이 반환됩니다. 앞 수업의 종료 시각과 다음 수업의 시작 시각이 같으면 추가할 수 있습니다. 충돌 테스트에는 두 분반의 시간이 같은 형식으로 기록된 데이터를 사용합니다.
4. `PUT /api/v1/timetables/{timetable_id}`로 `title`과 `is_primary`를 보냅니다. `200` 응답과 이후 상세 조회에서 변경 내용을 확인합니다.

동시 요청을 확인하려면 빈 테스트 시간표에 서로 겹치는 두 분반의 추가 요청을 병렬로 보냅니다. 하나는 `204`, 다른 하나는 `409`가 반환되고 상세 조회에는 분반 하나만 있어야 합니다.

## 월간·주간 일정표 관련

계정 A로 `POST /api/v1/events`에 아래 본문을 보내고 `201` 응답의 `id`를 `event_id`로 기록합니다. 날짜는 테스트할 학기의 조회 범위에 맞춰 조정합니다.

```json
{
  "title": "API 테스트 개인 일정",
  "starts_at": "2026-11-23T09:00:00+09:00",
  "ends_at": "2026-11-23T10:00:00+09:00",
  "color": "#4F46E5"
}
```

| 요청 | 입력 및 기대 결과 |
|---|---|
| `GET /api/v1/events/{event_id}` | `200`. 저장한 제목과 시간대를 확인합니다. 응답이 UTC로 표시되면 같은 시각을 나타내는지 확인합니다. |
| `PUT /api/v1/events/{event_id}` | 제목을 바꾼 전체 본문을 전송합니다. `200` 응답과 이후 상세 조회에서 변경을 확인합니다. |
| `POST /api/v1/events` | 종료를 시작과 같게 보내거나 시작·종료 시각에서 시간대 정보를 빼면 `422`가 반환됩니다. |
| `GET /api/v1/events` | 시간대가 포함된 `start`, `end`를 입력합니다. 조회 범위와 겹치는 개인 일정이 포함됩니다. |
| `GET /api/v1/calendar` | `start_date=2026-11-23`, `end_date=2026-11-30`, 앞에서 생성한 `timetable_id`를 입력합니다. `200` 응답의 `personal_events`에 생성한 일정이 있어야 합니다. |

캘린더 범위는 서울 기준으로 시작일을 포함하고 종료일 직전까지 조회합니다. 월간 조회에는 해당 월 1일과 다음 달 1일을 넣습니다. 학기 시작·종료일이 설정되어 있고 선택한 강의의 수업 요일이 조회 범위에 있으면 `classes`에서 수업 날짜와 `meeting_id`를 확인할 수 있습니다. 수업 시간은 `timetable.sections`의 `meetings` 배열에서 같은 ID로 찾습니다. 날짜 범위가 0일이거나 62일을 초과하면 `422`가 반환됩니다.

## 사용자 격리와 정리

PowerShell 로그인 예제로 계정 B의 토큰을 발급받으려면 `.env`의 `API_TEST_EMAIL`과 `API_TEST_PASSWORD`를 계정 B의 값으로 바꾸고 로그인 명령을 다시 실행합니다.

1. Swagger UI에서 **Authorize** → **Logout**을 누르고 계정 B의 토큰으로 다시 인증합니다. 계정 A의 `timetable_id`와 `event_id`에 대해 상세를 조회하면 각각 `404`가 반환됩니다. B의 시간표·일정 목록에도 A가 생성한 ID가 없어야 합니다.
2. 같은 ID로 계정 B가 시간표나 일정을 수정 또는 삭제하면 `404`가 반환됩니다. 계정 A로 돌아와 데이터가 그대로인지 확인합니다.
3. 계정 A로 돌아와 아래 요청을 순서대로 실행합니다. 각 삭제 요청은 `204`를 반환합니다.

| 정리 요청 | 후속 확인 |
|---|---|
| `DELETE /api/v1/timetables/{timetable_id}/sections/{section_id}` | 시간표 상세의 `sections`에서 삭제한 분반이 빠집니다. |
| `DELETE /api/v1/events/{event_id}` | 삭제한 일정의 상세 조회가 `404`를 반환합니다. |
| `DELETE /api/v1/timetables/{timetable_id}` | 삭제한 시간표의 상세 조회가 `404`를 반환합니다. 동시 요청 테스트에 별도의 시간표를 만들었다면 같은 방법으로 삭제합니다. |

## 응답이 예상과 다를 때

| 응답 | 확인할 항목 |
|---|---|
| `200`에 빈 목록 | 필터를 제거하고 학기와 분반을 다시 조회합니다. 카탈로그가 비어 있으면 PDF 적재와 `003` 적용 여부를 확인합니다. |
| 로그인 `400`에 `invalid_credentials` | `SUPABASE_URL`이 가리키는 프로젝트의 **Authentication → Users**에서 테스트 계정이 있는지 확인하고, `API_TEST_EMAIL`과 `API_TEST_PASSWORD`를 확인합니다. |
| 로그인 `400`에 `email_not_confirmed` | 테스트 계정의 이메일 인증 상태를 확인합니다. |
| `401` | 현재 토큰이 같은 Supabase 프로젝트의 로그인 세션에서 발급되었는지 확인합니다. 만료되었으면 다시 로그인합니다. |
| `404` | 생성 또는 조회 응답의 ID를 사용했는지, 현재 인증한 계정이 데이터 소유자인지 확인합니다. |
| `422` | 응답의 `detail`에서 누락된 필드, 범위 오류, 날짜와 시간대 입력을 확인합니다. |
| `503` | `.env`의 Supabase URL과 해당 키가 설정되었는지 확인합니다. |
| `502` 또는 `504` | API 터미널의 오류 메시지, Supabase 연결 상태, 마이그레이션 적용 여부를 확인합니다. |
