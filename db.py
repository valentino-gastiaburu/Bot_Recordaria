import os
import re as _re
from datetime import datetime, timedelta, timezone as dt_timezone

from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

_client: Client | None = None

_FRACTION_RE = _re.compile(r"^(?P<head>.+?)\.(?P<frac>\d+)(?P<tz>[+-]\d{2}:\d{2}|Z)?$")


def parse_ts(value: str) -> datetime:
    """datetime.fromisoformat en Python 3.10 solo acepta microsegundos con 3 o 6 dígitos,
    pero Postgres/Supabase puede devolver cualquier cantidad (ej. 5) — esto los normaliza
    a 6 antes de parsear, para no reventar en fechas que vienen de la base de datos."""
    m = _FRACTION_RE.match(value)
    if m:
        frac = (m.group("frac") + "000000")[:6]
        value = f"{m.group('head')}.{frac}{m.group('tz') or ''}"
    return datetime.fromisoformat(value)


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
    return _client


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).isoformat()


# ---------- users ----------

def get_or_create_user(chat_id: int, username: str | None) -> dict:
    client = get_client()
    existing = client.table("users").select("*").eq("telegram_chat_id", chat_id).execute()
    if existing.data:
        return existing.data[0]

    inserted = client.table("users").insert(
        {"telegram_chat_id": chat_id, "telegram_username": username}
    ).execute()
    user = inserted.data[0]

    client.table("user_scheduler_state").upsert(
        {"user_id": user["id"], "next_contact_at": _now_iso()}
    ).execute()
    return user


def get_user_by_id(user_id: int) -> dict:
    client = get_client()
    res = client.table("users").select("*").eq("id", user_id).single().execute()
    return res.data


def list_all_users() -> list[dict]:
    client = get_client()
    return client.table("users").select("*").execute().data


# ---------- tasks ----------

def list_pending_tasks(user_id: int) -> list[dict]:
    client = get_client()
    res = (
        client.table("tasks")
        .select("*")
        .eq("user_id", user_id)
        .in_("status", ["pending", "scheduled", "in_progress"])
        .order("created_at")
        .execute()
    )
    return res.data


def create_task(
    user_id: int,
    title: str,
    description: str | None,
    deadline_at: str | None,
    kind: str = "agreement",
) -> dict:
    client = get_client()
    res = client.table("tasks").insert(
        {
            "user_id": user_id,
            "title": title,
            "description": description,
            "deadline_at": deadline_at,
            "kind": kind,
        }
    ).execute()
    return res.data[0]


def get_task(user_id: int, task_id: int) -> dict | None:
    client = get_client()
    res = (
        client.table("tasks")
        .select("*")
        .eq("user_id", user_id)
        .eq("id", task_id)
        .execute()
    )
    return res.data[0] if res.data else None


def update_task(
    user_id: int,
    task_id: int,
    title: str | None = None,
    description: str | None = None,
    deadline_at: str | None = None,
    kind: str | None = None,
) -> dict | None:
    client = get_client()
    fields = {"updated_at": _now_iso()}
    if title is not None:
        fields["title"] = title
    if description is not None:
        fields["description"] = description
    if deadline_at is not None:
        fields["deadline_at"] = deadline_at
    if kind is not None:
        fields["kind"] = kind

    res = (
        client.table("tasks")
        .update(fields)
        .eq("user_id", user_id)
        .eq("id", task_id)
        .execute()
    )
    return res.data[0] if res.data else None


def set_task_estimate(user_id: int, task_id: int, estimated_minutes: int) -> dict | None:
    client = get_client()
    res = (
        client.table("tasks")
        .update({"estimated_minutes": estimated_minutes, "updated_at": _now_iso()})
        .eq("user_id", user_id)
        .eq("id", task_id)
        .execute()
    )
    return res.data[0] if res.data else None


def confirm_schedule_slot(user_id: int, task_id: int, start_at: str, estimated_minutes: int | None) -> dict | None:
    client = get_client()
    end_at = None
    if estimated_minutes:
        start_dt = parse_ts(start_at)
        end_at = (start_dt + timedelta(minutes=estimated_minutes)).isoformat()

    res = (
        client.table("tasks")
        .update(
            {
                "scheduled_start_at": start_at,
                "scheduled_end_at": end_at,
                "status": "scheduled",
                "updated_at": _now_iso(),
            }
        )
        .eq("user_id", user_id)
        .eq("id", task_id)
        .execute()
    )
    if not res.data:
        return None

    update_scheduler_state(user_id, next_contact_at=start_at, pending_nudge_kind="checkin", active_task_id=task_id)
    return res.data[0]


