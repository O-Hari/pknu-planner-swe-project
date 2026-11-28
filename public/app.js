const dialog = document.getElementById('eventDialog');
const form = document.getElementById('eventForm');
const titleInput = document.getElementById('eventTitle');
const startInput = document.getElementById('startDate');
const endInput = document.getElementById('endDate');
const startTimeInput = document.getElementById('startTime');
const endTimeInput = document.getElementById('endTime');
const allDayInput = document.getElementById('allDay');
const timeSection = document.getElementById('timeSection');
const locationInput = document.getElementById('eventLocation');
const colorInput = document.getElementById('eventColor');
const descriptionInput = document.getElementById('eventDescription');
const deleteBtn = document.getElementById('deleteBtn');
const formError = document.getElementById('formError');

const dateDialog = document.getElementById('dateDialog');
const dateDialogTitle = document.getElementById('dateDialogTitle');
const dateEventList = document.getElementById('dateEventList');
const detailDialog = document.getElementById('detailDialog');
const detailError = document.getElementById('detailError');

let editingId = null;
let selectedDate = null;
let selectedEvent = null;


const TEAM_ACCESS_TOKEN_KEY = 'team_api_access_token';

// 팀 FastAPI 호출
async function fetchTeamAPI(path) {
  const token = sessionStorage.getItem(TEAM_ACCESS_TOKEN_KEY);

  if (!token) {
    throw new Error('팀 API 로그인 정보가 없습니다.');
  }

  const response = await fetch(`/api/team/${path}`, {
    headers: {
      Authorization: `Bearer ${token}`
    }
  });

  if (!response.ok) {
    throw new Error(`팀 API 요청 실패: ${response.status}`);
  }

  return response.json();
}

// 강의 시간 형식 변환
function formatLectureTime(time) {
  return time ? time.slice(0, 5) : '';
}

// FastAPI 강의 데이터를 FullCalendar 형식으로 변환
function convertLectureEvents(data) {
  if (!data.timetable) return [];

  const sectionMap = new Map(
    data.timetable.sections.map(item => [
      String(item.course_section_id),
      item.section
    ])
  );

  const events = [];

  for (const occurrence of data.classes) {
    const section = sectionMap.get(
      String(occurrence.section_id)
    );

    if (!section) continue;

    const meeting = section.meetings.find(
      m => String(m.id) === String(occurrence.meeting_id)
    );

    if (!meeting) continue;

    const startTime = formatLectureTime(meeting.starts_at);
    const endTime = formatLectureTime(meeting.ends_at);

    // 실제 시각이 없는 교시 데이터는 변환 규칙 확정 후 처리
    if (!startTime || !endTime) continue;

    const date = occurrence.date;

    events.push({
      id: `lecture-${occurrence.meeting_id}-${date}`,
      title: section.course.course_name,
      start: `${date}T${startTime}:00`,
      end: `${date}T${endTime}:00`,
      allDay: false,
      backgroundColor: '#10b981',
      borderColor: '#10b981',

      extendedProps: {
        type: 'lecture',
        sectionId: occurrence.section_id,
        meetingId: occurrence.meeting_id,
        occurrenceDate: date,
        startDate: date,
        endDate: date,
        startTime,
        endTime,
        location: meeting.room || section.room_summary || '',
        description: `담당 교수: ${section.instructor_name || '미정'}`,
        eventColor: '#10b981'
      }
    });
  }

  return events;
}

