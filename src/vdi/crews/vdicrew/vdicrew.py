import os
import re
from datetime import datetime
from pathlib import Path

from crewai import Agent, Crew, Process, Task, LLM
from crewai.project import CrewBase, agent, crew, task

from ...corrida import RUN_DIR

# --- GUARDRAIL DE COMPLETITUD ---
# Cada tarea debe cerrar su informe con MARCA_FIN (se pide en tasks.yaml).
# Si el informe llega cortado, CrewAI repite la tarea. Tras MAX_REINTENTOS_INCOMPLETO
# fallos se acepta la salida tal cual para no perder la corrida, y queda registrado.
MARCA_FIN = "<!-- FIN DEL INFORME -->"
MAX_REINTENTOS_INCOMPLETO = 2
REGISTRO_GUARDRAIL = RUN_DIR / "_guardrail.log"

# Extension maxima de cada informe. Se pide en tasks.yaml ({max_palabras}) y en el reintento.
# Unas 6 000 palabras equivalen a ~15 000 tokens generados (2,5 tokens por palabra).
MAX_PALABRAS = 6000


def _registrar(tarea: str, estado: str, detalle: str, texto: str) -> None:
    try:
        REGISTRO_GUARDRAIL.parent.mkdir(parents=True, exist_ok=True)
        with open(REGISTRO_GUARDRAIL, "a", encoding="utf-8") as f:
            f.write(
                f"{datetime.now().isoformat(timespec='seconds')}\t{tarea}\t{estado}\t"
                f"{len(texto)} caracteres\t{len(texto.split())} palabras\t{detalle}\n"
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
            _registrar(tarea, "COMPLETO", f"intentos fallidos previos: {fallos['n']}", texto)
            return (True, output)

        detalle = "; ".join(problemas)
        if fallos["n"] >= MAX_REINTENTOS_INCOMPLETO:
            _registrar(tarea, "ACEPTADO_INCOMPLETO", detalle, texto)
            return (True, output)

        fallos["n"] += 1
        _registrar(tarea, "REINTENTO", detalle, texto)
        return (
            False,
            f"El informe quedo incompleto ({detalle}). Entrega una version COMPLETA y MAS BREVE que la "
            f"anterior, de maximo {MAX_PALABRAS} palabras: conserva el contenido esencial, NO agregues "
            f"secciones ni anexos nuevos, cierra todos los bloques de codigo y termina con la linea "
            f"exacta: {MARCA_FIN}",
        )

    return validar

# Configuracion del LLM. Se guarda tal cual en corrida.json.
LLM_CONFIG = {
    "model": "ollama_chat/qwen3.8-vdi",
    "base_url": "http://localhost:11434",
    "temperature": 0.2,
    "max_retries": 1,
    "timeout": 21600,  # 6 h: las tareas finales tardan casi 3 h solo en leer el contexto y responder
}


@CrewBase
class VdiCrew:
    """VDI 2206 Mechatronic Design Review Crew"""

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    # El contexto (num_ctx) NO se configura aqui: CrewAI usa el endpoint /v1 de Ollama, que lo ignora.
    # Esta fijado en el servidor, en el Modelfile de qwen3.8-vdi (131072 tokens).
    llm = LLM(**LLM_CONFIG)

    """     # Configuración del LLM con control de tokens
    llm = LLM(
        model="gemini/gemini-3.5-flash", # O la versión habilitada en tu API
        temperature=0.2,
        max_tokens=4000,                  # Límite máximo de tokens por respuesta del agente
        max_retries=4,                  # Límite de solicitudes por agente
        timeout=120,               # Tiempo máximo de espera por solicitud
    ) """

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
            output_file=str(RUN_DIR / "01_requirements_and_system.md"),
            guardrail=guardrail_informe_completo("requirements_ingestion_task"),
            guardrail_max_retries=MAX_REINTENTOS_INCOMPLETO,
        )

    @task
    def mechanical_subsystem_design_task(self) -> Task:
        return Task(
            config=self.tasks_config["mechanical_subsystem_design_task"],
            output_file=str(RUN_DIR / "02_mechanical_subsystem.md"),
            guardrail=guardrail_informe_completo("mechanical_subsystem_design_task"),
            guardrail_max_retries=MAX_REINTENTOS_INCOMPLETO,
        )

    @task
    def electronic_subsystem_design_task(self) -> Task:
        return Task(
            config=self.tasks_config["electronic_subsystem_design_task"],
            output_file=str(RUN_DIR / "03_electronic_subsystem.md"),
            guardrail=guardrail_informe_completo("electronic_subsystem_design_task"),
            guardrail_max_retries=MAX_REINTENTOS_INCOMPLETO,
        )

    @task
    def software_subsystem_design_task(self) -> Task:
        return Task(
            config=self.tasks_config["software_subsystem_design_task"],
            output_file=str(RUN_DIR / "04_software_architecture.md"),
            guardrail=guardrail_informe_completo("software_subsystem_design_task"),
            guardrail_max_retries=MAX_REINTENTOS_INCOMPLETO,
        )

    @task
    def maintenance_rams_eval_task(self) -> Task:
        return Task(
            config=self.tasks_config["maintenance_rams_eval_task"],
            output_file=str(RUN_DIR / "05_rams_maintenance.md"),
            guardrail=guardrail_informe_completo("maintenance_rams_eval_task"),
            guardrail_max_retries=MAX_REINTENTOS_INCOMPLETO,
        )

    @task
    def client_acceptance_review_task(self) -> Task:
        return Task(
            config=self.tasks_config["client_acceptance_review_task"],
            output_file=str(RUN_DIR / "06_client_acceptance.md"),
            guardrail=guardrail_informe_completo("client_acceptance_review_task"),
            guardrail_max_retries=MAX_REINTENTOS_INCOMPLETO,
        )

    @task
    def design_board_integration_task(self) -> Task:
        return Task(
            config=self.tasks_config["design_board_integration_task"],
            output_file=str(RUN_DIR / "00_vdi2206_consolidated_dossier.md"),
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