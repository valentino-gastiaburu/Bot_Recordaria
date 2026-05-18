from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes,MessageHandler,filters
from ai_logic import respuesta_llm
import os
from dotenv import load_dotenv

load_dotenv()
# Reemplaza con el token que te dio BotFather
TOKEN = os.getenv("TELEGRAM_TOKEN")

# Función que se ejecuta al escribir /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("¡Hola, Diego! Tu bot está activo y listo para trabajar.")

async def recordatorio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Me falta definir la función de recordatorio :D")

async def responder_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text
    if texto:
        print(f"Recibido: {texto}")
        instruccion= 'Eres alguien muy chill '
        await update.message.reply_text(respuesta_llm(texto,instruccion,0.5))
    else:
        await update.message.reply_text(f"mandaste algo que no es texto owo")

if __name__ == '__main__':
    # Creamos la aplicación con el token
    app = Application.builder().token(TOKEN).build()

    # Registramos el comando /start
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("recordatorio", recordatorio))

    # Recepcion de cualquier mensaje y respuesta
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, responder_texto))

    # El bot se queda escuchando mensajes (Polling)
    print("Bot en marcha...")
    app.run_polling()

