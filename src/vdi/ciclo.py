"""Orquestacion de la corrida con ciclo de correccion iterativo (modelo en V de la VDI 2206).

1. Pasada inicial: 01, 01b, 02, 03, 04, 05 y 06 (revision del cliente), en secuencia.
2. Ciclo de correccion, hasta MAX_CICLOS veces:
   a. Triaje (lider de integracion): cada ECR abierta recibe disciplina responsable y tipo. Recibe la asignacion
      de la iteracion anterior para no cambiar de disciplina sin motivo.
   b. Correccion: solo las disciplinas con ECR corregibles, en orden requerimientos (informe 01 del lider),
      sintesis (01b, del lider), mecanica, electronica, software, RAMS. Cada una entrega una version nueva de su
      informe con la tabla "Cambios de la iteracion N". Si declara impacto sobre una disciplina posterior que no
      tenia ECR, esa disciplina revisa el impacto en la misma iteracion; el impacto sobre una anterior lo detecta
      el cliente en la verificacion. Las ECR de nivel sistema (cambio de concepto o de arquitectura) no se
      corrigen en el ciclo.
   c. Verificacion del cliente: estado de cada ECR atendida frente a su criterio y ECR nuevas.
   El ciclo termina si no quedan ECR corregibles, si se alcanza MAX_CICLOS o si todas las corregibles
   restantes ya fallaron dos veces (quedan escaladas para el integrador). Una tarea del ciclo que falla tras
   MAX_INTENTOS_TAREA intentos no tumba la corrida: una correccion fallida deja sus ECR como fallidas y el ciclo
   sigue; un triaje o una verificacion fallidos terminan el ciclo con su motivo y se pasa al integrador.
3. Integrador final: consolida las versiones finales, resuelve conflictos de sistema y ECR escaladas. Un
   guardrail exige que su tabla de cierre diga Cumple exactamente en las ECR que el cliente dejo en Cumple.

Cada paso escribe su informe en RUN_DIR y se salta si el informe ya existe: si algo falla, volver a llamar a
ejecutar() continua desde el primer paso sin informe (asi funciona la reanudacion). El historial queda en
_iteraciones.json.

Modelo de diseno compartido (F1, src/vdi/modelo.py; VDI_MODELO=0 lo apaga): cada tarea corre en su transaccion
(se confirma si la tarea termina bien, se descarta si falla) y recibe en su descripcion la vista del modelo que le
toca. El codigo registra en el modelo la disciplina y el tipo del triaje y los estados de la verificacion, sugiere
al triaje la disciplina duena de lo que afecta cada ECR y calcula el impacto de cada correccion para compararlo con
el declarado. Al reanudar, el modelo se reconstruye con las operaciones de las tareas cuyo informe existe.
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
from .crews.vdicrew.herramientas import ROL_AGENTE, usar_modelo
from .crews.vdicrew.vdicrew import (MARCA_FIN, MAX_REINTENTOS_INCOMPLETO, MODELO_ACTIVO, VdiCrew, con_modelo,
                                    guardrail_informe_completo)
from .crews.vdicrew.vdicrew import _registrar as _registrar_guardrail
from .modelo import Modelo

MAX_CICLOS = int(os.environ.get("VDI_CICLOS", "4"))  # 0 = sin ciclo de correccion (flujo de las etapas anteriores)
MAX_FALLOS_ECR = 2  # una ECR corregible que falla esta cantidad de veces seguidas queda escalada
MAX_INTENTOS_TAREA = 2  # intentos de una tarea del ciclo antes de darla por fallida y seguir sin ella
HISTORIAL = RUN_DIR / "_iteraciones.json"
PASO_ACTUAL = {"tarea": "-"}  # ultima tarea lanzada, para registrar donde fallo una corrida
MODELO = {"m": None}  # modelo de diseno de la corrida (None si VDI_MODELO=0)

# Disciplina -> (agente, informe base, tarea de la pasada inicial). El orden es el de correccion: los informes
# del lider van primero porque sus cambios (requerimientos, QFD, evaluacion de conceptos) bajan a los dominios.
DISCIPLINAS = {
    "requerimientos": ("team_lead", "01_requirements_and_system", "requirements_ingestion_task"),
    "sintesis": ("team_lead", "01b_system_synthesis", "system_synthesis_task"),
    "mecanica": ("mecanico", "02_mechanical_subsystem", "mechanical_subsystem_design_task"),
    "electronica": ("electronico", "03_electronic_subsystem", "electronic_subsystem_design_task"),
    "software": ("software", "04_software_architecture", "software_subsystem_design_task"),
    "rams": ("mantenimiento", "05_rams_maintenance", "maintenance_rams_eval_task"),
}
ORDEN = list(DISCIPLINAS)
INICIALES = ["01_requirements_and_system", "01b_system_synthesis", "02_mechanical_subsystem", "03_electronic_subsystem",
             "04_software_architecture", "05_rams_maintenance", "06_client_acceptance"]
CLIENTE = "06_client_acceptance"
# Pasada inicial: (informe, tarea de tasks.yaml, agente), en orden
PASADA = [(base, clave, agente) for agente, base, clave in DISCIPLINAS.values()] + \
    [(CLIENTE, "client_acceptance_review_task", "cliente")]
DOSSIER = "00_vdi2206_consolidated_dossier"
ID_ECR = re.compile(r"ECR[\s\-‐-―−]*0*(\d+)", re.I)  # el modelo usa guiones tipograficos (U+2011, U+2013...)


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


def leer_cierre(texto: str) -> dict[str, str]:
    """Estados de la tabla de cierre del dossier: entre las tablas con columnas ECR y Estado, la de mas ECR."""
    mejor = {}
    for t in tablas(texto):
        if not (t and any("ecr" in k for k in t[0]) and any("estado" in k for k in t[0])):
            continue
        estados = {}
        for f in t:
            ecr = ecr_id(_valor(f, "ecr"))
            est = _sin_tildes(_valor(f, "estado"))
            if ecr and ecr not in estados:
                estados[ecr] = "no_cumple" if "no cumple" in est else "cumple" if "cumple" in est else "pendiente"
        if len(estados) > len(mejor):
            mejor = estados
    return mejor


def estados_cliente() -> dict[str, str]:
    """Estado de cada ECR en la ultima verificacion del cliente en que aparece (versiones 2 en adelante)."""
    estados = {}
    for v in range(2, ultima_version(CLIENTE)[0] + 1):
        estados.update(leer_verificacion(_leer(_archivo(CLIENTE, v))))
    return estados


def discrepancias_cierre(dossier: str, cliente: dict[str, str]) -> list[str]:
    """ECR en que la tabla de cierre del dossier no respeta el Cumple del cliente (ni de mas ni de menos)."""
    cierre = leer_cierre(dossier)
    if not cierre:
        return ["no hay una tabla de cierre con las columnas ECR y Estado"]
    problemas = []
    for ecr, est in sorted(cliente.items()):
        if ecr not in cierre:
            problemas.append(f"falta la {ecr} en la tabla de cierre")
        elif est == "cumple" and cierre[ecr] != "cumple":
            problemas.append(f"{ecr}: el cliente la dejó en Cumple y el dossier no")
        elif est != "cumple" and cierre[ecr] == "cumple":
            problemas.append(f"{ecr}: el dossier dice Cumple pero el cliente no la dejó en Cumple")
    return problemas


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
        texto = getattr(output, "raw", None) or ""
        problema = validar_formato(texto)
        if problema:  # queda en _guardrail.log, se repita o no
            _registrar_guardrail(tarea, "FORMATO" if fallos["n"] < MAX_REINTENTOS_INCOMPLETO else "ACEPTADO_SIN_FORMATO",
                                 problema, texto)
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


def _vista(rol: str, extra: str = "") -> str:
    m = MODELO["m"]
    return m.vista(rol) + extra if m else ""


def _tarea(vc: VdiCrew, clave: str, agente, salida: Path, contexto: list, reemplazos: dict, validar=None,
           vista: str = "") -> Task:
    cfg = vc.tasks_config[clave]
    desc = cfg["description"]
    for marca, valor in reemplazos.items():
        desc = desc.replace(f"[[{marca}]]", _sin_llaves(str(valor)))
    desc = con_modelo(desc, vista)
    nombre = f"{clave}:{salida.stem}"
    return Task(description=desc, expected_output=cfg["expected_output"], agent=agente, context=contexto,
                output_file=str(salida), name=clave,
                guardrail=_guardrail(nombre, validar) if validar else guardrail_informe_completo(nombre),
                guardrail_max_retries=MAX_REINTENTOS_INCOMPLETO)


def _ejecutar(vc: VdiCrew, tareas: list[Task], inputs: dict) -> None:
    PASO_ACTUAL["tarea"] = ", ".join(Path(t.output_file).stem for t in tareas)
    agentes = list({id(t.agent): t.agent for t in tareas}.values())
    Crew(agents=agentes, tasks=tareas, process=Process.sequential, memory=False, verbose=True).kickoff(inputs=inputs)


def _correr(vc: VdiCrew, tarea: Task, inputs: dict, iteracion: int = 0, despues=None) -> None:
    """Ejecuta una tarea dentro de su transaccion del modelo: si termina bien, despues(m) registra lo que el codigo
    extrae de su informe y se confirma; si falla, sus cambios se descartan."""
    m = MODELO["m"]
    if m:
        m.iniciar_tarea(Path(tarea.output_file).stem, iteracion)
    try:
        _ejecutar(vc, [tarea], inputs)
    except Exception:
        if m:
            m.descartar()
        raise
    if m:
        if despues:
            despues(m)
        m.confirmar()


def _intentar(crear_tarea, vc: VdiCrew, inputs: dict, iteracion: int = 0, despues=None) -> str | None:
    """Ejecuta una tarea del ciclo hasta MAX_INTENTOS_TAREA veces (cada intento con una tarea nueva). Devuelve None
    si salio bien o el motivo del ultimo fallo: el ciclo sigue sin esa tarea en vez de tumbar la corrida, porque
    repetirla igual en una reanudacion falla igual (job 23662: correccion de una ECR que era un problema de concepto)."""
    motivo = None
    for intento in range(1, MAX_INTENTOS_TAREA + 1):
        try:
            _correr(vc, crear_tarea(), inputs, iteracion, despues)
            return None
        except Exception as error:  # el fallo queda en el historial y en _guardrail.log
            motivo = f"{type(error).__name__}: {' '.join(str(error).split())[:300]}"
            _registrar_guardrail(PASO_ACTUAL["tarea"], "TAREA_FALLIDA", f"intento {intento}: {motivo}", "")
    return motivo


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

def _registrar_codigo(m: Modelo, columnas: list[str], filas: list[list[str]], rol: str, registro: dict) -> None:
    """El codigo registra campos de ECR en el modelo con el rol indicado; un rechazo queda en el historial."""
    if not filas:
        return
    tabla = "\n".join(["| ID | " + " | ".join(columnas) + " |", "|" + "---|" * (len(columnas) + 1)] +
                      ["| " + " | ".join(f) + " |" for f in filas])
    resultado = m.registrar("ecr", tabla, rol)
    if not resultado.startswith("Aplicadas"):
        registro.setdefault("modelo_rechazos", []).append(" ".join(resultado.split())[:600])


def pasada_inicial(vc: VdiCrew, inputs: dict) -> None:
    """Tareas 01 a 06 en secuencia, una por una: cada una en su transaccion del modelo y con la vista que dejaron
    las anteriores. Las que ya tienen informe no se repiten y aportan su texto como contexto."""
    hechas = []
    for base, clave, nombre in PASADA:
        ruta = _archivo(base)
        agente = getattr(vc, nombre)()
        if not ruta.exists():
            cfg = vc.tasks_config[clave]
            tarea = Task(description=con_modelo(cfg["description"], _vista(ROL_AGENTE[nombre])),
                         expected_output=cfg["expected_output"], agent=agente, context=list(hechas),
                         output_file=str(ruta), name=clave, guardrail=guardrail_informe_completo(clave),
                         guardrail_max_retries=MAX_REINTENTOS_INCOMPLETO)
            _correr(vc, tarea, inputs)
        hechas.append(_previa(base, _leer(ruta), agente))


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
        previa = next((it for it in h["iteraciones"] if it["iteracion"] == k - 1), {})
        anterior = "; ".join(f"{f['ecr']}: {f['disciplina']} ({f['tipo']})" for f in previa.get("triaje", [])) \
            or "ninguna (primera iteración)"
        def registrar_triaje(m):  # disciplina y tipo de cada ECR, desde la tabla del lider
            filas = [f for f in leer_triaje(_leer(ruta_triaje)) if f["tipo"] != "cumple"]
            registro["ecr_fuera_del_modelo"] = sorted({f["ecr"] for f in filas if f["ecr"] not in m.elementos})
            _registrar_codigo(m, ["Disciplina", "Tipo"], [[f["ecr"], f["disciplina"], f["tipo"]] for f in filas
                                                          if f["ecr"] in m.elementos], "lider", registro)

        sugerencias = MODELO["m"].sugerencias_triaje() if MODELO["m"] else ""
        fallo = _intentar(lambda: _tarea(vc, "ecr_triage_task", lider, ruta_triaje, ctx,
                                         {"ITERACION": k, "ESCALADAS": escaladas, "TRIAJE_ANTERIOR": anterior},
                                         lambda t: None if leer_triaje(t) else "falta la tabla | ECR | Disciplina | Tipo | Justificación |",
                                         _vista("lider", "\n\nDisciplina sugerida por el modelo (dueña de los elementos que "
                                                         "afecta cada ECR; decides tú):\n" + sugerencias)),
                          vc, inputs, k, registrar_triaje)
        if fallo:
            registro["resultado"] = "triaje fallido"
            registro["fallo"] = fallo
            h["fin"] = f"triaje fallido en la iteración {k}"
            _guardar_historial(h)
            return False
    triaje = leer_triaje(_leer(ruta_triaje))
    registro["triaje"] = triaje
    asignadas = {d: [f for f in triaje if f["disciplina"] == d and f["tipo"] == "corregible" and f["ecr"] not in h["escaladas"]]
                 for d in ORDEN}
    registro["escaladas_sistema"] = [f["ecr"] for f in triaje if f["disciplina"] == "sistema"]
    if not any(asignadas.values()):
        registro["resultado"] = "sin ECR corregibles"
        _guardar_historial(h)
        return False

    # b. Correcciones, en orden; los impactos declarados sobre disciplinas posteriores se revisan en esta iteracion.
    # Una correccion que falla tras sus intentos no tumba la corrida: su informe queda en la version anterior y sus
    # ECR cuentan como fallidas (a la segunda falla quedan escaladas para el integrador).
    cambios_previos, impactadas, ejecutadas, calculados = [], {}, [], {}
    fallidas_correccion = registro.setdefault("correcciones_fallidas", {})
    for d in ORDEN:
        ecrs, motivos = asignadas[d], impactadas.get(d, [])
        if not ecrs and not motivos:
            continue
        agente_nombre, base, _ = DISCIPLINAS[d]
        v, ruta_anterior = ultima_version(base)
        ruta_nueva = _archivo(base, k + 1)
        if not ruta_nueva.exists() and d not in fallidas_correccion:
            texto_ecr = "\n".join(f"- {f['ecr']}: {f['justificacion']}" for f in ecrs) or "- Ninguna ECR directa."
            if motivos:
                texto_ecr += "\n- Revisión de impacto: otra disciplina hizo cambios que afectan tu dominio: " + " ".join(motivos)
            agente = getattr(vc, agente_nombre)()
            # la sintesis es el contexto comun; si es el informe que se corrige, entra una sola vez
            comun = _contexto_actual(vc, ["01b_system_synthesis"]) if base != "01b_system_synthesis" else []
            ctx = comun + [_previa(ruta_anterior.stem, _leer(ruta_anterior), agente),
                           _previa(ruta_cliente.stem, _leer(ruta_cliente), agente)]
            reemplazos = {"ITERACION": k, "DOMINIO": d, "ECR_ASIGNADAS": texto_ecr,
                          "CAMBIOS_PREVIOS": "\n\n".join(cambios_previos) or "Ninguno."}
            rol = "lider" if d in ("requerimientos", "sintesis") else d
            fallo = _intentar(lambda: _tarea(vc, "domain_correction_task", agente, ruta_nueva, ctx, reemplazos,
                                             lambda t: None if seccion_cambios(t, k) and _tabla_con(seccion_cambios(t, k), "ecr", "impacto")
                                             else f"falta la sección '## Cambios de la iteración {k}' con su tabla de cambios",
                                             _vista(rol)),
                              vc, inputs, k, lambda m: calculados.__setitem__(d, m.impacto_pendiente(d)))
            if fallo:
                fallidas_correccion[d] = {"ecr": [f["ecr"] for f in ecrs], "motivo": fallo}
                _guardar_historial(h)
        if d in fallidas_correccion:
            continue
        cambios = seccion_cambios(_leer(ruta_nueva), k)
        cambios_previos.append(f"Disciplina {d}:\n{cambios}")
        ejecutadas.append({"disciplina": d, "ecr": [f["ecr"] for f in ecrs], "revision_impacto": bool(motivos),
                           "informe": ruta_nueva.name, "impacto_declarado": sorted(impactos(cambios) - {d}),
                           "impacto_calculado": calculados.get(d)})  # None sin modelo o si la tarea venia de antes
        for otra in impactos(cambios) - {d}:
            if ORDEN.index(otra) > ORDEN.index(d):
                impactadas.setdefault(otra, []).append(f"(desde {d}) {' '.join(cambios.split())[:1500]}")
            else:
                registro.setdefault("impactos_hacia_atras", []).append({"desde": d, "hacia": otra})
    registro["ejecutadas"] = ejecutadas

    # c. Verificacion del cliente, solo de lo que se corrigio; sin correcciones no hay nada que verificar
    ruta_verif = _archivo(CLIENTE, v_cliente + 1)
    no_corregidas = [e for f in fallidas_correccion.values() for e in f["ecr"]]
    atendidas = [f["ecr"] for e in asignadas.values() for f in e if f["ecr"] not in no_corregidas]
    if ejecutadas and not ruta_verif.exists():
        cliente = vc.cliente()
        ctx = [_previa(ruta_cliente.stem, _leer(ruta_cliente), cliente)] + [
            _previa(e["informe"], _leer(RUN_DIR / e["informe"]), cliente) for e in ejecutadas]
        def registrar_estados(m):  # estado de cada ECR, desde la tabla de verificacion del cliente
            estados_v = leer_verificacion(_leer(ruta_verif))
            registro["ecr_fuera_del_modelo_verificacion"] = sorted(e for e in estados_v if e not in m.elementos)
            _registrar_codigo(m, ["Estado"], [[e, est] for e, est in sorted(estados_v.items()) if e in m.elementos],
                              "cliente", registro)

        fallo = _intentar(lambda: _tarea(vc, "client_verification_task", cliente, ruta_verif, ctx,
                                         {"ITERACION": k, "ECR_ITERACION": ", ".join(atendidas)},
                                         lambda t: None if leer_verificacion(t) else "falta la tabla | ECR | Criterio de aceptación | Estado | Evidencia |",
                                         _vista("cliente")),
                          vc, inputs, k, registrar_estados)
        if fallo:  # las correcciones quedan sin verificar: el integrador solo ve el estado anterior del cliente
            registro["resultado"] = "verificación fallida"
            registro["fallo"] = fallo
            h["fin"] = f"verificación fallida en la iteración {k}"
            _guardar_historial(h)
            return False
    estados = leer_verificacion(_leer(ruta_verif)) if ruta_verif.exists() else {}
    registro["verificacion"] = estados
    registro["cerradas"] = [e for e in atendidas if estados.get(e) in ("cumple", "pendiente")]
    registro["fallidas"] = [e for e in atendidas if estados.get(e) == "no_cumple"] + no_corregidas
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
    sin_corregir = [f"{d} en la iteración {it['iteracion']} ({', '.join(f['ecr'])})"
                    for it in h["iteraciones"] for d, f in it.get("correcciones_fallidas", {}).items()]
    if sin_corregir:
        lineas += ["", "Correcciones que fallaron (la disciplina no pudo entregar su informe; sus ECR cuentan como fallidas "
                       "y pueden indicar un problema de concepto): " + "; ".join(sin_corregir) + "."]
    lineas += ["", f"ECR escaladas por fallar {MAX_FALLOS_ECR} veces: {', '.join(h['escaladas']) or 'ninguna'}.",
               f"ECR de nivel sistema (concepto o arquitectura), no enviadas a las disciplinas: {', '.join(sistema) or 'ninguna'}. "
               "Si resuelves alguna con una decisión tuya, queda en Pendiente de prueba: el cliente no la verificó.",
               f"Motivo de fin del ciclo: {h.get('fin') or 'sin ciclo'}."]
    return "\n".join(lineas)


def integrador(vc: VdiCrew, inputs: dict, h: dict):
    ruta = _archivo(DOSSIER)
    lider = vc.team_lead()
    hubo_ciclo = any(it.get("resultado") == "completada" for it in h["iteraciones"])
    cliente = estados_cliente() if hubo_ciclo else {}
    if not ruta.exists():
        bases = INICIALES if not hubo_ciclo else INICIALES[:-1]
        ctx = _contexto_actual(vc, bases)
        base = vc.design_board_integration_task()
        # la misma tarea con la vista del modelo (o sin la marca, si el modelo esta apagado)
        tarea = Task(description=con_modelo(vc.tasks_config["design_board_integration_task"]["description"],
                                            _vista("integrador")),
                     expected_output=base.expected_output, agent=base.agent, output_file=base.output_file,
                     name="design_board_integration_task", guardrail=base.guardrail,
                     guardrail_max_retries=MAX_REINTENTOS_INCOMPLETO)
        if hubo_ciclo:  # el informe original del cliente (matriz de conformidad) y su ultima verificacion
            ctx += [_previa(CLIENTE, _leer(_archivo(CLIENTE)), lider),
                    _previa(ultima_version(CLIENTE)[1].stem, _leer(ultima_version(CLIENTE)[1]), lider),
                    _previa("historial", _resumen_historial(h), lider)]
            # mismo encargo, con un guardrail que exige respetar el Cumple del cliente en la tabla de cierre
            validar = lambda t: "; ".join(discrepancias_cierre(t, cliente)) or None  # noqa: E731
            tarea = Task(description=tarea.description, expected_output=tarea.expected_output, agent=tarea.agent,
                         output_file=tarea.output_file, name="design_board_integration_task",
                         guardrail=_guardrail("design_board_integration_task", validar),
                         guardrail_max_retries=MAX_REINTENTOS_INCOMPLETO)
        tarea.context = ctx
        m = MODELO["m"]
        if m:  # el integrador solo consulta: lo que registre se descarta
            m.iniciar_tarea(DOSSIER)
        try:
            _ejecutar(vc, [tarea], inputs)
        finally:
            if m:
                m.descartar()
    if hubo_ciclo:  # lo que quede sin respetar tras los reintentos, para el analisis
        h["discrepancias_cierre"] = discrepancias_cierre(_leer(ruta), cliente)
        _guardar_historial(h)
    return TaskOutput(description="dossier", raw=_leer(ruta), agent=lider.role)


def ejecutar(inputs: dict):
    """Corrida completa. Es idempotente: los pasos con informe ya escrito se saltan (reanudacion)."""
    if MODELO_ACTIVO:
        m = Modelo(RUN_DIR)
        m.reconstruir({p.stem for p in RUN_DIR.glob("*.md")})
        MODELO["m"] = m
        usar_modelo(m)
    vc = VdiCrew()
    pasada_inicial(vc, inputs)
    h = _cargar_historial()
    if MODELO["m"] and "comprobaciones_iniciales" not in h:  # K-01 a K-10 al terminar la pasada inicial
        h["comprobaciones_iniciales"] = [f"{k}: {msg}" for k, msg in MODELO["m"].comprobar()]
        _guardar_historial(h)
    if h.get("fin") is None:
        k = 1
        while k <= MAX_CICLOS:
            if not iteracion(VdiCrew(), inputs, k, h):
                h["fin"] = h.get("fin") or "sin ECR corregibles"  # triaje o verificacion fallidos fijan su motivo
                break
            k += 1
        else:
            h["fin"] = f"límite de {MAX_CICLOS} iteraciones" if MAX_CICLOS else "ciclo desactivado"
        _guardar_historial(h)
        registrar_evento("ciclo", {"iteraciones": len([i for i in h["iteraciones"] if i.get("resultado") == "completada"]),
                                   "fin": h["fin"], "escaladas": h["escaladas"]})
    salida = integrador(VdiCrew(), inputs, h)
    if MODELO["m"]:
        h["comprobaciones_finales"] = [f"{k}: {msg}" for k, msg in MODELO["m"].comprobar()]
        _guardar_historial(h)
        MODELO["m"].exportar_rdf()
    return salida
