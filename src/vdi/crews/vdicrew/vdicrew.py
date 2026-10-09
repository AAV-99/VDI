import os
import re
from datetime import datetime
from pathlib import Path

from crewai import Agent, Crew, Process, Task, LLM
from crewai.project import CrewBase, agent, crew, task
from google.genai import types

# --- GUARDRAIL DE COMPLETITUD ---
# Cada tarea debe cerrar su informe con MARCA_FIN (se pide en tasks.yaml).
# Si el informe llega cortado, CrewAI repite la tarea. Tras MAX_REINTENTOS_INCOMPLETO
# fallos se acepta la salida tal cual para no perder la corrida, y queda registrado.
MARCA_FIN = "<!-- FIN DEL INFORME -->"
MAX_REINTENTOS_INCOMPLETO = 2
REGISTRO_GUARDRAIL = Path("output/_guardrail.log")


def _registrar(tarea: str, estado: str, detalle: str, n_caracteres: int) -> None:
    try:
        REGISTRO_GUARDRAIL.parent.mkdir(parents=True, exist_ok=True)
        with open(REGISTRO_GUARDRAIL, "a", encoding="utf-8") as f:
            f.write(
                f"{datetime.now().isoformat(timespec='seconds')}\t{tarea}\t{estado}\t"
                f"{n_caracteres} caracteres\t{detalle}\n"
            )
    except OSError:
        pass  # el registro nunca debe tumbar la corrida


def guardrail_informe_completo(tarea: str):
    """Devuelve un guardrail que verifica que el informe de `tarea` no este cortado."""
    fallos = {"n": 0}

    def validar(output):
        texto = (getattr(output, "raw", None) or "").strip()
        problemas = []
        if MARCA_FIN not in texto[-300:]:
            problemas.append("no termina con la marca de fin")
        if len(re.findall(r"^\s*```", texto, flags=re.MULTILINE)) % 2 != 0:
            problemas.append("hay un bloque de codigo o diagrama Mermaid sin cerrar")

        if not problemas:
            _registrar(tarea, "COMPLETO", f"intentos fallidos previos: {fallos['n']}", len(texto))
            return (True, output)

        detalle = "; ".join(problemas)
        if fallos["n"] >= MAX_REINTENTOS_INCOMPLETO:
            _registrar(tarea, "ACEPTADO_INCOMPLETO", detalle, len(texto))
            return (True, output)

        fallos["n"] += 1
        _registrar(tarea, "REINTENTO", detalle, len(texto))
        return (
            False,
            f"El informe quedo incompleto ({detalle}). Reescribe el informe COMPLETO desde el "
            f"inicio, cierra todos los bloques de codigo y termina con la linea exacta: {MARCA_FIN}",
        )

    return validar

@CrewBase
class VdiCrew:
    """VDI 2206 Mechatronic Design Review Crew"""

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    # Gemini API: requiere GEMINI_API_KEY en el .env de la raiz del proyecto.
    # CrewAI no pasa timeout al cliente de Gemini; se configura en http_options.
    llm = LLM(
        # Se puede cambiar sin tocar el código con VDI_LLM_MODEL en el .env
        model=os.environ.get("VDI_LLM_MODEL") or "gemini-3.5-flash-lite",
        temperature=0.2,
        max_tokens=32000,                # Incluye tokens de razonamiento; con 4000 los informes se cortan        
        max_retries=4,                    # Límite de solicitudes por agente
        timeout=120,                      # Tiempo máximo de espera por solicitud
    )

    # --- AGENTES VDI 2206 ---
    # Reintentos propios de CrewAI ante errores del LLM (p. ej. 503): maximo 3 por tarea.
    def _agente(self, nombre: str) -> Agent:
        return Agent(
            config=self.agents_config[nombre],  # type: ignore[index]
            llm=self.llm,
            max_retry_limit=3,
            verbose=True,
        )

    @agent
    def team_lead(self) -> Agent:
        return self._agente("team_lead")

    @agent
    def mecanico(self) -> Agent:
        return self._agente("mecanico")

    @agent
    def electronico(self) -> Agent:
        return self._agente("electronico")

    @agent
    def software(self) -> Agent:
        return self._agente("software")

    @agent
    def mantenimiento(self) -> Agent:
        return self._agente("mantenimiento")

    @agent
    def cliente(self) -> Agent:
        return self._agente("cliente")

    # --- TAREAS (output_file definido en tasks.yaml) ---
    def _tarea(self, nombre: str) -> Task:
        return Task(
            config=self.tasks_config[nombre],  # type: ignore[index]
            guardrail=guardrail_informe_completo(nombre),
            guardrail_max_retries=MAX_REINTENTOS_INCOMPLETO,
        )

    @task
    def requirements_ingestion_task(self) -> Task:
        return self._tarea("requirements_ingestion_task")

    @task
    def mechanical_subsystem_design_task(self) -> Task:
        return self._tarea("mechanical_subsystem_design_task")

    @task
    def electronic_subsystem_design_task(self) -> Task:
        return self._tarea("electronic_subsystem_design_task")

    @task
    def software_subsystem_design_task(self) -> Task:
        return self._tarea("software_subsystem_design_task")

    @task
    def maintenance_rams_eval_task(self) -> Task:
        return self._tarea("maintenance_rams_eval_task")

    @task
    def client_acceptance_review_task(self) -> Task:
        return self._tarea("client_acceptance_review_task")

    @task
    def design_board_integration_task(self) -> Task:
        return self._tarea("design_board_integration_task")

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            max_rpm=4,                   # Por debajo del limite de 5 RPM del plan gratuito
            verbose=True,
        )