"""Identity checks for actual Korean, English and engineering report layouts."""

from __future__ import annotations

import re

import fitz

from scripts.syllabus.storage import normalize
from scripts.syllabus.protocol import ProtocolError

DETAILS_VERSION = "course-details-v3"
HEADER_LABELS = {
    "교과목명": "course_name", "coursetitle": "course_name",
    "학수번호": "course_no", "coursenumber": "course_no", "coursecode": "course_no",
    "분반": "section", "classnumber": "section",
    "개설학(부)과": "department", "department/division": "department",
    "개설학년": "academic_grade", "academicyear": "academic_grade",
    "학점-이론-실습": "credits_raw", "credit-lecture-practice": "credits_raw", "학점": "credits_raw",
    "강의시간": "class_time_raw", "classtime": "class_time_raw",
    "강의실": "classroom_raw", "classroom": "classroom_raw",
    "담당교수": "staff_name", "instructor": "staff_name",
    "강의형태": "class_format", "on/off-lineoption": "class_format",
    "연구실": "_office", "instructor'soffice": "_office",
    "상담시간": "_office_hours", "officehours": "_office_hours",
    "연락처": "_phone", "phone": "_phone", "이메일": "_email", "e-mail": "_email",
    "수강대상": "_audience",
}
DAY_NUMBERS = {
    "월": 1, "mon": 1, "화": 2, "tue": 2, "수": 3, "wed": 3,
    "목": 4, "thu": 4, "금": 5, "fri": 5, "토": 6, "sat": 6, "일": 7, "sun": 7,
}
DAY_TOKEN = re.compile(
    r"[월화수목금토일](?:요일)?|(?<![A-Za-z])(?:Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)\.?",
    re.IGNORECASE,
)


def parse_class_time(value: str | None, classroom: str | None = None) -> tuple[list[dict], list[str]]:
    """Preserve numbered periods; only explicit clock times become starts_at/ends_at."""
    if not value:
        return [], []
    value = re.sub(r"\s+", " ", value).strip()
    days = list(DAY_TOKEN.finditer(value))
    warnings, meetings, pending_days = [], [], []
    room = classroom if classroom and re.fullmatch(r"[A-Za-z]\d{1,3}-[A-Za-z0-9-]+", classroom) else None
    if not days:
        return [], ["수업 시간의 요일·교시를 해석하지 못했습니다. 원문을 확인하세요."]
    if value[:days[0].start()].strip(" /,;()"):
        warnings.append("요일 앞의 추가 시간 표기를 확인하세요.")
    for index, day in enumerate(days):
        token = day.group().lower()
        weekday = DAY_NUMBERS.get(token[:3], DAY_NUMBERS.get(token[0]))
        end = days[index + 1].start() if index + 1 < len(days) else len(value)
        body = value[day.end():end].strip(" /,;()")
        pending_days.append(weekday)
        if not body:
            continue
        source_text = value[day.start():end].strip()
        common = {"room": room, "source_text": source_text}
        compact_body = re.sub(r"\s+|교시", "", body)
        clock = re.fullmatch(r"(\d{1,2}):(\d{2})[-~∼–](\d{1,2}):(\d{2})", compact_body)
        slots = []
        if clock:
            h1, m1, h2, m2 = map(int, clock.groups())
            if h1 < 24 and h2 <= 24 and m1 < 60 and m2 < 60 and (h2 < 24 or m2 == 0) and h1 * 60 + m1 < h2 * 60 + m2:
                slots.append({"periods": [], "starts_at": f"{h1:02}:{m1:02}:00", "ends_at": f"{h2:02}:{m2:02}:00", "time_basis": "clock"})
        elif re.fullmatch(r"\d{1,2}(?:[-~∼–,]\d{1,2})*", compact_body):
            periods = []
            valid = True
            for piece in compact_body.split(","):
                bounds = re.split(r"[-~∼–]", piece)
                start, finish = int(bounds[0]), int(bounds[-1])
                # Some published headers contain period 0. Retain it as written;
                # no wall-clock time is inferred from numbered periods.
                if not 0 <= start <= finish <= 24:
                    valid = False
                    break
                periods.extend(range(start, finish + 1))
            if valid:
                slots.append({"periods": sorted(set(periods)), "starts_at": None, "ends_at": None, "time_basis": "periods"})
        if not slots:
            warnings.append(f"수업 시간 원문을 확인하세요: {source_text}")
        else:
            for current_day in pending_days:
                for slot in slots:
                    meetings.append({"weekday": current_day, **slot, **common})
        pending_days.clear()
    if pending_days:
        warnings.append("요일에 대응하는 시간이 없습니다.")
    return meetings, warnings


