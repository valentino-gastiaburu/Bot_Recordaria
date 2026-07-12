import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from openai import OpenAI

import db
import tools as tools_module
from tools import TOOLS, TOOL_FUNCTIONS

load_dotenv()
API_KEY = os.getenv("IA_KEY")

client = OpenAI(api_key=API_KEY, base_url="https://api.deepseek.com")

MAX_TOOL_ITERATIONS = 6

_SCHEDULING_CLAIM_PATTERN = re.compile(
    r"agend|acordad|acordamos|confirmamos|queda(mos)?\s|arrancas?\s+a\s+las|empiezas?\s+a\s+las",
    re.IGNORECASE,
)
_DONE_CLAIM_PATTERN = re.compile(r"termin|acab|complet|marc.{0,10}hecho", re.IGNORECASE)
_GENERIC_CONFIRMATION_PATTERN = re.compile(r"confirm|guard|registr|anot", re.IGNORECASE)

_DEFERRAL_SIGNAL_PATTERN = re.compile(
    r"cansad|jugar|despu[eé]s|un rato|no quiero|ahorita no|luego lo hago|al rato|m[aá]s tarde|no tengo ganas",
    re.IGNORECASE,
)

_START_SIGNAL_PATTERN = re.compile(
    r"ya empec|ya estoy haciendo|lo estoy haciendo|ya arranqu",
    re.IGNORECASE,
)

_FINISH_SIGNAL_PATTERN = re.compile(r"ya termin|ya acab|ya lo hice", re.IGNORECASE)

_QUESTION_MARK_PATTERN = re.compile(r"\?")


def _looks_like_awaiting_reply(reply: str) -> bool:
    # Solo cuenta si el mensaje TERMINA en una pregunta real (permitiendo algo de
    # puntuación/emoji decorativo después) — frases sueltas tipo "avísame cualquier
    # cosa" como despedida casual no deben rearmar la insistencia rápida.
    tail = (reply or "").rstrip()[-25:]
    return bool(_QUESTION_MARK_PATTERN.search(tail))


def _needs_tool_backed_reply(reply: str, user_text: str, tools_called: set) -> bool:
    reply = reply or ""
    user_text = user_text or ""

    if _SCHEDULING_CLAIM_PATTERN.search(reply) and "confirm_schedule_slot" not in tools_called:
        return True
    if _DONE_CLAIM_PATTERN.search(reply) and not ({"mark_task_done", "acknowledge_reminder"} & tools_called):
        return True
    if _GENERIC_CONFIRMATION_PATTERN.search(reply) and not tools_called:
        return True
    if _DEFERRAL_SIGNAL_PATTERN.search(user_text) and "log_deferral" not in tools_called:
        return True
    if _START_SIGNAL_PATTERN.search(user_text) and "mark_task_in_progress" not in tools_called:
        return True
    if _FINISH_SIGNAL_PATTERN.search(user_text) and not ({"mark_task_done", "acknowledge_reminder"} & tools_called):
        return True
    return False

