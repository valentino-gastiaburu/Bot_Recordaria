-- Recordaria: migracion 5. Pegar y correr en el SQL editor de Supabase
-- (despues de las migraciones 1 a 4).

-- weekday nullable = "todos los dias" (ej. horarios de comida diarios), en vez de
-- tener que crear 7 filas iguales para algo que se repite cada dia.
alter table recurring_events alter column weekday drop not null;

alter table recurring_events add column if not exists category text not null default 'other'
    check (category in ('class', 'work', 'meal', 'other'));

alter table recurring_events add column if not exists requires_transport boolean not null default false;
