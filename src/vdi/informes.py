"""Informes, dossier e historial generados desde el modelo de diseno (F2).

El agente escribe el analisis en prosa; las tablas, los totales, las matrices derivadas, el cierre de ECR y el
historial los arma este modulo desde el modelo y su registro de operaciones (_modelo_ops.jsonl). Asi el informe
y el modelo no pueden divergir.

Prueba sin LLM sobre una corrida existente (escribe en <salida>, por defecto <corrida>/_f2/):
    uv run python -m vdi.informes <carpeta de la corrida> [salida]
"""

import json
import re
from pathlib import Path

from vdi.modelo import CLASES, EVIDENCIA, Elemento, Modelo, _orden_id, ids_en_texto

ORDEN_CLASES = ("sistema", "externo", "necesidad", "requerimiento", "funcion", "modulo", "interfaz", "componente",
                "parametro", "decision", "modo_falla", "ecr")
TITULO_CLASE = {"sistema": "Sistema", "externo": "Elementos externos", "necesidad": "Necesidades",
                "requerimiento": "Requerimientos", "funcion": "Funciones", "modulo": "Módulos",
                "interfaz": "Interfaces", "componente": "Componentes", "parametro": "Parámetros", "ecr": "ECR",
                "decision": "Decisiones", "modo_falla": "Modos de falla"}
# encabezado de cada campo en las tablas generadas (el primero de CLASES no siempre se lee bien)
ETIQUETA = {"texto": "Descripción", "deriva_de": "Deriva de", "verificacion": "Verificación", "limita": "Limita",
            "padre": "Padre", "modulo": "Módulo", "dominios": "Dominios", "presupuesto_masa": "Presupuesto de masa",
            "presupuesto_costo": "Presupuesto de costo", "presupuesto_tiempo": "Presupuesto de tiempo",
            "masa_unitaria": "Masa unitaria", "costo_unitario": "Costo unitario", "modelo": "Modelo candidato",
            "extremo_a": "Extremo A", "extremo_b": "Extremo B", "protocolo": "Protocolo o niveles",
            "descripcion": "Descripción", "criterio": "Criterio de aceptación", "afecta": "Afecta",
            "mide": "Mide", "determina": "Contribuye a", "justificacion": "Justificación",
            "motivada_por": "Motivada por", "s": "S", "o": "O", "d": "D", "mitigacion": "Acción recomendada"}
TITULO_INFORME = {"01_requirements_and_system": "Fase 1: requerimientos y caja negra",
                  "01b_system_synthesis": "Síntesis del sistema", "02_mechanical_subsystem": "Dominio mecánico",
                  "03_electronic_subsystem": "Dominio electrónico", "04_software_architecture": "Arquitectura de software",
                  "05_rams_maintenance": "RAMS y mantenimiento", "06_client_acceptance": "Revisión del cliente"}
ESTADO_ECR = {"abierta": "Abierta", "cumple": "Cumple", "no_cumple": "No cumple", "pendiente": "Pendiente de prueba"}


# ---------- utilidades ----------

def _celda(v) -> str:
    if v in (None, "", []):
        return "—"
    if isinstance(v, list):
        return ", ".join(f"{x:g}" if isinstance(x, float) else str(x) for x in v) if not all(
            isinstance(x, (int, float)) for x in v) else "–".join(f"{x:g}" for x in v)
    if isinstance(v, float):
        return f"{v:g}"
    return str(v).replace("|", "/").replace("\n", " ")


def _etiqueta(campo: str) -> str:
    return ETIQUETA.get(campo) or campo.replace("_", " ").capitalize()


def tabla(filas: list[list[str]], encabezado: list[str]) -> str:
    if not filas:
        return "_Ninguno._"
    lineas = ["| " + " | ".join(encabezado) + " |", "|" + "---|" * len(encabezado)]
    lineas += ["| " + " | ".join(_celda(c) for c in f) + " |" for f in filas]
    return "\n".join(lineas)


def tabla_clase(elementos: list[Elemento], clase: str) -> str:
    """Tabla de una clase con las columnas del esquema que tengan algun valor (sin el campo iteracion)."""
    campos = [c for c in CLASES[clase] if any(e.campos.get(c) not in (None, "", []) for e in elementos)]
    return tabla([[e.id] + [e.campos.get(c) for c in campos] for e in elementos], ["ID"] + [_etiqueta(c) for c in campos])


