from datetime import date, datetime, timezone
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

TermCode = Literal["1", "SUMMER", "2", "WINTER"]
TimetableTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
EventTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Color = Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$")]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Term(BaseModel):
    id: int
    academic_year: int
    term_code: TermCode
    display_name: str
    starts_on: date | None
    ends_on: date | None


class Course(BaseModel):
    id: int
    course_code: str | None
    course_name: str
    department: str | None
    credits: float | None
    course_type: str | None


class Meeting(BaseModel):
    id: int
    weekday: int
    starts_at: str | None
    ends_at: str | None
    period: int | None
    room: str | None
    week_pattern: str | None


class Section(BaseModel):
    id: int
    term_id: int
    section_code: str
    course: Course
    instructor_name: str | None
    room_summary: str | None
    capacity: int | None
    enrolled_count: int | None
    meetings: list[Meeting]


class Syllabus(BaseModel):
    id: str
    title: str | None = None
    filename: str | None = None
    variant: str | None = None
    source_url: str | None = None
    content_text: str | None = None
    parse_status: str | None = None
    download_available: bool


class Download(BaseModel):
    url: str
    expires_in: int


class TimetableCreate(Input):
    term_id: int = Field(gt=0, le=2**63 - 1)
    title: TimetableTitle = "내 시간표"
    is_primary: bool = False


class TimetableUpdate(Input):
    title: TimetableTitle
    is_primary: bool = False


class Timetable(BaseModel):
    id: int
    term_id: int
    term: Term
    title: str
    is_primary: bool
    created_at: datetime
    updated_at: datetime


class TimetableSection(BaseModel):
    course_section_id: int
    added_at: datetime
    section: Section


class TimetableDetail(Timetable):
    sections: list[TimetableSection]


class ConflictCheck(BaseModel):
    already_added: bool
    conflicting_section_ids: list[int]


class EventWrite(Input):
    title: EventTitle
    description: str | None = None
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    location: str | None = Field(default=None, max_length=200)
    color: Color = "#4F46E5"

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.ends_at.astimezone(timezone.utc) <= self.starts_at.astimezone(timezone.utc):
            raise ValueError("ends_at must be after starts_at")
        return self


class Event(BaseModel):
    id: int
    title: str
    description: str | None
    starts_at: datetime
    ends_at: datetime
    location: str | None
    color: str
    created_at: datetime
    updated_at: datetime


class ClassOccurrence(BaseModel):
    date: date
    section_id: int
    meeting_id: int


class Calendar(BaseModel):
    start_date: date
    end_date: date
    timezone: Literal["Asia/Seoul"] = "Asia/Seoul"
    timetable: TimetableDetail | None
    classes: list[ClassOccurrence]
    personal_events: list[Event]
