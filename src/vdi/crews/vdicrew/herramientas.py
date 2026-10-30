"""Herramientas de los agentes: consulta a los manuales (RAG), calculadora, busqueda web y matriz ponderada.

RAG. Indexa los documentos de knowledge/ (PDF, TXT o MD), que no van en Git y se copian a mano.
Los PDF se leen pagina por pagina con PyMuPDF conservando la disposicion de las tablas, se quitan los
encabezados y pies repetidos, y el texto se parte en fragmentos de unos 1000 caracteres. La busqueda es
hibrida: combina por rangos reciprocos (RRF) la similitud de embeddings (qwen3-embedding:0.6b, servido por
el mismo Ollama de la corrida) con BM25, que favorece terminos exactos como Y00 o 16.2N.m. Es la mejor
configuracion de la evaluacion diagnostico_rag (job 22918: acierto@1 0.95, acierto@4 1.0, MRR@4 0.975 en
20 consultas, frente a 0.55, 0.8 y 0.658 del modelo ONNX MiniLM inicial). El indice queda en .rag_db/ y
solo se reconstruye cuando cambian los documentos, el modelo o la particion; se construye al empezar la
corrida (necesita Ollama con el modelo de embeddings). Prueba sin el crew:
    uv run python -m vdi.crews.vdicrew.herramientas "allowable torque axis 6"

CALCULADORA. Evalua expresiones aritmeticas con funciones de math, sin eval de Python.

BUSQUEDA WEB. Consulta la API de Tavily (TAVILY_API_KEY en el .env de la raiz o en el entorno) para el
benchmarking de la Fase 1, la seleccion de componentes con su hoja de datos, datos de confiabilidad de
fabricantes y consultas tecnicas. Usa la busqueda avanzada, que devuelve de cada pagina los fragmentos
mas relacionados con la consulta (2 creditos por busqueda).

MATRIZ PONDERADA. Calcula las tablas grandes que el modelo sumaba a mano en el razonamiento hasta
agotar el tope de tokens (etapa C: 5 de 8 respuestas perdidas): la importancia tecnica del QFD y
la evaluacion ponderada de conceptos.

Cada agente recibe sus propias instancias de las herramientas, con un tope de usos por tarea (CrewAI
reinicia el contador al empezar cada tarea). Cada uso queda en RUN_DIR/_herramientas.log con la tarea que
lo pidio y, para el RAG y la busqueda web, todas las fuentes devueltas (para verificar las citas).
"""
import ast
import json
import math
import operator
import os
import re
import sys
import threading
import urllib.request
from collections import Counter
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from crewai.events import crewai_event_bus
from crewai.events.types.task_events import TaskStartedEvent
from crewai.tools import BaseTool
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from ...corrida import RUN_DIR

ROOT = Path(__file__).resolve().parents[4]
KNOWLEDGE_DIR = Path(os.environ.get("VDI_KNOWLEDGE_DIR") or ROOT / "knowledge")
RAG_DB_DIR = ROOT / ".rag_db"
HUELLA_FILE = RAG_DB_DIR / "huella.json"
FRAGMENTOS_FILE = RAG_DB_DIR / "fragmentos.json"
VECTORES_FILE = RAG_DB_DIR / "vectores.npy"
EXTENSIONES = {".pdf", ".txt", ".md"}
MODELO_EMBEDDINGS = os.environ.get("VDI_EMBEDDINGS", "qwen3-embedding:0.6b")
# Los modelos qwen3-embedding esperan una instruccion antes de la consulta (no antes de los documentos)
INSTRUCCION_CONSULTA = "Instruct: Given a question about an industrial robot, retrieve manual passages that answer it\nQuery:"
LIMITE_RESULTADOS = 4
TAM_FRAGMENTO = 1000  # caracteres
SOLAPE = 200
RRF_K = 60

# Topes de uso por tarea. Evitan que un agente se quede consultando en vez de redactar el informe.
MAX_CONSULTAS_RAG = 6
# Con 25 usos la sintesis hacia un calculo por peticion (prueba 22912); con 8 los agentes hacian a mano
# las tablas grandes hasta agotar el tope de tokens (etapa C). Las tablas van ahora a la matriz ponderada.
MAX_USOS_CALCULADORA = 15
MAX_USOS_MATRIZ = 6
MAX_BUSQUEDAS_WEB = 5
RESULTADOS_WEB = 5
FRAGMENTOS_POR_PAGINA = 3  # busqueda avanzada: fragmentos de cada pagina mas relacionados con la consulta
CHARS_POR_RESULTADO_WEB = 1200  # extracto de cada pagina que recibe el agente (con 700 las especificaciones quedaban cortadas)
TAVILY_URL = os.environ.get("VDI_TAVILY_URL", "https://api.tavily.com/search")  # otra URL solo para pruebas

