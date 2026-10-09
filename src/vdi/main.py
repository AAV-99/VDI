#!/usr/bin/env python
import os
import signal
import time
from pathlib import Path
from pydantic import BaseModel
from crewai.flow import Flow, listen, start

from .crews.vdicrew.vdicrew import VdiCrew

# Contexto del proyecto (tipo de sistema, alcance, requerimientos, costo) que leen todos los agentes
# La interfaz (front/app.py) define VDI_CONTEXT_FILE con la ruta del .txt que genera;
# sin esa variable (ejecucion manual con `uv run kickoff`) se usa contexto.txt.
# Reintentos de toda la corrida cuando Gemini responde "saturado" (503) o corta la conexion.
# Los reintentos propios de CrewAI son inmediatos; aqui se espera antes de volver a intentar.
ESPERAS_REINTENTO_S = [60, 120, 240]
MARCAS_ERROR_TEMPORAL = ("503", "UNAVAILABLE", "high demand", "Server disconnected", "overloaded")


def _es_error_temporal(error: Exception) -> bool:
    texto = f"{type(error).__name__}: {error}"
    return any(marca in texto for marca in MARCAS_ERROR_TEMPORAL)


CONTEXTO_FILE = Path(os.environ.get("VDI_CONTEXT_FILE") or Path(__file__).resolve().parent / "contexto.txt")


class VdiState(BaseModel):
    contexto: str = ""


class VdiFlow(Flow[VdiState]):

    @start()
    def load_context(self, crewai_trigger_payload: dict = None):
        print("Initializing VDI 2206 Design Cycle")

        if crewai_trigger_payload and crewai_trigger_payload.get("contexto"):
            self.state.contexto = crewai_trigger_payload["contexto"]
            print("Using context from trigger payload")
        else:
            self.state.contexto = CONTEXTO_FILE.read_text(encoding="utf-8").strip()
            print(f"Using context from {CONTEXTO_FILE}")
            print(f"[VDI] Contexto cargado: {len(self.state.contexto)} caracteres")

        if not self.state.contexto:
            raise ValueError(f"El contexto del proyecto esta vacio: {CONTEXTO_FILE}")

    @listen(load_context)
    def generate_design(self):
        print("Running multi-agent design review")
        print(f"[VDI] Modelo: {getattr(getattr(VdiCrew, 'llm', None), 'model', '(desconocido)')}")
        for intento in range(len(ESPERAS_REINTENTO_S) + 1):
            try:
                VdiCrew().crew().kickoff(inputs={"contexto": self.state.contexto})
                break
            except Exception as error:
                if not _es_error_temporal(error) or intento == len(ESPERAS_REINTENTO_S):
                    print(f"[VDI] Error definitivo en el intento {intento + 1}: {type(error).__name__}")
                    raise
                espera = ESPERAS_REINTENTO_S[intento]
                print(
                    f"[VDI] El modelo esta saturado (intento {intento + 1} de "
                    f"{len(ESPERAS_REINTENTO_S) + 1}). Reintento completo en {espera} s...",
                    flush=True,
                )
                time.sleep(espera)
        print("VDI 2206 Design review completed. Reports saved to output/")


def _salida_inmediata(signum, frame):
    # Ctrl+C solo interrumpe el hilo principal; el crew corre en otro hilo del Flow
    # y seguiria enviando peticiones al LLM. os._exit termina todo el proceso.
    print("\n[VDI] Interrumpido: cerrando proceso y conexiones al LLM.", flush=True)
    os._exit(130)


def kickoff():
    signal.signal(signal.SIGINT, _salida_inmediata)
    signal.signal(signal.SIGTERM, _salida_inmediata)
    vdi_flow = VdiFlow()
    vdi_flow.kickoff()


def plot():
    vdi_flow = VdiFlow()
    vdi_flow.plot()


def run_with_trigger():
    """
    Run the flow with trigger payload, e.g. {"contexto": "..."}.
    """
    import json
    import sys

    if len(sys.argv) < 2:
        raise Exception("No trigger payload provided. Please provide JSON payload as argument.")

    try:
        trigger_payload = json.loads(sys.argv[1])
    except json.JSONDecodeError:
        raise Exception("Invalid JSON payload provided as argument")

    vdi_flow = VdiFlow()

    try:
        result = vdi_flow.kickoff({"crewai_trigger_payload": trigger_payload})
        return result
    except Exception as e:
        raise Exception(f"An error occurred while running the flow with trigger: {e}")


if __name__ == "__main__":
    kickoff()