def leer_ops(carpeta: Path) -> list[dict]:
    """Operaciones del registro, cada una con 'antes': los campos que de verdad cambio y su valor anterior (el
    agente suele reenviar la fila completa al modificar)."""
    ruta = Path(carpeta) / "_modelo_ops.jsonl"
    if not ruta.exists():
        return []
    ops = [json.loads(l) for l in ruta.read_text(encoding="utf-8").split("\n") if l.strip()]
    m = Modelo(carpeta)
    for op in ops:
        previo = m.elementos[op["id"]].campos if op["id"] in m.elementos else {}
        op["antes"] = {k: previo.get(k) for k, v in op["campos"].items() if k != "iteracion" and previo.get(k) != v}
        m._aplicar(op)
    return ops


def modelo_hasta(carpeta: Path, tarea: str | None = None) -> Modelo:
    """El modelo tal como quedo al terminar la tarea indicada (o al final, sin tarea), reproduciendo el registro."""
    m = Modelo(carpeta)
    ops = leer_ops(carpeta)
    ultima = max((k for k, op in enumerate(ops) if op["tarea"] == tarea), default=len(ops) - 1) if tarea else len(ops) - 1
    for op in ops[:ultima + 1]:
        m._aplicar(op)
    return m


def _titulo(base: str) -> str:
    m = re.match(r"(.+?)(?:_v(\d+))?$", base)
    raiz, v = m.group(1), m.group(2)
    if raiz.startswith("07_triaje_it"):
        return f"Triaje de la iteración {raiz.rsplit('it', 1)[1]}"
    t = TITULO_INFORME.get(raiz, raiz)
    if v:
        return f"{'Verificación del cliente' if raiz == '06_client_acceptance' else t}, iteración {int(v) - 1}"
    return t


# ---------- secciones comunes ----------

def seccion_requerimientos(m: Modelo) -> str:
    """Requerimientos con su portador y su cumplimiento calculado (parametros que lo miden y totales con Limita)."""
    filas = []
    for r in m.de_clase("requerimiento"):
        portadores = [c.id for c in m.de_clase("componente") if r.id in c.campos.get("satisface", [])]
        portadores += [f"{p.id} (mide)" for p in m.de_clase("parametro") if r.id in p.campos.get("mide", [])]
        portadores += [p.id for p in m.de_clase("parametro") if r.id in p.campos.get("determina", [])]
        estado = []
        for p in m.de_clase("parametro"):
            if r.id in p.campos.get("mide", []) and not isinstance(p.campos.get("valor"), list):
                res = m.cumple(p.campos.get("valor"), p.campos.get("unidad", ""), r)
                if res:
                    estado.append(f"{p.id} {'cumple' if res[0] else 'NO cumple'} ({res[1]:.0%})")
        dim = r.campos.get("limita")
        if dim:
            total = m.total_sistema(dim)
            res = m.cumple(total, "kg" if dim == "masa" else "USD", r)
            if res:
                estado.append(f"total {total:.4g} {'cumple' if res[0] else 'NO cumple'} ({res[1]:.0%})")
        if dim:
            portadores.append(f"total de {dim}")
        unidad = r.campos.get("unidad", "") if r.campos.get("unidad") not in ("—", "-") else ""
        meta = f"{r.campos.get('comparador', '')} {_celda(r.campos.get('valor'))} {unidad}".strip()
        if not estado and m.cuantitativo(r):
            estado.append("sin parámetro que lo mida")
        elif not estado:
            estado.append("cualitativo: se cierra con su verificación")
        filas.append([r.id, r.campos.get("texto"), meta, r.campos.get("verificacion"), r.campos.get("deriva_de"),
                      ", ".join(portadores) or "sin portador", "; ".join(estado)])
    return tabla(filas, ["ID", "Requerimiento", "Meta", "Verificación", "Deriva de", "Portadores", "Cumplimiento calculado"])