load_dotenv(ROOT / ".env")  # la clave de Tavily puede venir del .env de la raiz del proyecto

REGISTRO = RUN_DIR / "_herramientas.log"
_lock = threading.Lock()
_tarea_actual = {"nombre": "-"}


def clave_tavily() -> str:
    return (os.environ.get("TAVILY_API_KEY") or "").strip()


@crewai_event_bus.on(TaskStartedEvent)
def _al_iniciar_tarea(source, event) -> None:
    tarea = getattr(event, "task", None)
    _tarea_actual["nombre"] = getattr(tarea, "name", None) or "-"


def _registrar(herramienta: str, entrada: str, resultado: str, fuentes: str = "-") -> None:
    try:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        nuevo = not REGISTRO.exists()
        linea = "\t".join([
            datetime.now().isoformat(timespec="seconds"), _tarea_actual["nombre"], herramienta,
            " ".join(entrada.split())[:300], str(len(resultado)), " ".join(resultado.split())[:200], fuentes,
        ])
        with _lock, open(REGISTRO, "a", encoding="utf-8") as f:
            if nuevo:
                f.write("hora\ttarea\therramienta\tentrada\tchars_resultado\tinicio_resultado\tfuentes\n")
            f.write(linea + "\n")
    except OSError:
        pass  # el registro nunca debe tumbar la corrida


# --- RAG ---------------------------------------------------------------------------------------

def documentos() -> list[Path]:
    if not KNOWLEDGE_DIR.is_dir():
        return []
    return sorted(p for p in KNOWLEDGE_DIR.iterdir() if p.suffix.lower() in EXTENSIONES)


def _huella(docs: list[Path]) -> dict[str, list[int]]:
    """Nombre, tamano y fecha de modificacion de cada documento: si algo cambia, se reindexa."""
    return {p.name: [p.stat().st_size, int(p.stat().st_mtime)] for p in docs}


def _huella_indice(docs: list[Path]) -> dict:
    return {"documentos": _huella(docs), "modelo": MODELO_EMBEDDINGS, "fragmento": [TAM_FRAGMENTO, SOLAPE]}


def huella_documentos() -> dict:
    """Huella del indice (documentos, modelo de embeddings y particion) para corrida.json."""
    return _huella_indice(documentos())


def _paginas(doc: Path) -> list[tuple[int, str]]:
    """Texto de cada pagina. En los PDF se ordena por posicion para que las tablas queden legibles."""
    if doc.suffix.lower() == ".pdf":
        import pymupdf

        with pymupdf.open(doc) as pdf:
            return [(i + 1, pagina.get_text("text", sort=True)) for i, pagina in enumerate(pdf)]
    return [(0, doc.read_text(encoding="utf-8", errors="replace"))]


def _lineas_repetidas(paginas: list[tuple[int, str]]) -> set[str]:
    """Encabezados y pies de pagina: lineas que aparecen en al menos el 30 % de las paginas."""
    if len(paginas) < 5:
        return set()
    conteo: dict[str, int] = {}
    for _, texto in paginas:
        for linea in {l.strip() for l in texto.split("\n") if l.strip()}:
            conteo[linea] = conteo.get(linea, 0) + 1
    return {l for l, n in conteo.items() if n >= 0.3 * len(paginas) and not l.isdigit()}


def _limpiar(texto: str) -> str:
    texto = re.sub(r"[ \t]{4,}", "   ", texto)  # columnas de tablas: basta con separarlas
    texto = re.sub(r"\n\s*\n+", "\n", texto)
    return texto.strip()


