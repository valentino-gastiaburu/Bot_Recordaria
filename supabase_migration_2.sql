-- Recordaria: migracion 2. Pegar y correr en el SQL editor de Supabase
-- (despues de haber corrido supabase_schema.sql).

alter table tasks add column if not exists kind text not null default 'agreement'
    check (kind in ('reminder','agreement','assignment'));

create table if not exists task_milestones (
    id bigserial primary key,
    task_id bigint not null references tasks(id) on delete cascade,
    label text not null,
    at timestamptz not null,
    created_at timestamptz not null default now()
);
create index if not exists idx_milestones_task on task_milestones(task_id);

alter table nudges drop constraint if exists nudges_kind_check;
alter table nudges add constraint nudges_kind_check check (kind in (
    'outreach','propose_time','checkin','escalation',
    'progress_check','progress_escalation',
    'reminder_ack','reminder_escalation','break_pushback'
));