def seccion_matriz_diseno(m: Modelo) -> str:
    """Matriz requerimientos x parametros desde mide y determina (QFD etapa 2 y diseno axiomatico)."""
    params = [p for p in m.de_clase("parametro") if p.campos.get("determina") or p.campos.get("mide")]
    liga = {p.id: p.campos.get("mide", []) + p.campos.get("determina", []) for p in params}
    reqs = [r for r in m.de_clase("requerimiento") if any(r.id in v for v in liga.values())]
    if not reqs:
        return "_Ningún parámetro mide ni determina un requerimiento._"
    # una fila por requerimiento (la matriz completa no cabe con 30-40 parametros)
    filas = [[r.id, r.campos.get("texto"),
              ", ".join(p.id for p in params if r.id in p.campos.get("mide", [])),
              ", ".join(p.id for p in params if r.id in p.campos.get("determina", []))] for r in reqs]
    compartidos = [f"{i} ({', '.join(v)})" for i, v in liga.items() if len(set(v)) > 1]
    nota = ("\n\nParámetros ligados a más de un requerimiento (acoplamiento a revisar): " + "; ".join(compartidos) + "."
            if compartidos else "\n\nCada parámetro está ligado a un solo requerimiento.")
    return tabla(filas, ["Requerimiento", "Descripción", "Lo mide", "Contribuyen"]) + nota


def seccion_decisiones(m: Modelo) -> str:
    """Decisiones vigentes (una fila IBIS/QOC cada una) y las cadenas de reemplazo."""
    filas = [[d.id, d.campos.get("asunto"), d.campos.get("elegida"), d.campos.get("alternativas"), d.campos.get("criterios"),
              d.campos.get("justificacion"), d.campos.get("evidencia"), d.campos.get("justifica"),
              d.campos.get("motivada_por")] for d in m.vigentes()]
    s = tabla(filas, ["ID", "Asunto", "Elegida", "Alternativas", "Criterios", "Justificación", "Evidencia", "Justifica",
                      "Motivada por"])
    viejas = [d for d in m.de_clase("decision") if m.reemplazada_por(d.id)]
    if viejas:
        s += "\n\nDecisiones reemplazadas:\n\n" + "\n".join(
            f"- {d.id} ({d.campos.get('asunto', '')}: {d.campos.get('elegida', '')}) → reemplazada por {m.reemplazada_por(d.id)}"
            for d in viejas)
    return s


def seccion_fmea(m: Modelo) -> str:
    mfs = sorted(m.de_clase("modo_falla"), key=lambda f: -(m.rpn(f) or 0))
    return tabla([[f.id, f.campos.get("elemento"), f.campos.get("modo"), f.campos.get("efecto"), f.campos.get("causa"),
                   f.campos.get("s"), f.campos.get("o"), f.campos.get("d"), m.rpn(f), f.campos.get("mitigacion")] for f in mfs],
                 ["ID", "Elemento", "Modo de falla", "Efecto", "Causa", "S", "O", "D", "RPN", "Acción recomendada"])


def _contenido_evidencia(e: Elemento) -> str:
    c = e.campos
    if e.clase == "calculo":
        return f"{c.get('expresion', '')} = {c.get('resultado', '')}"
    if e.clase == "fuente":
        extracto = " ".join(str(c.get("extracto", "")).split())
        extracto = extracto if len(extracto) <= 200 else extracto[:199] + "…"
        origen = ", ".join(x for x in (c.get("documento"), c.get("ubicacion")) if x)
        return f"{c.get('titulo', '') + ' — ' if c.get('titulo') else ''}{origen}" + (f": «{extracto}»" if extracto else "")
    # matriz: el orden que dio (filas Puesto | Elemento | Total del resultado) en vez de la tabla aplanada
    orden = [[x.strip() for x in l.strip("|").split("|")] for l in str(c.get("resultado", "")).split("\n")
             if re.match(r"^\|\s*\d+\s*\|", l.strip())]
    if orden:
        return f"{c.get('tipo', '')}: " + "; ".join(f"{f[0]}. {f[1]} ({f[2]})" for f in orden if len(f) >= 3)
    return f"{c.get('tipo', '')}: {' '.join(str(c.get('resultado', '')).split())}"


def seccion_evidencia(m: Modelo, ids: list[str] | None = None) -> str:
    """Calculos, fuentes y matrices (todos, o los indicados) y quien los cita."""
    es = [e for e in m.elementos.values() if e.clase in EVIDENCIA and (ids is None or e.id in ids)]
    es.sort(key=lambda e: _orden_id(e.id))
    citan = {e.id: [x.id for x in m.elementos.values()
                    if e.id in x.campos.get("evidencia", []) or e.id in ids_en_texto(str(x.campos.get("fuente", "")))]
             for e in es}
    return tabla([[e.id, _contenido_evidencia(e), citan[e.id]] for e in es], ["ID", "Contenido", "Citada por"])


