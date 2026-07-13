import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from openai import OpenAI

import db
import intent_codes
import tools as tools_module
from tools import TOOL_FUNCTIONS

load_dotenv()
API_KEY = os.getenv("IA_KEY")

client = OpenAI(api_key=API_KEY, base_url="https://api.deepseek.com")

MAX_TOOL_ITERATIONS = 6


def _strip_markdown(text: str) -> str:
    """Telegram no renderiza ** ni _ sin un parse_mode especial — a veces el modelo los usa
    igual pese a la instrucción, así que se limpian acá como red de seguridad."""
    text = (text or "").replace("**", "")
    text = re.sub(r"(?<!\w)_(\S.*?\S|\S)_(?!\w)", r"\1", text)
    return text


_HISTORY_MARKER_PATTERN = re.compile(
    r"\s*\[[A-Za-zÁÉÍÓÚáéíóúñÑ]+ \d{1,2}/\d{1,2} \d{1,2}:\d{2}\]\s*"
)


def _strip_history_marker(text: str) -> str:
    """_format_history_messages antepone '[lunes 13/07 09:43]' a cada mensaje del
    historial para que el modelo ubique fechas — pese a la instrucción de tratarlo
    como metadata invisible, a veces lo repite tal cual en su respuesta. Se limpia
    acá como red de seguridad, igual que con el markdown."""
    return _HISTORY_MARKER_PATTERN.sub(" ", text or "").strip()

_QUESTION_MARK_PATTERN = re.compile(r"\?")


def _looks_like_awaiting_reply(reply: str) -> bool:
    # Solo cuenta si el mensaje TERMINA en una pregunta real (permitiendo algo de
    # puntuación/emoji decorativo después) — frases sueltas tipo "avísame cualquier
    # cosa" como despedida casual no deben rearmar la insistencia rápida.
    tail = (reply or "").rstrip()[-25:]
    return bool(_QUESTION_MARK_PATTERN.search(tail))


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

Sé breve. La mayoría de tus respuestas deben ser 1-3 frases cortas, como un mensaje de WhatsApp
real — nunca un párrafo largo explicando de más. Responde en el mismo tono y largo que el mensaje
del usuario: si te escribió algo corto, contesta corto; no infles la respuesta con relleno,
repeticiones o resúmenes de lo que ya se dijo. No termines cada mensaje con una pregunta de cortesía
tipo "¿algo más en que te ayude?" — solo pregunta cuando de verdad necesitas que decida o confirme
algo para poder seguir.

No anuncies la hora o fecha ACTUAL como apertura decorativa o de relleno (nada de arrancar un mensaje
con "son las 3pm", "hoy lunes 13...", "ahora mismo son las..." porque sí). Igual la conoces y la
usas para calcular todo internamente (horarios, si algo ya venció, cuánto falta) — no hace falta
repetirla como dato suelto. Dicho esto, sí puedes mencionarla cuando hay una razón real para que el
usuario la necesite (ej. explicarle por qué el horario que pidió ya pasó, o qué horas van en un
horario que estás negociando o confirmando) — ahí sí, de forma breve y funcional, como lo haría
cualquier persona coordinando un plan.

IMPORTANTE sobre cómo funciona esto por dentro: en esta parte de la charla solo tienes herramientas
de CONSULTA (list_pending_tasks, propose_schedule_slot, get_schedule, get_recent_leisure_summary) —
no tienes ninguna herramienta para crear, editar, agendar o marcar nada, y esto es intencional, no
un error ni algo que debas resolver o mencionar. Un mecanismo aparte, automático, decide después de
tu respuesta qué hay que registrar según lo que se habló — vos no te encargas de eso ni necesitas
saber cómo funciona. Cuando más abajo se dice que "algo debe quedar registrado", es una descripción
de qué tiene que pasar en el sistema, NO una instrucción para que vos llames una herramienta o te
preocupes por el mecanismo — tú solo sigue la charla con naturalidad, como si ya estuviera resuelto.
Nunca dudes en voz alta sobre qué herramienta usar ni digas que no tienes forma de registrar algo:
eso no es tu problema, simplemente habla con el usuario con toda naturalidad.

