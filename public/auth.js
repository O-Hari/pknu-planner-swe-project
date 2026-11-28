/* Supabase 연결 정보 */
const SUPABASE_URL = 'https://auuifzalacavrjtzqkpy.supabase.co';
const SUPABASE_KEY = 'sb_publishable_h42szKPdtFu5amk-EE9BNQ_F9hW6gXD';

// Supabase 클라이언트 생성
const supabaseClient = window.supabase.createClient(
  SUPABASE_URL,
  SUPABASE_KEY
);

// app.js에서도 사용할 수 있게 설정
window.teamSupabase = supabaseClient;

/* HTML 요소 */
const loginDialog = document.getElementById('loginDialog');
const loginForm = document.getElementById('loginForm');
const loginBtn = document.getElementById('loginBtn');
const logoutBtn = document.getElementById('logoutBtn');
const loginStatus = document.getElementById('loginStatus');
const loginError = document.getElementById('loginError');

/* 로그인 상태에 따라 화면 변경 */
function updateLoginUI(user) {
  if (user) {
    loginStatus.textContent = user.email || '로그인됨';
    loginBtn.hidden = true;
    logoutBtn.hidden = false;
  } else {
    loginStatus.textContent = '로그인하지 않음';
    loginBtn.hidden = false;
    logoutBtn.hidden = true;
  }
}

/* 로그인창 열기 */
loginBtn.addEventListener('click', () => {
  loginError.textContent = '';
  loginDialog.showModal();
});

/* 로그인창 닫기 */
document.getElementById('loginCancelBtn').addEventListener(
  'click',
  () => loginDialog.close()
);

/* 이메일 + 비밀번호 로그인 */
loginForm.addEventListener('submit', async (event) => {
  event.preventDefault();

  loginError.textContent = '';

  const email = document.getElementById('loginEmail').value.trim();
  const password = document.getElementById('loginPassword').value;

  const { error } = await supabaseClient.auth.signInWithPassword({
    email,
    password
  });

  if (error) {
    loginError.textContent = '이메일 또는 비밀번호를 확인하세요.';
    return;
  }

  loginDialog.close();
  loginForm.reset();
});

/* 로그아웃 */
logoutBtn.addEventListener('click', async () => {
  const { error } = await supabaseClient.auth.signOut();

  if (error) {
    alert('로그아웃에 실패했습니다.');
  }
});

/* 로그인 상태 변경 감지 */
supabaseClient.auth.onAuthStateChange((event, session) => {
  updateLoginUI(session?.user ?? null);

  if (
    event === 'SIGNED_IN' ||
    event === 'SIGNED_OUT' ||
    event === 'INITIAL_SESSION'
  ) {
    // app.js에서 생성한 캘린더 갱신
    window.refreshTeamCalendar?.();
  }
});