def extract_course_details(data: bytes, task: dict) -> dict:
    """Extract header table cells, retaining blanks and catalog/PDF name variants."""
    inspect_pdf(data, task)
    fields, warnings = {}, []
    with fitz.open(stream=data, filetype="pdf") as document:
        page = document[0]
        for table in page.find_tables().tables:
            rows = table.extract()
            labels = {HEADER_LABELS.get(normalize(str(cell or ""))) for row in rows for cell in row}
            if not {"course_no", "course_name"}.issubset(labels):
                continue
            for row in rows:
                indexes = [(i, HEADER_LABELS[normalize(str(cell or ""))])
                           for i, cell in enumerate(row) if normalize(str(cell or "")) in HEADER_LABELS]
                for index, (position, key) in enumerate(indexes):
                    if key.startswith("_"):
                        continue
                    stop = indexes[index + 1][0] if index + 1 < len(indexes) else len(row)
                    values = [str(cell).strip() for cell in row[position + 1:stop] if cell is not None and str(cell).strip()]
                    value = re.sub(r"\s+", " ", " ".join(values)).strip() or None
                    if key not in fields or fields[key] is None:
                        fields[key] = value
            break
    if not fields:
        warnings.append("첫 페이지의 교과목 정보 표를 찾지 못했습니다.")
    raw_credit = fields.get("credits_raw") or task.get("credits_raw")
    credit_match = re.fullmatch(r"(\d+(?:\.\d+)?)(?:\s*-\s*\d+(?:\.\d+)?){0,2}", raw_credit or "")
    credits = float(credit_match[1]) if credit_match else None
    schedule, schedule_warnings = parse_class_time(fields.get("class_time_raw"), fields.get("classroom_raw"))
    warnings.extend(schedule_warnings)
    missing = [key for key in ("class_time_raw", "classroom_raw") if not fields.get(key)]
    return {
        "parser_version": DETAILS_VERSION,
        **{key: task.get(key) for key in ("year", "term", "course_no", "section", "variant", "level")},
        "course_name": task.get("course_name") or fields.get("course_name"),
        "staff_name": task.get("staff_name") or re.sub(r"\s*\(인\)$", "", fields.get("staff_name") or "") or None,
        "department": task.get("department") or fields.get("department"),
        "course_type": task.get("course_type"),
        "credits": credits,
        "academic_grade": fields.get("academic_grade"),
        "class_format": fields.get("class_format") or task.get("class_format"),
        "class_time_raw": fields.get("class_time_raw"),
        "classroom_raw": fields.get("classroom_raw"),
        "meetings": schedule,
        "pdf_fields": fields,
        "missing_fields": missing,
        "warnings": warnings,
        # Blank source cells are valid nullable data, not extraction failures.
        "extraction_status": "partial" if warnings else "complete",
    }