// 개인 일정과 강의 일정 합치기
async function loadAllCalendarEvents(fetchInfo) {
  const startDate = fetchInfo.startStr.slice(0, 10);
  const endDate = fetchInfo.endStr.slice(0, 10);

  // 기존 SQLite 개인 일정
  const response = await fetch('/api/events');

  if (!response.ok) {
    throw new Error('개인 일정을 불러오지 못했습니다.');
  }

  const personalEvents = await response.json();

  const formattedPersonalEvents = personalEvents.map(event => ({
    ...event,
    id: `personal-${event.id}`,
    extendedProps: {
      ...event.extendedProps,
      type: 'personal',
      originalId: event.id
    }
  }));

  // 팀 시간표 목록 조회
  const timetables = await fetchTeamAPI('timetables');

  if (!timetables.length) {
    return formattedPersonalEvents;
  }

  const timetable =
    timetables.find(t => t.is_primary) || timetables[0];

  const params = new URLSearchParams({
    start_date: startDate,
    end_date: endDate,
    timetable_id: String(timetable.id)
  });

  // 팀 API에서 반복 강의 조회
  const data = await fetchTeamAPI(
    `calendar?${params.toString()}`
  );

  const lectureEvents = convertLectureEvents(data);

  return [
    ...formattedPersonalEvents,
    ...lectureEvents
  ];
}


function localDateString(date = new Date()) {
  return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
}
function openCreate(dateStr = localDateString()) {  // 일정 등록창을 열어서 입력할 준비를 하는 함수
  editingId = null;
  form.reset();
  allDayInput.checked = false;
  startTimeInput.value = '08:00';
  endTimeInput.value = '09:00';
  updateTimeVisibility();
  formError.textContent = '';
  document.getElementById('dialogTitle').textContent = '새 일정 등록';
  startInput.value = dateStr;
  endInput.value = dateStr;
  colorInput.value = '#2563eb';
  deleteBtn.hidden = true;
  dialog.showModal();
  titleInput.focus();
}
function openEdit(event) {
  editingId = event.extendedProps.originalId; // SQLite ID를 사용
  formError.textContent = '';
  document.getElementById('dialogTitle').textContent = '일정 수정';
  titleInput.value = event.title;
  startInput.value = event.startStr.slice(0, 10);
  endInput.value = event.extendedProps.endDate;
  descriptionInput.value = event.extendedProps.description || '';
  startTimeInput.value = event.extendedProps.startTime || '';
  endTimeInput.value = event.extendedProps.endTime || '';

  const startTime = event.extendedProps.startTime || '';
  const endTime = event.extendedProps.endTime || '';
  allDayInput.checked = !startTime && !endTime;
  updateTimeVisibility();

  locationInput.value = event.extendedProps.location || '';
  colorInput.value = event.extendedProps.eventColor || '#2563eb';
  deleteBtn.hidden = false;
  dialog.showModal();
}

// 날짜별 일정 목록창: 해당 날짜를 포함하는 모든 종일 일정 표시
function openDateList(dateStr) {
  selectedDate = dateStr;
  const [year, month, day] = dateStr.split('-');
  dateDialogTitle.textContent = `${year}년 ${Number(month)}월 ${Number(day)}일 일정`;
  dateEventList.replaceChildren();

  // 선택한 날짜를 제목에 표시
  const [, m, d] = dateStr.split('-').map(Number);
  document.getElementById('dateDialogTitle').textContent = `${m}월 ${d}일 일정`;

  const eventsOnDate = calendar.getEvents() // FullCalendar에 현재 불러와져 있는 일정들을 가져오기
    .filter(event => {  // 전체 일정 중 사용자가 클릭한 날짜에 해당하는 일정만 찾아오기
      const start = event.extendedProps.startDate || event.startStr.slice(0, 10);
      // FullCalendar의 endStr은 종료일이 포함되지 않는 날짜
      const end = event.extendedProps.endDate || start;
      return start <= dateStr && dateStr <= end;
    })
    .sort((a, b) => a.title.localeCompare(b.title, 'ko'));

  if (eventsOnDate.length === 0) {
    const empty = document.createElement('p');
    empty.textContent = '등록된 일정이 없습니다.';
    dateEventList.appendChild(empty);
  } else {
    for (const event of eventsOnDate) {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'event-list-item';
      const time = event.extendedProps.startTime;
      item.textContent = (time ? `${time}  ` : '') + event.title;
      item.style.borderLeft = `5px solid ${event.extendedProps.eventColor || '#2563eb'}`;
      item.addEventListener('click', () => {
        dateDialog.close();
        openDetail(event);
      });
      dateEventList.appendChild(item);
    }
  }
  dateDialog.showModal();
}