BASE_PERSONALITY = """
Eres el asistente personal del usuario dentro de un chat de Telegram. Tu trabajo es ayudarlo a
organizarse: conocer sus tareas pendientes, su horario (clases, exámenes, planes), acordar con él
cuándo va a hacer cada cosa, y hacerle seguimiento hasta que las haga.

Hablas en español, de forma natural, cercana e informal, como un amigo que se preocupa por él —
nunca como una interfaz de software. Prohibido decir frases tipo "he creado la tarea en el
sistema", "he generado tu cronograma", "según mis registros" o cualquier cosa que suene a que eres
una base de datos hablando. También prohibido narrar tu propio proceso entre paréntesis o de forma
meta, tipo "(revisando la conversación anterior...)" o "(pensando...)" — directamente dile lo que
le tengas que decir, como lo haría una persona. Todo se siente como una conversación normal.

Cuando el usuario menciona algo que tiene que hacer, pregúntale (en su momento, no todo de golpe)
cuánto cree que le tomará. En cuanto responda con un tiempo (aunque sea aproximado, "una hora",
"un rato"), llama a set_task_estimate ANTES de decir nada más — nunca sigas la conversación con
ese dato en la cabeza sin haberlo guardado. Recién después, y usando propose_schedule_slot para
revisar que no choque con nada de su horario, propónle un horario. No le impongas un horario:
propónselo y negocia si no le viene bien.

Antes de llamar a create_task, revisa con list_pending_tasks si la tarea de la que están hablando
ya existe (por el mismo tema, aunque el usuario la mencione con otras palabras o dé más detalles
después). Si ya existe, usa update_task con su task_id para agregar/corregir información — jamás
crees una tarea duplicada para algo de lo que ya venían hablando.

Hay tres tipos de pendiente (parámetro "kind" de create_task, obligatorio elegir uno):
- "reminder": el usuario solo necesita que le avises algo en un momento dado, sin trabajo real de
  por medio (ej. "recuérdame llamar al dentista a las 3pm"). No le preguntes cuánto le tomará ni le
  niegocies un horario de trabajo. IMPORTANTE: la hora en la que debe avisársele va SIEMPRE en
  confirm_schedule_slot (start_at), NUNCA en deadline_at — un reminder no tiene deadline, tiene una
  hora de aviso. O sea: create_task(kind="reminder") y de inmediato confirm_schedule_slot(task_id,
  start_at=<la hora que dio el usuario>), en el mismo turno si es posible. Solo di "listo, te aviso a
  tal hora" si confirm_schedule_slot ya tuvo éxito. Cuando llegue esa hora, el sistema le manda el
  recordatorio automáticamente. Este tipo SOLO se cierra con acknowledge_reminder, y solo cuando el
  usuario confirme explícitamente que lo vio/entendió (ej. "ok ya vi", "entendido") — una respuesta
  ambigua o que no confirme claramente NO cuenta, vuelve a preguntar. Nunca uses mark_task_done para
  un reminder.
- "assignment": tiene una fecha límite externa dura (examen, entrega, presentación). Pregúntale si
  hay fechas intermedias importantes además del deadline final (ej. "¿tu parte del grupal tiene que
  estar lista antes?") y guárdalas con add_task_milestone — puede haber varias.
- "agreement": todo lo demás — algo que tiene que hacer pero sin fecha límite externa impuesta por
  alguien más.

Cuando el usuario confirme que ya empezó una tarea (aunque sea de pasada, "ya le entré", "ya estoy
en eso"), llama a mark_task_in_progress EN ESE MISMO TURNO. Cuando confirme que la terminó, llama a
mark_task_done EN ESE MISMO TURNO (nunca para un reminder, ver arriba). No lo dejes para después ni
asumas que "ya quedó claro" sin haber llamado la herramienta.

No eres completamente tolerante. Si el usuario quiere posponer/descansar/jugar en vez de avanzar,
llama a log_deferral EN ESE MISMO TURNO (siempre, sin excepción) con los minutos/motivo que haya
dado, antes de responder. Nunca hables de "veces que ya pospuso" o de un patrón de descansos sin
haber llamado a get_recent_leisure_summary primero y ver el resultado real — si no lo has llamado,
no sabes si pospuso antes, así que no lo inventes. Está bien ceder la primera vez, pero si el
resumen real muestra varios descansos/deferrals recientes, puedes ser más firme y cuestionarlo un
poco más, siempre en tono natural, no como una regla robótica.

Nunca digas que guardaste, agendaste o marcaste algo como hecho si no llamaste a la herramienta
correspondiente y esta tuvo éxito en este mismo turno. Si necesitas datos (tareas pendientes,
horario, resumen de descansos) para responder bien, usa las herramientas antes de contestar en vez
de inventar.

La lista de "tareas ya registradas" que te doy más abajo es la ÚNICA fuente confiable de qué está
realmente agendado — el historial de conversación puede contener cosas que se discutieron pero
nunca se guardaron de verdad (por ejemplo, si dijiste antes "queda acordado a las 9pm" pero esa
tarea no tiene horario_acordado en la lista, entonces NO está realmente agendada, pase lo que pase
en el chat). Si hay conflicto entre lo que el historial sugiere y lo que dice esa lista, confía en
la lista y acláralo con el usuario en vez de asumir que sí se guardó.

Nunca inventes ni asumas la hora actual — usa exactamente la que aparece en "Fecha y hora actual
del usuario" en este mismo mensaje. Si vas a mencionar cuánto falta para algo, calcúlalo a partir
de esa hora real, nunca de una que te parezca lógica por el contexto de la charla.
""".strip()


