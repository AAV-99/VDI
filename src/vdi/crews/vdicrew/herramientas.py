"""Herramientas de los agentes: consulta a los manuales (RAG) y calculadora.

RAG. Indexa los documentos de knowledge/ (PDF, TXT o MD), que no van en Git y se copian a mano.
Los PDF se leen pagina por pagina con PyMuPDF conservando la disposicion de las tablas, y el texto
se parte en fragmentos de unos 1000 caracteres. La busqueda es hibrida: combina por rangos
reciprocos (RRF) la similitud de embeddings (qwen3-embedding:0.6b, servido por el mismo Ollama de
la corrida) con BM25, que favorece terminos exactos como Y00 o 16.2N.m. Es la mejor configuracion
de la evaluacion diagnostico_rag (job 22918: acierto@1 0.95, acierto@4 1.0, MRR@4 0.975 en 20
consultas, frente a 0.55, 0.8 y 0.658 del modelo ONNX MiniLM inicial).
El indice queda en .rag_db/ y solo se reconstruye cuando cambian los documentos, el modelo o la
particion. Se construye al empezar la corrida (necesita Ollama con el modelo de embeddings). Prueba:
    uv run python -m vdi.crews.vdicrew.herramientas "allowable torque axis 6"

CALCULADORA. Evalua expresiones aritmeticas con funciones de math, sin eval de Python.

Cada agente recibe sus propias instancias de las herramientas, con un tope de usos por tarea
(CrewAI reinicia el contador al empezar cada tarea). Cada uso queda en RUN_DIR/_herramientas.log.
"""
import ast
import re
import json
import math
import operator
import os
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
# Con 25 usos la sintesis hacia un calculo por peticion y repetia el razonamiento en cada una
# (corrida de prueba 22912: 16 peticiones y 107 000 tokens solo en esa tarea).
MAX_USOS_CALCULADORA = 8

REGISTRO = RUN_DIR / "_herramientas.log"
_lock = threading.Lock()
_tarea_actual = {"nombre": "-"}


@crewai_event_bus.on(TaskStartedEvent)
def _al_iniciar_tarea(source, event) -> None:
    tarea = getattr(event, "task", None)
    _tarea_actual["nombre"] = getattr(tarea, "name", None) or "-"


def _registrar(herramienta: str, entrada: str, resultado: str) -> None:
    try:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        nuevo = not REGISTRO.exists()
        linea = "\t".join([
            datetime.now().isoformat(timespec="seconds"), _tarea_actual["nombre"], herramienta,
            " ".join(entrada.split())[:300], str(len(resultado)), " ".join(resultado.split())[:200],
        ])
        with _lock, open(REGISTRO, "a", encoding="utf-8") as f:
            if nuevo:
                f.write("hora\ttarea\therramienta\tentrada\tchars_resultado\tinicio_resultado\n")
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
        RAG_DB_DIR.mkdir(parents=True, exist_ok=True)
        FRAGMENTOS_FILE.write_text(json.dumps(fragmentos, ensure_ascii=False), encoding="utf-8")
        np.save(VECTORES_FILE, vectores)
        HUELLA_FILE.write_text(json.dumps(huella, ensure_ascii=False, indent=1), encoding="utf-8")
        print("[VDI] Indice listo", flush=True)
    return fragmentos, vectores, _BM25([f["texto"] for f in fragmentos])


def buscar(consulta: str, limite: int = LIMITE_RESULTADOS) -> str:
    """Fragmentos mas pertinentes (embeddings + BM25 combinados por RRF), con archivo y pagina."""
    import numpy as np

    fragmentos, vectores, bm25 = _indice()
    densa = vectores @ _embeddings([INSTRUCCION_CONSULTA + consulta])[0]
    lexica = np.array(bm25.puntajes(consulta))
    rrf = np.zeros(len(fragmentos))
    for puntaje in (densa, lexica):
        rangos = np.argsort(np.argsort(-puntaje))
        rrf += 1.0 / (RRF_K + rangos + 1)
    salida = []
    for i in np.argsort(-rrf)[:limite]:
        f = fragmentos[i]
        donde = f"{f['archivo']}, página {f['pagina']} del PDF" if f["pagina"] else f["archivo"]
        salida.append(f"[Fuente: {donde}]\n{f['texto']}")
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

    def _run(self, query: str) -> str:
        try:
            resultado = buscar(query)
        except Exception as error:  # una consulta fallida no debe tumbar la tarea
            resultado = f"Error al consultar los manuales: {type(error).__name__}: {error}"
        _registrar(self.name, query, resultado)
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


def calcular(expresiones: str) -> str:
    """Evalua una o varias lineas (separadas por salto de linea o ';'). Cada linea es una expresion
    o una asignacion 'nombre = expresion' que se puede usar en las lineas siguientes."""
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
            valor = _evaluar(ast.parse(expresion, mode="eval"), variables)
        except ZeroDivisionError:
            return f"Error en '{linea}': división por cero"
        except (SyntaxError, ValueError, TypeError, OverflowError) as error:
            return f"Error en '{linea}': {error}"
        valor = float(valor)
        texto = f"{valor:.6g}"
        if nombre:
            variables[nombre] = valor
            salida.append(f"{nombre} = {expresion} = {texto}")
        else:
            salida.append(f"{expresion} = {texto}")
    return "\n".join(salida) or "Error: no se recibió ninguna expresión"


class _EntradaCalculadora(BaseModel):
    expresiones: str = Field(
        ...,
        description="Una o varias líneas separadas por ';'. Ejemplo: 'm = 3.2 + 0.98; d = 0.085; T = m * g * d'",
    )


class Calculadora(BaseTool):
    name: str = "calculadora"
    description: str = (
        "Calcula expresiones numéricas exactas. Úsala para todo cálculo del informe (masas, pares, inercias, "
        "fuerzas, tiempos, corrientes, sumas de BOM y puntajes ponderados) en lugar de calcular de memoria. "
        "Reúne en una sola llamada todos los cálculos de una sección (por ejemplo, todo el BOM o toda la "
        "evaluación de conceptos), con una línea por cálculo. "
        "Admite + - * / ** %, paréntesis, variables con 'nombre = expresión' separadas por ';', las funciones "
        "sqrt, sin, cos, tan, asin, acos, atan, atan2, radians, degrees, log, log10, exp, hypot, abs, min, max, "
        "round, sum y las constantes pi, e y g = 9.81. Trabaja sin unidades: usa unidades del SI y escribe en el "
        "informe la fórmula y el resultado con su unidad."
    )
    args_schema: type[BaseModel] = _EntradaCalculadora
    max_usage_count: int | None = MAX_USOS_CALCULADORA

    def _run(self, expresiones: str) -> str:
        resultado = calcular(expresiones)
        _registrar(self.name, expresiones, resultado)
        return resultado


def herramientas_agente() -> list[BaseTool]:
    """Instancias nuevas para cada agente: cada una lleva su propio contador de usos por tarea."""
    return [BuscarManuales(), Calculadora()]


if __name__ == "__main__":
    # Construye (o verifica) el indice y hace una consulta de prueba sin LLM.
    consulta = " ".join(sys.argv[1:]) or "allowable torque and moment of inertia of axis 6"
    print(f"[VDI] Documentos en {KNOWLEDGE_DIR}:")
    for doc in documentos():
        print(f"[VDI]   {doc.name}")
    print(buscar(consulta))
