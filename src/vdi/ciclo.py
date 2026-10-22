"""Orquestacion de la corrida con ciclo de correccion iterativo (modelo en V de la VDI 2206).

1. Pasada inicial: 01, 01b, 02, 03, 04, 05 y 06 (revision del cliente), en secuencia.
2. Ciclo de correccion, hasta MAX_CICLOS veces:
   a. Triaje (lider de integracion): cada ECR abierta recibe disciplina responsable y tipo.
   b. Correccion: solo las disciplinas con ECR corregibles, en orden mecanica, electronica, software, RAMS.
      Cada una entrega una version nueva de su informe con la tabla "Cambios de la iteracion N". Si declara
      impacto sobre una disciplina posterior que no tenia ECR, esa disciplina revisa el impacto en la misma
      iteracion; el impacto sobre una anterior lo detecta el cliente en la verificacion.
   c. Verificacion del cliente: estado de cada ECR atendida frente a su criterio y ECR nuevas.
   El ciclo termina si no quedan ECR corregibles, si se alcanza MAX_CICLOS o si todas las corregibles
   restantes ya fallaron dos veces (quedan escaladas para el integrador).
3. Integrador final: consolida las versiones finales, resuelve conflictos de sistema y ECR escaladas.

Cada paso escribe su informe en RUN_DIR y se salta si el informe ya existe: si algo falla, volver a llamar a
ejecutar() continua desde el primer paso sin informe (asi funciona la reanudacion). El historial queda en
_iteraciones.json.
"""
import csv
import json
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from crewai import Crew, Process, Task
from crewai.tasks.task_output import TaskOutput

from .corrida import RUN_DIR, registrar_evento
from .crews.vdicrew.vdicrew import MARCA_FIN, MAX_REINTENTOS_INCOMPLETO, VdiCrew, guardrail_informe_completo

MAX_CICLOS = int(os.environ.get("VDI_CICLOS", "3"))  # 0 = sin ciclo de correccion (flujo de las etapas anteriores)
MAX_FALLOS_ECR = 2  # una ECR corregible que falla esta cantidad de veces seguidas queda escalada
HISTORIAL = RUN_DIR / "_iteraciones.json"
PASO_ACTUAL = {"tarea": "-"}  # ultima tarea lanzada, para registrar donde fallo una corrida

# Disciplina -> (agente, informe base, tarea de la pasada inicial)
DISCIPLINAS = {
    "mecanica": ("mecanico", "02_mechanical_subsystem", "mechanical_subsystem_design_task"),
    "electronica": ("electronico", "03_electronic_subsystem", "electronic_subsystem_design_task"),
    "software": ("software", "04_software_architecture", "software_subsystem_design_task"),
    "rams": ("mantenimiento", "05_rams_maintenance", "maintenance_rams_eval_task"),
}
ORDEN = list(DISCIPLINAS)
INICIALES = ["01_requirements_and_system", "01b_system_synthesis", "02_mechanical_subsystem", "03_electronic_subsystem",
             "04_software_architecture", "05_rams_maintenance", "06_client_acceptance"]
CLIENTE = "06_client_acceptance"
DOSSIER = "00_vdi2206_consolidated_dossier"
ID_ECR = re.compile(r"ECR[\s\-‐–]*0*(\d+)", re.I)


# --- utilidades de archivos y tablas -------------------------------------------------------------

def _archivo(base: str, version: int = 1) -> Path:
    return RUN_DIR / (f"{base}.md" if version == 1 else f"{base}_v{version}.md")


def ultima_version(base: str) -> tuple[int, Path]:
    version = 1
    while _archivo(base, version + 1).exists():
        version += 1
    return version, _archivo(base, version)


def _leer(ruta: Path) -> str:
    return ruta.read_text(encoding="utf-8")


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")


def _sin_llaves(texto: str) -> str:
    # El texto insertado en la descripcion de una tarea no puede tener llaves: CrewAI las toma como variables
    return texto.replace("{", "(").replace("}", ")")


def ecr_id(texto: str) -> str | None:
    m = ID_ECR.search(texto)
    return f"ECR-{int(m.group(1)):02d}" if m else None