def mark_task_in_progress(user_id: int, task_id: int, default_check_back_minutes: int = 30) -> dict | None:
    client = get_client()
    res = (
        client.table("tasks")
        .update({"status": "in_progress", "started_at": _now_iso(), "updated_at": _now_iso()})
        .eq("user_id", user_id)
        .eq("id", task_id)
        .execute()
    )
    if not res.data:
        return None

    task = res.data[0]
    check_back_minutes = task.get("estimated_minutes") or default_check_back_minutes
    next_contact = (datetime.now(dt_timezone.utc) + timedelta(minutes=check_back_minutes)).isoformat()
    update_scheduler_state(user_id, next_contact_at=next_contact, pending_nudge_kind="progress_check", active_task_id=task_id)
    return task


def mark_task_done(user_id: int, task_id: int) -> dict | None:
    client = get_client()
    res = (
        client.table("tasks")
        .update({"status": "done", "completed_at": _now_iso(), "updated_at": _now_iso()})
        .eq("user_id", user_id)
        .eq("id", task_id)
        .execute()
    )
    if not res.data:
        return None

    log_leisure_event(user_id, "task_completed_on_time", minutes=None, task_id=task_id)
    _clear_scheduler_state_if_tracking(user_id, task_id)
    return res.data[0]


def _clear_scheduler_state_if_tracking(user_id: int, task_id: int) -> None:
    """Solo limpia el cursor del scheduler si estaba vigilando justo esta tarea —
    evita pisar el seguimiento de otra tarea que esté en curso al mismo tiempo."""
    state = get_scheduler_state(user_id)
    if state and state.get("active_task_id") == task_id:
        update_scheduler_state(user_id, next_contact_at=_now_iso(), pending_nudge_kind=None, active_task_id=None)


def acknowledge_reminder(user_id: int, task_id: int) -> dict | None:
    client = get_client()
    res = (
        client.table("tasks")
        .update({"status": "done", "completed_at": _now_iso(), "updated_at": _now_iso()})
        .eq("user_id", user_id)
        .eq("id", task_id)
        .eq("kind", "reminder")
        .execute()
    )
    if not res.data:
        return None

    _clear_scheduler_state_if_tracking(user_id, task_id)
    return res.data[0]


def check_schedule_conflict(user_id: int, candidate_start_at: str, duration_minutes: int | None) -> dict:
    start_dt = parse_ts(candidate_start_at)
    duration = duration_minutes or 30
    end_dt = start_dt + timedelta(minutes=duration)

    client = get_client()
    conflicts = []

    tasks = (
        client.table("tasks")
        .select("id,title,scheduled_start_at,scheduled_end_at")
        .eq("user_id", user_id)
        .eq("status", "scheduled")
        .execute()
        .data
    )
    for t in tasks:
        if not t.get("scheduled_start_at") or not t.get("scheduled_end_at"):
            continue
        t_start = parse_ts(t["scheduled_start_at"])
        t_end = parse_ts(t["scheduled_end_at"])
        if start_dt < t_end and end_dt > t_start:
            conflicts.append(f"tarea agendada: {t['title']} ({t['scheduled_start_at']})")

    one_offs = client.table("one_off_events").select("title,start_at,end_at").eq("user_id", user_id).execute().data
    for e in one_offs:
        e_start = parse_ts(e["start_at"])
        e_end = parse_ts(e["end_at"])
        if start_dt < e_end and end_dt > e_start:
            conflicts.append(f"evento: {e['title']} ({e['start_at']})")

    weekday = start_dt.weekday()
    recurring = (
        client.table("recurring_events")
        .select("title,start_time,end_time")
        .eq("user_id", user_id)
        .eq("weekday", weekday)
        .eq("active", True)
        .execute()
        .data
    )
    for r in recurring:
        r_start = start_dt.replace(
            hour=int(r["start_time"].split(":")[0]), minute=int(r["start_time"].split(":")[1]), second=0, microsecond=0
        )
        r_end = start_dt.replace(
            hour=int(r["end_time"].split(":")[0]), minute=int(r["end_time"].split(":")[1]), second=0, microsecond=0
        )
        if start_dt < r_end and end_dt > r_start:
            conflicts.append(f"evento recurrente: {r['title']} ({r['start_time']}-{r['end_time']})")

    if conflicts:
        return {"ok": False, "conflicts": conflicts}
    return {"ok": True}


