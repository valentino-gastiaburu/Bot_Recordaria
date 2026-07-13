import asyncio
import random
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo

from telegram.ext import ContextTypes

import ai_logic
import db

BASE_INTERVALS_MIN = [15, 30, 60, 120, 240]  # creciente: se vuelve menos frecuente, no más
JITTER_FRACTION = 0.3
LONG_BACKOFF_MIN = 360  # tras agotar la curva sin respuesta, se espera bastante más
BUSY_BUFFER_MIN = 10

FAST_CONFIRMATION_WINDOW_MIN = 15  # ventana total de insistencia rápida
FAST_CONFIRMATION_SEQUENCE_MIN = [1, 2]  # arranca en 1 min, se topa en 2 min (se repite el último valor)
FAST_CONFIRMATION_JITTER_SECONDS = 15  # variación en segundos, no en porcentaje

_user_locks: dict[int, asyncio.Lock] = {}


def get_user_lock(user_id: int) -> asyncio.Lock:
    """Un solo lock por usuario, compartido entre el handler de chat en vivo (bot.py) y
    el scan proactivo de acá — evita que ambos procesen al mismo usuario al mismo tiempo
    y se pisen (turno en vivo vs. nudge proactivo compitiendo por la misma conversación)."""
    lock = _user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[user_id] = lock
    return lock


def _next_follow_up_delay_min(escalation_level: int, deadline_at, now_utc: datetime) -> int:
    """Minutos hasta el próximo intento, creciente y con jitter (asume que quizás el
    usuario no agarró el celular, no que lo está ignorando) — pero nunca más lento de lo
    que permite un deadline cercano."""
    base = LONG_BACKOFF_MIN if escalation_level >= len(BASE_INTERVALS_MIN) else BASE_INTERVALS_MIN[escalation_level]
    delay = base * random.uniform(1 - JITTER_FRACTION, 1 + JITTER_FRACTION)

    if deadline_at:
        minutes_left = (db.parse_ts(deadline_at) - now_utc).total_seconds() / 60
        if minutes_left > 0:
            delay = min(delay, max(5, minutes_left / 3))

    return max(5, round(delay))


def _next_fast_confirmation_delay_min(escalation_level: int, elapsed_min: float):
    """Insiste cada 1-2 min (nunca más de 2), con jitter de unos segundos, hasta que el
    tiempo transcurrido desde el primer intento llegue a FAST_CONFIRMATION_WINDOW_MIN —
    ahí devuelve None, momento en que hay que dejar de insistir así de rápido.

    Se usa para cualquier cosa que sea, en el fondo, "te pregunté algo puntual y
    espero un sí/no": ¿ya iniciaste?, ¿confirmas que viste esto?, una pregunta a
    mitad de charla. Para seguimientos de progreso ("cómo vas con la tarea") se usa
    en cambio _next_follow_up_delay_min, en bloques grandes — ahí no se espera una
    confirmación puntual, así que insistir cada 1-2 min sería molesto sin sentido."""
    if elapsed_min >= FAST_CONFIRMATION_WINDOW_MIN:
        return None
    base = FAST_CONFIRMATION_SEQUENCE_MIN[min(escalation_level, len(FAST_CONFIRMATION_SEQUENCE_MIN) - 1)]
    jitter_min = random.uniform(-FAST_CONFIRMATION_JITTER_SECONDS, FAST_CONFIRMATION_JITTER_SECONDS) / 60
    return max(0.5, base + jitter_min)


def _augment_with_unresolved_thread(user_id: int, context: dict) -> dict:
    """Si quedó una pregunta sin resolver de una charla anterior, la suma al contexto de
    este nudge para que se pueda retomar de forma natural, y la da por mencionada."""
    unresolved = db.get_unresolved_awaiting_reply(user_id)
    if unresolved:
        context["unresolved_topic"] = unresolved["message_text"]
        db.mark_nudge_responded_by_id(unresolved["id"])
    return context


def _in_quiet_hours(now_local: datetime, quiet_start: str, quiet_end: str) -> bool:
    start = dt_time.fromisoformat(quiet_start)
    end = dt_time.fromisoformat(quiet_end)
    now_t = now_local.time()
    if start <= end:
        return start <= now_t < end
    return now_t >= start or now_t < end