def tablas(texto: str) -> list[list[dict]]:
    """Tablas Markdown del texto como listas de filas {columna normalizada: valor}."""
    salida, bloque = [], []
    for linea in texto.splitlines() + [""]:
        if linea.strip().startswith("|"):
            bloque.append(linea.strip())
            continue
        if len(bloque) >= 2:
            cab = [_sin_tildes(c.strip().strip("*")) for c in bloque[0].strip("|").split("|")]
            filas = []
            for fila in bloque[2:] if re.match(r"^\|[\s:|-]+\|$", bloque[1]) else bloque[1:]:
                celdas = [c.strip() for c in fila.strip("|").split("|")]
                filas.append(dict(zip(cab, celdas + [""] * (len(cab) - len(celdas)))))
            salida.append(filas)
        bloque = []
    return salida


def _tabla_con(texto: str, *columnas: str) -> list[dict] | None:
    for t in tablas(texto):
        if t and all(any(col in k for k in t[0]) for col in columnas):
            return t
    return None


def _valor(fila: dict, columna: str) -> str:
    return next((v for k, v in fila.items() if columna in k), "")


def leer_triaje(texto: str) -> list[dict]:
    """Filas validas del triaje: {ecr, disciplina, tipo, justificacion}."""
    tabla = _tabla_con(texto, "ecr", "disciplina", "tipo")
    filas = []
    for f in tabla or []:
        ecr = ecr_id(_valor(f, "ecr"))
        dis = _sin_tildes(_valor(f, "disciplina"))
        tipo = _sin_tildes(_valor(f, "tipo"))
        disciplina = next((d for d in ORDEN + ["sistema"] if d in dis), "mantenimiento" in dis and "rams" or None)
        tipo = ("corregible" if "correg" in tipo else "prueba" if re.search(r"prueba|ensayo|medic", tipo)
                else "cotizacion" if "cotiz" in tipo else "cumple" if "cumple" in tipo else None)
        if ecr and disciplina and tipo:
            filas.append({"ecr": ecr, "disciplina": disciplina, "tipo": tipo,
                          "justificacion": " ".join(_valor(f, "justific").split())[:400]})
    return filas


def leer_verificacion(texto: str) -> dict[str, str]:
    """Estado de cada ECR en la tabla de verificacion del cliente: cumple, no_cumple o pendiente."""
    tabla = _tabla_con(texto, "ecr", "estado")
    estados = {}
    for f in tabla or []:
        ecr = ecr_id(_valor(f, "ecr"))
        est = _sin_tildes(_valor(f, "estado"))
        if ecr:
            estados[ecr] = "no_cumple" if "no cumple" in est else "cumple" if "cumple" in est else "pendiente"
    return estados


def seccion_cambios(texto: str, iteracion: int) -> str:
    m = re.search(rf"^##\s*Cambios de la iteraci[oó]n\s*{iteracion}\b.*?(?=^##\s|\Z)", texto, re.S | re.M | re.I)
    return m.group(0).replace(MARCA_FIN, "").strip() if m else ""


def impactos(cambios: str) -> set[str]:
    """Disciplinas mencionadas en la columna de impacto de la tabla de cambios."""
    tabla = _tabla_con(cambios, "ecr", "impacto") or []
    afectadas = set()
    for f in tabla:
        celda = _sin_tildes(_valor(f, "impacto"))
        afectadas |= {d for d in ORDEN if d in celda}
        if "mantenimiento" in celda:
            afectadas.add("rams")
    return afectadas


# --- guardrails con validacion de formato -------------------------------------------------------

def _guardrail(tarea: str, validar_formato):
    """Guardrail de informe completo mas una validacion propia; si el formato no sirve, pide repetir."""
    completo = guardrail_informe_completo(tarea)
    fallos = {"n": 0}

    def validar(output):
        problema = validar_formato((getattr(output, "raw", None) or ""))
        if problema and fallos["n"] < MAX_REINTENTOS_INCOMPLETO:
            fallos["n"] += 1
            return (False, f"El informe no cumple el formato obligatorio: {problema}. Entrégalo de nuevo completo, "
                           f"con el formato pedido y terminando con la línea exacta {MARCA_FIN}")
        return completo(output)

    return validar


# --- construccion de tareas ----------------------------------------------------------------------

def _previa(nombre: str, texto: str, agente) -> Task:
    """Tarea ya resuelta que solo aporta su informe como contexto."""
    t = Task(description=f"Informe previo {nombre}", expected_output="Informe", agent=agente)
    t.output = TaskOutput(description=t.description, raw=texto, agent=agente.role)
    return t


