"""LLM de Gemini con cadena de modelos de respaldo ante el error 429 (cuota agotada).

El plan gratuito de Gemini tiene una cuota diaria POR MODELO. Cada corrida empieza con el mejor modelo de
MODELOS y, si Gemini responde 429 por cuota diaria, la MISMA llamada se repite con el siguiente modelo de la
lista: el crew sigue desde la tarea en que iba, sin repetir las anteriores. Los modelos agotados se saltan
durante el resto de la corrida.

Si el 429 es por el limite POR MINUTO, se espera y se reintenta con el mismo modelo: no hace falta cambiar.
"""
import os
import re
import time

from crewai.llms.providers.gemini.completion import GeminiCompletion

# Del mejor al de menor capacidad. Se puede cambiar sin tocar el codigo con VDI_LLM_MODELS en el .env
# (separados por comas). Solo modelos con version en el nombre: CrewAI deduce de ella si el modelo
# admite herramientas y razonamiento (con "gemini-flash-latest" los desactiva).
MODELOS_POR_DEFECTO = (
    "gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite,"
    "gemini-3-flash-preview,gemini-3.1-flash-lite,gemini-2.5-flash,gemini-2.5-flash-lite"
)
MODELOS = [m.strip() for m in (os.environ.get("VDI_LLM_MODELS") or MODELOS_POR_DEFECTO).split(",") if m.strip()]

ESPERA_POR_MINUTO_S = 60  # si Gemini no indica cuanto esperar
MAX_ESPERAS_POR_MINUTO = 3  # despues se trata como cuota agotada y se cambia de modelo


class CuotaAgotadaError(RuntimeError):
    """Todos los modelos de la cadena devolvieron 429. No es un error temporal: la corrida se detiene."""


def _es_429(error: Exception) -> bool:
    texto = str(error)
    return getattr(error, "code", None) == 429 or "429" in texto or "RESOURCE_EXHAUSTED" in texto


def _es_limite_por_minuto(error: Exception) -> bool:
    texto = str(error)
    return "PerMinute" in texto and "PerDay" not in texto


def _segundos_de_espera(error: Exception) -> float:
    encontrado = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s", str(error))
    return float(encontrado.group(1)) + 1 if encontrado else ESPERA_POR_MINUTO_S


# Modelos que ya devolvieron 429 por cuota en esta corrida. Es comun a todos los agentes (aunque cada uno
# tenga su propia copia del LLM) y se reinicia en cada corrida, que es un proceso nuevo.
_AGOTADOS: set[str] = set()


class GeminiConRespaldo(GeminiCompletion):
    """GeminiCompletion de CrewAI que cambia de modelo cuando el actual agota su cuota."""

    def call(self, *args, **kwargs):
        esperas_por_minuto = 0
        while True:
            if self.model in _AGOTADOS:
                self._cambiar_de_modelo()
            try:
                return super().call(*args, **kwargs)
            except Exception as error:
                if not _es_429(error):
                    raise
                if _es_limite_por_minuto(error) and esperas_por_minuto < MAX_ESPERAS_POR_MINUTO:
                    esperas_por_minuto += 1
                    espera = _segundos_de_espera(error)
                    print(f"[VDI] Limite por minuto de {self.model} (429). Reintento en {espera:.0f} s...", flush=True)
                    time.sleep(espera)
                    continue
                _AGOTADOS.add(self.model)
                esperas_por_minuto = 0

    def _cambiar_de_modelo(self) -> None:
        agotado = self.model
        pendientes = [m for m in MODELOS if m not in _AGOTADOS]
        if not pendientes:
            raise CuotaAgotadaError(
                f"Cuota de Gemini agotada en todos los modelos ({', '.join(MODELOS)}). "
                "Intenta de nuevo cuando se renueve la cuota diaria."
            )
        self.model = pendientes[0]
        print(f"[VDI] Cuota agotada en {agotado} (429). Se continua con {self.model}.", flush=True)


def crear_llm(**kwargs) -> GeminiConRespaldo:
    """LLM compartido por todos los agentes. Empieza siempre con el mejor modelo de la cadena."""
    return GeminiConRespaldo(model=MODELOS[0], provider="gemini", **kwargs)