# ---------- task milestones ----------

def add_task_milestone(user_id: int, task_id: int, label: str, at: str) -> dict | None:
    task = get_task(user_id, task_id)
    if not task:
        return None
    client = get_client()
    res = client.table("task_milestones").insert({"task_id": task_id, "label": label, "at": at}).execute()
    return res.data[0]


def get_task_milestones(task_id: int) -> list[dict]:
    client = get_client()
    return (
        client.table("task_milestones")
        .select("*")
        .eq("task_id", task_id)
        .order("at")
        .execute()
        .data
    )


# ---------- schedule (recurring / one-off) ----------

def add_recurring_event(user_id: int, title: str, weekday: int, start_time: str, end_time: str) -> dict:
    client = get_client()
    res = client.table("recurring_events").insert(
        {"user_id": user_id, "title": title, "weekday": weekday, "start_time": start_time, "end_time": end_time}
    ).execute()
    return res.data[0]


def add_one_off_event(user_id: int, title: str, start_at: str, end_at: str) -> dict:
    client = get_client()
    res = client.table("one_off_events").insert(
        {"user_id": user_id, "title": title, "start_at": start_at, "end_at": end_at}
    ).execute()
    return res.data[0]


def get_recurring_events(user_id: int) -> list[dict]:
    client = get_client()
    return (
        client.table("recurring_events")
        .select("*")
        .eq("user_id", user_id)
        .eq("active", True)
        .execute()
        .data
    )


def get_one_off_events(user_id: int, start_range: str, end_range: str) -> list[dict]:
    client = get_client()
    return (
        client.table("one_off_events")
        .select("*")
        .eq("user_id", user_id)
        .gte("start_at", start_range)
        .lte("start_at", end_range)
        .execute()
        .data
    )


def get_scheduled_tasks_in_range(user_id: int, start_range: str, end_range: str) -> list[dict]:
    client = get_client()
    return (
        client.table("tasks")
        .select("*")
        .eq("user_id", user_id)
        .in_("status", ["scheduled", "in_progress"])
        .gte("scheduled_start_at", start_range)
        .lte("scheduled_start_at", end_range)
        .execute()
        .data
    )


# ---------- leisure log ----------

def log_leisure_event(user_id: int, kind: str, minutes: int | None, task_id: int | None) -> dict:
    client = get_client()
    res = client.table("leisure_log").insert(
        {"user_id": user_id, "kind": kind, "minutes": minutes, "task_id": task_id}
    ).execute()
    return res.data[0]


