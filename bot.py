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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    await asyncio.to_thread(db.get_or_create_user, chat.id, chat.username)
    await update.message.reply_text(
        "Hola! Soy tu asistente para que no se te pase nada. Cuéntame qué tienes pendiente y vamos viendo cómo organizarlo."
    )


async def recordatorio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = await asyncio.to_thread(db.get_or_create_user, chat.id, chat.username)
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
    user = await asyncio.to_thread(db.get_or_create_user, chat.id, chat.username)
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