def _fragmentos(texto: str) -> list[str]:
    """Ventanas de TAM_FRAGMENTO caracteres con SOLAPE, cortando en un salto de linea cuando se puede."""
    piezas, inicio = [], 0
    while inicio < len(texto):
        fin = min(inicio + TAM_FRAGMENTO, len(texto))
        if fin < len(texto):
            corte = texto.rfind("\n", inicio + TAM_FRAGMENTO // 2, fin)
            fin = corte if corte > 0 else fin
        piezas.append(texto[inicio:fin].strip())
        if fin >= len(texto):
            break
        inicio = max(fin - SOLAPE, inicio + 1)
    return [p for p in piezas if len(p) > 40]


def _embeddings(textos: list[str]):
    """Vectores normalizados del modelo de embeddings servido por Ollama."""
    import numpy as np

    url = "http://" + os.environ.get("OLLAMA_HOST", "localhost:11434") + "/api/embed"
    vectores = []
    for i in range(0, len(textos), 32):
        cuerpo = json.dumps({"model": MODELO_EMBEDDINGS, "input": textos[i:i + 32], "truncate": True}).encode()
        req = urllib.request.Request(url, data=cuerpo, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as r:
            vectores += json.loads(r.read())["embeddings"]
    m = np.array(vectores, dtype=np.float32)
    return m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-12)


def _tokens(texto: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", texto.lower())


class _BM25:
    def __init__(self, textos: list[str], k1: float = 1.5, b: float = 0.75):
        self.docs = [Counter(_tokens(t)) for t in textos]
        self.largos = [sum(d.values()) for d in self.docs]
        self.media = sum(self.largos) / max(len(self.largos), 1)
        self.df = Counter(t for d in self.docs for t in d)
        self.k1, self.b, self.n = k1, b, len(textos)

    def puntajes(self, consulta: str) -> list[float]:
        salida = [0.0] * self.n
        for t in set(_tokens(consulta)):
            if t not in self.df:
                continue
            idf = math.log(1 + (self.n - self.df[t] + 0.5) / (self.df[t] + 0.5))
            for i, d in enumerate(self.docs):
                f = d.get(t)
                if f:
                    salida[i] += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.largos[i] / self.media))
        return salida