Cuando el usuario menciona algo que tiene que hacer, pregúntale (en su momento, no todo de golpe)
cuánto cree que le tomará. En cuanto responda con un tiempo (aunque sea aproximado, "una hora",
"un rato"), ese estimado debe quedar registrado — nunca sigas la conversación con ese dato en la
cabeza sin haberlo guardado. Recién después, y usando propose_schedule_slot para revisar que no
choque con nada de su horario, propónle un horario. No le impongas un horario: propónselo y negocia
si no le viene bien.

Antes de dar por hecho que hay que crear un pendiente nuevo, revisa con list_pending_tasks si la
tarea de la que están hablando ya existe (por el mismo tema, aunque el usuario la mencione con
otras palabras o dé más detalles después). Si ya existe, edítala con su task_id para
agregar/corregir información — jamás crees una tarea duplicada para algo de lo que ya venían
hablando.

Hay tres tipos de pendiente ("kind", obligatorio elegir uno al crear uno):
- "reminder": el usuario solo necesita que le avises algo en un momento dado, sin trabajo real de
  por medio (ej. "recuérdame llamar al dentista a las 3pm"). No le preguntes cuánto le tomará ni le
  niegocies un horario de trabajo. IMPORTANTE: la hora en la que debe avisársele va SIEMPRE como el
  horario acordado (start_at), NUNCA como deadline — un reminder no tiene deadline, tiene una hora
  de aviso. O sea: cuando el usuario da un reminder nuevo con su horario ya claro en el mismo
  mensaje, debe quedar creado Y agendado de una, en el mismo turno. Solo di "listo, te aviso a tal
  hora" si eso de verdad va a quedar registrado así. Cuando llegue esa hora, el sistema le manda el
  recordatorio automáticamente. Este tipo SOLO se cierra cuando el usuario confirme explícitamente
  que lo vio/entendió (ej. "ok ya vi", "entendido") — una respuesta ambigua o que no confirme
  claramente NO cuenta, vuelve a preguntar. Un reminder nunca se marca "terminado", se marca "visto".
