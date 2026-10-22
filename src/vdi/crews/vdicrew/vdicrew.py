import hashlib
import os
import re
from datetime import datetime
from pathlib import Path

from crewai import Agent, Crew, Process, Task, LLM
from crewai.project import CrewBase, agent, crew, task

from ...corrida import RUN_DIR
from ...registro_llm import RegistroLLM
from .herramientas import (MAX_BUSQUEDAS_WEB, MAX_CONSULTAS_RAG, MAX_USOS_CALCULADORA, MAX_USOS_MATRIZ, clave_tavily,
                           herramientas_agente)

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

# Tope de tokens generados por respuesta, contando el razonamiento. Corta un razonamiento en bucle:
# una tarea normal genera entre 9 000 y 15 000; a 57 tokens/s el tope se alcanza en unos 12 min.
MAX_TOKENS_RESPUESTA = 40000


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

# Contexto metodologico comun (VDI 2206 y herramientas de diseno conceptual de las Fases 1 y 2).
# Se agrega al backstory de todos los agentes. Su huella queda en corrida.json para saber que version se uso.
# No debe contener llaves: CrewAI las interpreta como variables de entrada.
ARCHIVO_METODOLOGIA = Path(__file__).parent / "config" / "metodologia.md"
METODOLOGIA = ARCHIVO_METODOLOGIA.read_text(encoding="utf-8").strip()

# Herramientas (RAG de los manuales y calculadora). VDI_HERRAMIENTAS=0 las apaga y reproduce las etapas
# anteriores. Cuando estan activas, cada agente recibe tambien las reglas de uso de herramientas.md.
HERRAMIENTAS_ACTIVAS = os.environ.get("VDI_HERRAMIENTAS", "1").strip() != "0"
ARCHIVO_HERRAMIENTAS = Path(__file__).parent / "config" / "herramientas.md"
if HERRAMIENTAS_ACTIVAS:
    METODOLOGIA += "\n\n" + ARCHIVO_HERRAMIENTAS.read_text(encoding="utf-8").strip()

# Busqueda web (Tavily): para los agentes que hacen benchmarking, seleccionan componentes comerciales o usan
# datos de confiabilidad de fabricantes.
# Se activa si hay TAVILY_API_KEY; VDI_BUSQUEDA_WEB=0 la apaga. Sus reglas estan en busqueda_web.md.
AGENTES_CON_WEB = {"team_lead", "mecanico", "electronico", "mantenimiento"}
BUSQUEDA_WEB_PEDIDA = os.environ.get("VDI_BUSQUEDA_WEB", "1").strip() != "0"
BUSQUEDA_WEB_ACTIVA = HERRAMIENTAS_ACTIVAS and BUSQUEDA_WEB_PEDIDA and bool(clave_tavily())
if HERRAMIENTAS_ACTIVAS and BUSQUEDA_WEB_PEDIDA and not BUSQUEDA_WEB_ACTIVA:
    print("[VDI] AVISO: no hay TAVILY_API_KEY; la corrida va SIN busqueda web.", flush=True)
ARCHIVO_BUSQUEDA_WEB = Path(__file__).parent / "config" / "busqueda_web.md"
if BUSQUEDA_WEB_ACTIVA:
    METODOLOGIA += "\n\n" + ARCHIVO_BUSQUEDA_WEB.read_text(encoding="utf-8").strip()
METODOLOGIA_ID = hashlib.sha256(METODOLOGIA.encode("utf-8")).hexdigest()[:12]
# Con herramientas cada consulta o calculo es una iteracion mas del agente (el valor por defecto de CrewAI es 25)
MAX_ITERACIONES = 25 + (MAX_CONSULTAS_RAG + MAX_USOS_CALCULADORA + MAX_USOS_MATRIZ if HERRAMIENTAS_ACTIVAS else 0) \
    + (MAX_BUSQUEDAS_WEB if BUSQUEDA_WEB_ACTIVA else 0)

# Configuracion del LLM. Se guarda tal cual en corrida.json.
LLM_CONFIG = {
    "model": "ollama_chat/qwen3.8-vdi",
    "base_url": "http://" + os.environ.get("OLLAMA_HOST", "localhost:11434"),
    # Muestreo recomendado por Qwen para el modo de razonamiento. Con temperatura 0.2 el razonamiento entraba
    # a veces en bucles de repeticion que terminaban en respuesta vacia o en el tope de tokens
    # (prueba diagnostico_muestreo, job 22902: 1 de 7 vacia con 0.2 y 0 de 14 con 0.6).
    "temperature": 0.6,
    "top_p": 0.95,
    "max_retries": 1,
    "max_tokens": MAX_TOKENS_RESPUESTA,
    "timeout": 1800,  # 30 min: en Apolo la peticion mas larga de una corrida normal dura menos de 5 min
}

# Esfuerzo de razonamiento del modelo: low, medium, high o xhigh. Vacio = el que trae el modelo por defecto.
# Se envia en extra_body porque CrewAI descarta reasoning_effort como argumento directo con Ollama.
ESFUERZO_RAZONAMIENTO = os.environ.get("VDI_REASONING_EFFORT", "").strip()
if ESFUERZO_RAZONAMIENTO:
    LLM_CONFIG["extra_body"] = {"reasoning_effort": ESFUERZO_RAZONAMIENTO}


@CrewBase
class VdiCrew:
    """VDI 2206 Mechatronic Design Review Crew"""

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    # El contexto (num_ctx) NO se configura aqui: CrewAI usa el endpoint /v1 de Ollama, que lo ignora.
    # Esta fijado en el servidor, en el Modelfile de qwen3.8-vdi (131072 tokens).
    # El interceptor anota cada respuesta del modelo en _llm.log y guarda las que llegan vacias en llm_vacias/.
    llm = LLM(**LLM_CONFIG, interceptor=RegistroLLM(RUN_DIR, MARCA_FIN))

    """     # Configuración del LLM con control de tokens
    llm = LLM(
        model="gemini/gemini-3.5-flash", # O la versión habilitada en tu API
        temperature=0.2,
        max_tokens=4000,                  # Límite máximo de tokens por respuesta del agente
        max_retries=4,                  # Límite de solicitudes por agente
        timeout=120,               # Tiempo máximo de espera por solicitud
    ) """

    def _agente(self, nombre: str) -> Agent:
        """Agente con su backstory de agents.yaml mas el contexto metodologico comun y, si estan
        activas, sus propias instancias de las herramientas (cada una con su tope de usos por tarea)."""
        cfg = self.agents_config[nombre]
        return Agent(
            config=cfg,
            backstory=cfg["backstory"].rstrip() + "\n\n" + METODOLOGIA,
            llm=self.llm,
            tools=herramientas_agente(web=BUSQUEDA_WEB_ACTIVA and nombre in AGENTES_CON_WEB) if HERRAMIENTAS_ACTIVAS else [],
            max_iter=MAX_ITERACIONES,
            verbose=True,
        )

    # --- AGENTES VDI 2206 ---
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
    def system_synthesis_task(self) -> Task:
        return Task(
            config=self.tasks_config["system_synthesis_task"],
            output_file=str(RUN_DIR / "01b_system_synthesis.md"),
            guardrail=guardrail_informe_completo("system_synthesis_task"),
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
