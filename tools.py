import db

_WEEKDAYS = ["lunes", "martes", "miercoles", "miércoles", "jueves", "viernes", "sabado", "sábado", "domingo"]
_WEEKDAY_TO_INT = {
    "lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2, "jueves": 3,
    "viernes": 4, "sabado": 5, "sábado": 5, "domingo": 6,
}


def _weekday_to_int(weekday) -> int | None:
    if weekday is None:
        return None
    if isinstance(weekday, int):
        return weekday
    key = str(weekday).strip().lower()
    if key in ("diario", "todos_los_dias", "cada_dia", ""):
        return None
    if key in _WEEKDAY_TO_INT:
        return _WEEKDAY_TO_INT[key]
    return int(key)


def list_pending_tasks(user_id: int) -> dict:
    tasks = db.list_pending_tasks(user_id)
    for t in tasks:
        if t.get("kind") == "assignment":
            t["milestones"] = db.get_task_milestones(t["id"])
    return {"tasks": tasks}


def create_task(
    user_id: int,
    title: str,
    description: str = None,
    deadline_at: str = None,
    kind: str = "agreement",
) -> dict:
    task = db.create_task(user_id, title, description, deadline_at, kind)
    return {"task": task}


def update_task(
    user_id: int,
    task_id: int,
    title: str = None,
    description: str = None,
    deadline_at: str = None,
    kind: str = None,
) -> dict:
    task = db.update_task(user_id, task_id, title, description, deadline_at, kind)
    if not task:
        return {"error": "task_not_found"}
    return {"task": task}


def add_task_milestone(user_id: int, task_id: int, label: str, at: str) -> dict:
    milestone = db.add_task_milestone(user_id, task_id, label, at)
    if not milestone:
        return {"error": "task_not_found"}
    return {"milestone": milestone}


def acknowledge_reminder(user_id: int, task_id: int) -> dict:
    task = db.acknowledge_reminder(user_id, task_id)
    if not task:
        return {"error": "task_not_found_or_not_a_reminder"}
    return {"task": task}


def set_task_estimate(user_id: int, task_id: int, estimated_minutes: int) -> dict:
    task = db.set_task_estimate(user_id, task_id, estimated_minutes)
    if not task:
        return {"error": "task_not_found"}
    return {"task": task}


def propose_schedule_slot(user_id: int, task_id: int, candidate_start_at: str) -> dict:
    task = db.get_task(user_id, task_id)
    if not task:
        return {"error": "task_not_found"}
    result = db.check_schedule_conflict(user_id, candidate_start_at, task.get("estimated_minutes"))
    return result


def confirm_schedule_slot(user_id: int, task_id: int, start_at: str) -> dict:
    from datetime import datetime, timezone

    task = db.get_task(user_id, task_id)
    if not task:
        return {"error": "task_not_found"}

    now = datetime.now(timezone.utc)
    if db.parse_ts(start_at) <= now:
        return {
            "error": "start_at_en_el_pasado",
            "now": now.isoformat(),
            "detalle": "Ese horario ya pasó. Pídele al usuario un horario futuro y vuelve a intentar.",
        }

    updated = db.confirm_schedule_slot(user_id, task_id, start_at, task.get("estimated_minutes"))
    if not updated:
        return {"error": "task_not_found"}
    return {"task": updated}


def mark_task_in_progress(user_id: int, task_id: int) -> dict:
    task = db.mark_task_in_progress(user_id, task_id)
    if not task:
        return {"error": "task_not_found"}
    return {"task": task}


def mark_task_done(user_id: int, task_id: int) -> dict:
    task = db.mark_task_done(user_id, task_id)
    if not task:
        return {"error": "task_not_found"}
    return {"task": task}


def log_deferral(user_id: int, task_id: int = None, requested_minutes: int = None, reason: str = None) -> dict:
    return db.log_deferral(user_id, task_id, requested_minutes, reason)


def add_recurring_event(
    user_id: int,
    title: str,
    weekday,
    start_time: str,
    end_time: str,
    category: str = "other",
    requires_transport: bool = False,
) -> dict:
    weekday_int = _weekday_to_int(weekday)
    event = db.add_recurring_event(user_id, title, weekday_int, start_time, end_time, category, requires_transport)
    return {"event": event}


def set_recurring_event_active(user_id: int, event_id: int, active: bool) -> dict:
    event = db.set_recurring_event_active(user_id, event_id, active)
    if not event:
        return {"error": "event_not_found"}
    return {"event": event}