def _tarea(vc: VdiCrew, clave: str, agente, salida: Path, contexto: list, reemplazos: dict, validar=None) -> Task:
    cfg = vc.tasks_config[clave]
    desc = cfg["description"]
    for marca, valor in reemplazos.items():
        desc = desc.replace(f"[[{marca}]]", _sin_llaves(str(valor)))
    nombre = f"{clave}:{salida.stem}"
    return Task(description=desc, expected_output=cfg["expected_output"], agent=agente, context=contexto,
                output_file=str(salida), name=clave,
                guardrail=_guardrail(nombre, validar) if validar else guardrail_informe_completo(nombre),
                guardrail_max_retries=MAX_REINTENTOS_INCOMPLETO)


def _ejecutar(vc: VdiCrew, tareas: list[Task], inputs: dict) -> None:
    PASO_ACTUAL["tarea"] = ", ".join(Path(t.output_file).stem for t in tareas)
    agentes = list({id(t.agent): t.agent for t in tareas}.values())
    Crew(agents=agentes, tasks=tareas, process=Process.sequential, memory=False, verbose=True).kickoff(inputs=inputs)


# --- historial ------------------------------------------------------------------------------------

def _cargar_historial() -> dict:
    if HISTORIAL.exists():
        return json.loads(HISTORIAL.read_text(encoding="utf-8"))
    return {"max_ciclos": MAX_CICLOS, "iteraciones": [], "escaladas": [], "fin": None}


