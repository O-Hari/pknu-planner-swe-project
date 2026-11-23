from datetime import date, datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import AwareDatetime

from api.catalog import ID, MEETING_ORDER, SECTION_FIELDS, TERM_FIELDS, Limit, Offset, first
from api.schemas import (
    Calendar, ClassOccurrence, ConflictCheck, Event, EventWrite, Timetable,
    TimetableCreate, TimetableDetail, TimetableUpdate,
)
from api.supabase import Supabase, UserDB

timetable_router = APIRouter(tags=["시간표 관련"])
calendar_router = APIRouter(tags=["월간·주간 일정표 관련"])
SEOUL = ZoneInfo("Asia/Seoul")
TIMETABLE_FIELDS = f"id,term_id,title,is_primary,created_at,updated_at,term:academic_terms({TERM_FIELDS})"
TIMETABLE_DETAIL = f"{TIMETABLE_FIELDS},sections:timetable_sections(course_section_id,added_at,section:course_sections({SECTION_FIELDS}))"
EVENT_FIELDS = "id,title,description,starts_at,ends_at,location,color,created_at,updated_at"
RETURN_ROW = "return=representation"


async def owned_timetable(timetable_id: int, db: Supabase, user_id: str, *, detail: bool = False):
    params = {"id": f"eq.{timetable_id}", "user_id": f"eq.{user_id}",
              "select": TIMETABLE_DETAIL if detail else TIMETABLE_FIELDS}
    if detail:
        params.update({"sections.order": "added_at,course_section_id",
                       "sections.section.meetings.order": MEETING_ORDER})
    return first(await db.rest("timetables", params=params), "timetable_not_found")


@timetable_router.get("/timetables", response_model=list[Timetable])
async def list_timetables(
    auth: UserDB,
    term_id: Annotated[int | None, Query(gt=0, le=2**63 - 1)] = None,
    limit: Limit = 50,
    offset: Offset = 0,
):
    db, user_id = auth
    params = {"select": TIMETABLE_FIELDS, "user_id": f"eq.{user_id}",
              "order": "is_primary.desc,id.desc", "limit": limit, "offset": offset}
    if term_id is not None:
        params["term_id"] = f"eq.{term_id}"
    return await db.rest("timetables", params=params)


@timetable_router.post("/timetables", response_model=Timetable, status_code=201)
async def create_timetable(payload: TimetableCreate, auth: UserDB):
    db, user_id = auth
    first(await db.rest("academic_terms", params={"id": f"eq.{payload.term_id}", "select": "id"}), "term_not_found")
    rows = await db.rest(
        "timetables", "POST", params={"select": TIMETABLE_FIELDS},
        data={**payload.model_dump(), "user_id": user_id}, prefer=RETURN_ROW,
    )
    return rows[0]


@timetable_router.get("/timetables/{timetable_id}", response_model=TimetableDetail)
async def get_timetable(timetable_id: ID, auth: UserDB):
    return await owned_timetable(timetable_id, *auth, detail=True)


@timetable_router.put("/timetables/{timetable_id}", response_model=Timetable)
async def update_timetable(timetable_id: ID, payload: TimetableUpdate, auth: UserDB):
    db, user_id = auth
    rows = await db.rest("timetables", "PATCH", params={
        "select": TIMETABLE_FIELDS, "id": f"eq.{timetable_id}", "user_id": f"eq.{user_id}",
    }, data=payload.model_dump(), prefer=RETURN_ROW)
    return first(rows, "timetable_not_found")


@timetable_router.delete("/timetables/{timetable_id}", status_code=204)
async def delete_timetable(timetable_id: ID, auth: UserDB):
    db, user_id = auth
    rows = await db.rest("timetables", "DELETE", params={
        "select": "id", "id": f"eq.{timetable_id}", "user_id": f"eq.{user_id}",
    }, prefer=RETURN_ROW)
    first(rows, "timetable_not_found")
    return Response(status_code=204)


@timetable_router.get("/timetables/{timetable_id}/sections/{section_id}/conflicts", response_model=ConflictCheck)
async def check_conflicts(timetable_id: ID, section_id: ID, auth: UserDB):
    db, user_id = auth
    timetable = await owned_timetable(timetable_id, db, user_id)
    section = first(await db.rest("course_sections", params={
        "id": f"eq.{section_id}", "select": "term_id",
    }), "section_not_found")
    if timetable["term_id"] != section["term_id"]:
        raise HTTPException(409, {"code": "different_term"})
    rows = await db.rest("rpc/planner_section_conflicts", "POST", data={
        "p_timetable_id": timetable_id, "p_section_id": section_id,
    })
    existing = await db.rest("timetable_sections", params={
        "select": "course_section_id", "timetable_id": f"eq.{timetable_id}",
        "course_section_id": f"eq.{section_id}",
    })
    return ConflictCheck(already_added=bool(existing), conflicting_section_ids=[row["course_section_id"] for row in rows])


