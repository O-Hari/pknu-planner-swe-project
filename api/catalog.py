from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Path, Query, Response

from api.schemas import Download, Section, Syllabus, Term, TermCode
from api.supabase import PublicDB, StorageDB

router = APIRouter(tags=["강의계획서 관련"])

ID = Annotated[int, Path(gt=0, le=2**63 - 1)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=2**31 - 1)]
TERM_FIELDS = "id,academic_year,term_code,display_name,starts_on,ends_on"
SECTION_FIELDS = (
    "id,term_id,section_code,instructor_name,room_summary,capacity,enrolled_count,"
    "course:courses(id,course_code,course_name,department,credits,course_type),"
    "meetings:class_meetings(id,weekday,starts_at,ends_at,period,room,week_pattern)"
)
MEETING_ORDER = "weekday,starts_at.nullslast,period,id"


def first(rows: list, code: str):
    if not rows:
        raise HTTPException(404, {"code": code})
    return rows[0]


@router.get("/terms", response_model=list[Term])
async def list_terms(
    db: PublicDB,
    year: Annotated[int | None, Query(ge=1, le=32767)] = None,
    term_code: TermCode | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
):
    params = {"select": TERM_FIELDS, "order": "academic_year.desc,id.desc", "limit": limit, "offset": offset}
    if year is not None:
        params["academic_year"] = f"eq.{year}"
    if term_code is not None:
        params["term_code"] = f"eq.{term_code}"
    return await db.rest("academic_terms", params=params)


@router.get("/sections", response_model=list[Section])
async def search_sections(
    db: PublicDB,
    term_id: Annotated[int | None, Query(gt=0, le=2**63 - 1)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    weekday: Annotated[int | None, Query(ge=1, le=7)] = None,
    course_code: Annotated[str | None, Query(max_length=32)] = None,
    department: Annotated[str | None, Query(max_length=120)] = None,
    course_type: Annotated[str | None, Query(max_length=60)] = None,
    limit: Limit = 50,
    offset: Offset = 0,
):
    return await db.rest(
        "rpc/search_planner_sections", "POST",
        params={"select": SECTION_FIELDS, "order": "id", "meetings.order": MEETING_ORDER,
                "limit": limit, "offset": offset},
        data={"p_term_id": term_id, "p_query": (q or "").strip() or None,
              "p_weekday": weekday, "p_course_code": course_code,
              "p_department": department, "p_course_type": course_type},
    )


@router.get("/sections/{section_id}", response_model=Section)
async def get_section(section_id: ID, db: PublicDB):
    rows = await db.rest("course_sections", params={
        "select": SECTION_FIELDS, "id": f"eq.{section_id}", "meetings.order": MEETING_ORDER,
    })
    return first(rows, "section_not_found")


@router.get("/sections/{section_id}/syllabi", response_model=list[Syllabus])
async def list_syllabi(section_id: ID, db: PublicDB, storage: StorageDB):
    await get_section(section_id, db)
    params = {"course_section_id": f"eq.{section_id}"}
    imported = await storage.rest("syllabus_files", params={
        **params, "select": "source_id,course_title,attachment_name,source_page_url,variant:course_metadata->>variant",
        "order": "source_id",
    })
    records = await db.rest("syllabi", params={
        **params, "select": "id,source_url,file_storage_key,original_file_name,content_text,parse_status",
        "order": "id",
    })
    return [
        Syllabus(id=row["source_id"], title=row["course_title"], filename=row["attachment_name"],
                 variant=row["variant"], source_url=row["source_page_url"], download_available=True)
        for row in imported
    ] + [
        Syllabus(id=f"record-{row['id']}", filename=row["original_file_name"],
                 source_url=row["source_url"], content_text=row["content_text"],
                 parse_status=row["parse_status"], download_available=bool(row["file_storage_key"]))
        for row in records
    ]


@router.get("/sections/{section_id}/syllabi/{syllabus_id}/download", response_model=Download)
async def download_syllabus(
    section_id: ID,
    syllabus_id: Annotated[str, Path(pattern=r"^(?:[0-9a-f]{24}|record-[1-9][0-9]{0,18})$")],
    db: PublicDB,
    storage: StorageDB,
    response: Response,
):
    params = {"course_section_id": f"eq.{section_id}"}
    bucket = "course-syllabi"
    if syllabus_id.startswith("record-"):
        record_id = int(syllabus_id.removeprefix("record-"))
        if record_id > 2**63 - 1:
            raise HTTPException(404, {"code": "syllabus_not_found"})
        rows = await db.rest("syllabi", params={
            **params, "id": f"eq.{record_id}", "select": "file_storage_key",
        })
        path = first(rows, "syllabus_not_found")["file_storage_key"]
    else:
        rows = await storage.rest("syllabus_files", params={
            **params, "source_id": f"eq.{syllabus_id}", "select": "storage_bucket,storage_path",
        })
        record = first(rows, "syllabus_not_found")
        path = record["storage_path"] if record["storage_bucket"] == bucket else None
    if not path or any(part in {"", ".", ".."} for part in path.split("/")):
        raise HTTPException(404, {"code": "syllabus_file_not_found"})
    expires_in = 300
    signed = await storage.request(
        "POST", f"/storage/v1/object/sign/{bucket}/{quote(path, safe='/')}",
        data={"expiresIn": expires_in},
    )
    signed_path = signed.get("signedURL", "") if isinstance(signed, dict) else ""
    if not signed_path.startswith(f"/object/sign/{bucket}/"):
        raise HTTPException(502, "Invalid Supabase signed URL")
    response.headers["Cache-Control"] = "no-store"
    return Download(url=f"{storage.url}/storage/v1{signed_path}", expires_in=expires_in)