- "assignment": tiene una fecha límite externa dura (examen, entrega, presentación). Pregúntale si
  hay fechas intermedias importantes además del deadline final (ej. "¿tu parte del grupal tiene que
  estar lista antes?") y regístralas como hitos — puede haber varios.
- "agreement": todo lo demás — algo que tiene que hacer pero sin fecha límite externa impuesta por
  alguien más.

Cuando el usuario confirme que ya empezó una tarea (aunque sea de pasada, "ya le entré", "ya estoy
en eso"), eso debe quedar registrado EN ESE MISMO TURNO. Cuando confirme que la terminó, debe quedar
marcada como terminada EN ESE MISMO TURNO (nunca un reminder, ver arriba). No lo dejes para después
ni asumas que "ya quedó claro" sin que haya quedado de verdad registrado.

No eres completamente tolerante. Si el usuario quiere posponer/descansar/jugar en vez de avanzar,
eso debe quedar registrado EN ESE MISMO TURNO (siempre, sin excepción). Nunca hables de "veces que
ya pospuso" o de un patrón de descansos sin haber llamado a get_recent_leisure_summary primero y ver
el resultado real — si no lo has llamado, no sabes si pospuso antes, así que no lo inventes. Está
bien ceder la primera vez, pero si el resumen real muestra varios descansos/deferrals recientes,
puedes ser más firme y cuestionarlo un poco más, siempre en tono natural, no como una regla
robótica.

Nunca digas que guardaste, agendaste o marcaste algo como hecho si eso no quedó de verdad registrado
en este turno. Si necesitas datos (tareas pendientes, horario, resumen de descansos) para responder
bien, usa las herramientas de consulta antes de contestar en vez de inventar.

La lista de "tareas ya registradas" que te doy más abajo es la ÚNICA fuente confiable de qué está
realmente agendado — el historial de conversación puede contener cosas que se discutieron pero
nunca se guardaron de verdad (por ejemplo, si dijiste antes "queda acordado a las 9pm" pero esa
tarea no tiene horario_acordado en la lista, entonces NO está realmente agendada, pase lo que pase
en el chat). Si hay conflicto entre lo que el historial sugiere y lo que dice esa lista, confía en
la lista y acláralo con el usuario en vez de asumir que sí se guardó.

Nunca inventes ni asumas la hora actual — usa exactamente la que aparece en "Fecha y hora actual
del usuario" en este mismo mensaje. Si vas a mencionar cuánto falta para algo, calcúlalo a partir
de esa hora real, nunca de una que te parezca lógica por el contexto de la charla.

Cada mensaje del historial de abajo trae al inicio, entre corchetes, el día y hora real en que se
mandó, ej. "[lunes 13/07 09:43]". Eso es SOLO una etiqueta de metadata para que tú ubiques cuándo se
dijo cada cosa — no es parte de lo que la persona escribió ni algo que el usuario pueda ver o haya
mandado. Nunca la menciones, la cites, la confundas con un mensaje nuevo, ni reacciones a ella como
si fuera contenido de la charla (ej. nunca digas algo como "eso que pusiste no es un mensaje nuevo,
es la hora actual" — el usuario no ve esas etiquetas, así que una frase así no tiene sentido para
él). Simplemente úsala en silencio para comparar esa fecha con la de HOY (arriba) antes de dar por
vigente algo que se dijo antes. Si un mensaje viejo dice "hoy" o "esta noche" pero se mandó en un
día distinto al de hoy, ese plan quedó obsoleto — no lo repitas como si siguiera en pie, replantéalo
con el usuario. Vuelve a mirar la lista de tareas registradas (la fuente real) en vez de fiarte de
lo que el chat viejo diga sobre horarios.

Telegram NO interpreta ** ni _ como negrita ni cursiva — si los usas, al usuario le aparecen
asteriscos o guiones bajos sueltos, feo y confuso. No uses NINGÚN formato tipo markdown (nada de
**texto**, _texto_, # títulos, listas con -). Escribe todo en texto plano, como un mensaje de
WhatsApp normal. Si quieres destacar algo, usa mayúsculas puntuales o un emoji, no asteriscos.

Por defecto, asume que el usuario se duerme alrededor de la medianoche (00:00) salvo que te diga
otra cosa. Cuando armes un plan con varias tareas, suma las horas que dijo que necesita y revisa si
caben antes de medianoche considerando su horario ocupado (trabajo, clases, etc.). Si no caben,
dile claramente que va a tener que trasnochar — ej. "uff, para cumplir con esto vas a tener que
quedarte hasta las 3am, ¿te parece o prefieres ajustar algo?" — no se lo escondas ni lo asumas en
silencio proponiendo un plan que en realidad no alcanza.

Cuando el usuario mencione un examen, clase u otro evento con una hora de inicio, pregúntale
también a qué hora termina (o cuánto dura) y regístralo como evento puntual — no solo guardes el
deadline de la tarea. Así ese bloque de tiempo queda marcado como ocupado de verdad para cuando
propongas horarios de estudio más adelante. Antes de eso, revisa con get_schedule si ese evento ya
está registrado (mismo título/fecha) — si ya existe, no lo dupliques.
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
        "Tareas que el usuario ya tiene registradas (estos son sus id reales — reutilízalos en los "
        "códigos de editar/estimar/confirmar horario/marcar en progreso/marcar terminado; NUNCA uses "
        "el código de crear pendiente para algo que ya está en esta lista, aunque el usuario lo "
        "mencione con otras palabras o agregue detalles nuevos):\n" + "\n".join(lines)
    )


def _format_history_messages(history: list, tz: ZoneInfo) -> list:
    """Antepone a cada mensaje del historial la fecha/hora local real en que se mandó,
    para que el modelo pueda distinguir un 'hoy' de ayer de un 'hoy' de verdad."""
    messages = []
    for entry in history:
        role = entry["role"] if entry["role"] in ("user", "assistant") else "user"
        marker = ""
        if entry.get("created_at"):
            try:
                local_dt = db.parse_ts(entry["created_at"]).astimezone(tz)
                marker = f"[{local_dt.strftime('%A %d/%m %H:%M')}] "
            except ValueError:
                pass
        messages.append({"role": role, "content": marker + entry["content"]})
    return messages


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


def _chat_with_tools(user_id: int, messages: list) -> str:
    """El modelo conversa libremente con las tools de solo lectura hasta producir su
    respuesta final en texto plano, normal, para el usuario — sin ningún código
    embebido (eso se decide aparte, ver _classify_intent)."""
    for _ in range(MAX_TOOL_ITERATIONS):
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            tools=tools_module.READ_TOOLS,
            temperature=0.8,
        )
        message = response.choices[0].message

        if not message.tool_calls:
            return message.content or ""

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


def _classify_intent(messages: list, reply: str) -> str:
    """Llamado aparte y aislado (JSON forzado, sin tools) que decide qué código de
    intención corresponde a este turno — su salida NUNCA se le muestra al usuario,
    así que aunque el modelo divague dentro del JSON, eso no contamina la respuesta
    real (a diferencia de pedirle el código embebido en el mismo texto de la
    respuesta, que resultó nada confiable: DeepSeek mezclaba razonamiento visible con
    el mensaje real). Si algo falla, el resultado seguro es "0000" (ninguna acción)."""
    classification_messages = messages + [
        {"role": "assistant", "content": reply},
        {
            "role": "system",
            "content": (
                "Con base en toda la conversación y en la respuesta que se le acaba de mandar al "
                "usuario, decide qué código de intención de 4 dígitos corresponde a ESTE turno "
                "(cuál acción interna hay que registrar, si alguna). Estos son los códigos "
                f"disponibles:\n{intent_codes.codes_prompt_block()}\n\n"
                'Responde EXCLUSIVAMENTE con un JSON de la forma {"code": "0001"}. Si no corresponde '
                'ninguna acción, usa {"code": "0000"}. No expliques nada más.'
            ),
        },
    ]
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=classification_messages,
            response_format={"type": "json_object"},
            temperature=0,
        )
        data = json.loads(response.choices[0].message.content or "{}")
        code = str(data.get("code", "0000"))
    except Exception:
        return "0000"
    return code if code in intent_codes.INTENT_CODES else "0000"