@timetable_router.put("/timetables/{timetable_id}/sections/{section_id}", status_code=204)
async def add_section(timetable_id: ID, section_id: ID, auth: UserDB):
    db, _ = auth
    await db.rest("timetable_sections", "POST", params={"on_conflict": "timetable_id,course_section_id"},
                  data={"timetable_id": timetable_id, "course_section_id": section_id},
                  prefer="resolution=ignore-duplicates,return=minimal")
    return Response(status_code=204)


@timetable_router.delete("/timetables/{timetable_id}/sections/{section_id}", status_code=204)
async def remove_section(timetable_id: ID, section_id: ID, auth: UserDB):
    db, user_id = auth
    await owned_timetable(timetable_id, db, user_id)
    await db.rest("timetable_sections", "DELETE", params={
        "timetable_id": f"eq.{timetable_id}", "course_section_id": f"eq.{section_id}",
    })
    return Response(status_code=204)


def event_query(user_id: str, start: datetime | None, end: datetime | None):
    if start is not None and end is not None and end <= start:
        raise HTTPException(422, "end must be after start")
    params = {"select": EVENT_FIELDS, "user_id": f"eq.{user_id}", "order": "starts_at,id"}
    if start is not None:
        params["ends_at"] = f"gt.{start.isoformat()}"
    if end is not None:
        params["starts_at"] = f"lt.{end.isoformat()}"
    return params


@calendar_router.get("/events", response_model=list[Event])
async def list_events(
    auth: UserDB, start: AwareDatetime | None = None, end: AwareDatetime | None = None,
    limit: Limit = 50, offset: Offset = 0,
):
    db, user_id = auth
    return await db.rest("personal_events", params={
        **event_query(user_id, start, end), "limit": limit, "offset": offset,
    })


@calendar_router.post("/events", response_model=Event, status_code=201)
async def create_event(payload: EventWrite, auth: UserDB):
    db, user_id = auth
    rows = await db.rest("personal_events", "POST", params={"select": EVENT_FIELDS},
                         data={**payload.model_dump(mode="json"), "user_id": user_id}, prefer=RETURN_ROW)
    return rows[0]


@calendar_router.get("/events/{event_id}", response_model=Event)
async def get_event(event_id: ID, auth: UserDB):
    db, user_id = auth
    rows = await db.rest("personal_events", params={
        "select": EVENT_FIELDS, "id": f"eq.{event_id}", "user_id": f"eq.{user_id}",
    })
    return first(rows, "event_not_found")


@calendar_router.put("/events/{event_id}", response_model=Event)
async def update_event(event_id: ID, payload: EventWrite, auth: UserDB):
    db, user_id = auth
    rows = await db.rest("personal_events", "PATCH", params={
        "select": EVENT_FIELDS, "id": f"eq.{event_id}", "user_id": f"eq.{user_id}",
    }, data=payload.model_dump(mode="json"), prefer=RETURN_ROW)
    return first(rows, "event_not_found")


@calendar_router.delete("/events/{event_id}", status_code=204)
async def delete_event(event_id: ID, auth: UserDB):
    db, user_id = auth
    rows = await db.rest("personal_events", "DELETE", params={
        "select": "id", "id": f"eq.{event_id}", "user_id": f"eq.{user_id}",
    }, prefer=RETURN_ROW)
    first(rows, "event_not_found")
    return Response(status_code=204)


@calendar_router.get("/calendar", response_model=Calendar)
async def get_calendar(
    auth: UserDB, start_date: date, end_date: date,
    timetable_id: Annotated[int | None, Query(gt=0, le=2**63 - 1)] = None,
):
    days = (end_date - start_date).days
    if not 1 <= days <= 62:
        raise HTTPException(422, "Calendar requires a date range of 1 to 62 days")
    db, user_id = auth
    timetable = None
    classes = []
    if timetable_id is not None:
        timetable = TimetableDetail.model_validate(await owned_timetable(timetable_id, db, user_id, detail=True))
        term = timetable.term
        if term.starts_on is not None and term.ends_on is not None:
            for offset in range(days):
                day = start_date + timedelta(days=offset)
                if term.starts_on <= day <= term.ends_on:
                    classes.extend(
                        ClassOccurrence(date=day, section_id=item.course_section_id, meeting_id=meeting.id)
                        for item in timetable.sections for meeting in item.section.meetings
                        if meeting.weekday == day.isoweekday()
                    )

    params = event_query(user_id, datetime.combine(start_date, time.min, SEOUL),
                         datetime.combine(end_date, time.min, SEOUL))
    events = []
    while True:
        page = await db.rest("personal_events", params={**params, "limit": 100, "offset": len(events)})
        if not page:
            break
        events.extend(page)
    return Calendar(start_date=start_date, end_date=end_date, timetable=timetable, classes=classes,
                    personal_events=events)
