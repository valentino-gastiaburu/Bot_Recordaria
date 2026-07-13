import asyncio
from datetime import time as dt_time

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
import os
from dotenv import load_dotenv

import db
import scheduler
from ai_logic import handle_user_message

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")

ONBOARDING_TEXT = (
    "¡Hola! Soy tu asistente personal para que no se te pase nada. Esto es lo que puedo hacer:\n\n"
    "🔔 Recordatorios puntuales — \"recuérdame llamar al doctor a las 3pm\" y te aviso justo a esa hora.\n"
    "📚 Tareas con fecha límite — exámenes, entregas, trabajos. Te ayudo a planear cuándo hacerlas antes de que se venzan.\n"
    "🤝 Cosas sin fecha límite — lo que sea que tengas pendiente, negocio contigo cuándo te conviene arrancar.\n"
    "⏰ Te hago seguimiento — si quedamos en algo, te pregunto si ya empezaste, cómo vas, y no te dejo en visto.\n"
    "📅 Conozco tu horario — cuéntame tus clases o compromisos fijos y los tomo en cuenta para no chocarte planes.\n\n"
    "Solo escríbeme como si fuera un amigo, nada de comandos raros. ¿Qué tienes pendiente ahorita?"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    await asyncio.to_thread(db.get_or_create_user, chat.id, chat.username)
    await update.message.reply_text(ONBOARDING_TEXT)


async def recordatorio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user, _ = await asyncio.to_thread(db.get_or_create_user, chat.id, chat.username)
    tasks = await asyncio.to_thread(db.list_pending_tasks, user["id"])

    if not tasks:
        await update.message.reply_text("No tienes nada pendiente anotado ahorita.")
        return

    lineas = []
    for t in tasks:
        detalle = t["title"]
        if t.get("scheduled_start_at"):
            detalle += f" (agendado: {t['scheduled_start_at']})"
        elif t.get("deadline_at"):
            detalle += f" (para: {t['deadline_at']})"
        lineas.append(f"- {detalle}")

    await update.message.reply_text("Esto es lo que tienes pendiente:\n" + "\n".join(lineas))


async def responder_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text
    if not texto:
        await update.message.reply_text("mandaste algo que no es texto owo")
        return

    chat = update.effective_chat
    user, es_nuevo = await asyncio.to_thread(db.get_or_create_user, chat.id, chat.username)
    if es_nuevo:
        await update.message.reply_text(ONBOARDING_TEXT)

    async with scheduler.get_user_lock(user["id"]):
        respuesta = await asyncio.to_thread(handle_user_message, user["id"], texto)
    await update.message.reply_text(respuesta)


if __name__ == '__main__':
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("recordatorio", recordatorio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, responder_texto))

    app.job_queue.run_repeating(scheduler.scan_users, interval=300, first=30)
    app.job_queue.run_daily(scheduler.daily_outreach, time=dt_time(hour=13, minute=0))

    print("Bot en marcha...")
    app.run_polling()