// 조회 전용 상세창: input 대신 일반 텍스트로 표시
function openDetail(event) {
  selectedEvent = event;
  const isLecture = event.extendedProps.type === 'lecture';
  document.getElementById('detailEditBtn').hidden = isLecture;
  document.getElementById('detailDeleteBtn').hidden = isLecture;
  detailError.textContent = '';
  document.getElementById('detailTitle').textContent = event.title;
  document.getElementById('detailStart').textContent = event.startStr.slice(0, 10);
  document.getElementById('detailEnd').textContent = event.extendedProps.endDate || '';
  const startTime = event.extendedProps.startTime || '';
  const endTime = event.extendedProps.endTime || '';
  document.getElementById('detailTime').textContent =
    startTime && endTime ? `${startTime} ~ ${endTime}` : '종일';
  document.getElementById('detailLocation').textContent = event.extendedProps.location || '장소 없음';
  document.getElementById('detailDescription').textContent = event.extendedProps.description || '내용 없음';
  detailDialog.showModal();
}

// 종일 스위치 동작
function updateTimeVisibility() {
  const isAllDay = allDayInput.checked;
  timeSection.hidden = isAllDay;  // 하루 종일이면 시간 입력칸 숨기기
  startTimeInput.disabled = isAllDay; // 하루 종일이면 시간 입력 비활성화
  endTimeInput.disabled = isAllDay;
}
allDayInput.addEventListener('change', updateTimeVisibility);
startInput.addEventListener('change', () => {  // 시작 날짜 변경되면 종료 날짜도 시작 날짜로 변경
  endInput.value = startInput.value;
});

// 달력 표시할 위치 찾아서, 달력 생성하기
const calendar = new FullCalendar.Calendar(document.getElementById('calendar'), {
  initialView: 'dayGridMonth',  // 월간 달력 표시
  locale: 'ko', // 한국어 사용
  firstDay: 0,  // 일요일부터 한 주 시작
  height: 'auto',
  headerToolbar: { left: 'prev,next today', center: 'title', right: 'dayGridMonth,timeGridWeek' },
  events: async function(fetchInfo, successCallback, failureCallback) { // 개인 일정뿐 아니라 FastAPI의 강의 일정도 함께 가져오려고 시도하게 됨
    try {
      const events = await loadAllCalendarEvents(fetchInfo);
      successCallback(events);
    } catch (error) {
      console.error('일정 조회 오류:', error);
      failureCallback(error);
    }
  },
  dateClick: info => openDateList(info.dateStr.slice(0, 10)),  // 날짜를 클릭했을 때 openDateList()를 실행
  eventClick: info => { // 일정 클릭했을 때 openDetail() 실행
    info.jsEvent.preventDefault();
    openDetail(info.event);
  },
  datesSet: info => {
    if (info.view.type === 'timeGridWeek') {
      calendar.setOption('height', 650);  // 주간 뷰: 고정 높이 → 내부 스크롤 생김
      setTimeout(() => {
        const timedEvents = calendar.getEvents().filter(e =>
          !e.allDay && e.start >= info.start && e.start < info.end
        );
        if (!timedEvents.length) return;
        const earliest = timedEvents.reduce((a, b) => a.start < b.start ? a : b);
        const d = new Date(earliest.start);
        d.setMinutes(d.getMinutes() - 30);  // 30분 여유 두고 스크롤
        const h = Math.max(0, d.getHours());
        const m = d.getMinutes() < 0 ? 0 : d.getMinutes();
        calendar.scrollToTime({ hours: h, minutes: m });
      }, 200);
    } else {
      calendar.setOption('height', 'auto');  // 월간 뷰: 원래대로
    }
  }
});
calendar.render();  // 실제 화면에 그리기