@lru_cache
def _indice():
    """Fragmentos, sus vectores y el indice BM25. Se reconstruye solo si cambia la huella."""
    import numpy as np

    docs = documentos()
    if not docs:
        raise FileNotFoundError(f"No hay documentos para el RAG en {KNOWLEDGE_DIR}")
    huella = _huella_indice(docs)
    anterior = json.loads(HUELLA_FILE.read_text(encoding="utf-8")) if HUELLA_FILE.exists() else None
    if huella == anterior and FRAGMENTOS_FILE.exists() and VECTORES_FILE.exists():
        fragmentos = json.loads(FRAGMENTOS_FILE.read_text(encoding="utf-8"))
        vectores = np.load(VECTORES_FILE)
    else:
        print(f"[VDI] Indexando {len(docs)} documentos de {KNOWLEDGE_DIR} con {MODELO_EMBEDDINGS}...", flush=True)
        fragmentos = []
        for doc in docs:
            paginas = _paginas(doc)
            repetidas = _lineas_repetidas(paginas)
            n0 = len(fragmentos)
            for pagina, texto in paginas:
                texto = "\n".join(l for l in texto.split("\n") if l.strip() not in repetidas)
                fragmentos += [{"archivo": doc.name, "pagina": pagina, "texto": f} for f in _fragmentos(_limpiar(texto))]
            print(f"[VDI]   {doc.name}: {len(fragmentos) - n0} fragmentos", flush=True)
        vectores = _embeddings([f["texto"] for f in fragmentos])
        # Escritura atomica (archivo temporal propio + os.replace): varias corridas en paralelo pueden
        # construir el mismo indice a la vez sin que ninguna lea un archivo a medio escribir.
        # La huella se escribe al final, cuando los otros dos ya estan completos.
        RAG_DB_DIR.mkdir(parents=True, exist_ok=True)
        sufijo = f".{os.getpid()}.tmp"
        tmp = FRAGMENTOS_FILE.with_name(FRAGMENTOS_FILE.name + sufijo)
        tmp.write_text(json.dumps(fragmentos, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, FRAGMENTOS_FILE)
        tmp = VECTORES_FILE.with_name(VECTORES_FILE.name + sufijo)
        with open(tmp, "wb") as f:
            np.save(f, vectores)
        os.replace(tmp, VECTORES_FILE)
        tmp = HUELLA_FILE.with_name(HUELLA_FILE.name + sufijo)
        tmp.write_text(json.dumps(huella, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, HUELLA_FILE)
        print("[VDI] Indice listo", flush=True)
    return fragmentos, vectores, _BM25([f["texto"] for f in fragmentos])


def fragmentos_pertinentes(consulta: str, limite: int = LIMITE_RESULTADOS) -> list[dict]:
    """Fragmentos mas pertinentes (embeddings + BM25 combinados por RRF): archivo, pagina y texto."""
    import numpy as np

    fragmentos, vectores, bm25 = _indice()
    densa = vectores @ _embeddings([INSTRUCCION_CONSULTA + consulta])[0]
    lexica = np.array(bm25.puntajes(consulta))
    rrf = np.zeros(len(fragmentos))
    for puntaje in (densa, lexica):
        rangos = np.argsort(np.argsort(-puntaje))
        rrf += 1.0 / (RRF_K + rangos + 1)
    return [fragmentos[i] for i in np.argsort(-rrf)[:limite]]


def buscar(consulta: str, limite: int = LIMITE_RESULTADOS, rol: str | None = None) -> str:
    """Fragmentos como texto para el agente, con archivo y pagina; con el modelo encendido (y un rol), cada uno
    con su ID de fuente FT-xx para citarlo."""
    salida = []
    for f in fragmentos_pertinentes(consulta, limite):
        donde = f"{f['archivo']}, página {f['pagina']} del PDF" if f["pagina"] else f["archivo"]
        i = _evidencia("fuente", {"documento": f["archivo"], "ubicacion": f"página {f['pagina']} del PDF" if f["pagina"] else "",
                                  "consulta": consulta, "extracto": f["texto"]}, rol) if rol is not None else None
        salida.append(f"[Fuente{' ' + i if i else ''}: {donde}]\n{f['texto']}")
    return "\n\n".join(salida) or "No se encontró contenido en los manuales para esa consulta."


class _EntradaRag(BaseModel):
    query: str = Field(..., description="Consulta en INGLÉS, concreta (por ejemplo: 'allowable torque axis 6').")


class BuscarManuales(BaseTool):
    name: str = "buscar_manuales_crp"
    description: str = (
        "Busca en los manuales técnicos del robot del laboratorio (en INGLÉS): operación del robot, "
        "instrucciones de programación, gabinete eléctrico G4-CD60 (E/S, cableado, alimentación), manual del "
        "PLC y ficha técnica del brazo RA09A-07 (carga útil, alcances, velocidades de los ejes, par e inercia "
        "admisibles en la muñeca). Úsala para datos del robot y del gabinete; no tiene catálogos de componentes "
        "ni precios. Escribe SIEMPRE la consulta en inglés. Devuelve los fragmentos más parecidos con su archivo "
        "de origen y página. Cita en el informe el manual y la página de los que sale cada dato."
    )
    args_schema: type[BaseModel] = _EntradaRag
    max_usage_count: int | None = MAX_CONSULTAS_RAG
    rol: str = ""

    def _run(self, query: str) -> str:
        try:
            resultado = buscar(query, rol=self.rol)
        except Exception as error:  # una consulta fallida no debe tumbar la tarea
            resultado = f"Error al consultar los manuales: {type(error).__name__}: {error}"
        # Todas las fuentes devueltas (archivo|pagina), para verificar despues las citas de los informes
        fuentes = ";".join(f"{a}|{p}" for a, p in re.findall(r"\[Fuente(?: FT-\d+)?: (.+?), página (\d+) del PDF\]", resultado))
        if "[Fuente FT-" in resultado:
            resultado += "\n\n" + AVISO_CITA
        _registrar(self.name, query, resultado, fuentes or "-")
        return resultado


# --- CALCULADORA -------------------------------------------------------------------------------

_OPERADORES = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.Pow: operator.pow, ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}
_FUNCIONES = {
    nombre: getattr(math, nombre)
    for nombre in ("sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "radians", "degrees",
                   "log", "log10", "exp", "hypot", "ceil", "floor")
}
_FUNCIONES.update({"abs": abs, "min": min, "max": max, "round": round, "sum": lambda *a: sum(a)})
_CONSTANTES = {"pi": math.pi, "e": math.e, "g": 9.81}


def _evaluar(nodo: ast.AST, variables: dict[str, float]) -> float:
    if isinstance(nodo, ast.Expression):
        return _evaluar(nodo.body, variables)
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, (int, float)) and not isinstance(nodo.value, bool):
        return nodo.value
    if isinstance(nodo, ast.Name):
        if nodo.id in variables:
            return variables[nodo.id]
        if nodo.id in _CONSTANTES:
            return _CONSTANTES[nodo.id]
        raise ValueError(f"variable no definida: {nodo.id}")
    if isinstance(nodo, ast.BinOp) and type(nodo.op) in _OPERADORES:
        izquierda, derecha = _evaluar(nodo.left, variables), _evaluar(nodo.right, variables)
        if isinstance(nodo.op, ast.Pow) and abs(derecha) > 100:
            raise ValueError("exponente demasiado grande")
        return _OPERADORES[type(nodo.op)](izquierda, derecha)
    if isinstance(nodo, ast.UnaryOp) and type(nodo.op) in _OPERADORES:
        return _OPERADORES[type(nodo.op)](_evaluar(nodo.operand, variables))
    if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name) and nodo.func.id in _FUNCIONES and not nodo.keywords:
        return _FUNCIONES[nodo.func.id](*[_evaluar(a, variables) for a in nodo.args])
    raise ValueError(f"expresión no permitida: {ast.dump(nodo)[:80]}")


