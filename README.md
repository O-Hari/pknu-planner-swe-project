# 월간/주간 캘린더

## 준비물
- Node.js 지원 LTS 버전
- VS Code
- 인터넷 연결 (FullCalendar CDN)

## 설치와 실행
```bash
npm init -y
npm install express better-sqlite3
node server.js
```
브라우저에서 http://localhost:3000 열기.

## 파일 설명
- `server.js`: Express 서버, SQLite 데이터베이스, API
- `public/index.html`: 화면 구조
- `public/style.css`: 달력 디자인
- `public/app.js`: 달력 동작, 서버 호출
- `calendar.db`: 서버 최초 실행 시 자동 생성

## 참고
- 모든 일정은 종일 일정이며 시작일과 종료일을 포함합니다.
- 혼자 로컬에서 학습하는 예제입니다. 외부 공개 전에 로그인 및 접근 제어, HTTPS, 백업, 배포 설정 등을 추가해야 합니다.
- FullCalendar JavaScript 파일은 CDN에서 받아 사용하므로 최초 접속 때 인터넷이 필요합니다.
