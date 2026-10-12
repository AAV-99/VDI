import os
import re
from datetime import datetime
from pathlib import Path

from crewai import Agent, Crew, Process, Task
from crewai.events import crewai_event_bus
from crewai.events.types.mcp_events import (
    MCPConnectionCompletedEvent,
    MCPConnectionFailedEvent,
    MCPToolExecutionCompletedEvent,
    MCPToolExecutionFailedEvent,
)
from crewai.mcp import MCPServerHTTP
from crewai.mcp.filters import create_static_tool_filter
from crewai.project import CrewBase, agent, crew, task
from dotenv import load_dotenv
from google.genai import types

from .conocimiento import herramienta_rag
from .modelos import crear_llm

load_dotenv()  # asegura que GEMINI_API_KEY y TAVILY_API_KEY del .env esten cargadas

# --- MCP DE BUSQUEDA WEB (TAVILY) ---
# Servidor MCP remoto de Tavily: requiere TAVILY_API_KEY en el .env de la raiz del proyecto.
# Solo lo usan los agentes que arman BOM con componentes comerciales, y solo con las
# herramientas de busqueda y extraccion (crawl, map y research gastan mas creditos).
TAVILY_MCP_URL = "https://mcp.tavily.com/mcp/"
HERRAMIENTAS_TAVILY = ["tavily_search", "tavily_extract"]
AGENTES_CON_BUSQUEDA_WEB = {"mecanico", "electronico"}
TAREAS_CON_BUSQUEDA_WEB = {"mechanical_subsystem_design_task", "electronic_subsystem_design_task"}

# Evidencia de uso del MCP: cada conexion y cada busqueda queda en output/_mcp.log
# y en la consola con el prefijo [MCP].
REGISTRO_MCP = Path("output/_mcp.log")
_busquedas = {"total": 0, "tarea_actual": 0}  # tarea_actual se reinicia al aceptar cada tarea
_urls_tarea: list[str] = []  # URL devueltas por las busquedas de la tarea en curso


