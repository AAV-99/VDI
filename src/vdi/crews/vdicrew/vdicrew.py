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
    # CrewAI no pasa max_retries/timeout al cliente de Gemini; se configuran en http_options.
    # Reintentos con espera exponencial (5, 10, 20, 40, 80, 120... s) ante 429/5xx,
    # p. ej. "503 This model is currently experiencing high demand".
    llm = LLM(
        model="gemini/gemini-3.5-flash", # O la versión habilitada en tu API
        temperature=0.2,
        max_tokens=32000,                # Incluye tokens de razonamiento; con 4000 los informes se cortan
        client_params={
            "http_options": types.HttpOptions(
                timeout=600_000,         # Tiempo máximo por solicitud (ms)
                retry_options=types.HttpRetryOptions(
                    attempts=8,
                    initial_delay=5.0,
                    max_delay=120.0,
                    http_status_codes=[408, 429, 500, 502, 503, 504],
                ),
            )
        },
    )

    # --- AGENTES VDI 2206 ---
    @agent
    def team_lead(self) -> Agent:
        return Agent(config=self.agents_config["team_lead"], llm=self.llm, verbose=True)

    @agent
    def mecanico(self) -> Agent:
        return Agent(config=self.agents_config["mecanico"], llm=self.llm, verbose=True)

    @agent
    def electronico(self) -> Agent:
        return Agent(config=self.agents_config["electronico"], llm=self.llm, verbose=True)

    @agent
    def software(self) -> Agent:
        return Agent(config=self.agents_config["software"], llm=self.llm, verbose=True)

    @agent
    def mantenimiento(self) -> Agent:
        return Agent(config=self.agents_config["mantenimiento"], llm=self.llm, verbose=True)

    @agent
    def cliente(self) -> Agent:
        return Agent(config=self.agents_config["cliente"], llm=self.llm, verbose=True)

    # --- TAREAS CON SALIDA A ARCHIVOS INDIVIDUALES ---
    @task
    def requirements_ingestion_task(self) -> Task:
        return Task(
            config=self.tasks_config["requirements_ingestion_task"],
            output_file="output/01_requirements_and_system.md",
            guardrail=guardrail_informe_completo("requirements_ingestion_task"),
            guardrail_max_retries=MAX_REINTENTOS_INCOMPLETO,
        )

    @task
    def mechanical_subsystem_design_task(self) -> Task:
        return Task(
            config=self.tasks_config["mechanical_subsystem_design_task"],
            output_file="output/02_mechanical_subsystem.md",
            guardrail=guardrail_informe_completo("mechanical_subsystem_design_task"),
            guardrail_max_retries=MAX_REINTENTOS_INCOMPLETO,
        )

    @task
    def electronic_subsystem_design_task(self) -> Task:
        return Task(
            config=self.tasks_config["electronic_subsystem_design_task"],
            output_file="output/03_electronic_subsystem.md",
            guardrail=guardrail_informe_completo("electronic_subsystem_design_task"),
            guardrail_max_retries=MAX_REINTENTOS_INCOMPLETO,
        )

    @task
    def software_subsystem_design_task(self) -> Task:
        return Task(
            config=self.tasks_config["software_subsystem_design_task"],
            output_file="output/04_software_architecture.md",
            guardrail=guardrail_informe_completo("software_subsystem_design_task"),
            guardrail_max_retries=MAX_REINTENTOS_INCOMPLETO,
        )

    @task
    def maintenance_rams_eval_task(self) -> Task:
        return Task(
            config=self.tasks_config["maintenance_rams_eval_task"],
            output_file="output/05_rams_maintenance.md",
            guardrail=guardrail_informe_completo("maintenance_rams_eval_task"),
            guardrail_max_retries=MAX_REINTENTOS_INCOMPLETO,
        )

    @task
    def client_acceptance_review_task(self) -> Task:
        return Task(
            config=self.tasks_config["client_acceptance_review_task"],
            output_file="output/06_client_acceptance.md",
            guardrail=guardrail_informe_completo("client_acceptance_review_task"),
            guardrail_max_retries=MAX_REINTENTOS_INCOMPLETO,
        )

    @task
    def design_board_integration_task(self) -> Task:
        return Task(
            config=self.tasks_config["design_board_integration_task"],
            output_file="output/00_vdi2206_consolidated_dossier.md",
            guardrail=guardrail_informe_completo("design_board_integration_task"),
            guardrail_max_retries=MAX_REINTENTOS_INCOMPLETO,
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            memory=False,
            verbose=True,
        )