def _pending_tasks_context(user_id: int) -> str:
    tasks = tools_module.list_pending_tasks(user_id)["tasks"]
    if not tasks:
        return "El usuario no tiene tareas registradas ahora mismo."

    lines = []
    for t in tasks:
        line = (
            f"- id={t['id']} kind={t.get('kind')} \"{t['title']}\" status={t['status']} "
            f"estimado_min={t.get('estimated_minutes')} deadline={t.get('deadline_at')} "
            f"horario_acordado={t.get('scheduled_start_at')}"
        )
        milestones = t.get("milestones") or []
        for m in milestones:
            line += f"\n    hito: \"{m['label']}\" a las {m['at']}"
        lines.append(line)
    return (
        "Tareas que el usuario ya tiene registradas (estos son sus id reales — reutilízalos con "
        "update_task/set_task_estimate/confirm_schedule_slot/mark_task_in_progress/mark_task_done; "
        "NUNCA llames create_task para algo que ya está en esta lista, aunque el usuario lo mencione "
        "con otras palabras o agregue detalles nuevos):\n" + "\n".join(lines)
    )


def _system_prompt(user: dict, user_id: int) -> str:
    tz = ZoneInfo(user.get("timezone") or "America/Lima")
    now_local = datetime.now(tz)
    return (
        f"{BASE_PERSONALITY}\n\n"
        f"Fecha y hora actual del usuario: {now_local.strftime('%A %d de %B de %Y, %H:%M')} "
        f"({user.get('timezone')}). Usa siempre este año como referencia para calcular fechas "
        f"relativas ('el viernes', 'la próxima semana', etc.) — nunca asumas un año distinto.\n\n"
        f"{_pending_tasks_context(user_id)}"
    )


def _run_tool_call(user_id: int, tool_call) -> dict:
    name = tool_call.function.name
    try:
        args = json.loads(tool_call.function.arguments or "{}")
    except json.JSONDecodeError:
        args = {}

    fn = TOOL_FUNCTIONS.get(name)
    if not fn:
        return {"error": f"unknown_tool:{name}"}

    try:
        return fn(user_id=user_id, **args)
    except Exception as e:
        return {"error": str(e)}


def _chat_with_tools(user_id: int, messages: list, user_text: str) -> str:
    tools_called = set()
    correction_attempted = False

    for _ in range(MAX_TOOL_ITERATIONS):
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            tools=TOOLS,
            temperature=0.8,
        )
        message = response.choices[0].message

        if not message.tool_calls:
            reply = message.content or ""
            if not correction_attempted and _needs_tool_backed_reply(reply, user_text, tools_called):
                correction_attempted = True
                messages.append({"role": "assistant", "content": reply})
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "Revisa tu respuesta: suena a que confirmaste/agendaste/guardaste/terminaste "
                            "algo, o el usuario te pidió posponer/dijo que ya empezó o terminó, pero no "
                            "llamaste la herramienta específica que corresponde a eso en este turno (ej. "
                            "confirm_schedule_slot para agendar, mark_task_done/acknowledge_reminder para "
                            "cerrar, log_deferral para posponer, mark_task_in_progress para 'ya empecé'). "
                            "Llama ahora la herramienta correcta antes de responder. No inventes datos sin "
                            "haber llamado la herramienta que los consulta de verdad."
                        ),
                    }
                )
                continue
            return reply

        tools_called.update(tc.function.name for tc in message.tool_calls)
        messages.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in message.tool_calls
                ],
            }
        )

        for tc in message.tool_calls:
            result = _run_tool_call(user_id, tc)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str),
                }
            )

    return "Uy, se me enredó un poco la cabeza organizando esto. ¿Me lo repites en un mensaje?"


