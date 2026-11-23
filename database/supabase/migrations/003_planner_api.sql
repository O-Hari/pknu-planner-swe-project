begin;

-- The crawler can supply a clock interval or a numbered period.
alter table public.class_meetings
  alter column starts_at drop not null,
  alter column ends_at drop not null,
  add column if not exists period smallint,
  add column if not exists imported boolean not null default false;

alter table public.class_meetings drop constraint if exists chk_meetings_time;
alter table public.class_meetings add constraint chk_meetings_time check (
  (period is null and starts_at is not null and ends_at is not null and ends_at > starts_at)
  or (period is not null and period between 0 and 24 and starts_at is null and ends_at is null)
);
create unique index if not exists uq_meeting_period
  on public.class_meetings (course_section_id, weekday, period) where period is not null;

alter table public.syllabus_files add column if not exists course_section_id bigint
  references public.course_sections (id) on delete set null;
create index if not exists ix_syllabus_files_section
  on public.syllabus_files (course_section_id);

-- Attach newly synced PDFs to stable catalog IDs. Serialize initial course creation
-- across variants and terms because the legacy course code column isn't unique.
create or replace function public.link_crawled_syllabus()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  metadata jsonb := new.course_metadata;
  year_text text := metadata ->> 'year';
  catalog_term_code text := upper(metadata ->> 'term');
  code text := nullif(btrim(metadata ->> 'course_no'), '');
  section text := nullif(btrim(metadata ->> 'section'), '');
  catalog_term bigint;
  catalog_course bigint;
begin
  new.course_section_id := null;
  if coalesce(year_text, '') !~ '^[0-9]{4}$' or year_text = '0000'
    or catalog_term_code is null or catalog_term_code not in ('1', '2', 'SUMMER', 'WINTER')
    or code is null or length(code) > 32 or section is null or length(section) > 20 then
    return new;
  end if;

  perform pg_catalog.pg_advisory_xact_lock(99103, pg_catalog.hashtext(code));
  insert into public.academic_terms (academic_year, term_code, display_name)
  values (
    year_text::smallint, catalog_term_code,
    year_text || '학년도 ' || case catalog_term_code
      when '1' then '1학기' when '2' then '2학기'
      when 'SUMMER' then '여름학기' else '겨울학기' end
  ) on conflict on constraint uq_academic_terms_year_code do nothing;

  select t.id into catalog_term from public.academic_terms t
    where t.academic_year = year_text::smallint and t.term_code = catalog_term_code;
  select c.id into catalog_course from public.courses c
    where c.course_code = code
    order by exists (
      select 1 from public.course_sections s
      where s.course_id = c.id and s.term_id = catalog_term and s.section_code = section
    ) desc, c.id limit 1;

  if catalog_course is null then
    insert into public.courses (course_code, course_name, department, credits, course_type)
    values (
      code, left(coalesce(nullif(metadata ->> 'course_name', ''), new.course_title), 200),
      left(nullif(metadata ->> 'department', ''), 120),
      case when jsonb_typeof(metadata -> 'credits') = 'number'
        then case when (metadata ->> 'credits')::numeric between 0 and 99.9
          then (metadata ->> 'credits')::numeric end end,
      left(nullif(metadata ->> 'course_type', ''), 60)
    ) returning id into catalog_course;
  end if;

  insert into public.course_sections (term_id, course_id, section_code, instructor_name, room_summary, source_url)
  values (
    catalog_term, catalog_course, section, left(nullif(metadata ->> 'staff_name', ''), 120),
    left(nullif(metadata ->> 'classroom_raw', ''), 200), left(new.source_page_url, 2048)
  ) on conflict on constraint uq_course_sections_term_course_section do nothing;

  select s.id into new.course_section_id from public.course_sections s
    where s.term_id = catalog_term and s.course_id = catalog_course and s.section_code = section;
  return new;
end;
$$;

create or replace function public.refresh_crawled_meetings(p_section_id bigint)
returns void
language plpgsql
set search_path = ''
as $$
declare
  slots jsonb;
begin
  select f.course_metadata -> 'meetings' into slots
  from public.syllabus_files f
  where f.course_section_id = p_section_id
    and jsonb_typeof(f.course_metadata -> 'meetings') = 'array'
  order by (f.course_metadata -> 'meetings' <> '[]'::jsonb) desc,
    case f.course_metadata ->> 'variant' when 'kor' then 0 when 'eng' then 1 else 2 end,
    f.synced_at desc, f.source_id
  limit 1;

  delete from public.class_meetings where course_section_id = p_section_id and imported;

  -- Explicitly maintained schedules take precedence over PDF extraction.
  if slots is null or exists (
    select 1 from public.class_meetings where course_section_id = p_section_id
  ) then
    return;
  end if;

  insert into public.class_meetings (course_section_id, weekday, starts_at, ends_at, room, period, imported)
  select p_section_id, slot.weekday, slot.starts_at, slot.ends_at, left(slot.room, 120), null, true
  from jsonb_to_recordset(slots) as slot(weekday smallint, starts_at time, ends_at time, room text)
  where slot.weekday between 1 and 7 and slot.ends_at > slot.starts_at
  union all
  select p_section_id, slot.weekday, null, null, left(slot.room, 120), p.period, true
  from jsonb_to_recordset(slots) as slot(weekday smallint, periods smallint[], room text)
  cross join lateral unnest(slot.periods) as p(period)
  where slot.weekday between 1 and 7 and p.period between 0 and 24
  on conflict do nothing;
end;
$$;

