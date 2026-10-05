#!/usr/bin/env python
import os
import signal
from pathlib import Path
from pydantic import BaseModel
from crewai.flow import Flow, listen, start

from .crews.vdicrew.vdicrew import VdiCrew

# Contexto del proyecto (tipo de sistema, alcance, requerimientos, costo) que leen todos los agentes
CONTEXTO_FILE = Path(__file__).resolve().parent / "contexto.txt"


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

        if not self.state.contexto:
            raise ValueError(f"El contexto del proyecto esta vacio: {CONTEXTO_FILE}")

    @listen(load_context)
    def generate_design(self):
        print("Running multi-agent design review")
        VdiCrew().crew().kickoff(inputs={"contexto": self.state.contexto})
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