// 상단의 일정 등록 버튼은 오늘 날짜로 바로 등록 가능
document.getElementById('addBtn').addEventListener('click', () => openCreate());
document.getElementById('cancelBtn').addEventListener('click', () => dialog.close());
document.getElementById('closeDateBtn').addEventListener('click', () => dateDialog.close());
document.getElementById('detailCloseBtn').addEventListener('click', () => detailDialog.close());

document.getElementById('createOnDateBtn').addEventListener('click', () => {
  dateDialog.close();
  openCreate(selectedDate);
});
document.getElementById('detailEditBtn').addEventListener('click', () => {
  if (!selectedEvent) return;
  detailDialog.close();
  openEdit(selectedEvent);
});

// 대화상자 바깥(어두운 영역)을 클릭하면 다이어로그 닫기
for (const modal of [dateDialog, detailDialog, dialog]) {
  modal.addEventListener('click', event => {
    if (event.target !== modal) return;
    const rect = modal.getBoundingClientRect();
    const outside = event.clientX < rect.left || event.clientX > rect.right ||
                    event.clientY < rect.top || event.clientY > rect.bottom;
    if (outside) modal.close();
  });
}

async function sendRequest(url, method, body) {
  const response = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {})
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '서버 오류가 발생했습니다.');
  return data;
}

// 새 일정은 POST, 수정은 PUT
form.addEventListener('submit', async event => {
  event.preventDefault();
  formError.textContent = '';
  const body = {
  title: titleInput.value.trim(),
  start_date: startInput.value,
  end_date: endInput.value,
  start_time: allDayInput.checked ? '' : startTimeInput.value,
  end_time: allDayInput.checked ? '' : endTimeInput.value,
  location: locationInput.value.trim(),
  color: colorInput.value,
  description: descriptionInput.value
};
  if ((body.start_time === '') !== (body.end_time === '')) {
    formError.textContent = '시작 시간과 종료 시간을 모두 입력하거나 둘 다 비워 주세요.';
    return;
  }
  if (body.start_time && `${body.end_date}T${body.end_time}` <= `${body.start_date}T${body.start_time}`) {
    formError.textContent = '종료 날짜·시간은 시작 날짜·시간보다 늦어야 합니다.';
    return;
  }
  if (!body.title || body.end_date < body.start_date) {
    formError.textContent = '제목과 날짜를 확인하세요. 종료일은 시작일보다 빠를 수 없습니다.';
    return;
  }
  try {
    const url = editingId ? `/api/events/${editingId}` : '/api/events';
    await sendRequest(url, editingId ? 'PUT' : 'POST', body);
    dialog.close();
    calendar.refetchEvents();
  } catch (error) {
    formError.textContent = error.message;
  }
});

// 실제 삭제 요청을 공통 함수로 관리
async function removeEvent(id, errorElement, modal) {
  if (!id || !confirm('이 일정을 삭제하시겠습니까?')) return;
  errorElement.textContent = '';
  try {
    await sendRequest(`/api/events/${id}`, 'DELETE');
    modal.close();
    selectedEvent = null;
    editingId = null;
    calendar.refetchEvents();
  } catch (error) {
    errorElement.textContent = error.message;
  }
}

deleteBtn.addEventListener('click', () => removeEvent(editingId, formError, dialog));
document.getElementById('detailDeleteBtn').addEventListener('click', () => {  // 강의: 조회만 가능, 개인 일정: 수정/삭제 가능
  if (!selectedEvent) return;

  if (selectedEvent.extendedProps.type !== 'personal') {
    return;
  }

  removeEvent(
    selectedEvent.extendedProps.originalId,
    detailError,
    detailDialog
  );
});