def _guardar_historial(h: dict) -> None:
    tmp = HISTORIAL.with_name(HISTORIAL.name + ".tmp")
    tmp.write_text(json.dumps(h, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, HISTORIAL)


def _peticiones_llm() -> int:
    """Numero de respuestas del modelo registradas hasta ahora en _llm.log (para medir cada iteracion)."""
    try:
        with open(RUN_DIR / "_llm.log", encoding="utf-8") as f:
            return sum(1 for _ in f) - 1
    except OSError:
        return 0


def _tokens_desde(n: int) -> int:
    try:
        with open(RUN_DIR / "_llm.log", encoding="utf-8") as f:
            filas = list(csv.DictReader(f, delimiter="\t"))
        return sum(int(r.get("tokens_respuesta") or 0) for r in filas[n:])
    except (OSError, ValueError):
        return 0


# --- pasos ----------------------------------------------------------------------------------------

def pasada_inicial(vc: VdiCrew, inputs: dict) -> None:
    """Tareas 01 a 06 en secuencia; las que ya tienen informe no se repiten y aportan su texto como contexto."""
    completo = vc.crew()
    tareas = [t for t in completo.tasks if Path(t.output_file).stem in INICIALES]
    pendiente = next((i for i, t in enumerate(tareas) if not Path(t.output_file).exists()), None)
    if pendiente is None:
        return
    for t in tareas[:pendiente]:
        t.output = TaskOutput(description=t.description, raw=_leer(Path(t.output_file)), agent=t.agent.role)
    for j in range(pendiente, len(tareas)):
        tareas[j].context = tareas[:j]
    _ejecutar(vc, tareas[pendiente:], inputs)


def _contexto_actual(vc: VdiCrew, bases: list[str]) -> list[Task]:
    return [_previa(b, _leer(ultima_version(b)[1]), vc.team_lead()) for b in bases]


def iteracion(vc: VdiCrew, inputs: dict, k: int, h: dict) -> bool:
    """Ejecuta (o completa) la iteracion k. Devuelve False si no hay nada que corregir y el ciclo debe terminar."""
    registro = next((it for it in h["iteraciones"] if it["iteracion"] == k), None)
    if registro is None:
        registro = {"iteracion": k, "inicio": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "peticion_inicial": _peticiones_llm()}
        h["iteraciones"].append(registro)
        _guardar_historial(h)
    lider = vc.team_lead()
    v_cliente, ruta_cliente = ultima_version(CLIENTE)

    # a. Triaje
    ruta_triaje = RUN_DIR / f"07_triaje_it{k}.md"
    if not ruta_triaje.exists():
        ctx = _contexto_actual(vc, ["01b_system_synthesis"]) + [_previa(ruta_cliente.stem, _leer(ruta_cliente), lider)]
        escaladas = ", ".join(h["escaladas"]) or "ninguna"
        _ejecutar(vc, [_tarea(vc, "ecr_triage_task", lider, ruta_triaje, ctx,
                              {"ITERACION": k, "ESCALADAS": escaladas},
                              lambda t: None if leer_triaje(t) else "falta la tabla | ECR | Disciplina | Tipo | Justificación |")],
                  inputs)
    triaje = leer_triaje(_leer(ruta_triaje))
    registro["triaje"] = triaje
    asignadas = {d: [f for f in triaje if f["disciplina"] == d and f["tipo"] == "corregible" and f["ecr"] not in h["escaladas"]]
                 for d in ORDEN}
    registro["escaladas_sistema"] = [f["ecr"] for f in triaje if f["disciplina"] == "sistema"]
    if not any(asignadas.values()):
        registro["resultado"] = "sin ECR corregibles"
        _guardar_historial(h)
        return False

    # b. Correcciones, en orden; los impactos declarados sobre disciplinas posteriores se revisan en esta iteracion
    cambios_previos, impactadas, ejecutadas = [], {}, []
    for d in ORDEN:
        ecrs, motivos = asignadas[d], impactadas.get(d, [])
        if not ecrs and not motivos:
            continue
        agente_nombre, base, _ = DISCIPLINAS[d]
        v, ruta_anterior = ultima_version(base)
        ruta_nueva = _archivo(base, k + 1)
        if not ruta_nueva.exists():
            texto_ecr = "\n".join(f"- {f['ecr']}: {f['justificacion']}" for f in ecrs) or "- Ninguna ECR directa."
            if motivos:
                texto_ecr += "\n- Revisión de impacto: otra disciplina hizo cambios que afectan tu dominio: " + " ".join(motivos)
            agente = getattr(vc, agente_nombre)()
            ctx = _contexto_actual(vc, ["01b_system_synthesis"]) + [
                _previa(ruta_anterior.stem, _leer(ruta_anterior), agente), _previa(ruta_cliente.stem, _leer(ruta_cliente), agente)]
            _ejecutar(vc, [_tarea(vc, "domain_correction_task", agente, ruta_nueva, ctx,
                                  {"ITERACION": k, "DOMINIO": d, "ECR_ASIGNADAS": texto_ecr,
                                   "CAMBIOS_PREVIOS": "\n\n".join(cambios_previos) or "Ninguno."},
                                  lambda t: None if seccion_cambios(t, k) and _tabla_con(seccion_cambios(t, k), "ecr", "impacto")
                                  else f"falta la sección '## Cambios de la iteración {k}' con su tabla de cambios")],
                      inputs)
        cambios = seccion_cambios(_leer(ruta_nueva), k)
        cambios_previos.append(f"Disciplina {d}:\n{cambios}")
        ejecutadas.append({"disciplina": d, "ecr": [f["ecr"] for f in ecrs], "revision_impacto": bool(motivos),
                           "informe": ruta_nueva.name})
        for otra in impactos(cambios) - {d}:
            if ORDEN.index(otra) > ORDEN.index(d):
                impactadas.setdefault(otra, []).append(f"(desde {d}) {' '.join(cambios.split())[:1500]}")
            else:
                registro.setdefault("impactos_hacia_atras", []).append({"desde": d, "hacia": otra})
    registro["ejecutadas"] = ejecutadas

    # c. Verificacion del cliente
    ruta_verif = _archivo(CLIENTE, v_cliente + 1)
    atendidas = [f["ecr"] for e in asignadas.values() for f in e]
    if not ruta_verif.exists():
        cliente = vc.cliente()
        ctx = [_previa(ruta_cliente.stem, _leer(ruta_cliente), cliente)] + [
            _previa(e["informe"], _leer(RUN_DIR / e["informe"]), cliente) for e in ejecutadas]
        _ejecutar(vc, [_tarea(vc, "client_verification_task", cliente, ruta_verif, ctx,
                              {"ITERACION": k, "ECR_ITERACION": ", ".join(atendidas)},
                              lambda t: None if leer_verificacion(t) else "falta la tabla | ECR | Criterio de aceptación | Estado | Evidencia |")],
                  inputs)
    estados = leer_verificacion(_leer(ruta_verif))
    registro["verificacion"] = estados
    registro["cerradas"] = [e for e in atendidas if estados.get(e) in ("cumple", "pendiente")]
    registro["fallidas"] = [e for e in atendidas if estados.get(e) == "no_cumple"]
    anteriores = {f["ecr"] for it in h["iteraciones"] if it["iteracion"] < k for f in it.get("triaje", [])}
    registro["nuevas"] = sorted(set(estados) - anteriores - {f["ecr"] for f in triaje})

    # ECR que fallan MAX_FALLOS_ECR veces seguidas quedan escaladas
    for e in registro["fallidas"]:
        racha = 0
        for it in sorted(h["iteraciones"], key=lambda x: x["iteracion"], reverse=True):
            if e in it.get("fallidas", []):
                racha += 1
            else:
                break
        if racha >= MAX_FALLOS_ECR and e not in h["escaladas"]:
            h["escaladas"].append(e)
    registro["fin"] = datetime.now().astimezone().isoformat(timespec="seconds")
    registro["tokens"] = _tokens_desde(registro["peticion_inicial"])
    registro["resultado"] = "completada"
    _guardar_historial(h)
    return True


def _resumen_historial(h: dict) -> str:
    lineas = ["# Historial del ciclo de corrección", "",
              "| Iteración | Disciplinas que corrigieron | ECR atendidas | Cerradas | Fallidas | Nuevas |", "|---|---|---|---|---|---|"]
    for it in h["iteraciones"]:
        if it.get("resultado") != "completada":
            continue
        lineas.append(f"| {it['iteracion']} | {', '.join(e['disciplina'] for e in it.get('ejecutadas', []))} | "
                      f"{', '.join(x for e in it.get('ejecutadas', []) for x in e['ecr'])} | {', '.join(it.get('cerradas', [])) or '-'} | "
                      f"{', '.join(it.get('fallidas', [])) or '-'} | {', '.join(it.get('nuevas', [])) or '-'} |")
    sistema = sorted({e for it in h["iteraciones"] for e in it.get("escaladas_sistema", [])})
    lineas += ["", f"ECR escaladas por fallar {MAX_FALLOS_ECR} veces: {', '.join(h['escaladas']) or 'ninguna'}.",
               f"ECR de nivel sistema (concepto o arquitectura), no enviadas a las disciplinas: {', '.join(sistema) or 'ninguna'}.",
               f"Motivo de fin del ciclo: {h.get('fin') or 'sin ciclo'}."]
    return "\n".join(lineas)


def integrador(vc: VdiCrew, inputs: dict, h: dict):
    ruta = _archivo(DOSSIER)
    lider = vc.team_lead()
    if not ruta.exists():
        hubo_ciclo = any(it.get("resultado") == "completada" for it in h["iteraciones"])
        bases = INICIALES if not hubo_ciclo else INICIALES[:-1]
        ctx = _contexto_actual(vc, bases)
        if hubo_ciclo:  # el informe original del cliente (matriz de conformidad) y su ultima verificacion
            ctx += [_previa(CLIENTE, _leer(_archivo(CLIENTE)), lider),
                    _previa(ultima_version(CLIENTE)[1].stem, _leer(ultima_version(CLIENTE)[1]), lider),
                    _previa("historial", _resumen_historial(h), lider)]
        tarea = vc.design_board_integration_task()
        tarea.context = ctx
        _ejecutar(vc, [tarea], inputs)
    return TaskOutput(description="dossier", raw=_leer(ruta), agent=lider.role)


def ejecutar(inputs: dict):
    """Corrida completa. Es idempotente: los pasos con informe ya escrito se saltan (reanudacion)."""
    vc = VdiCrew()
    pasada_inicial(vc, inputs)
    h = _cargar_historial()
    if h.get("fin") is None:
        k = 1
        while k <= MAX_CICLOS:
            if not iteracion(VdiCrew(), inputs, k, h):
                h["fin"] = "sin ECR corregibles"
                break
            k += 1
        else:
            h["fin"] = f"límite de {MAX_CICLOS} iteraciones" if MAX_CICLOS else "ciclo desactivado"
        _guardar_historial(h)
        registrar_evento("ciclo", {"iteraciones": len([i for i in h["iteraciones"] if i.get("resultado") == "completada"]),
                                   "fin": h["fin"], "escaladas": h["escaladas"]})
    return integrador(VdiCrew(), inputs, h)