def seccion_bom(m: Modelo) -> str:
    partes = []
    for mod in m.de_clase("modulo"):
        cs = [c for c in m.de_clase("componente") if c.campos.get("modulo") == mod.id]
        if not cs:
            continue
        filas = [[c.id, c.campos.get("nombre"), c.campos.get("dominio"), c.campos.get("cantidad"),
                  c.campos.get("masa_unitaria"), c.campos.get("costo_unitario"), c.campos.get("modelo"),
                  c.campos.get("nivel")] for c in cs]
        partes.append(f"**{mod.id} {mod.campos.get('nombre', '')}**\n\n" +
                      tabla(filas, ["ID", "Componente", "Dominio", "Cant.", "Masa unitaria", "Costo unitario",
                                    "Modelo candidato", "Nivel"]))
    partes.append("**Totales calculados por el código**\n\n" + m.totales_texto())
    return "\n\n".join(partes)


def seccion_supuestos(m: Modelo) -> str:
    es = [e for e in m.elementos.values() if e.clase in ("componente", "parametro")
          and (e.campos.get("nivel") == "N1" or "supuesto" in str(e.campos.get("fuente", "")).lower())]
    es.sort(key=lambda e: _orden_id(e.id))
    filas = [[e.id, e.campos.get("nombre"), e.campos.get("valor") if e.clase == "parametro" else e.campos.get("modelo"),
              e.campos.get("unidad") if e.clase == "parametro" else "", e.campos.get("fuente")] for e in es]
    return tabla(filas, ["ID", "Nombre", "Valor o modelo", "Unidad", "Fuente"])


def seccion_comprobaciones(m: Modelo) -> str:
    avisos = m.comprobar()
    return "\n".join(f"- {k}: {msg}" for k, msg in avisos) or "- Sin avisos."


def seccion_cierre_ecr(m: Modelo, ops: list[dict]) -> str:
    """Estado de cada ECR tal como lo dejo el cliente: el codigo lo copia, nadie lo reescribe."""
    filas = []
    for e in m.de_clase("ecr"):
        citas = sorted({op["tarea"] for op in ops if op.get("ecr") == e.id})
        filas.append([e.id, e.campos.get("descripcion"), e.campos.get("criterio"), e.campos.get("disciplina"),
                      e.campos.get("tipo"), ESTADO_ECR.get(e.campos.get("estado"), e.campos.get("estado")),
                      ", ".join(citas) or "—"])
    return tabla(filas, ["ECR", "Hallazgo", "Criterio de aceptación", "Disciplina", "Tipo", "Estado (cliente)",
                         "Cambios en"])


def cambios_de_tarea(ops: list[dict], tarea: str) -> str:
    """Tabla de cambios de una tarea del ciclo, desde el registro (cada cambio lleva su ECR): solo los campos que
    cambiaron, con su valor anterior."""
    filas = []
    for op in ops:
        if op["tarea"] != tarea or op["clase"] in ("ecr", *EVIDENCIA):
            continue
        if op["accion"] == "crear":
            detalle = "; ".join(f"{_etiqueta(k)}: {_celda(v)}" for k, v in op["campos"].items() if k != "iteracion")
        else:
            detalle = "; ".join(f"{_etiqueta(k)}: {_celda(a)} → {_celda(op['campos'].get(k))}"
                                for k, a in op["antes"].items()) or "sin cambio de valores"
        filas.append([op["id"], op["accion"], detalle, op.get("ecr")])
    return tabla(filas, ["Elemento", "Acción", "Cambio", "ECR"])


# ---------- documentos ----------