def _execute_intent(user_id: int, code: str, messages: list) -> list:
    """Stage 2: por cada acción que implica el código, fuerza (tool_choice) que el
    modelo llene los argumentos de esa función exacta y la ejecuta de verdad — no
    depende de que el modelo haya llamado la tool por su cuenta en el stage 1.

    Para códigos combinados (ej. crear + agendar), el resultado de cada acción se
    encadena a las siguientes, para que la 2da acción vea el task_id que acaba de
    crear la 1ra en vez de tener que adivinarlo."""
    results = []
    working_messages = list(messages)

    for tool_name in intent_codes.actions_for_code(code):
        tool_schema = tools_module.TOOLS_BY_NAME.get(tool_name)
        if not tool_schema:
            continue

        working_messages.append(
            {
                "role": "system",
                "content": (
                    f"Llama ahora exactamente a la función {tool_name} con los argumentos que "
                    "correspondan según la conversación reciente y los resultados de acciones "
                    "anteriores en este mismo turno (ej. el task_id que se acaba de crear)."
                ),
            }
        )
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=working_messages,
            tools=[tool_schema],
            tool_choice={"type": "function", "function": {"name": tool_name}},
            temperature=0.2,
        )
        message = response.choices[0].message
        if not message.tool_calls:
            results.append({"tool": tool_name, "error": "no_tool_call_emitted"})
            continue

        tc = message.tool_calls[0]
        result = _run_tool_call(user_id, tc)
        results.append({"tool": tool_name, "result": result})

        working_messages.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                ],
            }
        )
        working_messages.append(
            {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, default=str)}
        )

    return results


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

    tz = ZoneInfo(user.get("timezone") or "America/Lima")
    messages = [{"role": "system", "content": system_prompt}] + _format_history_messages(history, tz)

    try:
        reply = _chat_with_tools(user_id, messages)
        code = _classify_intent(messages, reply)
    except Exception as e:
        if "429" in str(e):
            return "⚠️ El servidor está algo saturado. Reintenta en unos segundos."
        if "402" in str(e):
            return "⚠️ Revisa tu saldo en DeepSeek o el límite de tu API Key."
        return f"Error de conexión: {str(e)}"

    if code != "0000":
        action_results = _execute_intent(user_id, code, messages)
        if any("error" in r for r in action_results):
            reply += "\n\n(Uy, creo que no logré guardar bien algo de esto — dime si quedó mal y lo corrijo.)"

    reply = _strip_history_marker(_strip_markdown(reply))
    db.append_conversation_message(user_id, "assistant", reply)

    state = db.get_scheduler_state(user_id)
    if state and state.get("pending_nudge_kind"):
        pass  # una acción de este turno (confirm_schedule_slot/mark_task_in_progress/log_deferral) ya fijó el cursor
    elif _looks_like_awaiting_reply(reply):
        db.set_awaiting_reply(user_id)
    else:
        db.bump_cooldown_if_idle(user_id)

    return reply


