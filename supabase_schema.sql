-- Recordaria: esquema inicial. Pegar y correr en el SQL editor de Supabase.

create table if not exists users (
    id bigserial primary key,
    telegram_chat_id bigint unique not null,
    telegram_username text,
    timezone text not null default 'America/Lima',
    quiet_hours_start time not null default '23:00',
    quiet_hours_end time not null default '08:00',
    created_at timestamptz not null default now()
);

create table if not exists tasks (
    id bigserial primary key,
    user_id bigint not null references users(id) on delete cascade,
    title text not null,
    description text,
    status text not null default 'pending'
        check (status in ('pending','scheduled','in_progress','done','cancelled')),
    deadline_at timestamptz,
    estimated_minutes int,
    scheduled_start_at timestamptz,
    scheduled_end_at timestamptz,
    started_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists recurring_events (
    id bigserial primary key,
    user_id bigint not null references users(id) on delete cascade,
    title text not null,
    weekday int not null check (weekday between 0 and 6), -- 0=lunes
    start_time time not null,
    end_time time not null,
    active boolean not null default true,
    created_at timestamptz not null default now()
);

create table if not exists one_off_events (
    id bigserial primary key,
    user_id bigint not null references users(id) on delete cascade,
    title text not null,
    start_at timestamptz not null,
    end_at timestamptz not null,
    created_at timestamptz not null default now()
);

create table if not exists nudges (
    id bigserial primary key,
    user_id bigint not null references users(id) on delete cascade,
    task_id bigint references tasks(id) on delete set null,
    kind text not null
        check (kind in ('outreach','propose_time','checkin','escalation','break_pushback')),
    sent_at timestamptz not null default now(),
    message_text text not null,
    user_responded_at timestamptz,
    escalation_level int not null default 0
);

create table if not exists conversation_messages (
    id bigserial primary key,
    user_id bigint not null references users(id) on delete cascade,
    role text not null check (role in ('user','assistant','system_event')),
    content text not null,
    created_at timestamptz not null default now()
);

create table if not exists leisure_log (
    id bigserial primary key,
    user_id bigint not null references users(id) on delete cascade,
    kind text not null check (kind in ('break_granted','deferral','task_completed_on_time')),
    minutes int,
    task_id bigint references tasks(id) on delete set null,
    created_at timestamptz not null default now()
);

create table if not exists user_scheduler_state (
    user_id bigint primary key references users(id) on delete cascade,
    next_contact_at timestamptz not null default now(),
    pending_nudge_kind text,
    active_task_id bigint references tasks(id) on delete set null,
    updated_at timestamptz not null default now()
);

create index if not exists idx_tasks_user_status on tasks(user_id, status);
create index if not exists idx_tasks_user_scheduled on tasks(user_id, scheduled_start_at);
create index if not exists idx_nudges_user_sent on nudges(user_id, sent_at desc);
create index if not exists idx_conv_user_created on conversation_messages(user_id, created_at desc);
create index if not exists idx_scheduler_next_contact on user_scheduler_state(next_contact_at);