def informe(carpeta: Path, tarea: str, analisis: str) -> str:
    """Informe de una tarea: analisis del agente, datos que registro (o sus cambios, en el ciclo) y comprobaciones."""
    m = modelo_hasta(carpeta, tarea)
    ops = leer_ops(carpeta)
    propias = [op for op in ops if op["tarea"] == tarea]
    partes = [f"# {_titulo(tarea)}", "## Análisis", analisis.strip() or "_Sin análisis._"]
    if any(op.get("ecr") for op in propias):
        partes += ["## Cambios registrados en el modelo", cambios_de_tarea(ops, tarea)]
    else:
        ids = list(dict.fromkeys(op["id"] for op in propias))
        datos = []
        for clase in ORDEN_CLASES:
            es = [m.elementos[i] for i in ids if i in m.elementos and m.elementos[i].clase == clase]
            if es:
                datos.append(f"### {TITULO_CLASE[clase]}\n\n" + tabla_clase(sorted(es, key=lambda e: _orden_id(e.id)), clase))
        if datos:
            partes += ["## Datos registrados en el modelo"] + datos
    if any(op["clase"] == "componente" for op in propias):
        partes += ["## Totales calculados por el código", m.totales_texto()]
    evidencia = [op["id"] for op in propias if op["clase"] in EVIDENCIA]
    if evidencia:
        partes += ["## Evidencia de la tarea (cálculos, fuentes citadas y matrices)", seccion_evidencia(m, evidencia)]
    partes += ["## Comprobaciones automáticas tras la tarea", seccion_comprobaciones(m)]
    return "\n\n".join(partes) + "\n"


def dossier(carpeta: Path, textos: dict[str, str], titulo: str = "Dossier de diseño conceptual (VDI/VDE 2206)") -> str:
    """Dossier generado desde el modelo final. textos: lo que escribe el integrador (resumen, decisiones de integracion,
    cambios recomendados); lo demas sale del modelo y del registro."""
    m = modelo_hasta(carpeta)
    ops = leer_ops(carpeta)
    sis = m.elementos.get("SIS")
    externos = {x.id for x in m.de_clase("externo")} | {"SIS"}
    caja = [i for i in m.de_clase("interfaz") if {i.campos.get("extremo_a"), i.campos.get("extremo_b")} & externos]
    s = [f"# {titulo}",
         "## 0. Resumen ejecutivo", textos.get("resumen", "").strip() or "_Sin resumen del integrador._",
         "## 1. Concepto y caja negra",
         (f"**Concepto:** {sis.campos.get('concepto', '')}\n\n**Principio de solución:** {sis.campos.get('principio', '—')}"
          if sis else "_El sistema no está registrado en el modelo._"),
         "### 1.1 Elementos externos", tabla_clase(m.de_clase("externo"), "externo"),
         "### 1.2 Interfaces con el exterior", tabla_clase(caja, "interfaz"),
         "## 2. Necesidades y requerimientos",
         "### 2.1 Necesidades", tabla_clase(m.de_clase("necesidad"), "necesidad"),
         "### 2.2 Requerimientos, portadores y cumplimiento", seccion_requerimientos(m),
         "## 3. Arquitectura del producto",
         "### 3.1 Funciones", tabla_clase(m.de_clase("funcion"), "funcion"),
         "### 3.2 Módulos", tabla_clase(m.de_clase("modulo"), "modulo"),
         "### 3.3 Interfaces internas", tabla_clase([i for i in m.de_clase("interfaz") if i not in caja], "interfaz"),
         "## 4. Matriz de diseño (requerimientos × parámetros)", seccion_matriz_diseno(m),
         "## 5. Parámetros de diseño", tabla_clase(m.de_clase("parametro"), "parametro"),
         "## 6. Lista de materiales y presupuestos", seccion_bom(m),
         "## 7. Decisiones",
         "### 7.1 Decisiones de diseño (vigentes)", seccion_decisiones(m),
         "### 7.2 Decisiones de integración", textos.get("decisiones", "").strip() or "_Ninguna._",
         "## 8. Modos de falla (FMEA)", seccion_fmea(m),
         "## 9. Cierre de ECR (estado que dejó el cliente)", seccion_cierre_ecr(m, ops),
         "## 10. Cambios recomendados (no aplicados al diseño)", textos.get("recomendados", "").strip() or "_Ninguno._",
         "## 11. Supuestos y evidencia",
         "### 11.1 Supuestos (nivel N1)", seccion_supuestos(m),
         "### 11.2 Evidencia (cálculos, fuentes citadas y matrices)", seccion_evidencia(m),
         "## 12. Comprobaciones automáticas finales", seccion_comprobaciones(m)]
    return "\n\n".join(s) + "\n"


