#!/usr/bin/env python
import os
import signal
from pathlib import Path
from pydantic import BaseModel
from crewai.flow import Flow, listen, start

from . import ciclo
from .corrida import RUN_DIR, registrar_evento, registrar_fin, registrar_inicio
from .crews.vdicrew import herramientas
from .crews.vdicrew.vdicrew import _registrar as _registrar_guardrail
from .crews.vdicrew.vdicrew import (AGENTES_CON_WEB, BUSQUEDA_WEB_ACTIVA, HERRAMIENTAS_ACTIVAS, LLM_CONFIG, MAX_ITERACIONES, MAX_PALABRAS,
                                   MAX_REINTENTOS_INCOMPLETO, METODOLOGIA_ID, VdiCrew)


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
        parametros = {"max_palabras_por_informe": MAX_PALABRAS, "max_reintentos_guardrail": MAX_REINTENTOS_INCOMPLETO,
                      "contexto_metodologico": METODOLOGIA_ID, "archivo_contexto": CONTEXTO_FILE.name,
                      "herramientas": {"activas": HERRAMIENTAS_ACTIVAS}}
        if HERRAMIENTAS_ACTIVAS:
            # El indice se abre (o se construye) antes de empezar: si falla, la corrida se detiene aqui
            # en vez de seguir con una herramienta que solo devuelve errores.
            herramientas.buscar("allowable payload")
            parametros["herramientas"].update({
                "indice_rag": herramientas.huella_documentos(),
                "max_consultas_rag_por_tarea": herramientas.MAX_CONSULTAS_RAG,
                "max_usos_calculadora_por_tarea": herramientas.MAX_USOS_CALCULADORA,
                "max_usos_matriz_por_tarea": herramientas.MAX_USOS_MATRIZ,
                "busqueda_web": BUSQUEDA_WEB_ACTIVA,
                "max_iteraciones_agente": MAX_ITERACIONES,
            })
        if BUSQUEDA_WEB_ACTIVA:
            # Igual que con el indice: si la API de busqueda no responde, la corrida se detiene aqui.
            herramientas.buscar_web("vacuum gripper end effector")
            parametros["herramientas"].update({
                "agentes_con_busqueda_web": sorted(AGENTES_CON_WEB),
                "max_busquedas_web_por_tarea": herramientas.MAX_BUSQUEDAS_WEB,
                "resultados_por_busqueda": herramientas.RESULTADOS_WEB,
            })
        parametros["max_reanudaciones"] = MAX_REANUDACIONES
        parametros["ciclo_correccion"] = {"max_iteraciones": ciclo.MAX_CICLOS, "max_fallos_por_ecr": ciclo.MAX_FALLOS_ECR}
        registrar_inicio(LLM_CONFIG, inputs, parametros)

        result = _ejecutar_con_reanudacion(inputs)

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


# Si una tarea falla (por ejemplo, el modelo responde vacio varias veces seguidas y CrewAI aborta), la corrida
# se reanuda: ciclo.ejecutar() salta los pasos que ya tienen informe y continua desde el primero que falta.
# Cada reanudacion queda en corrida.json (clave "reanudaciones") y en _guardrail.log.
MAX_REANUDACIONES = 2


def _ejecutar_con_reanudacion(inputs: dict):
    for intento in range(MAX_REANUDACIONES + 1):
        try:
            return ciclo.ejecutar(inputs)
        except Exception as error:
            if intento == MAX_REANUDACIONES:
                raise
            tarea = ciclo.PASO_ACTUAL["tarea"]
            motivo = f"{type(error).__name__}: {' '.join(str(error).split())[:300]}"
            print(f"[VDI] La tarea {tarea} fallo ({motivo}). Reanudacion {intento + 1} de {MAX_REANUDACIONES}.", flush=True)
            registrar_evento("reanudaciones", {"intento": intento + 1, "tarea": tarea, "motivo": motivo})
            _registrar_guardrail(tarea, "REANUDACION", f"intento {intento + 1}: {motivo}", "")


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