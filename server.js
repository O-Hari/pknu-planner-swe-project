const express = require('express'); // Express 라이브러리를 불러오는 코드
const Database = require('better-sqlite3'); // better-sqlite3 라이브러리를 불러오는 코드
const path = require('path');

const app = express();  // 서버 프로그램 생성
const db = new Database(path.join(__dirname, 'calendar.db')); // SQLite DB 파일 열기 (없으면 생성)
db.pragma('journal_mode = WAL');

// CREATE TABLE IF NOT EXISTS events: 테이블이 없을 때만 새로 생성하기 => calender.db는 서버 처음 실행될 때 자동 생성됨 (직접 만들 필요 X)
// id: 일정을 등록할 때마다 번호를 자동으로 부여
// TEXT NOT NULL: 해당 값이 반드시 존재해야 함
db.exec(`
  CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    start_time TEXT NOT NULL DEFAULT '',
    end_time TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '#2563eb',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
  );
`);

app.use(express.json());  // 웹 페이지에서 전달한 JSON 데이터 읽기
app.use(express.static(path.join(__dirname, 'public')));  // HTML/CSS/JavaScript 파일 제공

function validDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}
function validEvent(body) {
  if (!body || typeof body.title !== 'string' || !body.title.trim() || body.title.trim().length > 100 ||
      (body.description !== undefined && (typeof body.description !== 'string' || body.description.length > 2000)) ||
      (body.location !== undefined && (typeof body.location !== 'string' || body.location.length > 200)) ||
      !validDate(body.start_date) || !validDate(body.end_date) || body.end_date < body.start_date) return false;

  const startTime = body.start_time ?? '';
  const endTime = body.end_time ?? '';
  const validTime = value => typeof value === 'string' && /^([01]\d|2[0-3]):[0-5]\d$/.test(value);
  if (startTime !== '' || endTime !== '') {
    if (!validTime(startTime) || !validTime(endTime)) return false;
    if (`${body.end_date}T${endTime}` <= `${body.start_date}T${startTime}`) return false;
  }
  const color = body.color ?? '#2563eb';
  return typeof color === 'string' && /^#[0-9a-fA-F]{6}$/.test(color);
}
function nextDate(dateStr) {
  const date = new Date(`${dateStr}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + 1);
  return date.toISOString().slice(0, 10);
}
function toCalendarEvent(row) {
  const timed = Boolean(row.start_time && row.end_time);
  return {
    id: String(row.id),
    title: row.title,
    start: timed ? `${row.start_date}T${row.start_time}:00` : row.start_date,
    end: timed ? `${row.end_date}T${row.end_time}:00` : nextDate(row.end_date),
    allDay: !timed,
    backgroundColor: row.color,
    borderColor: row.color,
    extendedProps: {
      description: row.description,
      startDate: row.start_date,
      endDate: row.end_date,
      startTime: row.start_time,
      endTime: row.end_time,
      location: row.location,
      eventColor: row.color
    }
  };
}

// 일정 조회 api
app.get('/api/events', (req, res) => {
  const rows = db.prepare('SELECT * FROM events ORDER BY start_date, id').all();
  res.json(rows.map(toCalendarEvent));
});

// 일정 등록 api
app.post('/api/events', (req, res) => {
  if (!validEvent(req.body)) return res.status(400).json({ error: '제목, 날짜, 시간, 장소, 색상 입력값을 확인하세요.' });
  const { title, description = '', start_date, end_date, start_time = '', end_time = '', location = '', color = '#2563eb' } = req.body;
  const info = db.prepare('INSERT INTO events (title, description, start_date, end_date, start_time, end_time, location, color) VALUES (?, ?, ?, ?, ?, ?, ?, ?)')
    .run(title.trim(), description, start_date, end_date, start_time, end_time, location, color);
  res.status(201).json({ id: Number(info.lastInsertRowid) });
});

// 일정 수정 api
app.put('/api/events/:id', (req, res) => {
  if (!validEvent(req.body)) return res.status(400).json({ error: '입력 내용을 확인하세요.' });
    const { title, description = '', start_date, end_date, start_time = '', end_time = '', location = '', color = '#2563eb' } = req.body;
  const result = db.prepare('UPDATE events SET title = ?, description = ?, start_date = ?, end_date = ?, start_time = ?, end_time = ?, location = ?, color = ? WHERE id = ?')
    .run(title.trim(), description, start_date, end_date, start_time, end_time, location, color, req.params.id);
  if (!result.changes) return res.status(404).json({ error: '일정을 찾을 수 없습니다.' });
  res.json({ success: true });
});

// 일정 삭제 api
app.delete('/api/events/:id', (req, res) => {
  const result = db.prepare('DELETE FROM events WHERE id = ?').run(req.params.id);
  if (!result.changes) return res.status(404).json({ error: '일정을 찾을 수 없습니다.' });
  res.json({ success: true });
});
//app.listen(3000, () => console.log('캘린더 실행: http://localhost:3000'));  // 3000번 포트에서 요청 기디리기

const TEAM_API_URL = (process.env.TEAM_API_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');

// 팀 FastAPI 조회 API 연결
app.get('/api/team/*path', async (req, res) => {
  const authorization = req.get('authorization');

  if (!authorization?.startsWith('Bearer ')) {
    return res.status(401).json({
      error: '로그인이 필요합니다.'
    });
  }

  try {
    const segments = req.params.path;
    const apiPath = Array.isArray(segments)
      ? segments.join('/')
      : segments;

    const url = new URL(`/api/v1/${apiPath}`, TEAM_API_URL);

    for (const [key, value] of Object.entries(req.query)) {
      if (typeof value === 'string') {
        url.searchParams.set(key, value);
      }
    }

    const response = await fetch(url, {
      headers: {
        Authorization: authorization
      },
      signal: AbortSignal.timeout(15000)
    });

    const result = await response.json();
    res.status(response.status).json(result);

  } catch (error) {
    console.error('팀 API 연결 오류:', error);
    res.status(502).json({
      error: '팀 FastAPI 연결에 실패했습니다.'
    });
  }
});


// Render 서버가 지정하는 포트에서 요청 기다리기
const PORT = process.env.PORT || 3000;
app.listen(PORT, '0.0.0.0', () => { console.log(`Server running on port ${PORT}`); });