def historial(carpeta: Path, h: dict | None = None) -> str:
    """Historia de cada ECR (apertura, triaje, cambios, verificaciones) desde el registro, y resumen del ciclo."""
    ops = leer_ops(carpeta)
    m = modelo_hasta(carpeta)
    partes = ["# Historial del diseño"]
    if h:
        lineas = [f"Ciclo: {len([i for i in h.get('iteraciones', []) if i.get('resultado') == 'completada'])} "
                  f"iteraciones completadas de {h.get('max_ciclos')}; fin: {h.get('fin', '—')}; "
                  f"escaladas: {', '.join(h.get('escaladas', [])) or 'ninguna'}.", ""]
        for it in h.get("iteraciones", []):
            fallidas = it.get("correcciones_fallidas") or {}
            lineas.append(f"- Iteración {it['iteracion']}: {it.get('resultado', '—')}; corrigieron "
                          f"{', '.join(e['disciplina'] for e in it.get('ejecutadas', [])) or 'nadie'}"
                          + (f"; fallaron {', '.join(fallidas)}" if fallidas else ""))
        partes.append("\n".join(lineas))
    for e in m.de_clase("ecr"):
        lineas = []
        for op in ops:
            if op["id"] == e.id and op["clase"] == "ecr":
                c = op["campos"]
                if op["accion"] == "crear":
                    lineas.append(f"- {op['tarea']}: abierta por {op['rol']}. {c.get('descripcion', '')}")
                elif "disciplina" in c or "tipo" in c:
                    lineas.append(f"- {op['tarea']}: triaje -> {c.get('disciplina', '')} ({c.get('tipo', '')})")
                elif "estado" in c:
                    lineas.append(f"- {op['tarea']}: el cliente la deja en {ESTADO_ECR.get(c['estado'], c['estado'])}")
            elif op.get("ecr") == e.id:
                detalle = ", ".join(f"{_etiqueta(k)}: {_celda(a)} → {_celda(op['campos'].get(k))}"
                                    for k, a in op["antes"].items()) if op["accion"] != "crear" else "nuevo"
                lineas.append(f"- {op['tarea']}: {op['rol']} {op['accion']} {op['id']} ({detalle or 'sin cambio de valores'})")
        partes.append(f"## {e.id}: {ESTADO_ECR.get(e.campos.get('estado'), '—')}\n\n" + "\n".join(lineas))
    return "\n\n".join(partes) + "\n"


# ---------- ensayo sobre una corrida existente ----------

def _sin_tablas(texto: str) -> str:
    """Aproxima el analisis de F2 con un informe de F1: el mismo texto sin sus tablas."""
    return re.sub(r"\n{3,}", "\n\n", "\n".join(l for l in texto.split("\n") if not l.lstrip().startswith("|")))


def seccion(texto: str, patron: str) -> str:
    """Cuerpo de la seccion de nivel 2 cuyo titulo (sin numeracion) empieza con el patron."""
    m = re.search(rf"^##\s+[\d.]*\s*({patron}).*?$(.*?)(?=^##\s|\Z)", texto, re.I | re.M | re.S)
    return m.group(2).strip() if m else ""


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    corrida = Path(sys.argv[1])
    salida = Path(sys.argv[2]) if len(sys.argv) > 2 else corrida / "_f2"
    salida.mkdir(parents=True, exist_ok=True)
    tareas = list(dict.fromkeys(op["tarea"] for op in leer_ops(corrida)))
    for t in tareas:
        original = corrida / f"{t}.md"
        analisis = _sin_tablas(original.read_text(encoding="utf-8")) if original.exists() else ""
        (salida / f"{t}.md").write_text(informe(corrida, t, analisis), encoding="utf-8")
    viejo = (corrida / "00_vdi2206_consolidated_dossier.md").read_text(encoding="utf-8")
    textos = {"resumen": _sin_tablas(seccion(viejo, "resumen")), "decisiones": seccion(viejo, "decisiones"),
              "recomendados": seccion(viejo, "cambios recomendados")}
    (salida / "00_dossier.md").write_text(dossier(corrida, textos), encoding="utf-8")
    hist = corrida / "_iteraciones.json"
    (salida / "historial_de_diseno.md").write_text(
        historial(corrida, json.loads(hist.read_text(encoding="utf-8")) if hist.exists() else None), encoding="utf-8")
    print(f"{len(tareas)} informes, dossier e historial en {salida}")