def calcular(expresiones: str, rol: str | None = None) -> str:
    """Evalua una o varias lineas (separadas por salto de linea o ';'). Cada linea es una expresion
    o una asignacion 'nombre = expresion' que se puede usar en las lineas siguientes. Con el modelo encendido (y
    un rol), cada linea lleva su ID de calculo CAL-xx; la evidencia guarda tambien los valores de las variables."""
    variables: dict[str, float] = {}
    salida = []
    for linea in [x.strip() for x in expresiones.replace(";", "\n").split("\n") if x.strip()]:
        nombre, expresion = None, linea
        if "=" in linea and "==" not in linea:
            izquierda, expresion = (s.strip() for s in linea.split("=", 1))
            if not izquierda.isidentifier():
                return f"Error en '{linea}': el nombre de la variable no es válido"
            nombre = izquierda
        try:
            arbol = ast.parse(expresion, mode="eval")
            valor = _evaluar(arbol, variables)
        except ZeroDivisionError:
            return f"Error en '{linea}': división por cero"
        except (SyntaxError, ValueError, TypeError, OverflowError) as error:
            return f"Error en '{linea}': {error}"
        valor = float(valor)
        texto = f"{valor:.6g}"
        usadas = sorted({n.id for n in ast.walk(arbol) if isinstance(n, ast.Name) and n.id in variables})
        if nombre:
            variables[nombre] = valor
            texto_linea = f"{nombre} = {expresion} = {texto}"
        else:
            texto_linea = f"{expresion} = {texto}"
        if rol is not None:
            contexto = f" (con {', '.join(f'{v} = {variables[v]:.6g}' for v in usadas)})" if usadas else ""
            i = _evidencia("calculo", {"expresion": f"{nombre + ' = ' if nombre else ''}{expresion}{contexto}",
                                       "resultado": texto}, rol)
            texto_linea = f"{i}: {texto_linea}" if i else texto_linea
        salida.append(texto_linea)
    return "\n".join(salida) or "Error: no se recibió ninguna expresión"


class _EntradaCalculadora(BaseModel):
    expresiones: str = Field(
        ...,
        description="Una o varias líneas separadas por ';'. Ejemplo: 'm = 3.2 + 0.98; d = 0.085; T = m * g * d'",
    )


class Calculadora(BaseTool):
    name: str = "calculadora"
    description: str = (
        "Calcula expresiones numéricas exactas. Úsala para los cálculos del informe (masas, pares, inercias, "
        "fuerzas, tiempos, corrientes y sumas de BOM) en lugar de calcular de memoria; para el QFD y la "
        "evaluación de conceptos usa matriz_ponderada. Reúne en una sola llamada todos los cálculos de una "
        "sección (por ejemplo, todo el BOM), con una línea por cálculo. "
        "Admite + - * / ** %, paréntesis, variables con 'nombre = expresión' separadas por ';', las funciones "
        "sqrt, sin, cos, tan, asin, acos, atan, atan2, radians, degrees, log, log10, exp, hypot, abs, min, max, "
        "round, sum y las constantes pi, e y g = 9.81. Trabaja sin unidades: usa unidades del SI y escribe en el "
        "informe la fórmula y el resultado con su unidad."
    )
    args_schema: type[BaseModel] = _EntradaCalculadora
    max_usage_count: int | None = MAX_USOS_CALCULADORA
    rol: str = ""

    def _run(self, expresiones: str) -> str:
        resultado = calcular(expresiones, rol=self.rol)
        if re.match(r"CAL-\d+: ", resultado):
            resultado += "\n\n" + AVISO_CITA
        _registrar(self.name, expresiones, resultado)
        return resultado


# --- BUSQUEDA WEB ------------------------------------------------------------------------------

