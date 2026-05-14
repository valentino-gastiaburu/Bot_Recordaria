from openai import OpenAI
import os
from dotenv import load_dotenv
# Configuración del cliente de DeepSeek
# Reemplaza con tu API Key real
load_dotenv()
API_KEY = os.getenv("DEEPSEEK_KEY")

client = OpenAI(
    api_key=API_KEY, 
    base_url="https://api.deepseek.com"
)

def respuesta_llm(prompt: str, instruccion_interna: str, temperatura: float = 0.7) -> str:
    """
    Consulta a DeepSeek y devuelve la respuesta como un string.
    """
    try:
        response = client.chat.completions.create(
            model="deepseek-chat", # O "deepseek-reasoner" si tienes acceso
            messages=[
                {"role": "system", "content": instruccion_interna},
                {"role": "user", "content": prompt},
            ],
            temperature=temperatura,
            stream=False
        )
        # Extraemos el contenido del mensaje de respuesta
        return response.choices[0].message.content
    except Exception as e:
        return f"Error de conexión con la IA: {str(e)}"