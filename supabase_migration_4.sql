-- Recordaria: migracion 4. Pegar y correr en el SQL editor de Supabase
-- (despues de supabase_schema.sql, supabase_migration_2.sql y supabase_migration_3.sql).

alter table nudges drop constraint if exists nudges_kind_check;
alter table nudges add constraint nudges_kind_check check (kind in (
    'outreach','outreach_escalation','propose_time','checkin','escalation',
    'progress_check','progress_escalation',
    'reminder_ack','reminder_escalation',
    'awaiting_reply','awaiting_reply_escalation',
    'break_pushback'
));
