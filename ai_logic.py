from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("IA_KEY")

client = OpenAI(
    api_key=API_KEY, 
    base_url="https://openrouter.ai/api/v1"
)

def respuesta_llm(prompt: str, instruccion_interna: str, temperatura: float = 0.9) -> str:
    """
    Consulta al modelo MythoMax 13B. 
    Subimos un poco la temperatura a 0.9 para aprovechar su creatividad.
    """
    try:
        response = client.chat.completions.create(
            model="gryphe/mythomax-l2-13b",
            messages=[
                {"role": "system", "content": instruccion_interna},
                {"role": "user", "content": prompt},
            ],
            temperature=temperatura,
            stream=False
        )
        return response.choices[0].message.content
    except Exception as e:
        if "429" in str(e):
            return "⚠️ El servidor está algo saturado. Reintenta en unos segundos."
        if "402" in str(e):
            return "⚠️ Revisa tu saldo en OpenRouter o el límite de tu API Key."
        return f"Error de conexión: {str(e)}"