def get_recent_leisure_summary(user_id: int, hours: int = 48) -> dict:
    client = get_client()
    since = (datetime.now(dt_timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = (
        client.table("leisure_log")
        .select("kind,minutes")
        .eq("user_id", user_id)
        .gte("created_at", since)
        .execute()
        .data
    )
    summary = {"deferral_count": 0, "deferral_minutes": 0, "task_completed_on_time_count": 0}
    for r in rows:
        if r["kind"] == "deferral":
            summary["deferral_count"] += 1
            summary["deferral_minutes"] += r.get("minutes") or 0
        elif r["kind"] == "task_completed_on_time":
            summary["task_completed_on_time_count"] += 1
    summary["window_hours"] = hours
    return summary


def log_deferral(user_id: int, task_id: int | None, requested_minutes: int | None, reason: str | None) -> dict:
    minutes = requested_minutes or 20
    log_leisure_event(user_id, "deferral", minutes=minutes, task_id=task_id)

    next_contact = (datetime.now(dt_timezone.utc) + timedelta(minutes=minutes)).isoformat()
    update_scheduler_state(user_id, next_contact_at=next_contact, pending_nudge_kind="checkin", active_task_id=task_id)
    return {"ok": True, "next_contact_in_minutes": minutes}


# ---------- conversation memory ----------

def get_recent_conversation(user_id: int, limit: int = 40) -> list[dict]:
    client = get_client()
    res = (
        client.table("conversation_messages")
        .select("role,content,created_at")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return list(reversed(res.data))


def append_conversation_message(user_id: int, role: str, content: str) -> None:
    client = get_client()
    client.table("conversation_messages").insert({"user_id": user_id, "role": role, "content": content}).execute()


# ---------- scheduler state ----------

def update_scheduler_state(
    user_id: int,
    next_contact_at: str,
    pending_nudge_kind: str | None,
    active_task_id: int | None,
) -> None:
    client = get_client()
    client.table("user_scheduler_state").upsert(
        {
            "user_id": user_id,
            "next_contact_at": next_contact_at,
            "pending_nudge_kind": pending_nudge_kind,
            "active_task_id": active_task_id,
            "updated_at": _now_iso(),
        }
    ).execute()


def get_scheduler_state(user_id: int) -> dict | None:
    client = get_client()
    res = client.table("user_scheduler_state").select("*").eq("user_id", user_id).execute()
    return res.data[0] if res.data else None


def bump_cooldown_if_idle(user_id: int, cooldown_minutes: int = 15) -> None:
    """Evita que el scheduler vuelva a escribirle de inmediato tras una charla normal,
    salvo que ya haya un compromiso (checkin/escalation) pendiente de esa misma charla."""
    state = get_scheduler_state(user_id)
    if not state or state.get("pending_nudge_kind"):
        return
    next_contact = datetime.now(dt_timezone.utc) + timedelta(minutes=cooldown_minutes)
    update_scheduler_state(user_id, next_contact.isoformat(), None, None)


def set_awaiting_reply(user_id: int, first_delay_minutes: int = 1) -> None:
    """El bot acaba de preguntar algo que necesita respuesta — insistir pronto si no contesta."""
    next_contact = datetime.now(dt_timezone.utc) + timedelta(minutes=first_delay_minutes)
    update_scheduler_state(user_id, next_contact.isoformat(), "awaiting_reply", None)


def clear_awaiting_reply_if_active(user_id: int) -> None:
    """El usuario acaba de responder — si el cursor seguía esperando esa respuesta, se libera
    para que el scheduler no vuelva a insistir sobre algo que ya se contestó."""
    state = get_scheduler_state(user_id)
    if state and state.get("pending_nudge_kind") == "awaiting_reply":
        update_scheduler_state(user_id, _now_iso(), None, None)


def get_due_scheduler_states() -> list[dict]:
    client = get_client()
    now = _now_iso()
    return (
        client.table("user_scheduler_state")
        .select("*, users(*)")
        .lte("next_contact_at", now)
        .execute()
        .data
    )


def log_nudge(user_id: int, task_id: int | None, kind: str, message_text: str, escalation_level: int = 0) -> dict:
    client = get_client()
    res = client.table("nudges").insert(
        {
            "user_id": user_id,
            "task_id": task_id,
            "kind": kind,
            "message_text": message_text,
            "escalation_level": escalation_level,
        }
    ).execute()
    return res.data[0]


def get_last_nudge(user_id: int) -> dict | None:
    client = get_client()
    res = (
        client.table("nudges")
        .select("*")
        .eq("user_id", user_id)
        .order("sent_at", desc=True)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def get_last_nudge_for_task(user_id: int, task_id: int) -> dict | None:
    client = get_client()
    res = (
        client.table("nudges")
        .select("*")
        .eq("user_id", user_id)
        .eq("task_id", task_id)
        .order("sent_at", desc=True)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def mark_last_nudge_responded(user_id: int) -> None:
    last = get_last_nudge(user_id)
    if last and not last.get("user_responded_at"):
        client = get_client()
        client.table("nudges").update({"user_responded_at": _now_iso()}).eq("id", last["id"]).execute()


def mark_nudge_responded_by_id(nudge_id: int) -> None:
    client = get_client()
    client.table("nudges").update({"user_responded_at": _now_iso()}).eq("id", nudge_id).execute()


def get_unresolved_awaiting_reply(user_id: int, hours: int = 24) -> dict | None:
    client = get_client()
    since = (datetime.now(dt_timezone.utc) - timedelta(hours=hours)).isoformat()
    res = (
        client.table("nudges")
        .select("*")
        .eq("user_id", user_id)
        .in_("kind", ["awaiting_reply", "awaiting_reply_escalation"])
        .is_("user_responded_at", "null")
        .gte("sent_at", since)
        .order("sent_at", desc=True)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def get_first_unresponded_awaiting_reply(user_id: int) -> dict | None:
    """El primer ping de la racha actual de awaiting_reply sin resolver (para medir
    cuánto tiempo lleva insistiendo y saber cuándo cortar la ventana rápida)."""
    client = get_client()
    res = (
        client.table("nudges")
        .select("*")
        .eq("user_id", user_id)
        .in_("kind", ["awaiting_reply", "awaiting_reply_escalation"])
        .is_("user_responded_at", "null")
        .order("sent_at")
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None