def extract_saved_courses(output, *, year=None, term=None, limit=None) -> dict:
    """Extract a snapshot of completed downloads while the crawler can continue."""
    from datetime import datetime, timezone
    from scripts.syllabus.catalog import task_id
    from scripts.syllabus.storage import ConsoleProgress, course_label, atomic_json, exclusive_run, is_complete, load_state, target_path

    output = output.resolve()
    with exclusive_run(output / "_state" / "extract.lock"):
        state = load_state(output / "_state" / "downloads.json")
        selected = [(key, entry) for key, entry in state["downloads"].items()
                    if entry.get("status") == "downloaded"
                    and (year is None or entry.get("task", {}).get("year") == year)
                    and (term is None or entry.get("task", {}).get("term") == term)]
        if limit:
            selected = selected[:limit]
        documents, errors = {}, {}
        with ConsoleProgress("데이터 추출", len(selected)) as progress:
            for receipt_id, entry in selected:
                task = entry.get("task", {})
                progress.start(course_label(task), "파일·해시 검증 및 PDF 표 추출")
                try:
                    if receipt_id != task.get("id") or receipt_id != task_id(task) or not is_complete(output, task, state):
                        raise ValueError("교과목 식별자 또는 수집 파일 검증 실패")
                    path = target_path(output, task)
                    details = extract_course_details(path.read_bytes(), task)
                    documents[receipt_id] = {"path": entry["path"], "sha256": entry["sha256"], **details}
                    status = "추출 성공" if details["extraction_status"] == "complete" else "일부 미확인"
                    progress.advance(status)
                except Exception as exc:
                    errors[receipt_id] = str(exc)
                    progress.advance("실패", str(exc))
            result = {"version": 1, "parser_version": DETAILS_VERSION,
                      "created_at": datetime.now(timezone.utc).isoformat(),
                      "scope": {"year": year, "term": term, "limit": limit},
                      "documents": documents, "errors": errors}
            progress.update("course-data.json 저장 중")
            atomic_json(output / "course-data.json", result)
        return result


def inspect_pdf(data: bytes, task: dict) -> dict:
    if not data.startswith(b"%PDF-"):
        raise ProtocolError("PDF 시그니처가 없습니다.")
    with fitz.open(stream=data, filetype="pdf") as document:
        if document.is_encrypted or not document.page_count:
            raise ProtocolError("비어 있거나 암호화된 PDF입니다.")
        first_page = document[0].get_text()
        # Check identifiers in the report's first-page header, rather than
        # matching an unrelated course/year mentioned in weekly references.
        header = normalize(first_page[:1500])
        heading = normalize(first_page[:250])
        course = re.escape(normalize(task["course_no"]))
        section = re.escape(task["section"].lstrip("0") or "0")
        if not re.search(rf"(?:학수번호|coursenumber|coursecode)[:：]?{course}(?!\d)", header):
            raise ProtocolError("PDF 헤더의 학수번호가 요청과 다릅니다.")
        labeled_section = rf"(?:분반|classnumber|section(?:number|no\.?)?)[:：]?0*{section}(?!\d)"
        combined_section = rf"(?:학수번호|coursenumber)[:：]?{course}[-–—/]0*{section}(?!\d)"
        if not any(re.search(pattern, header) for pattern in (labeled_section, combined_section)):
            raise ProtocolError("PDF 헤더의 분반이 요청과 다릅니다.")
        if not re.search(rf"(?<!\d){task['year']}(?!\d)", heading):
            raise ProtocolError("PDF 헤더의 학년도가 요청과 다릅니다.")
        term_pattern = {
            "1": r"(?:1학기|1stsemester|firstsemester)",
            "2": r"(?:2학기|2ndsemester|secondsemester)",
            "summer": r"(?:여름|하계|summer)",
            "winter": r"(?:겨울|동계|winter)",
        }[task["term"]]
        if not re.search(term_pattern, heading):
            raise ProtocolError("PDF 헤더의 학기가 요청과 다릅니다.")
        variant_pattern = {
            "kor": "강의계획서", "eng": "coursesyllabus", "tech": "공학인증",
        }[task["variant"]]
        if variant_pattern not in header:
            raise ProtocolError("PDF의 국문·영문·공학인증 구분이 요청과 다릅니다.")
        return {
            "pages": document.page_count,
            "text_characters": sum(len(page.get_text()) for page in document),
            "verified_fields": ["course_no", "section", "year", "term", "variant"],
        }
