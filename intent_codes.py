"""Registro de codigos de intencion de 4 digitos.

Cada respuesta final del modelo en handle_user_message debe empezar con uno de
estos codigos seguido de un salto de linea, declarando que accion interna
corresponde a este turno. El codigo es la unica fuente de verdad de la
intencion, en vez de intentar adivinar por el texto si "suena" a que confirmo
algo (esa deteccion por regex resulto fragil en la practica).

Si el codigo implica una o mas acciones, se dispara un segundo llamado a la
API por cada una, forzando (tool_choice) la funcion exacta correspondiente
para extraer sus argumentos — asi la accion ocurre de verdad, sin depender de
que el modelo se acuerde de llamar la tool por su cuenta en el mismo paso en
que redacta el texto para el usuario.
"""

INTENT_CODES = {
    "0000": {
        "description": "Solo responder, no hay ninguna accion que registrar.",
        "actions": [],
    },
    "0001": {
        "description": "Registrar un pendiente nuevo (reminder/agreement/assignment).",
        "actions": ["create_task"],
    },
    "0002": {
        "description": "Editar un pendiente que ya existe.",
        "actions": ["update_task"],
    },
    "0003": {
        "description": "Agregar un hito intermedio a un assignment.",
        "actions": ["add_task_milestone"],
    },
    "0004": {
        "description": "El usuario confirmo explicitamente que vio un recordatorio.",
        "actions": ["acknowledge_reminder"],
    },
    "0005": {
        "description": "Guardar el estimado de minutos que dio el usuario para una tarea.",
        "actions": ["set_task_estimate"],
    },
    "0006": {
        "description": "Confirmar el horario acordado para empezar una tarea.",
        "actions": ["confirm_schedule_slot"],
    },
    "0007": {
        "description": "El usuario confirmo que ya empezo una tarea.",
        "actions": ["mark_task_in_progress"],
    },
    "0008": {
        "description": "El usuario confirmo que termino una tarea.",
        "actions": ["mark_task_done"],
    },
    "0009": {
        "description": "El usuario pidio posponer/descansar en vez de avanzar.",
        "actions": ["log_deferral"],
    },
    "0010": {
        "description": "Registrar un evento semanal recurrente (ej. una clase).",
        "actions": ["add_recurring_event"],
    },
    "0011": {
        "description": "Registrar un evento puntual con horario (ej. un examen).",
        "actions": ["add_one_off_event"],
    },
    "1001": {
        "description": (
            "Combo: crear un pendiente Y de una confirmar su horario en el mismo turno "
            "(el caso tipico de un reminder: se crea y se agenda el aviso de una)."
        ),
        "actions": ["create_task", "confirm_schedule_slot"],
    },
    "1002": {
        "description": "Combo: crear un pendiente Y de una guardar el estimado de minutos que dio el usuario.",
        "actions": ["create_task", "set_task_estimate"],
    },
    "1003": {
        "description": "Combo: editar un pendiente existente Y de una confirmar su horario.",
        "actions": ["update_task", "confirm_schedule_slot"],
    },
}


def actions_for_code(code: str) -> list:
    entry = INTENT_CODES.get(code)
    return entry["actions"] if entry else []


def codes_prompt_block() -> str:
    return "\n".join(f"{code}: {info['description']}" for code, info in INTENT_CODES.items())