def buscar_web(consulta: str, rol: str | None = None) -> tuple[str, list[dict]]:
    """Resultados de Tavily como texto para el agente, y cada resultado (url, titulo, extracto) tal como lo recibe.
    Con el modelo encendido (y un rol), cada resultado lleva su ID de fuente FT-xx."""
    cuerpo = json.dumps({"query": consulta, "search_depth": "advanced", "chunks_per_source": FRAGMENTOS_POR_PAGINA,
                         "max_results": RESULTADOS_WEB, "include_answer": False, "include_raw_content": False}).encode()
    req = urllib.request.Request(TAVILY_URL, data=cuerpo, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {clave_tavily()}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        resultados = json.loads(r.read()).get("results") or []
    hoy = datetime.now().strftime("%Y-%m-%d")
    salida, recibidos = [], []
    for i, res in enumerate(resultados, 1):
        url, titulo = res.get("url", ""), res.get("title", "").strip()
        extracto = " ".join(str(res.get("content", "")).split())[:CHARS_POR_RESULTADO_WEB]
        recibidos.append({"url": url, "titulo": titulo, "extracto": extracto})
        ft = _evidencia("fuente", {"documento": url, "titulo": titulo, "ubicacion": f"consultada el {hoy}",
                                   "consulta": consulta, "extracto": extracto}, rol) if rol is not None else None
        salida.append(f"[Fuente web {ft or i}: {titulo} | {url} | consultada el {hoy}]\n{extracto}")
    return "\n\n".join(salida) or "La búsqueda no devolvió resultados.", recibidos


def _registrar_web(consulta: str, recibidos: list[dict], error: str = "") -> None:
    """Cada busqueda completa en _web.jsonl: lo que leyo el agente, para verificar despues las citas web."""
    try:
        linea = json.dumps({"hora": datetime.now().isoformat(timespec="seconds"), "tarea": _tarea_actual["nombre"],
                            "consulta": consulta, "error": error, "resultados": recibidos}, ensure_ascii=False)
        with _lock, open(RUN_DIR / "_web.jsonl", "a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except OSError:
        pass  # el registro nunca debe tumbar la corrida


class _EntradaWeb(BaseModel):
    query: str = Field(..., description="Consulta concreta, de preferencia en inglés (por ejemplo: "
                                        "'vacuum ejector suction flow datasheet 6 bar' o 'solenoid valve 24 V coil current 5/2').")


class BuscarWeb(BaseTool):
    name: str = "buscar_web"
    description: str = (
        "Busca en internet y devuelve hasta 5 resultados con título, URL, fecha de consulta y los fragmentos de "
        "cada página más relacionados con la consulta. Úsala para el benchmarking de productos comparables, para "
        "seleccionar componentes comerciales con su hoja de datos, para datos de confiabilidad de fabricantes y "
        "para consultas técnicas puntuales. No la uses para precios. Cita en el informe la URL de cada dato que "
        "tomes de un resultado; si el dato no aparece en los fragmentos, es Supuesto."
    )
    args_schema: type[BaseModel] = _EntradaWeb
    max_usage_count: int | None = MAX_BUSQUEDAS_WEB
    rol: str = ""

    def _run(self, query: str) -> str:
        recibidos: list[dict] = []
        try:
            resultado, recibidos = buscar_web(query, rol=self.rol)
            if "[Fuente web FT-" in resultado:
                resultado += "\n\n" + AVISO_CITA
            error = ""
        except Exception as e:  # una busqueda fallida no debe tumbar la tarea
            resultado = error = f"Error en la búsqueda web: {type(e).__name__}: {e}"
        _registrar(self.name, query, resultado, ";".join(r["url"] for r in recibidos) or "-")
        _registrar_web(query, recibidos, error)
        return resultado


# --- MATRIZ PONDERADA --------------------------------------------------------------------------

def _lista(texto: str) -> list[str]:
    return [x.strip() for x in re.split(r"[;\n]", texto) if x.strip()]


def matriz_ponderada(tipo: str, columnas: str, filas: str, pesos: str) -> str:
    """tipo 'qfd': un peso por fila (importancia de cada necesidad); devuelve por columna la suma de
    peso x relacion. tipo 'evaluacion': un peso por columna (criterio); devuelve por fila la suma de
    peso x puntaje. Las filas van como 'nombre: v1, v2, ...' separadas por ';' o salto de linea."""
    tipo = tipo.strip().lower()
    if tipo not in ("qfd", "evaluacion"):
        return "Error: tipo debe ser 'qfd' o 'evaluacion'"
    nombres_col = [c.strip() for c in columnas.split(",") if c.strip()]
    try:
        w = [float(x.replace(" ", "")) for x in re.split(r"[;,\s]+", pesos.strip()) if x]
        nombres_fila, valores = [], []
        for linea in _lista(filas):
            if ":" not in linea:
                return f"Error en la fila '{linea}': falta 'nombre: valores'"
            nombre, resto = linea.split(":", 1)
            v = [float(x) if x.strip() else 0.0 for x in resto.split(",")]
            if len(v) != len(nombres_col):
                return f"Error en la fila '{nombre.strip()}': tiene {len(v)} valores y hay {len(nombres_col)} columnas"
            nombres_fila.append(nombre.strip())
            valores.append(v)
    except ValueError as error:
        return f"Error: valor no numérico ({error})"
    if not valores:
        return "Error: no hay filas"
    esperados = len(valores) if tipo == "qfd" else len(nombres_col)
    if len(w) != esperados:
        return f"Error: hay {len(w)} pesos y se esperaban {esperados} (uno por {'fila' if tipo == 'qfd' else 'columna'})"
    if tipo == "qfd":
        nombres, totales = nombres_col, [sum(w[i] * valores[i][j] for i in range(len(valores))) for j in range(len(nombres_col))]
        encabezado = "Importancia técnica por columna (suma de peso de fila × relación)"
    else:
        nombres, totales = nombres_fila, [sum(w[j] * fila[j] for j in range(len(w))) for fila in valores]
        encabezado = "Puntaje ponderado por fila (suma de peso de columna × puntaje)"
    suma = sum(totales)
    orden = sorted(range(len(totales)), key=lambda k: -totales[k])
    lineas = [encabezado, "| Puesto | Elemento | Total | % del total |", "|---|---|---|---|"]
    for puesto, k in enumerate(orden, 1):
        pct = 100 * totales[k] / suma if suma else 0
        lineas.append(f"| {puesto} | {nombres[k]} | {totales[k]:.6g} | {pct:.2f} |")
    lineas.append(f"Suma de totales: {suma:.6g}. Suma de pesos: {sum(w):.6g}.")
    if tipo == "evaluacion" and sum(w):
        lineas.append("Puntaje normalizado (total / suma de pesos): " +
                      ", ".join(f"{nombres[k]} = {totales[k] / sum(w):.4g}" for k in orden))
    return "\n".join(lineas)


class _EntradaMatriz(BaseModel):
    tipo: str = Field(..., description="'qfd' (un peso por fila) o 'evaluacion' (un peso por columna).")
    columnas: str = Field(..., description="Nombres de las columnas separados por comas. Ej.: 'RF-01, RF-02, RNF-01'")
    filas: str = Field(..., description="Una fila por línea o separadas por ';', como 'nombre: v1, v2, v3'. "
                                        "Ej.: 'N-01: 9, 3, 0; N-02: 1, 9, 3'. Celda vacía = 0; decimales con punto.")
    pesos: str = Field(..., description="Pesos separados por comas, con punto decimal: uno por fila si tipo='qfd' (importancia de cada "
                                        "necesidad), uno por columna si tipo='evaluacion' (peso de cada criterio).")


class MatrizPonderada(BaseTool):
    name: str = "matriz_ponderada"
    description: str = (
        "Calcula tablas ponderadas completas en una sola llamada. tipo='qfd': filas = necesidades con su "
        "importancia como peso, columnas = requerimientos (o variables de diseño en el QFD etapa 2), valores = "
        "relación 9/3/1/0; devuelve la importancia técnica de cada columna y su porcentaje. tipo='evaluacion': "
        "filas = conceptos, columnas = criterios con su peso, valores = puntaje 1 a 5; devuelve el puntaje "
        "ponderado de cada concepto y el orden. Úsala para el QFD y la evaluación de conceptos en lugar de "
        "sumar a mano o con la calculadora."
    )
    args_schema: type[BaseModel] = _EntradaMatriz
    max_usage_count: int | None = MAX_USOS_MATRIZ
    rol: str = ""

    def _run(self, tipo: str, columnas: str, filas: str, pesos: str) -> str:
        resultado = matriz_ponderada(tipo, columnas, filas, pesos)
        if not resultado.startswith("Error"):
            i = _evidencia("matriz", {"tipo": tipo.strip().lower(), "tabla": f"columnas: {columnas}\nfilas: {filas}\npesos: {pesos}",
                                      "resultado": resultado}, self.rol)
            if i:
                resultado = f"Matriz registrada como {i}: cítala en la Evidencia de la decisión que respalda.\n{resultado}"
        _registrar(self.name, f"{tipo} | {columnas} | {pesos}", resultado)
        return resultado


# --- MODELO DE DISENO (F1) -----------------------------------------------------------------------
# El modelo vive en ciclo.py (una instancia por corrida, con su transaccion por tarea); estas herramientas solo
# lo leen y escriben con el rol del agente, que decide que puede modificar (capa de propiedad de modelo.py).

MAX_REGISTROS_MODELO = 10
MAX_CONSULTAS_MODELO = 6
ROL_AGENTE = {"team_lead": "lider", "mecanico": "mecanica", "electronico": "electronica", "software": "software",
              "mantenimiento": "rams", "cliente": "cliente"}
_modelo = {"actual": None}


def usar_modelo(modelo) -> None:
    """ciclo.py fija aqui el modelo de la corrida (None lo desactiva)."""
    _modelo["actual"] = modelo


# F2: la calculadora, los manuales, la web y la matriz registran su evidencia (CAL, FT, MAT) y muestran el ID; con
# el modelo apagado su salida es la misma de siempre
AVISO_CITA = ("Para usar un dato de este resultado, cita su ID (CAL-xx, FT-xx) en la columna Fuente del parámetro o "
              "en la Evidencia de la decisión; el código comprueba que el valor esté en la fuente citada.")


def _evidencia(clase: str, campos: dict, rol: str | None) -> str | None:
    modelo = _modelo["actual"]
    if modelo is None or rol is None:
        return None
    try:
        return modelo.nueva_evidencia(clase, campos, rol or "-")
    except Exception:  # un fallo del modelo no debe tumbar la herramienta
        return None


class _EntradaRegistro(BaseModel):
    clase: str = Field(..., description="Clase de los elementos de la tabla: necesidad, requerimiento, funcion, modulo, "
                                        "componente, parametro, interfaz, ecr, sistema, externo, decision o modo de falla.")
    tabla: str = Field(..., description="Tabla Markdown con encabezado: la columna ID y las columnas de esa clase "
                                        "descritas en las reglas del modelo de diseño. Una fila por elemento.")


class RegistrarEnModelo(BaseTool):
    name: str = "registrar_en_modelo"
    description: str = (
        "Registra o modifica elementos del modelo de diseño compartido con una tabla Markdown de una sola clase. "
        "Un ID nuevo crea el elemento; uno existente modifica solo las columnas con valor; la columna Acción con "
        "«eliminar» lo borra. La tabla se aplica completa o no se aplica: si hay errores, la respuesta los lista por "
        "fila; corrígelos y envía otra vez la tabla completa. Solo puedes modificar los elementos de tu rol. Las "
        "sumas de masa y costo y las comprobaciones las hace el código: no las registres."
    )
    args_schema: type[BaseModel] = _EntradaRegistro
    max_usage_count: int | None = MAX_REGISTROS_MODELO
    rol: str = ""

    def _run(self, clase: str, tabla: str) -> str:
        modelo = _modelo["actual"]
        if modelo is None:
            resultado = "El modelo de diseño no está activo en esta corrida."
        else:
            try:
                resultado = modelo.registrar(clase, tabla, self.rol)
            except Exception as error:  # un fallo del modelo no debe tumbar la tarea
                resultado = f"Error al registrar: {type(error).__name__}: {error}"
        _registrar(self.name, f"{self.rol} | {clase} | {tabla}", resultado)
        return resultado


class _EntradaConsultaModelo(BaseModel):
    consulta: str = Field(..., description="Una de: 'elemento <ID>', 'trazabilidad <ID>', 'componentes <dominio>', "
                                           "'presupuestos', 'requerimientos sin portador', 'ecr abiertas', 'decisiones', "
                                           "'modos de falla'.")


class ConsultarModelo(BaseTool):
    name: str = "consultar_modelo"
    description: str = (
        "Consulta el modelo de diseño compartido: un elemento por su ID, su trazabilidad (de qué deriva y qué lo "
        "usa), los componentes de un dominio, los totales de masa y costo por módulo frente a su presupuesto "
        "(calculados por el código), los requerimientos sin portador, las ECR abiertas, las decisiones vigentes o "
        "los modos de falla. Con 'elemento <ID>' también lees un cálculo (CAL-xx), una fuente (FT-xx) o una matriz "
        "(MAT-xx) citados."
    )
    args_schema: type[BaseModel] = _EntradaConsultaModelo
    max_usage_count: int | None = MAX_CONSULTAS_MODELO

    def _run(self, consulta: str) -> str:
        modelo = _modelo["actual"]
        resultado = modelo.consultar(consulta) if modelo is not None else "El modelo de diseño no está activo en esta corrida."
        _registrar(self.name, consulta, resultado)
        return resultado


def herramientas_modelo(agente: str) -> list[BaseTool]:
    """Herramientas del modelo para un agente de agents.yaml, con su rol (instancias nuevas: topes propios)."""
    return [RegistrarEnModelo(rol=ROL_AGENTE[agente]), ConsultarModelo()] if agente in ROL_AGENTE else []


def herramientas_agente(web: bool = False, agente: str | None = None) -> list[BaseTool]:
    """Instancias nuevas para cada agente: cada una lleva su propio contador de usos por tarea y el rol del agente
    (quien produjo la evidencia, en F2)."""
    rol = ROL_AGENTE.get(agente or "", "")
    return [BuscarManuales(rol=rol), Calculadora(rol=rol), MatrizPonderada(rol=rol)] + ([BuscarWeb(rol=rol)] if web else [])


if __name__ == "__main__":
    # Construye (o verifica) el indice y hace una consulta de prueba sin LLM.
    consulta = " ".join(sys.argv[1:]) or "allowable torque and moment of inertia of axis 6"
    print(f"[VDI] Documentos en {KNOWLEDGE_DIR}:")
    for doc in documentos():
        print(f"[VDI]   {doc.name}")
    print(buscar(consulta))