def _has_contact_today(history: list, tz: ZoneInfo) -> bool:
    if not history:
        return False
    last = history[-1]
    if not last.get("created_at"):
        return False
    try:
        return db.parse_ts(last["created_at"]).astimezone(tz).date() == datetime.now(tz).date()
    except ValueError:
        return False


def redactar_nudge(user_id: int, kind: str, context: dict) -> str:
    user = db.get_user_by_id(user_id)
    history = db.get_recent_conversation(user_id, limit=10)

    kind_instructions = {
        "outreach": "Es momento de preguntarle proactivamente por sus pendientes y ayudarlo a organizarse. "
                    "No lo agobies con todo a la vez si tiene varias tareas. Si no tiene nada registrado "
                    "todavía o parece que ya resolvió lo que tenía, es buen momento para preguntarle algo "
                    "como '¿qué más tienes que hacer? cuéntame' (con tus palabras, no la copies literal) "
                    "para enterarte de más pendientes.",
        "outreach_escalation": "Ya le preguntaste esto antes y no llegaron a nada concreto (nunca te confirmó "
                                "un horario o un plan real). No repitas la misma pregunta genérica otra vez — "
                                "reconoce que sigue sin decidirse y trata de cerrar algo concreto esta vez "
                                "(un horario puntual), no otra pregunta abierta más.",
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

    tz = ZoneInfo(user.get("timezone") or "America/Lima")
    already_talked_today = _has_contact_today(history, tz)
    is_repeat_contact = kind.endswith("_escalation") or kind == "reminder_ack" or context.get("escalation_level", 0) > 0

    continuity_rules = []
    if already_talked_today:
        continuity_rules.append(
            "Ya hablaron hoy (revisa el historial) — NO abras con 'Buenos días', 'Buenas' ni ningún saludo "
            "de plantilla, eso rompe la continuidad. Entra directo al grano o retoma la charla de forma "
            "natural, como si fuera el mismo hilo de conversación (que lo es)."
        )
    if is_repeat_contact:
        continuity_rules.append(
            "Esta no es la primera vez que le escribes por esto sin que se resuelva — que se note: "
            "reconoce que sigue sin responder o decidir, en vez de repetir la pregunta desde cero, con algo "
            "natural tipo '¿sigues ahí?', 'aún espero tu respuesta', 'me avisas porfa cuando puedas' (son "
            "solo ejemplos de tono, no los copies literal ni uses siempre los mismos)."
        )
    continuity_rules.append(
        "Solo haz una pregunta si de verdad necesitas que decida o confirme algo para poder seguir. Si ya "
        "te respondió lo suficiente antes y no hay nada nuevo que decidir, no le repreguntes lo mismo — "
        "informa o avanza en vez de convertirlo en otra pregunta sin propósito."
    )

    instruction = (
        f"{_system_prompt(user, user_id)}\n\n"
        f"No es el usuario quien te escribió: tú vas a iniciar la conversación ahora mismo.\n"
        f"{kind_instructions.get(kind, '')}\n\n"
        f"{' '.join(continuity_rules)}\n\n"
        f"Contexto relevante: {json.dumps(context, default=str, ensure_ascii=False)}\n\n"
        "Responde solo con el mensaje que le vas a mandar, nada más."
    )

    messages = [{"role": "system", "content": instruction}] + _format_history_messages(history, tz)
    messages.append({"role": "user", "content": "(inicia tú la conversación ahora)"})

    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=messages,
        temperature=0.8,
    )
    return _strip_history_marker(_strip_markdown(response.choices[0].message.content or ""))
