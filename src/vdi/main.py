#!/usr/bin/env python
import os
import signal
from pathlib import Path
from pydantic import BaseModel
from crewai.flow import Flow, listen, start

from .corrida import RUN_DIR, registrar_fin, registrar_inicio
from .crews.vdicrew.vdicrew import (LLM_CONFIG, MAX_PALABRAS, MAX_REINTENTOS_INCOMPLETO, METODOLOGIA_ID,
                                   VdiCrew)


# Contexto del caso de estudio (tipo de sistema, alcance, requerimientos y costo) en un archivo de texto,
# independiente del codigo. Por defecto src/vdi/contexto.txt; VDI_CONTEXT_FILE permite usar otro caso.
CONTEXTO_FILE = Path(os.environ.get("VDI_CONTEXT_FILE") or Path(__file__).resolve().parent / "contexto.txt")

# Titulo de cada seccion del archivo -> variable que usan agents.yaml y tasks.yaml
SECCIONES = {
    "TIPO DE SISTEMA": "system_type",
    "ALCANCE DEL PROYECTO": "project_scope",
    "REQUERIMIENTOS PRIMARIOS": "primary_requirements",
    "COSTO OBJETIVO": "target_cost",
}


def leer_contexto(archivo: Path) -> dict[str, str]:
    """Lee las secciones '## TITULO' del archivo de contexto. Las lineas de cada seccion se unen con
    espacios y se quita la vineta '- ', asi el texto llega a los agentes en una sola linea.
    Las lineas que empiezan con un solo '#' son comentarios."""
    datos: dict[str, list[str]] = {}
    actual = None
    for linea in archivo.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea.startswith("## "):
            titulo = linea[3:].strip().upper()
            if titulo not in SECCIONES:
                raise ValueError(f"Seccion desconocida '{linea}' en {archivo}. Validas: {', '.join(SECCIONES)}")
            actual = SECCIONES[titulo]
            datos[actual] = []
        elif linea and not linea.startswith("#") and actual:
            datos[actual].append(linea[2:].strip() if linea.startswith("- ") else linea)
    faltan = [titulo for titulo, clave in SECCIONES.items() if not datos.get(clave)]
    if faltan:
        raise ValueError(f"Faltan secciones o estan vacias en {archivo}: {', '.join(faltan)}")
    contexto = {clave: " ".join(lineas) for clave, lineas in datos.items()}
    if any("{" in texto or "}" in texto for texto in contexto.values()):
        raise ValueError(f"El contexto no puede tener llaves: CrewAI las interpreta como variables ({archivo})")
    return contexto


class VdiState(BaseModel):
    system_type: str = ""
    project_scope: str = ""
    primary_requirements: str = ""
    target_cost: str = ""
    final_design: str = ""


class VdiFlow(Flow[VdiState]):

    @start()
    def plan_design(self, crewai_trigger_payload: dict = None):
        print("Initializing VDI 2206 Design Cycle")

        for clave, texto in leer_contexto(CONTEXTO_FILE).items():
            setattr(self.state, clave, texto)
        print(f"[VDI] Contexto del caso: {CONTEXTO_FILE}", flush=True)

        if crewai_trigger_payload:
            self.state.system_type = crewai_trigger_payload.get("system_type", self.state.system_type)
            self.state.project_scope = crewai_trigger_payload.get("project_scope", self.state.project_scope)
            self.state.primary_requirements = crewai_trigger_payload.get("primary_requirements", self.state.primary_requirements)
            self.state.target_cost = crewai_trigger_payload.get("target_cost", self.state.target_cost)
            print(f"Using trigger payload: {crewai_trigger_payload}")

        print(f"Target System: {self.state.system_type}")

    @listen(plan_design)
    def generate_design(self):
        print(f"Running multi-agent design review for: {self.state.system_type}")
        
        # Diccionario con los 3 placeholders genéricos
        inputs = {
            "system_type": self.state.system_type,
            "project_scope": self.state.project_scope,
            "primary_requirements": self.state.primary_requirements,
            "target_cost": self.state.target_cost,
            "max_palabras": MAX_PALABRAS,
        }
        registrar_inicio(
            LLM_CONFIG,
            inputs,
            {"max_palabras_por_informe": MAX_PALABRAS, "max_reintentos_guardrail": MAX_REINTENTOS_INCOMPLETO,
             "contexto_metodologico": METODOLOGIA_ID, "archivo_contexto": CONTEXTO_FILE.name},
        )
        
        result = (
            VdiCrew()
            .crew()
            .kickoff(inputs=inputs)
        )

        print("VDI 2206 Design review completed")
        self.state.final_design = result.raw

    @listen(generate_design)
    def save_design(self):
        print("Saving conceptual design dossier")
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        with open(RUN_DIR / "vdi2206_conceptual_design.md", "w", encoding="utf-8") as f:
            f.write(self.state.final_design)
        registrar_fin("completada")
        print(f"Dossier saved to {RUN_DIR / 'vdi2206_conceptual_design.md'}")


def _salida_inmediata(signum, frame):
    # Ctrl+C solo interrumpe el hilo principal; el crew corre en otro hilo del Flow
    # y seguiria enviando peticiones a Ollama. os._exit termina todo el proceso.
    print("\n[VDI] Interrumpido: cerrando proceso y conexiones a Ollama.", flush=True)
    registrar_fin("interrumpida")
    os._exit(130)


def kickoff():
    signal.signal(signal.SIGINT, _salida_inmediata)
    signal.signal(signal.SIGTERM, _salida_inmediata)
    print(f"[VDI] Carpeta de la corrida: {RUN_DIR}", flush=True)
    vdi_flow = VdiFlow()
    try:
        vdi_flow.kickoff()
    except Exception as e:
        registrar_fin(f"error: {type(e).__name__}: {e}")
        raise


def plot():
    vdi_flow = VdiFlow()
    vdi_flow.plot()


def run_with_trigger():
    """
    Run the flow with trigger payload.
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