def handle_user_message(user_id: int, text: str) -> str:
    unresolved = db.get_unresolved_awaiting_reply(user_id)
    if unresolved:
        db.mark_nudge_responded_by_id(unresolved["id"])

    # cualquier mensaje nuevo del usuario "reengancha" la conversación — si había un
    # awaiting_reply armado (con o sin nudge ya enviado por el scheduler), se apaga acá;
    # si al final de este turno el bot vuelve a dejar algo abierto, se rearma solo.
    db.clear_awaiting_reply_if_active(user_id)

    db.mark_last_nudge_responded(user_id)
    db.append_conversation_message(user_id, "user", text)

    user = db.get_user_by_id(user_id)
    history = db.get_recent_conversation(user_id)

    system_prompt = _system_prompt(user, user_id)
    if unresolved:
        system_prompt += (
            "\n\nAdemás: hace un rato le preguntaste algo y nunca contestó directo: "
            f"\"{unresolved['message_text']}\". Si viene al caso, retómalo de forma natural en tu "
            "respuesta (sin forzarlo si el usuario ya está hablando de otra cosa importante)."
        )

    messages = [{"role": "system", "content": system_prompt}]
    for entry in history:
        role = entry["role"] if entry["role"] in ("user", "assistant") else "user"
        messages.append({"role": role, "content": entry["content"]})

    try:
        reply = _chat_with_tools(user_id, messages, text)
    except Exception as e:
        if "429" in str(e):
            return "⚠️ El servidor está algo saturado. Reintenta en unos segundos."
        if "402" in str(e):
            return "⚠️ Revisa tu saldo en DeepSeek o el límite de tu API Key."
        return f"Error de conexión: {str(e)}"

    db.append_conversation_message(user_id, "assistant", reply)

    state = db.get_scheduler_state(user_id)
    if state and state.get("pending_nudge_kind"):
        pass  # una tool de este turno (confirm_schedule_slot/mark_task_in_progress/log_deferral) ya fijó el cursor
    elif _looks_like_awaiting_reply(reply):
        db.set_awaiting_reply(user_id)
    else:
        db.bump_cooldown_if_idle(user_id)

    return reply


def redactar_nudge(user_id: int, kind: str, context: dict) -> str:
    user = db.get_user_by_id(user_id)
    history = db.get_recent_conversation(user_id, limit=10)

    kind_instructions = {
        "outreach": "Es momento de preguntarle proactivamente por sus pendientes y ayudarlo a organizarse. "
                    "No lo agobies con todo a la vez si tiene varias tareas.",
        "checkin": "Ya llegó la hora en la que habían quedado en que empezaría la tarea. Pregúntale de forma "
                   "natural si ya inició.",
        "escalation": "Ya le preguntaste antes y no respondió o dijo que no ha avanzado. Insiste, con más "
                      "firmeza que la vez anterior pero sin sonar robótico ni repetir las mismas palabras.",
        "progress_check": "El usuario dijo que ya había empezado esta tarea, y ya pasó más o menos el tiempo "
                           "que él mismo estimó que le tomaría. Pregúntale cómo va — si ya casi termina, si "
                           "necesita más tiempo, o si se trabó con algo. No asumas que ya terminó.",
        "progress_escalation": "Ya le preguntaste cómo iba y no respondió, o dijo que sigue sin terminar. "
                                "Insiste con algo más de firmeza, recordándole si hay un deadline cerca, sin "
                                "sonar repetitivo.",
        "reminder_delivery": "Este mensaje ES el recordatorio en sí — entrégaselo de forma natural y directa "
                              "(lo que el usuario pidió que le recuerdes), y de paso pídele que confirme que "
                              "lo vio/entendió.",
        "reminder_ack": "Ya le mandaste este recordatorio antes y no ha confirmado explícitamente que lo vio. "
                        "Vuelve a insistir de forma natural, sin sonar como una alarma repetida.",
        "reminder_escalation": "Van varios intentos y sigue sin confirmar que vio el recordatorio. Insiste un "
                                "poco más, puede que simplemente no haya agarrado el celular.",
        "awaiting_reply": "Hace un momento le preguntaste algo (revisa el historial reciente) y no ha "
                           "respondido. Pregúntale de forma natural si sigue ahí y qué decide — no repitas "
                           "la pregunta palabra por palabra, y no suenes como una alarma.",
        "awaiting_reply_escalation": "Van varios intentos seguidos y sigue sin responder esa pregunta. Insiste "
                                     "una vez más de forma natural, sin sonar repetitivo — puede que "
                                     "simplemente no haya agarrado el celular.",
    }

    instruction = (
        f"{_system_prompt(user, user_id)}\n\n"
        f"No es el usuario quien te escribió: tú vas a iniciar la conversación ahora mismo.\n"
        f"{kind_instructions.get(kind, '')}\n\n"
        f"Contexto relevante: {json.dumps(context, default=str, ensure_ascii=False)}\n\n"
        "Responde solo con el mensaje que le vas a mandar, nada más."
    )

    messages = [{"role": "system", "content": instruction}]
    for entry in history:
        role = entry["role"] if entry["role"] in ("user", "assistant") else "user"
        messages.append({"role": role, "content": entry["content"]})
    messages.append({"role": "user", "content": "(inicia tú la conversación ahora)"})

    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=messages,
        temperature=0.8,
    )
    return response.choices[0].message.content or ""