def _registrar_mcp(estado: str, detalle: str) -> None:
    print(f"[MCP] {estado}: {detalle}", flush=True)
    try:
        REGISTRO_MCP.parent.mkdir(parents=True, exist_ok=True)
        with open(REGISTRO_MCP, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')}\t{estado}\t{detalle}\n")
    except OSError:
        pass  # el registro nunca debe tumbar la corrida


def _resumen(valor, limite: int = 160) -> str:
    return " ".join(str(valor).split())[:limite]


# --- PARCHE: error falso al cerrar la conexion MCP ---
# En algunas instalaciones, al cerrar la conexion con el servidor remoto CrewAI lanza
# "Error during MCP client disconnect: no running event loop" DESPUES de que la busqueda
# ya respondio. Ese error hacia que el agente recibiera "Tool Failed" en vez de los
# resultados. Aqui se ignora el fallo de cierre para que el resultado llegue al agente.
_aviso_desconexion = {"hecho": False}
try:
    from crewai.mcp.client import MCPClient

    _disconnect_original = MCPClient.disconnect

    async def _disconnect_tolerante(self):
        try:
            await _disconnect_original(self)
        except RuntimeError as error:
            if not _aviso_desconexion["hecho"]:
                _aviso_desconexion["hecho"] = True
                _registrar_mcp("AVISO_CIERRE_IGNORADO", _resumen(error))

    MCPClient.disconnect = _disconnect_tolerante
except Exception as error:  # si CrewAI cambia su estructura interna, se sigue sin el parche
    print(f"[MCP] No se pudo aplicar el parche de desconexion: {error}", flush=True)


@crewai_event_bus.on(MCPConnectionCompletedEvent)
def _mcp_conectado(source, event):
    _registrar_mcp("CONEXION_OK", f"servidor {event.server_name}")


@crewai_event_bus.on(MCPConnectionFailedEvent)
def _mcp_conexion_fallida(source, event):
    _registrar_mcp("CONEXION_FALLIDA", f"servidor {event.server_name}: {_resumen(event.error)}")


@crewai_event_bus.on(MCPToolExecutionCompletedEvent)
def _mcp_busqueda_ok(source, event):
    _busquedas["total"] += 1
    _busquedas["tarea_actual"] += 1
    urls = re.findall(r'"url"\s*:\s*"([^"]+)"', str(event.result))
    _urls_tarea.extend(u for u in urls if u not in _urls_tarea)
    consulta = (event.tool_args or {}).get("query") or _resumen(event.tool_args)
    _registrar_mcp(
        f"BUSQUEDA_{_busquedas['total']}",
        f"{event.tool_name} \"{consulta}\" -> {len(urls)} resultados: {' | '.join(urls) or _resumen(event.result)}",
    )


@crewai_event_bus.on(MCPToolExecutionFailedEvent)
def _mcp_busqueda_fallida(source, event):
    _registrar_mcp("BUSQUEDA_FALLIDA", f"{event.tool_name} {_resumen(event.tool_args)}: {_resumen(event.error)}")


def _mcp_busqueda_web() -> list:
    """Devuelve la configuracion del MCP de Tavily, o lista vacia si no hay API key."""
    key = (os.environ.get("TAVILY_API_KEY") or "").strip()
    if not key:
        print("[MCP] TAVILY_API_KEY no encontrada: los agentes corren SIN busqueda web.", flush=True)
        return []
    return [
        MCPServerHTTP(
            url=TAVILY_MCP_URL,
            # La key va en el encabezado y no en la URL para que no quede en los logs.
            headers={"Authorization": f"Bearer {key}"},
            tool_filter=create_static_tool_filter(allowed_tool_names=HERRAMIENTAS_TAVILY),
            cache_tools_list=True,
        )
    ]

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


def guardrail_informe_completo(tarea: str, exige_busqueda: bool = False):
    """Devuelve un guardrail que verifica que el informe de `tarea` no este cortado.

    Con `exige_busqueda`, tambien verifica que el agente haya usado al menos una vez
    la busqueda web del MCP durante la tarea.
    """
    fallos = {"n": 0}

    def validar(output):
        texto = (getattr(output, "raw", None) or "").strip()
        problemas = []
        if MARCA_FIN not in texto[-300:]:
            problemas.append("no termina con la marca de fin")
        if len(re.findall(r"^\s*```", texto, flags=re.MULTILINE)) % 2 != 0:
            problemas.append("hay un bloque de codigo o diagrama Mermaid sin cerrar")
        sin_busqueda = exige_busqueda and _busquedas["tarea_actual"] == 0
        if sin_busqueda:
            problemas.append("no se uso la herramienta de busqueda web")
        sin_citas = exige_busqueda and bool(_urls_tarea) and not any(u in texto for u in _urls_tarea)
        if sin_citas:
            problemas.append("el BOM no cita ninguna URL devuelta por la busqueda web")

        if not problemas:
            _registrar(
                tarea,
                "COMPLETO",
                f"intentos fallidos previos: {fallos['n']}; busquedas web: {_busquedas['tarea_actual']}",
                len(texto),
            )
            _busquedas["tarea_actual"] = 0
            _urls_tarea.clear()
            return (True, output)

        detalle = "; ".join(problemas)
        if fallos["n"] >= MAX_REINTENTOS_INCOMPLETO:
            _registrar(tarea, "ACEPTADO_INCOMPLETO", detalle, len(texto))
            _busquedas["tarea_actual"] = 0
            _urls_tarea.clear()
            return (True, output)

        fallos["n"] += 1
        _registrar(tarea, "REINTENTO", detalle, len(texto))
        instruccion_busqueda = (
            "ANTES de reescribir, llama a la herramienta de busqueda web (tavily_search) al menos "
            "una vez y usa en la columna Fuente las URL exactas que devuelva. "
            if sin_busqueda
            else ""
        )
        if sin_citas:
            instruccion_busqueda += (
                "Tus busquedas devolvieron estas URL: " + " , ".join(_urls_tarea[:8]) + " . En el BOM usa "
                "los productos reales de esos resultados y pon su URL exacta en la columna Fuente. "
            )
        return (
            False,
            f"El informe no cumple ({detalle}). {instruccion_busqueda}Reescribe el informe COMPLETO "
            f"desde el inicio, cierra todos los bloques de codigo y termina con la linea exacta: {MARCA_FIN}",
        )

    return validar

@CrewBase
class VdiCrew:
    """VDI 2206 Mechatronic Design Review Crew"""

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    # Gemini API: requiere GEMINI_API_KEY en el .env de la raiz del proyecto.
    # Empieza con el mejor modelo de la cadena (modelos.py) y baja al siguiente si responde 429 (cuota agotada).
    llm = crear_llm(
        temperature=0.2,
        max_tokens=32000,                # Incluye tokens de razonamiento; con 4000 los informes se cortan
    )

    # --- AGENTES VDI 2206 ---
    # Reintentos propios de CrewAI ante errores del LLM (p. ej. 503): maximo 3 por tarea.
    # Los agentes de subsistemas consultan los manuales CRP con RAG (tools=[herramienta_rag()]).
    # Mecanico y electronico tienen ademas busqueda web por MCP (Tavily) para los BOM.
    def _agente(self, nombre: str, con_rag: bool = False) -> Agent:
        mcps = _mcp_busqueda_web() if nombre in AGENTES_CON_BUSQUEDA_WEB else []
        if mcps:
            _registrar_mcp("AGENTE_CON_MCP", f"'{nombre}' con herramientas {HERRAMIENTAS_TAVILY}")
        return Agent(
            config=self.agents_config[nombre],  # type: ignore[index]
            llm=self.llm,
            tools=[herramienta_rag()] if con_rag else [],
            mcps=mcps or None,
            max_retry_limit=3,
            verbose=True,
        )

    @agent
    def team_lead(self) -> Agent:
        return self._agente("team_lead")

    @agent
    def mecanico(self) -> Agent:
        return self._agente("mecanico", con_rag=True)

    @agent
    def electronico(self) -> Agent:
        return self._agente("electronico", con_rag=True)

    @agent
    def software(self) -> Agent:
        return self._agente("software", con_rag=True)

    @agent
    def mantenimiento(self) -> Agent:
        return self._agente("mantenimiento", con_rag=True)

    @agent
    def cliente(self) -> Agent:
        return self._agente("cliente")

    # --- TAREAS (output_file definido en tasks.yaml) ---
    def _tarea(self, nombre: str) -> Task:
        return Task(
            config=self.tasks_config[nombre],  # type: ignore[index]
            guardrail=guardrail_informe_completo(
                nombre,
                exige_busqueda=nombre in TAREAS_CON_BUSQUEDA_WEB and bool(os.environ.get("TAVILY_API_KEY")),
            ),
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