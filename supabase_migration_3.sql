-- Recordaria: migracion 3. Pegar y correr en el SQL editor de Supabase
-- (despues de supabase_schema.sql y supabase_migration_2.sql).

alter table nudges drop constraint if exists nudges_kind_check;
alter table nudges add constraint nudges_kind_check check (kind in (
    'outreach','propose_time','checkin','escalation',
    'progress_check','progress_escalation',
    'reminder_ack','reminder_escalation',
    'awaiting_reply','awaiting_reply_escalation',
    'break_pushback'
));