def _next_quiet_hours_end(now_local: datetime, quiet_end: str) -> datetime:
    end = dt_time.fromisoformat(quiet_end)
    candidate = now_local.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if candidate <= now_local:
        candidate += timedelta(days=1)
    return candidate


def _current_busy_event(user_id: int, now_local: datetime, now_utc: datetime):
    for ev in db.get_recurring_events(user_id):
        if ev["weekday"] is not None and ev["weekday"] != now_local.weekday():
            continue
        start_t = dt_time.fromisoformat(ev["start_time"])
        end_t = dt_time.fromisoformat(ev["end_time"])
        if start_t <= now_local.time() < end_t:
            end_dt_local = now_local.replace(hour=end_t.hour, minute=end_t.minute, second=0, microsecond=0)
            return end_dt_local

    day_start = (now_utc - timedelta(hours=1)).isoformat()
    day_end = (now_utc + timedelta(hours=1)).isoformat()
    for ev in db.get_one_off_events(user_id, day_start, day_end):
        start_dt = db.parse_ts(ev["start_at"])
        end_dt = db.parse_ts(ev["end_at"])
        if start_dt <= now_utc < end_dt:
            return end_dt

    return None


def _pick_pending_task_without_plan(user_id: int):
    for task in db.list_pending_tasks(user_id):
        if task["status"] != "pending":
            continue
        if not task.get("scheduled_start_at"):
            return task
        if task.get("kind") != "reminder" and not task.get("estimated_minutes"):
            return task
    return None


