-- Migration 002: private syllabus storage and regular-course metadata.
-- Run after 001_core_schema.sql in Supabase SQL Editor.
-- Can also be rerun to add course_metadata to an existing syllabus_files table.
insert into storage.buckets (id, name, public, file_size_limit)
values ('course-syllabi', 'course-syllabi', false, 52428800)
on conflict (id) do nothing;

create table if not exists public.syllabus_files (
  source_id text primary key,
  course_title text not null,
  attachment_name text not null,
  source_page_url text,
  source_file_url text,
  crawler_relative_path text not null,
  storage_bucket text not null default 'course-syllabi',
  storage_path text not null,
  content_sha256 char(64) not null,
  file_size_bytes bigint not null check (file_size_bytes >= 0),
  content_type text,
  course_metadata jsonb not null default '{}'::jsonb,
  synced_at timestamptz not null default now()
);

-- CREATE TABLE IF NOT EXISTS does not add columns to existing tables.
alter table public.syllabus_files
  add column if not exists course_metadata jsonb not null default '{}'::jsonb;

comment on column public.syllabus_files.course_metadata is
  'Regular-course identity and PDF header fields: year, term, course_no, course_name, section, staff_name, department, credits, class_time_raw, classroom_raw, meetings, variant, level, pdf_fields, extraction_status, warnings';

alter table public.syllabus_files enable row level security;

revoke all on table public.syllabus_files from anon, authenticated;
grant all on table public.syllabus_files to service_role;

create index if not exists ix_syllabus_files_title
  on public.syllabus_files (course_title);

create index if not exists ix_syllabus_files_synced_at
  on public.syllabus_files (synced_at desc);

create index if not exists ix_syllabus_files_course_identity
  on public.syllabus_files (
    (course_metadata ->> 'year'), (course_metadata ->> 'term'),
    (course_metadata ->> 'course_no'), (course_metadata ->> 'section')
  );

create index if not exists ix_syllabus_files_instructor
  on public.syllabus_files ((course_metadata ->> 'staff_name'));