def add_one_off_event(user_id: int, title: str, start_at: str, end_at: str) -> dict:
    event = db.add_one_off_event(user_id, title, start_at, end_at)
    return {"event": event}


def get_schedule(user_id: int, range: str = "today") -> dict:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    if range == "this_week":
        start = now
        end = now + timedelta(days=7)
    else:
        start = now
        end = now + timedelta(days=1)

    return {
        "recurring_events": db.get_recurring_events(user_id),
        "one_off_events": db.get_one_off_events(user_id, start.isoformat(), end.isoformat()),
        "scheduled_tasks": db.get_scheduled_tasks_in_range(user_id, start.isoformat(), end.isoformat()),
    }


def get_recent_leisure_summary(user_id: int) -> dict:
    return db.get_recent_leisure_summary(user_id)


TOOL_FUNCTIONS = {
    "list_pending_tasks": list_pending_tasks,
    "create_task": create_task,
    "update_task": update_task,
    "add_task_milestone": add_task_milestone,
    "acknowledge_reminder": acknowledge_reminder,
    "set_task_estimate": set_task_estimate,
    "propose_schedule_slot": propose_schedule_slot,
    "confirm_schedule_slot": confirm_schedule_slot,
    "mark_task_in_progress": mark_task_in_progress,
    "mark_task_done": mark_task_done,
    "log_deferral": log_deferral,
    "add_recurring_event": add_recurring_event,
    "set_recurring_event_active": set_recurring_event_active,
    "add_one_off_event": add_one_off_event,
    "get_schedule": get_schedule,
    "get_recent_leisure_summary": get_recent_leisure_summary,
}


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_pending_tasks",
            "description": "Lista las tareas pendientes, agendadas o en curso del usuario, con su deadline, estimado y horario si ya se acordó.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Crea un nuevo pendiente para el usuario. No fija cuánto tiempo tomará (excepto reminders): eso se hace luego con set_task_estimate una vez que el usuario responda.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Título corto"},
                    "description": {"type": "string", "description": "Detalle opcional"},
                    "deadline_at": {"type": "string", "description": "Fecha/hora límite en formato ISO 8601, si el usuario dio una"},
                    "kind": {
                        "type": "string",
                        "enum": ["reminder", "agreement", "assignment"],
                        "description": (
                            "'reminder' = solo necesita avisarle algo y que confirme explícitamente que lo vio, "
                            "sin trabajo real de por medio (ej. 'recuérdame llamar al dentista'). "
                            "'agreement' = algo que tiene que hacer sin fecha límite externa dura, se negocia cuándo "
                            "empezar y se confirma cuando termina. "
                            "'assignment' = tiene una fecha límite externa dura (examen, entrega) y puede tener "
                            "varios hitos intermedios (usa add_task_milestone para esos)."
                        ),
                    },
                },
                "required": ["title", "kind"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_task",
            "description": "Edita un pendiente que YA existe (título, descripción, deadline y/o kind) cuando el usuario da más detalles sobre algo que ya habían mencionado antes. Úsalo en vez de create_task siempre que ya aparezca en list_pending_tasks — nunca crees uno duplicado para lo mismo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "deadline_at": {"type": "string", "description": "Fecha/hora límite en ISO 8601"},
                    "kind": {"type": "string", "enum": ["reminder", "agreement", "assignment"]},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_task_milestone",
            "description": "Agrega una fecha/hito intermedio a una tarea de tipo 'assignment' (ej: 'mi parte lista a las 7pm' cuando la entrega final es a las 11:59pm). Se puede llamar varias veces para varios hitos.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                    "label": {"type": "string", "description": "Qué representa este hito, ej. 'mi parte lista'"},
                    "at": {"type": "string", "description": "Fecha/hora del hito en ISO 8601"},
                },
                "required": ["task_id", "label", "at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "acknowledge_reminder",
            "description": "Cierra un recordatorio (kind='reminder') cuando el usuario confirma EXPLÍCITAMENTE que lo vio/entendió (ej. 'sí, ya vi', 'entendido', 'ok gracias por avisar'). No la llames ante una respuesta ambigua o que no confirme claramente — para eso, vuelve a preguntar. Nunca uses mark_task_done para un reminder.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_task_estimate",
            "description": "Guarda cuántos minutos cree el usuario que le tomará una tarea. Llamar siempre que el usuario responda a '¿cuánto crees que te tome?'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                    "estimated_minutes": {"type": "integer"},
                },
                "required": ["task_id", "estimated_minutes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_schedule_slot",
            "description": "Verifica (sin confirmar nada) si un horario candidato para empezar una tarea choca con clases, eventos u otras tareas ya agendadas. Úsalo antes de proponerle un horario al usuario.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                    "candidate_start_at": {"type": "string", "description": "Horario candidato en ISO 8601"},
                },
                "required": ["task_id", "candidate_start_at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_schedule_slot",
            "description": "Confirma el horario en el que el usuario y tú acordaron que empezará una tarea. Solo llamar cuando el usuario ya aceptó explícitamente ese horario.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                    "start_at": {"type": "string", "description": "Horario acordado en ISO 8601"},
                },
                "required": ["task_id", "start_at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_task_in_progress",
            "description": "Marca que el usuario confirmó que ya empezó una tarea.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_task_done",
            "description": "Marca una tarea como terminada, cuando el usuario confirma que la completó.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_deferral",
            "description": "Registra que el usuario pidió posponer/descansar/jugar en vez de empezar una tarea. Llamar SIEMPRE que esto pase, para poder reprogramar el seguimiento y recordar el patrón.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "Tarea de la que se está posponiendo, si aplica"},
                    "requested_minutes": {"type": "integer", "description": "Minutos que el usuario pidió para descansar/esperar, si dio un número"},
                    "reason": {"type": "string", "description": "Motivo que dio el usuario, ej. 'cansado', 'quiere jugar'"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_recurring_event",
            "description": (
                "Registra algo que se repite periódicamente en la vida del usuario: una clase, el "
                "trabajo, o una rutina fija como el horario en que suele almorzar/cenar. Úsalo también "
                "para aprender su rutina diaria (comidas, gimnasio, etc.), no solo clases."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "weekday": {
                        "type": "string",
                        "description": (
                            "Día de la semana en español (ej. 'lunes'), o 'diario' si se repite todos "
                            "los días (ej. la hora en que suele almorzar)."
                        ),
                    },
                    "start_time": {"type": "string", "description": "Hora de inicio HH:MM"},
                    "end_time": {"type": "string", "description": "Hora de fin HH:MM"},
                    "category": {
                        "type": "string",
                        "enum": ["class", "work", "meal", "other"],
                        "description": "'class'=clase, 'work'=trabajo, 'meal'=comida/rutina diaria, 'other'=otro.",
                    },
                    "requires_transport": {
                        "type": "boolean",
                        "description": (
                            "true si para llegar a esto el usuario necesita trasladarse (ej. ir a la "
                            "universidad u oficina) — se usa para dejar holgura antes de proponerle "
                            "empezar otra cosa justo después de que termine."
                        ),
                    },
                },
                "required": ["title", "weekday", "start_time", "end_time", "category"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_recurring_event_active",
            "description": (
                "Pausa o reactiva un evento recurrente sin borrarlo — para cuando el usuario avisa que "
                "algo periódico no aplica por un tiempo (ej. 'esta semana no tengo clases, son de "
                "vacaciones') y luego vuelve a aplicar."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "integer"},
                    "active": {"type": "boolean"},
                },
                "required": ["event_id", "active"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_one_off_event",
            "description": "Registra un evento puntual del usuario, como un examen o una salida familiar en una fecha específica.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "start_at": {"type": "string", "description": "Inicio en ISO 8601"},
                    "end_at": {"type": "string", "description": "Fin en ISO 8601"},
                },
                "required": ["title", "start_at", "end_at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_schedule",
            "description": "Devuelve el horario del usuario (clases, eventos, tareas agendadas) para hoy o esta semana.",
            "parameters": {
                "type": "object",
                "properties": {
                    "range": {"type": "string", "enum": ["today", "this_week"]},
                },
                "required": ["range"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_leisure_summary",
            "description": "Devuelve un resumen de cuántas veces el usuario pospuso tareas y cuánto tiempo de descanso tomó recientemente, y cuántas tareas completó a tiempo. Úsalo antes de decidir qué tan firme ser cuando el usuario pide posponer de nuevo.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


# Tools de solo lectura: se dejan disponibles libremente durante la charla (stage 1),
# no mutan estado asi que no tienen el riesgo de "confirmacion falsa" que motivo el
# esquema de codigos de intencion (ver intent_codes.py).
READ_TOOL_NAMES = {"list_pending_tasks", "propose_schedule_slot", "get_schedule", "get_recent_leisure_summary"}

TOOLS_BY_NAME = {t["function"]["name"]: t for t in TOOLS}
READ_TOOLS = [t for t in TOOLS if t["function"]["name"] in READ_TOOL_NAMES]