def _process_due_user(state: dict):
    """Hace todo el trabajo bloqueante (DB + LLM) para un usuario vencido.

    Devuelve (chat_id, mensaje) si hay que mandar algo por Telegram, o None si no.
    El envío real por Telegram lo hace el caller (async), esta función nunca lo hace.
    """
    user = state["users"]
    user_id = user["id"]
    tz = ZoneInfo(user.get("timezone") or "America/Lima")
    now_utc = datetime.now(ZoneInfo("UTC"))
    now_local = now_utc.astimezone(tz)

    if _in_quiet_hours(now_local, user["quiet_hours_start"], user["quiet_hours_end"]):
        next_time = _next_quiet_hours_end(now_local, user["quiet_hours_end"])
        db.update_scheduler_state(
            user_id, next_time.astimezone(ZoneInfo("UTC")).isoformat(),
            state.get("pending_nudge_kind"), state.get("active_task_id"),
        )
        return None

    busy_until = _current_busy_event(user_id, now_local, now_utc)
    if busy_until:
        db.update_scheduler_state(
            user_id, (busy_until + timedelta(minutes=BUSY_BUFFER_MIN)).isoformat(),
            state.get("pending_nudge_kind"), state.get("active_task_id"),
        )
        return None

    pending_kind = state.get("pending_nudge_kind")
    active_task_id = state.get("active_task_id")

    if pending_kind == "checkin" and active_task_id:
        task = db.get_task(user_id, active_task_id)
        if not task or task["status"] not in ("scheduled", "in_progress"):
            db.update_scheduler_state(user_id, (now_utc + timedelta(hours=6)).isoformat(), None, None)
            return None

        if task.get("kind") == "reminder":
            # primera entrega del recordatorio en sí, luego se pasa a esperar confirmación explícita
            context = _augment_with_unresolved_thread(
                user_id, {"task": {"title": task["title"], "description": task.get("description")}}
            )
            message = ai_logic.redactar_nudge(user_id, "reminder_delivery", context)

            db.log_nudge(user_id, active_task_id, "reminder_ack", message, 0)
            db.append_conversation_message(user_id, "assistant", message)

            next_delay = _next_fast_confirmation_delay_min(0, 0.0)
            db.update_scheduler_state(
                user_id, (now_utc + timedelta(minutes=next_delay)).isoformat(), "reminder_ack", active_task_id
            )
            return user["telegram_chat_id"], message

        last_nudge = db.get_last_nudge_for_task(user_id, active_task_id)
        if last_nudge and last_nudge["kind"] in ("checkin", "escalation") and not last_nudge.get("user_responded_at"):
            escalation_level = last_nudge["escalation_level"] + 1
            kind = "escalation"
        else:
            escalation_level = 0
            kind = "checkin"

        # "¿ya iniciaste?" es una confirmación puntual (sí/no) — insiste rápido
        # (1-2 min) los primeros ~15 min; recién si eso se agota sin respuesta cae
        # al backoff lento normal (consciente del deadline).
        first_nudge = db.get_first_unresponded_nudge_for_task(user_id, active_task_id, ["checkin", "escalation"])
        elapsed_min = 0.0 if not first_nudge else (now_utc - db.parse_ts(first_nudge["sent_at"])).total_seconds() / 60
        fast_delay = _next_fast_confirmation_delay_min(escalation_level, elapsed_min)
        next_delay = fast_delay if fast_delay is not None else _next_follow_up_delay_min(
            escalation_level, task.get("deadline_at"), now_utc
        )

        context = _augment_with_unresolved_thread(user_id, {
            "task": {"title": task["title"], "deadline_at": task.get("deadline_at")},
            "escalation_level": escalation_level,
            "leisure_summary": db.get_recent_leisure_summary(user_id),
        })
        message = ai_logic.redactar_nudge(user_id, kind, context)

        db.log_nudge(user_id, active_task_id, kind, message, escalation_level)
        db.append_conversation_message(user_id, "assistant", message)

        db.update_scheduler_state(
            user_id, (now_utc + timedelta(minutes=next_delay)).isoformat(), "checkin", active_task_id
        )
        return user["telegram_chat_id"], message

    if pending_kind == "progress_check" and active_task_id:
        task = db.get_task(user_id, active_task_id)
        if not task or task["status"] != "in_progress":
            db.update_scheduler_state(user_id, (now_utc + timedelta(hours=6)).isoformat(), None, None)
            return None

        last_nudge = db.get_last_nudge_for_task(user_id, active_task_id)
        if last_nudge and not last_nudge.get("user_responded_at"):
            escalation_level = last_nudge["escalation_level"] + 1
            kind = "progress_escalation"
        else:
            escalation_level = 0
            kind = "progress_check"

        context = _augment_with_unresolved_thread(user_id, {
            "task": {
                "title": task["title"],
                "deadline_at": task.get("deadline_at"),
                "estimated_minutes": task.get("estimated_minutes"),
                "started_at": task.get("started_at"),
            },
            "escalation_level": escalation_level,
        })
        message = ai_logic.redactar_nudge(user_id, kind, context)

        db.log_nudge(user_id, active_task_id, kind, message, escalation_level)
        db.append_conversation_message(user_id, "assistant", message)

        next_delay = _next_follow_up_delay_min(escalation_level, task.get("deadline_at"), now_utc)
        db.update_scheduler_state(
            user_id, (now_utc + timedelta(minutes=next_delay)).isoformat(), "progress_check", active_task_id
        )
        return user["telegram_chat_id"], message

    if pending_kind == "reminder_ack" and active_task_id:
        task = db.get_task(user_id, active_task_id)
        if not task or task["status"] != "scheduled":
            db.update_scheduler_state(user_id, (now_utc + timedelta(hours=6)).isoformat(), None, None)
            return None

        last_nudge = db.get_last_nudge_for_task(user_id, active_task_id)
        if last_nudge and last_nudge["kind"] in ("reminder_ack", "reminder_escalation") and not last_nudge.get("user_responded_at"):
            escalation_level = last_nudge["escalation_level"] + 1
            kind = "reminder_escalation"
        else:
            escalation_level = 0
            kind = "reminder_ack"

        # "confírmame que lo viste" también es una confirmación puntual — misma
        # lógica de rápido-primero-luego-lento que en checkin.
        first_nudge = db.get_first_unresponded_nudge_for_task(
            user_id, active_task_id, ["reminder_ack", "reminder_escalation"]
        )
        elapsed_min = 0.0 if not first_nudge else (now_utc - db.parse_ts(first_nudge["sent_at"])).total_seconds() / 60
        fast_delay = _next_fast_confirmation_delay_min(escalation_level, elapsed_min)
        next_delay = fast_delay if fast_delay is not None else _next_follow_up_delay_min(escalation_level, None, now_utc)

        context = _augment_with_unresolved_thread(
            user_id, {"task": {"title": task["title"], "description": task.get("description")}}
        )
        message = ai_logic.redactar_nudge(user_id, kind, context)

        db.log_nudge(user_id, active_task_id, kind, message, escalation_level)
        db.append_conversation_message(user_id, "assistant", message)

        db.update_scheduler_state(
            user_id, (now_utc + timedelta(minutes=next_delay)).isoformat(), "reminder_ack", active_task_id
        )
        return user["telegram_chat_id"], message

    if pending_kind == "awaiting_reply":
        last_nudge = db.get_last_nudge(user_id)
        if last_nudge and last_nudge["kind"] in ("awaiting_reply", "awaiting_reply_escalation") and not last_nudge.get("user_responded_at"):
            escalation_level = last_nudge["escalation_level"] + 1
        else:
            escalation_level = 0

        first_nudge = db.get_first_unresponded_awaiting_reply(user_id)
        elapsed_min = 0.0 if not first_nudge else (now_utc - db.parse_ts(first_nudge["sent_at"])).total_seconds() / 60

        next_delay = _next_fast_confirmation_delay_min(escalation_level, elapsed_min)
        if next_delay is None:
            # ventana de ~15 min agotada sin respuesta: se libera el cursor. El nudge
            # queda sin user_responded_at en el log — así se puede retomar más adelante
            # (ver _augment_with_unresolved_thread / get_unresolved_awaiting_reply).
            db.update_scheduler_state(user_id, (now_utc + timedelta(hours=6)).isoformat(), None, None)
            return None

        kind = "awaiting_reply_escalation" if escalation_level > 0 else "awaiting_reply"
        message = ai_logic.redactar_nudge(user_id, kind, {"escalation_level": escalation_level})

        db.log_nudge(user_id, None, kind, message, escalation_level)
        db.append_conversation_message(user_id, "assistant", message)
        db.update_scheduler_state(
            user_id, (now_utc + timedelta(minutes=next_delay)).isoformat(), "awaiting_reply", None
        )
        return user["telegram_chat_id"], message

    task = _pick_pending_task_without_plan(user_id)
    if not task:
        db.update_scheduler_state(user_id, (now_utc + timedelta(hours=6)).isoformat(), None, None)
        return None

    last_nudge = db.get_last_nudge_for_task(user_id, task["id"])
    if last_nudge and last_nudge["kind"] in ("outreach", "outreach_escalation") and not last_nudge.get("user_responded_at"):
        escalation_level = last_nudge["escalation_level"] + 1
        kind = "outreach_escalation"
    else:
        escalation_level = 0
        kind = "outreach"

    context = _augment_with_unresolved_thread(user_id, {
        "task": {"title": task["title"], "deadline_at": task.get("deadline_at")},
        "escalation_level": escalation_level,
        "leisure_summary": db.get_recent_leisure_summary(user_id),
    })
    message = ai_logic.redactar_nudge(user_id, kind, context)

    db.log_nudge(user_id, task["id"], kind, message, escalation_level)
    db.append_conversation_message(user_id, "assistant", message)

    next_delay = _next_follow_up_delay_min(escalation_level, task.get("deadline_at"), now_utc)
    db.update_scheduler_state(user_id, (now_utc + timedelta(minutes=next_delay)).isoformat(), None, None)

    return user["telegram_chat_id"], message


async def scan_users(context: ContextTypes.DEFAULT_TYPE):
    due_states = await asyncio.to_thread(db.get_due_scheduler_states)
    for state in due_states:
        user_id = state["users"]["id"]
        lock = get_user_lock(user_id)
        if lock.locked():
            # el usuario está en medio de un turno en vivo ahora mismo — no compitas,
            # se retoma solo en la próxima pasada del scan (5 min después)
            continue
        async with lock:
            result = await asyncio.to_thread(_process_due_user, state)
        if result:
            chat_id, message = result
            await context.bot.send_message(chat_id=chat_id, text=message)


async def daily_outreach(context: ContextTypes.DEFAULT_TYPE):
    def _reset_all():
        now_iso = datetime.now(ZoneInfo("UTC")).isoformat()
        for user in db.list_all_users():
            if db.list_pending_tasks(user["id"]):
                db.update_scheduler_state(user["id"], now_iso, None, None)

    await asyncio.to_thread(_reset_all)
