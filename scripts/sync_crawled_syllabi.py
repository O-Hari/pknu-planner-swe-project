"""Sync verified regular-course PDFs from this project's crawler to Supabase.

Input: crawled_syllabus/_state/downloads.json, produced by scripts.syllabus.
Only successful, identity-checked downloads are eligible; filenames are not filters.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_SYLLABUS_ROOT = PROJECT_ROOT / "crawled_syllabus"
BUCKET = "course-syllabi"
MAX_FILE_BYTES = 50 * 1024 * 1024

# Support both `python scripts/sync_crawled_syllabi.py` and `python -m scripts...`.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_local_env(path: Path = PROJECT_ROOT / ".env") -> None:
    """Load simple KEY=value settings; existing process variables take priority."""
    if not path.is_file():
        return
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if not match:
            raise ValueError(f".env {line_number}행의 KEY=value 형식을 확인하세요.")
        key, value = match.groups()
        if value.startswith(("'", '"')):
            quoted = re.fullmatch(r"(['\"])(.*?)\1\s*(?:#.*)?", value)
            if not quoted:
                raise ValueError(f".env {line_number}행의 따옴표를 확인하세요.")
            value = quoted.group(2)
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        os.environ.setdefault(key, value)


def validate_supabase_config(url: str, key: str) -> None:
    if not url or not key:
        raise ValueError("프로젝트 루트의 .env에 SUPABASE_URL과 SUPABASE_SERVICE_ROLE_KEY를 설정하세요.")
    if any(marker in (url + " " + key).lower() for marker in
           ("your-project", "your-service", "project-ref", "<", ">", "placeholder")):
        raise ValueError(".env의 Supabase URL/키가 예시 값입니다. 실제 프로젝트 값으로 바꿔주세요.")
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.path
            or parsed.query or parsed.fragment or parsed.username or parsed.password):
        raise ValueError("SUPABASE_URL에는 https://<프로젝트ID>.supabase.co 형식의 프로젝트 URL을 입력하세요.")
    if key.startswith("sb_secret_"):
        return
    try:
        payload = key.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError, UnicodeDecodeError):
        raise ValueError("서버용 service_role JWT 또는 sb_secret_ 키를 설정하세요.") from None
    if not isinstance(claims, dict) or claims.get("role") != "service_role":
        raise ValueError("업로드에는 서버용 service_role 또는 secret 키가 필요합니다. anon/publishable 키는 사용할 수 없습니다.")


def collect_syllabi(syllabus_root: Path, *, year=None, term=None, limit=0, upload_only=False) -> list[dict]:
    from scripts.syllabus.catalog import SITE_URL, task_id
    from scripts.syllabus.storage import ConsoleProgress, course_label, is_complete, load_state, target_path

    if not upload_only:
        from scripts.syllabus.documents import extract_course_details

    syllabus_root = syllabus_root.resolve()
    state_path = syllabus_root / "_state" / "downloads.json"
    if not state_path.is_file():
        raise FileNotFoundError(
            "정규 교과목 수집 이력이 없습니다. 먼저 프로젝트 루트에서 "
            "python -m scripts.syllabus crawl --year 2026 --term 2 --limit 1 을 실행하세요."
        )
    state = load_state(state_path)
    cached_documents = {}
    if upload_only:
        cache_path = syllabus_root / "course-data.json"
        if not cache_path.is_file():
            raise FileNotFoundError("course-data.json이 없습니다. 먼저 python -m scripts.syllabus extract 명령으로 데이터를 추출하세요.")
        cache = json.loads(cache_path.read_text(encoding="utf-8-sig"))
        if not isinstance(cache, dict) or cache.get("version") != 1 or not isinstance(cache.get("documents"), dict):
            raise ValueError("course-data.json의 형식이 올바르지 않습니다. 데이터를 다시 추출하세요.")
        cached_documents = cache["documents"]
    selected = [(key, entry) for key, entry in sorted(state["downloads"].items())
                if entry.get("status") == "downloaded"
                and (year is None or entry.get("task", {}).get("year") == year)
                and (term is None or entry.get("task", {}).get("term") == term)]
    if limit:
        selected = selected[:limit]
    records: list[dict] = []
    label = "적재용 파일 검증" if upload_only else "적재 전 데이터 추출"
    with ConsoleProgress(label, len(selected)) as progress:
        for receipt_id, entry in selected:
            task = entry["task"]
            phase = "파일·해시 및 저장된 추출 결과 확인" if upload_only else "파일·해시 검증 및 PDF 표 추출"
            progress.start(course_label(task), phase)
            try:
                if receipt_id != task.get("id") or receipt_id != task_id(task):
                    raise ValueError(f"수집 이력의 교과목 식별자가 일치하지 않습니다: {receipt_id}")
                if not is_complete(syllabus_root, task, state):
                    raise ValueError(f"성공한 정규 교과목 PDF가 아닙니다: {receipt_id}")
                local_path = target_path(syllabus_root, task)
                if upload_only:
                    cached = cached_documents.get(receipt_id)
                    if not isinstance(cached, dict):
                        raise ValueError(f"저장된 추출 결과가 없습니다: {course_label(task)}. extract 명령을 다시 실행하세요.")
                    identity_matches = all(cached.get(key) == task.get(key) for key in
                                           ("year", "term", "course_no", "section", "variant", "course_name"))
                    if (cached.get("sha256") != entry["sha256"] or cached.get("path") != entry["path"]
                            or not identity_matches or cached.get("extraction_status") not in ("complete", "partial")
                            or not isinstance(cached.get("parser_version"), str)):
                        raise ValueError(f"파일과 추출 결과가 일치하지 않습니다: {course_label(task)}. 데이터를 다시 추출하세요.")
                    course_details = {key: value for key, value in cached.items() if key not in ("path", "sha256")}
                else:
                    course_details = extract_course_details(local_path.read_bytes(), task)
                records.append({
                    "source_id": receipt_id,
                    "course_title": task["course_name"],
                    "attachment_name": local_path.name,
                    "source_page_url": SITE_URL,
                    "source_file_url": entry.get("download_url", ""),
                    "crawler_relative_path": local_path.relative_to(PROJECT_ROOT).as_posix(),
                    "local_path": local_path,
                    "expected_sha256": entry["sha256"],
                    "course_metadata": course_details,
                })
                progress.advance("검증 완료" if upload_only else
                                 ("추출 성공" if course_details["extraction_status"] == "complete" else "일부 미확인"))
            except Exception:
                progress.advance("실패")
                raise
    return records


class SupabaseRequestError(RuntimeError):
    def __init__(self, status: int, detail: str):
        self.status, self.detail = status, detail
        hint = ""
        if "course_metadata" in detail and ("PGRST204" in detail or "42703" in detail):
            hint = " SQL Editor에서 최신 002_syllabus_files.sql을 다시 적용하세요."
        elif "bucket" in detail.lower() and "not found" in detail.lower():
            hint = " SQL Editor에서 002_syllabus_files.sql을 먼저 적용하세요."
        elif status == 404 or "PGRST205" in detail or "42P01" in detail:
            hint = " syllabus_files 테이블 및 002_syllabus_files.sql 적용 여부를 확인하세요."
        elif status in (401, 403):
            hint = " 같은 프로젝트의 service_role/secret 키와 권한을 확인하세요."
        super().__init__(f"Supabase 요청 실패 ({status}): {detail}{hint}")


def supabase_request(
    url: str,
    service_key: str,
    method: str,
    body: bytes | None = None,
    content_type: str | None = None,
    extra_headers: dict[str, str] | None = None,
):
    headers = {"apikey": service_key}
    # New secret keys are API keys, not JWT bearer tokens.
    if not service_key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {service_key}"
    if content_type:
        headers["Content-Type"] = content_type
    if extra_headers:
        headers.update(extra_headers)

    request = Request(url, data=body, headers=headers, method=method)
    for attempt in range(3):
        try:
            with urlopen(request, timeout=60) as response:
                content = response.read()
            return json.loads(content) if content else None
        except HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace").replace(service_key, "[REDACTED]")
                delay = exc.headers.get("Retry-After", "") if exc.headers else ""
            finally:
                exc.close()
            if exc.code in (408, 429, 500, 502, 503, 504) and attempt < 2:
                wait = min(float(delay), 30) if delay.isdigit() else 2 ** attempt
                print(f"\n[재시도] HTTP {exc.code}: {wait:g}초 후 {attempt + 2}/3번째 요청", file=sys.stderr, flush=True)
                time.sleep(wait)
                continue
            raise SupabaseRequestError(exc.code, detail) from None
        except (URLError, TimeoutError) as exc:
            if attempt < 2:
                print(f"\n[재시도] 연결 오류: {2 ** attempt}초 후 {attempt + 2}/3번째 요청", file=sys.stderr, flush=True)
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError("Supabase 연결 실패: 네트워크·프로젝트 주소를 확인하세요.") from None
        except json.JSONDecodeError:
            raise RuntimeError("Supabase가 JSON 이외의 응답을 반환했습니다. 프로젝트 URL을 확인하세요.") from None


def preflight(supabase_url: str, service_key: str) -> int:
    """Fail before uploading when the table, column or private bucket is missing."""
    query = urlencode({"select": "source_id,course_metadata,content_sha256,storage_path", "limit": "0"})
    supabase_request(f"{supabase_url}/rest/v1/syllabus_files?{query}", service_key, "GET")
    bucket = supabase_request(f"{supabase_url}/storage/v1/bucket/{BUCKET}", service_key, "GET")
    if not isinstance(bucket, dict) or bucket.get("id") != BUCKET:
        raise RuntimeError("course-syllabi 버킷 정보를 확인하지 못했습니다.")
    if bucket.get("public") is not False:
        raise RuntimeError("course-syllabi 버킷을 비공개로 설정하세요.")
    return min(int(bucket.get("file_size_limit") or MAX_FILE_BYTES), MAX_FILE_BYTES)


def storage_matches(url: str, key: str, storage_path: str, size: int) -> bool:
    path = quote(f"{BUCKET}/{storage_path}", safe="/")
    try:
        info = supabase_request(f"{url}/storage/v1/object/info/{path}", key, "GET")
    except SupabaseRequestError as exc:
        if exc.status in (400, 404) and any(word in exc.detail.lower() for word in ("not found", "not_found", "nosuchkey")):
            return False
        raise
    if not isinstance(info, dict):
        raise RuntimeError("Storage 파일 정보 응답이 올바르지 않습니다.")
    remote_size = info.get("size", (info.get("metadata") or {}).get("size"))
    return str(remote_size) == str(size)


def upload_record(record: dict, supabase_url: str, service_key: str, *, force=False, max_bytes=MAX_FILE_BYTES, on_progress=None) -> str:
    notify = on_progress if on_progress is not None else lambda phase: None
    notify("로컬 파일 확인")
    local_path: Path = record["local_path"]
    size = local_path.stat().st_size
    if size > max_bytes:
        raise RuntimeError(
            f"파일 크기가 업로드 상한 {max_bytes} bytes를 초과합니다: {local_path.name}"
        )

    content = local_path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != record["expected_sha256"]:
        raise ValueError(f"검증 이후 로컬 PDF가 변경됐습니다: {local_path.name}")
    # Storage rejects some Unicode/special characters even after URL encoding.
    # Keep the Korean original name locally and in attachment_name, use ASCII remotely.
    extension = local_path.suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,10}", extension):
        extension = ".bin"
    object_name = f"syllabus{extension}"
    storage_path = f"crawler/{record['source_id']}/{object_name}"
    encoded_path = quote(f"{BUCKET}/{storage_path}", safe="/")
    storage_url = f"{supabase_url}/storage/v1/object/{encoded_path}"
    content_type = mimetypes.guess_type(object_name)[0] or "application/octet-stream"

    row = {
        "source_id": record["source_id"],
        "course_title": record["course_title"],
        "attachment_name": record["attachment_name"],
        "source_page_url": record["source_page_url"],
        "source_file_url": record["source_file_url"],
        "crawler_relative_path": record["crawler_relative_path"],
        "storage_bucket": BUCKET,
        "storage_path": storage_path,
        "content_sha256": digest,
        "file_size_bytes": size,
        "content_type": content_type,
        "course_metadata": record["course_metadata"],
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }
    lookup = urlencode({"source_id": f"eq.{record['source_id']}", "select": "*", "limit": "1"})
    notify("원격 DB·파일 확인")
    rows = supabase_request(f"{supabase_url}/rest/v1/syllabus_files?{lookup}", service_key, "GET")
    if not isinstance(rows, list):
        raise RuntimeError("syllabus_files 조회 응답이 올바르지 않습니다.")
    existing = rows[0] if rows else {}
    same_file = (existing.get("content_sha256") == digest
                 and existing.get("storage_bucket") == BUCKET
                 and existing.get("storage_path") == storage_path)
    file_ready = same_file and storage_matches(supabase_url, service_key, storage_path, size)
    same_row = all(existing.get(key) == value for key, value in row.items() if key != "synced_at")
    if not force and file_ready and same_row:
        return "skipped"
    uploaded = force or not file_ready
    if uploaded:
        notify(f"PDF 업로드 ({size / 1024 / 1024:.2f} MiB)")
        supabase_request(storage_url, service_key, "POST", content, content_type, {"x-upsert": "true"})
        notify("Storage 파일 크기 검증")
        if not storage_matches(supabase_url, service_key, storage_path, size):
            raise RuntimeError("Storage에 저장된 PDF 크기가 로컬 파일과 다릅니다. 다시 실행하세요.")
    rest_url = f"{supabase_url}/rest/v1/syllabus_files?{urlencode({'on_conflict': 'source_id'})}"
    notify("DB 메타데이터 기록·검증")
    saved = supabase_request(
        rest_url,
        service_key,
        "POST",
        json.dumps(row, ensure_ascii=False).encode("utf-8"),
        "application/json",
        {"Prefer": "resolution=merge-duplicates,return=representation"},
    )
    if not isinstance(saved, list) or len(saved) != 1 or any(saved[0].get(key) != value for key, value in row.items() if key != "synced_at"):
        raise RuntimeError("DB에서 반환한 적재 결과가 로컬 파일·교과목 정보와 다릅니다.")
    return "uploaded" if uploaded else "metadata_updated"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="프로젝트의 정규 교과목 크롤러가 수집·검증한 PDF를 Supabase로 동기화합니다."
    )
    parser.add_argument(
        "--syllabus-root",
        type=Path,
        default=LOCAL_SYLLABUS_ROOT,
        help="현재 프로젝트의 수집 결과 경로 (기본값: crawled_syllabus)",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="수집된 정규 교과목 PDF와 해시만 확인합니다 (Supabase 설정 불필요).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="대상 목록만 확인합니다 (로컬 복사와 Supabase 업로드 없음).",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="처리 파일 수 제한 (0은 전체)"
    )
    parser.add_argument("--year", type=int, help="적재할 학년도")
    parser.add_argument("--term", choices=["1", "summer", "2", "winter"], help="적재할 학기")
    parser.add_argument("--force", action="store_true", help="동일한 원격 파일도 다시 업로드")
    parser.add_argument("--upload-only", action="store_true",
                        help="course-data.json을 사용해 PDF 데이터를 재추출하지 않고 적재 (파일 해시 검증 유지)")
    return parser.parse_args()


def main() -> int:
    load_local_env()
    args = parse_args()
    if args.limit < 0:
        raise ValueError("--limit은 0 이상이어야 합니다")
    if args.year is not None and args.year < 2021:
        raise ValueError("학년도는 2021년 이후를 지정하세요.")
    syllabus_root = args.syllabus_root.expanduser()
    if not syllabus_root.is_absolute():
        syllabus_root = PROJECT_ROOT / syllabus_root
    if not syllabus_root.resolve().is_relative_to(LOCAL_SYLLABUS_ROOT.resolve()):
        raise ValueError("수집 결과는 현재 프로젝트의 crawled_syllabus/ 안에 있어야 합니다.")
    records = collect_syllabi(syllabus_root.resolve(), year=args.year, term=args.term,
                             limit=args.limit, upload_only=args.upload_only)

    print(f"검증된 정규 교과목 PDF {len(records)}개를 찾았습니다.")
    if not records:
        print("업로드할 PDF가 없습니다. 정규 교과목 크롤러의 수집 결과를 확인하세요.")
        return 0

    if args.dry_run or args.local_only:
        return 0

    supabase_url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not args.local_only:
        validate_supabase_config(supabase_url, service_key)

    from scripts.syllabus.storage import ConsoleProgress, course_label, exclusive_run

    counts = {"uploaded": 0, "metadata_updated": 0, "skipped": 0, "failed": 0}
    directory = syllabus_root.resolve() / "_state"
    status_labels = {"uploaded": "업로드", "metadata_updated": "정보 갱신", "skipped": "건너뜀", "failed": "실패"}
    with exclusive_run(directory / "supabase-sync.lock"), ConsoleProgress("Supabase 적재", len(records)) as progress:
        progress.update("Supabase 연결·테이블·버킷 확인")
        max_bytes = preflight(supabase_url, service_key)
        with (directory / "supabase-sync.jsonl").open("a", encoding="utf-8") as history:
            for record in records:
                progress.start(course_label(record["course_metadata"]), "적재 준비")
                error = None
                try:
                    status = upload_record(record, supabase_url, service_key, force=args.force, max_bytes=max_bytes, on_progress=progress.update)
                except Exception as exc:
                    status, error = "failed", str(exc).replace(service_key, "[REDACTED]")
                counts[status] += 1
                receipt = {"at": datetime.now(timezone.utc).isoformat(), "project_url": supabase_url,
                           "source_id": record["source_id"], "status": status,
                           "sha256": record["expected_sha256"], "path": record["crawler_relative_path"]}
                if error:
                    receipt["error"] = error
                history.write(json.dumps(receipt, ensure_ascii=False) + "\n")
                history.flush()
                os.fsync(history.fileno())
                progress.advance(status_labels[status], error or "")
    print(f"완료: 신규/재업로드 {counts['uploaded']}, 정보 갱신 {counts['metadata_updated']}, 동일 파일 건너뜀 {counts['skipped']}, 실패 {counts['failed']}")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        raise SystemExit(1)