create or replace function public.sync_crawled_meetings()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op <> 'INSERT' and old.course_section_id is not null then
    perform pg_catalog.pg_advisory_xact_lock(
      99103, pg_catalog.hashtext(old.course_metadata ->> 'course_no')
    );
    perform public.refresh_crawled_meetings(old.course_section_id);
  end if;
  if tg_op = 'INSERT'
    or (tg_op = 'UPDATE' and new.course_section_id is distinct from old.course_section_id) then
    perform public.refresh_crawled_meetings(new.course_section_id);
  end if;
  return null;
end;
$$;

revoke execute on function public.link_crawled_syllabus(), public.sync_crawled_meetings(),
  public.refresh_crawled_meetings(bigint) from public, anon, authenticated;
grant execute on function public.refresh_crawled_meetings(bigint) to service_role;

drop trigger if exists trg_sync_crawled_meetings on public.syllabus_files;
drop trigger if exists trg_link_crawled_syllabus on public.syllabus_files;
create trigger trg_link_crawled_syllabus before insert or update of course_metadata
on public.syllabus_files for each row execute function public.link_crawled_syllabus();

-- Link rows already uploaded before the API was installed, then import slots once per section.
update public.syllabus_files set course_metadata = course_metadata where course_section_id is null;
select public.refresh_crawled_meetings(course_section_id)
from (select distinct course_section_id from public.syllabus_files where course_section_id is not null) sections;

create trigger trg_sync_crawled_meetings after insert or update of course_metadata or delete
on public.syllabus_files for each row execute function public.sync_crawled_meetings();

create or replace function public.search_planner_sections(
  p_term_id bigint default null, p_query text default null, p_weekday integer default null,
  p_course_code text default null, p_department text default null, p_course_type text default null
)
returns setof public.course_sections
language sql stable security invoker
set search_path = ''
as $$
  select s.*
  from public.course_sections s join public.courses c on c.id = s.course_id
  where (p_term_id is null or s.term_id = p_term_id)
    and (p_query is null or strpos(lower(c.course_name), lower(p_query)) > 0
      or strpos(lower(s.instructor_name), lower(p_query)) > 0
      or strpos(lower(c.course_code), lower(p_query)) > 0)
    and (p_course_code is null or c.course_code = p_course_code)
    and (p_department is null or c.department = p_department)
    and (p_course_type is null or c.course_type = p_course_type)
    and (p_weekday is null or exists (
      select 1 from public.class_meetings m where m.course_section_id = s.id and m.weekday = p_weekday
    ));
$$;

create or replace function public.planner_section_conflicts(p_timetable_id bigint, p_section_id bigint)
returns table (course_section_id bigint)
language sql stable security invoker
set search_path = ''
as $$
  select distinct selected.course_section_id
  from public.timetable_sections selected
  join public.class_meetings existing on existing.course_section_id = selected.course_section_id
  join public.class_meetings incoming on incoming.course_section_id = p_section_id
    and incoming.weekday = existing.weekday
  where selected.timetable_id = p_timetable_id and selected.course_section_id <> p_section_id
    and (incoming.period = existing.period
      or (incoming.starts_at < existing.ends_at and existing.starts_at < incoming.ends_at))
  order by selected.course_section_id;
$$;

create or replace function public.guard_planner_timetable_section()
returns trigger
language plpgsql security invoker
set search_path = ''
as $$
declare
  timetable_term bigint;
  section_term bigint;
  conflicts jsonb;
begin
  -- The timetable row serializes concurrent additions for the same user timetable.
  select t.term_id into timetable_term from public.timetables t
    where t.id = new.timetable_id for update;
  if timetable_term is null then
    raise sqlstate 'PT404' using message = 'timetable_not_found';
  end if;
  select s.term_id into section_term from public.course_sections s where s.id = new.course_section_id;
  if section_term is null then
    raise sqlstate 'PT404' using message = 'section_not_found';
  end if;
  if timetable_term <> section_term then
    raise sqlstate 'PT409' using message = 'different_term';
  end if;
  if exists (
    select 1 from public.timetable_sections s
    where s.timetable_id = new.timetable_id and s.course_section_id = new.course_section_id
  ) then
    return new;
  end if;
  select jsonb_agg(c.course_section_id) into conflicts
    from public.planner_section_conflicts(new.timetable_id, new.course_section_id) c;
  if conflicts is not null then
    raise sqlstate 'PT409' using message = 'class_time_conflict', detail = conflicts::text;
  end if;
  return new;
end;
$$;

drop trigger if exists trg_guard_timetable_section on public.timetable_sections;
create trigger trg_guard_timetable_section before insert
on public.timetable_sections for each row execute function public.guard_planner_timetable_section();

revoke execute on function public.search_planner_sections(bigint, text, integer, text, text, text),
  public.planner_section_conflicts(bigint, bigint), public.guard_planner_timetable_section()
  from public, anon, authenticated;
grant execute on function public.search_planner_sections(bigint, text, integer, text, text, text)
  to anon, authenticated;
grant execute on function public.planner_section_conflicts(bigint, bigint) to authenticated, service_role;

grant select on public.academic_terms, public.courses, public.course_sections,
  public.class_meetings, public.syllabi to anon, authenticated;
grant select, insert on public.academic_terms, public.courses, public.course_sections,
  public.class_meetings to service_role;
grant delete on public.class_meetings to service_role;
grant usage on sequence public.academic_terms_id_seq, public.courses_id_seq,
  public.course_sections_id_seq, public.class_meetings_id_seq to service_role;
grant select, insert, delete on public.timetables, public.timetable_sections, public.personal_events to authenticated;
grant update on public.personal_events to authenticated;
grant usage on sequence public.timetables_id_seq, public.personal_events_id_seq to authenticated;
revoke update on public.timetables, public.timetable_sections from authenticated;
grant update (title, is_primary) on public.timetables to authenticated;

notify pgrst, 'reload schema';
commit;
