"""Modelo de diseno compartido (etapa F1): los datos del diseno en un grafo que los agentes escriben y el codigo revisa.

Hasta E2 los datos del diseno (requerimientos, BOM, presupuestos, interfaces) solo existian en el texto de los
informes, y las sumas y las referencias las revisaba el cliente cuando las veia. En F1 cada agente registra sus
datos con tablas Markdown (herramienta registrar_en_modelo) y el codigo los valida, calcula los totales y avisa de
las incoherencias. Los informes siguen escribiendose y pasando como contexto, igual que en E2 (decision D5).

Esquema (VDI/VDE 2206:2021): necesidades N, requerimientos RF/RNF/R/E, funciones F, modulos M, componentes CP,
parametros DP, interfaces I, ECR y el sistema SIS (concepto y principio). Relaciones: deriva_de (requerimiento ->
necesidad), asignada_a (funcion -> modulo), pertenece_a (componente -> modulo), realiza (componente -> funcion),
satisface (componente o modulo -> requerimiento), mide (parametro -> requerimiento cuyo valor es el parametro),
determina (parametro -> requerimiento al que contribuye), conecta (interfaz -> dos extremos), afecta (ECR -> elementos).

F2 agrega la justificacion: decisiones D-xx (asunto, alternativas, criterios, elegida, justificacion, evidencia y los
elementos que justifica; una decision reemplazada queda en el modelo), modos de falla MF-xx (FMEA de RAMS) y la
evidencia que crea el codigo al usar las herramientas: calculos CAL-xx, fuentes FT-xx (solo las que algun agente
cita) y matrices MAT-xx. Los agentes no registran evidencia: la citan por su ID.

Validacion en cuatro capas: 1) esquema (campos, enumeraciones, unidades con pint), 2) referencias (IDs existentes;
RF01 o RF-001 se normalizan a RF-01 con aviso), 3) propiedad (cada clase y campo tiene duenos) y 4) coherencia
(comprobaciones K-01 a K-16, solo avisos). Las capas 1 a 3 rechazan la tabla entera: se aplica toda o nada.

Transacciones: los cambios de una tarea van a una copia de trabajo y se confirman cuando la tarea termina bien
(se escriben en _modelo_ops.jsonl y _modelo.json) o se descartan si falla. Al reanudar, el modelo se reconstruye
reproduciendo las operaciones de las tareas confirmadas. Al final se exporta a RDF (_modelo.ttl).

Prueba sin el crew (lote de ejemplo con errores provocados):
    uv run python -m vdi.modelo
"""
import copy
import difflib
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path

import pint
from pydantic import BaseModel

# ---------- unidades ----------

UREG = pint.UnitRegistry()
UREG.define("USD = [moneda] = usd")
# unidades de conteo (adimensionales: ciclos/h, pasos, pines) y litros normales (Nl/s)
for _d in ("ciclo = 1 = ciclos", "operacion = 1 = operaciones", "paso = 1 = pasos", "pin = 1 = pines",
           "senal = 1 = senales", "unidad = 1 = unidades = und", "pieza = 1 = piezas", "Nl = liter = NL",
           # tiempo y multiplos en espanol, ya sin tildes ni enes (año -> ano)
           "ano = year = anos = anio = anios", "mes = month = meses", "semana = week = semanas", "dia = day = dias",
           "hora = hour = horas", "minuto = minute = minutos", "segundo = second = segundos",
           "millon = 1e6 = millones"):
    UREG.define(_d)
DIMENSION = {"masa": "[mass]", "costo": "[moneda]", "tiempo": "[time]"}
UNIDAD_BASE = {"masa": "kg", "costo": "USD", "tiempo": "s"}
SIN_UNIDAD = {"", "-", "—", "1", "adimensional", "n/a"}


def _texto_unidad(u: str) -> str:
    """Unidad escrita por el agente -> sintaxis de pint: N·m -> N*m, kg·m² -> kg*m**2, µm -> um, $ -> USD."""
    u = u.strip().replace("·", "*").replace("²", "**2").replace("³", "**3").replace("µ", "u").replace("μ", "u")
    u = re.sub(r"(?i)^(us\$|\$|usd)$", "USD", u.replace("Ω", "ohm"))
    u = re.sub(r"(?<=[a-zA-Z])([23])\b", r"**\1", u)  # cm3 -> cm**3, m2 -> m**2
    # millones de ciclos -> millones ciclos (pint leeria «de» como deci-carga elemental); USD por año -> USD/año
    u = re.sub(r"\s+por\s+", "/", re.sub(r"\s+del?\s+", " ", u))
    # sin tildes ni enes (señales -> senales, operación -> operacion) sin cambiar mayusculas (mA, Nl)
    return "".join(c for c in unicodedata.normalize("NFD", u) if unicodedata.category(c) != "Mn")


def conteos_nuevos(unidad: str) -> list[str]:
    """Palabras de la unidad que pint no conoce. Cada una se define como unidad de conteo (adimensional): celdas,
    intentos, puntos... Una lista de conteos nunca queda completa, y rechazar la fila cuesta otra ronda al agente."""
    if unidad.strip().lower() in SIN_UNIDAD:
        return []
    nuevas = []
    for palabra in dict.fromkeys(re.findall(r"[A-Za-z_]+", _texto_unidad(unidad))):
        try:
            UREG.parse_units(palabra)
        except (pint.errors.UndefinedUnitError, AttributeError, ValueError):
            UREG.define(f"{palabra} = 1")
            nuevas.append(palabra)
    return nuevas


def cantidad(valor: float, unidad: str) -> pint.Quantity:
    conteos_nuevos(unidad)
    return UREG.Quantity(valor, _texto_unidad(unidad) if unidad.strip().lower() not in SIN_UNIDAD else "")


def numero(texto: str) -> float:
    """Primer numero del texto, con coma o punto decimal."""
    m = re.search(r"-?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?", texto or "")
    if not m:
        raise ValueError(f"«{texto}» no es un número")
    return float(m.group(0).replace(",", "."))


def numero_en_texto(valor: float, texto: str) -> bool:
    """¿Aparece el valor en el texto? Acepta coma o punto decimal, separador de miles y un factor 1000 (g y kg,
    mm y m), porque el extracto puede usar otra unidad que el parametro."""
    vistos = set()
    for t in re.findall(r"\d[\d.,]*\d|\d", texto or ""):
        for v in (t.replace(",", "."), t.replace(",", ""), t.replace(".", "").replace(",", ".")):
            try:
                vistos.add(float(v))
            except ValueError:
                pass
    return any(abs(x - valor * f) <= 1e-6 * max(1.0, abs(valor * f)) for x in vistos for f in (1, 1000, 0.001))


def separar_cantidad(texto: str) -> tuple[float, str]:
    """«0,012 kg» -> (0.012, 'kg'). Lanza ValueError si no hay numero o la unidad no existe."""
    texto = (texto or "").strip()
    m = re.match(r"^\s*(-?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?)\s*(.*)$", texto)
    if not m:
        raise ValueError(f"«{texto}» no es una cantidad: escribe número y unidad, p. ej. «0.25 kg»")
    valor, unidad = float(m.group(1).replace(",", ".")), m.group(2).strip()
    try:
        cantidad(valor, unidad)
    except (pint.errors.UndefinedUnitError, pint.errors.DefinitionSyntaxError, AttributeError, TypeError, ValueError):
        raise ValueError(f"la unidad «{unidad}» de «{texto}» no se reconoce") from None
    return valor, unidad


# ---------- esquema ----------

ROLES = ("lider", "mecanica", "electronica", "software", "rams", "cliente")
DOMINIOS = ("mecanica", "electronica", "software")
NIVELES = ("N1", "N2", "N3", "N4")
PREFIJOS = {"N": "necesidad", "RF": "requerimiento", "RNF": "requerimiento", "R": "requerimiento",
            "E": "requerimiento", "F": "funcion", "M": "modulo", "CP": "componente", "DP": "parametro",
            "I": "interfaz", "ECR": "ecr", "SIS": "sistema", "X": "externo",
            "D": "decision", "MF": "modo_falla", "CAL": "calculo", "FT": "fuente", "MAT": "matriz"}
ID_RE = re.compile(r"^(RNF|RF|ECR|CP|DP|SIS|CAL|MAT|MF|FT|N|R|E|F|M|I|X|D)[\s\-‐‑‒–]?0*(\d{1,3})$|^(SIS)$", re.I)
EVIDENCIA = ("calculo", "fuente", "matriz")  # las crea el codigo al usar las herramientas; los agentes las citan
# campos de trazabilidad: cambiarlos no cambia el diseno, asi que no invalidan la decision que lo justifica (K-13)
TRAZA = {"satisface", "realiza", "mide", "determina", "deriva_de", "fuente", "nivel", "iteracion"}


class Elemento(BaseModel):
    id: str
    clase: str
    campos: dict = {}
    creado_por: str = ""
    tarea: str = ""
    cambio: int = 0  # paso (tarea y rol) del ultimo cambio de diseno, para K-13


# columnas de cada clase: campo -> (encabezados aceptados, tipo, obligatorio al crear)
# tipos: texto, entero15, enum:<a|b>, ids:<clases>, id:<clases>, cantidad:<dimension>, valor, unidad, nivel
CLASES = {
    "necesidad": {
        "texto": (("necesidad", "texto", "descripcion"), "texto", True),
        "stakeholder": (("stakeholder", "interesado"), "texto", False),
        "tipo": (("tipo",), "enum:exigencia|deseo", True),
        "importancia": (("importancia",), "entero15", True),
    },
    "requerimiento": {
        "texto": (("requerimiento", "texto", "descripcion"), "texto", True),
        "magnitud": (("magnitud",), "texto", False),
        "comparador": (("comparador",), "enum:≤|≥|=|rango", False),
        "valor": (("valor", "valor objetivo"), "valor", False),
        "unidad": (("unidad",), "unidad", False),
        "verificacion": (("verificacion", "metodo de verificacion", "metodo"), "texto", False),
        "deriva_de": (("deriva de", "necesidades", "deriva_de"), "ids:necesidad", False),
        # que total del sistema limita, si alguno: el codigo lo compara con la suma del BOM (K-04, K-06)
        "limita": (("limita", "total que limita", "total"), "enum:masa|costo", False),
    },
    "funcion": {
        "texto": (("funcion", "texto", "descripcion", "subfuncion"), "texto", True),
        "padre": (("padre", "funcion padre"), "id:funcion", False),
        "entradas": (("entradas", "flujos de entrada", "entrada"), "texto", False),
        "salidas": (("salidas", "flujos de salida", "salida"), "texto", False),
        "modulo": (("modulo", "asignada a", "asignada_a"), "id:modulo|sistema", False),  # SIS: la funcion global
    },
    "modulo": {
        "nombre": (("nombre", "modulo"), "texto", True),
        "dominios": (("dominios", "dominio"), "dominios", True),
        "presupuesto_masa": (("presupuesto de masa", "presupuesto masa", "masa"), "cantidad:masa", False),
        "presupuesto_costo": (("presupuesto de costo", "presupuesto costo", "costo"), "cantidad:costo", False),
        "presupuesto_tiempo": (("presupuesto de tiempo", "presupuesto tiempo", "tiempo"), "cantidad:tiempo", False),
    },
    "componente": {
        "nombre": (("nombre", "componente"), "texto", True),
        "dominio": (("dominio",), "dominio", False),
        "modulo": (("modulo", "pertenece a"), "id:modulo", True),
        "cantidad": (("cantidad", "cant"), "entero", True),
        "tipo": (("tipo", "propio o comercial"), "enum:propio|comercial", False),
        "modelo": (("modelo candidato", "modelo", "referencia"), "texto", False),
        "masa_unitaria": (("masa unitaria", "masa"), "cantidad:masa", True),
        "costo_unitario": (("costo unitario", "costo", "precio unitario"), "cantidad:costo", True),
        "realiza": (("realiza", "funciones"), "ids:funcion", False),
        "satisface": (("satisface",), "ids:requerimiento", False),
        "fuente": (("fuente",), "texto", False),
        "nivel": (("nivel", "nivel de evidencia"), "nivel", False),
    },
    "parametro": {
        "nombre": (("nombre", "parametro"), "texto", True),
        "valor": (("valor",), "valor", True),
        "unidad": (("unidad",), "unidad", False),
        "elemento": (("elemento", "pertenece a"), "id:sistema|modulo|componente", True),
        # mide: el parametro ES el valor que se compara con la meta del requerimiento (K-06 los compara);
        # determina: el parametro contribuye al requerimiento pero no es su medida
        "mide": (("mide",), "ids:requerimiento", False),
        "determina": (("determina", "contribuye a", "contribuye"), "ids:requerimiento", False),
        "fuente": (("fuente",), "texto", False),
        "nivel": (("nivel", "nivel de evidencia"), "nivel", False),
    },
    "interfaz": {
        "extremo_a": (("extremo a", "desde", "de"), "id:sistema|modulo|componente|externo", True),
        "extremo_b": (("extremo b", "hacia", "a"), "id:sistema|modulo|componente|externo", True),
        "tipo": (("tipo",), "enum:mecanica|energia|señal|datos|material", True),
        "protocolo": (("protocolo o niveles", "protocolo", "niveles"), "texto", False),
    },
    "ecr": {
        "descripcion": (("descripcion", "hallazgo"), "texto", True),
        "criterio": (("criterio de aceptacion", "criterio"), "texto", True),
        "afecta": (("afecta", "elementos afectados"), "ids:*", False),
        "disciplina": (("disciplina",), "enum:requerimientos|sintesis|mecanica|electronica|software|rams|sistema", False),
        "tipo": (("tipo",), "enum:corregible|prueba|cotizacion", False),
        "estado": (("estado",), "enum:abierta|cumple|no_cumple|pendiente", False),
    },
    # lo que esta fuera del sistema y con lo que tiene interfaces (robot, gabinete, pieza, operador): caja negra
    "externo": {
        "nombre": (("nombre", "elemento", "externo", "elemento externo"), "texto", True),
        "tipo": (("tipo",), "texto", False),
        "descripcion": (("descripcion", "datos", "datos conocidos"), "texto", False),
    },
    "sistema": {
        "concepto": (("concepto", "concepto seleccionado"), "texto", True),
        "principio": (("principio", "principio de solucion"), "texto", False),
    },
    # decision de diseno (IBIS/QOC en una fila): pregunta, opciones, criterios, eleccion y su argumento
    "decision": {
        "asunto": (("asunto", "pregunta", "decision"), "texto", True),
        "alternativas": (("alternativas", "opciones"), "texto", True),  # separadas por «;»
        "criterios": (("criterios",), "ids:requerimiento", True),
        "elegida": (("elegida", "alternativa elegida", "opcion elegida"), "texto", True),
        "justificacion": (("justificacion", "por que"), "texto", True),
        "evidencia": (("evidencia", "respaldo"), "ids:calculo|fuente|matriz|parametro", False),
        "justifica": (("justifica", "elementos que fija", "fija"), "ids:sistema|modulo|funcion|componente|parametro|interfaz", True),
        "motivada_por": (("motivada por", "motivo"), "ids:ecr|requerimiento", False),
        "reemplaza": (("reemplaza",), "id:decision", False),
    },
    # modo de falla (FMEA de RAMS); el RPN = S x O x D lo calcula el codigo
    "modo_falla": {
        "elemento": (("elemento", "componente", "modulo"), "id:sistema|modulo|componente", True),
        "modo": (("modo", "modo de falla"), "texto", True),
        "efecto": (("efecto",), "texto", True),
        "causa": (("causa",), "texto", False),
        "s": (("s", "severidad"), "entero110", True),
        "o": (("o", "ocurrencia"), "entero110", True),
        "d": (("d", "deteccion"), "entero110", True),
        "mitigacion": (("accion recomendada", "mitigacion", "medida"), "texto", False),
    },
    # evidencia (la crea el codigo; ver Modelo.nueva_evidencia)
    "calculo": {
        "expresion": (("expresion",), "texto", True),
        "resultado": (("resultado",), "texto", True),
    },
    "fuente": {
        "documento": (("documento", "url"), "texto", True),
        "titulo": (("titulo",), "texto", False),
        "ubicacion": (("ubicacion", "pagina"), "texto", False),
        "consulta": (("consulta",), "texto", False),
        "extracto": (("extracto",), "texto", False),
    },
    "matriz": {
        "tipo": (("tipo",), "texto", True),
        "tabla": (("tabla",), "texto", False),
        "resultado": (("resultado", "ranking"), "texto", True),
    },
}
# duenos por clase (y por campo cuando difieren); los componentes y sus parametros son de su dominio; las
# decisiones, de quien es dueno de lo que justifican (ver _dueno)
DUENOS = {"necesidad": {"lider"}, "requerimiento": {"lider"}, "funcion": {"lider"}, "modulo": {"lider"},
          "sistema": {"lider"}, "interfaz": {"lider"}, "ecr": {"cliente"},
          "externo": {"lider"}, "modo_falla": {"rams"}, "calculo": set(), "fuente": set(), "matriz": set()}
DUENOS_CAMPO = {("interfaz", "protocolo"): {"lider", *DOMINIOS},
                ("ecr", "disciplina"): {"lider"}, ("ecr", "tipo"): {"lider"}}
COMPARADORES = {"<=": "≤", "=<": "≤", "≤": "≤", "<": "≤", "max": "≤", "máx": "≤", ">=": "≥", "=>": "≥", "≥": "≥",
                ">": "≥", "min": "≥", "mín": "≥", "=": "=", "rango": "rango"}


def sin_tildes(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (t or "").lower()) if unicodedata.category(c) != "Mn")


def normalizar_id(texto: str) -> str | None:
    m = ID_RE.match((texto or "").strip().strip("*`"))
    if not m:
        return None
    if m.group(3):
        return "SIS"
    return f"{m.group(1).upper()}-{int(m.group(2)):02d}"


def ids_en_texto(texto: str) -> list[str]:
    """IDs citados dentro de un texto libre («Manual p. 12, FT-03 y CAL-2» -> FT-03, CAL-02)."""
    patron = r"\b(?:RNF|RF|ECR|CP|DP|CAL|MAT|MF|FT|SIS|N|R|E|F|M|I|X|D)[\-‐‑‒–]?\d{1,3}\b|\bSIS\b"
    return list(dict.fromkeys(j for t in re.findall(patron, texto or "") if (j := normalizar_id(t))))


def clase_de_id(i: str) -> str | None:
    return PREFIJOS.get(i.split("-")[0]) if i else None


def nombre_clase(texto: str) -> str | None:
    t = sin_tildes(texto).strip().rstrip("s")
    alias = {"necesidade": "necesidad", "requerimiento": "requerimiento", "requisito": "requerimiento",
             "funcione": "funcion", "funcion": "funcion", "modulo": "modulo", "componente": "componente",
             "parametro": "parametro", "interface": "interfaz", "interfaz": "interfaz", "interfac": "interfaz",
             "ecr": "ecr", "sistema": "sistema", "necesidad": "necesidad", "externo": "externo",
             "elemento externo": "externo", "elementos externo": "externo", "decisione": "decision",
             "decision": "decision", "modo de falla": "modo_falla", "modos de falla": "modo_falla",
             "modo_falla": "modo_falla", "calculo": "calculo", "fuente": "fuente", "matrice": "matriz",
             "matriz": "matriz"}
    return alias.get(t) or (t if t in CLASES else None)


def leer_tabla(tabla: str) -> tuple[list[str], list[list[str]]]:
    """Encabezado y filas de la primera tabla Markdown del texto, sin negritas."""
    filas = []
    for linea in (tabla or "").split("\n"):
        s = linea.strip()
        if not s.startswith("|"):
            if filas:
                break
            continue
        celdas = [c.replace("**", "").strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in celdas if c) and any(celdas):
            continue
        filas.append(celdas)
    return (filas[0], filas[1:]) if filas else ([], [])


# ---------- modelo ----------

class Modelo:
    """Almacen del modelo con copia de trabajo por tarea, registro de operaciones y validacion."""

    def __init__(self, carpeta: Path):
        self.carpeta = Path(carpeta)
        self.ops_ruta = self.carpeta / "_modelo_ops.jsonl"
        self.foto_ruta = self.carpeta / "_modelo.json"
        self.elementos: dict[str, Elemento] = {}
        self._confirmado: dict[str, Elemento] = {}
        self._pendientes: list[dict] = []
        self.tarea, self.iteracion = "-", 0
        self._paso, self._clave_paso = 0, None  # contador de (tarea, iteracion, rol) para Elemento.cambio
        # fuentes que devolvieron las herramientas y aun nadie cita: entran al modelo cuando un agente las cita
        self._candidatas: dict[str, tuple[str, dict, str]] = {}  # ID -> (clase, campos, rol)

    # --- transacciones ---

    def iniciar_tarea(self, tarea: str, iteracion: int = 0) -> None:
        self.tarea, self.iteracion = tarea, iteracion
        self._confirmado = copy.deepcopy(self.elementos)
        self._pendientes = []

    def confirmar(self) -> None:
        """La tarea termino bien: sus operaciones van al registro y se guarda la foto del modelo."""
        self.carpeta.mkdir(parents=True, exist_ok=True)
        with open(self.ops_ruta, "a", encoding="utf-8") as f:
            for op in self._pendientes:
                f.write(json.dumps(op, ensure_ascii=False) + "\n")
        self._pendientes = []
        self._confirmado = copy.deepcopy(self.elementos)
        self.guardar_foto()

    def descartar(self) -> None:
        """La tarea fallo: el modelo vuelve al ultimo estado confirmado."""
        self.elementos = copy.deepcopy(self._confirmado)
        self._pendientes = []

    def guardar_foto(self) -> None:
        foto = {"actualizado": datetime.now().astimezone().isoformat(timespec="seconds"),
                "elementos": {i: e.model_dump() for i, e in sorted(self.elementos.items())},
                "totales": self.totales_texto()}
        self.foto_ruta.write_text(json.dumps(foto, ensure_ascii=False, indent=1), encoding="utf-8")

    def reconstruir(self, tareas_validas: set[str] | None = None) -> int:
        """Reproduce las operaciones confirmadas (de las tareas cuyo informe existe, si se indican)."""
        self.elementos = {}
        self._paso, self._clave_paso = 0, None
        n = 0
        if self.ops_ruta.exists():
            for linea in self.ops_ruta.read_text(encoding="utf-8").split("\n"):
                if not linea.strip():
                    continue
                op = json.loads(linea)
                if tareas_validas is not None and op["tarea"] not in tareas_validas:
                    continue
                self._aplicar(op)
                n += 1
        self._confirmado = copy.deepcopy(self.elementos)
        return n

    def _aplicar(self, op: dict) -> None:
        clave = (op["tarea"], op["iteracion"], op["rol"])
        if clave != self._clave_paso:
            self._paso, self._clave_paso = self._paso + 1, clave
        i = op["id"]
        if op["accion"] == "eliminar":
            self.elementos.pop(i, None)
        elif i in self.elementos:
            e = self.elementos[i]
            # el agente suele reenviar la fila completa: solo cuenta como cambio de diseno lo que de verdad cambio
            if any(e.campos.get(k) != v for k, v in op["campos"].items() if k not in TRAZA):
                e.cambio = self._paso
            e.campos.update(op["campos"])
        else:
            self.elementos[i] = Elemento(id=i, clase=op["clase"], campos=dict(op["campos"]),
                                         creado_por=op["rol"], tarea=op["tarea"], cambio=self._paso)

    # --- escritura (capas 1 a 3) ---

    def registrar(self, clase_texto: str, tabla: str, rol: str) -> str:
        """Valida la tabla completa y la aplica toda o nada. Devuelve el resumen o la lista de errores por fila."""
        clase = nombre_clase(clase_texto)
        if not clase:
            return f"Clase «{clase_texto}» desconocida. Clases: {', '.join(c for c in CLASES if c not in EVIDENCIA)}."
        if clase in EVIDENCIA:
            return ("Los cálculos (CAL-xx), las fuentes (FT-xx) y las matrices (MAT-xx) los registra el código al usar "
                    "las herramientas: no se registran a mano; cita su ID en Fuente o en Evidencia.")
        encabezado, filas = leer_tabla(tabla)
        if not filas:
            return "No encontré una tabla Markdown con encabezado y al menos una fila."
        columnas, errores, avisos = self._columnas(clase, encabezado)
        if errores:
            return "Tabla rechazada, no se aplicó nada:\n" + "\n".join(f"- {e}" for e in errores)
        ops = []
        nuevos = {}  # IDs creados en esta misma tabla, para validar referencias entre filas
        for n, fila in enumerate(filas, 1):
            op, err, av = self._fila(clase, columnas, fila, rol, nuevos)
            errores += [f"Fila {n}: {e}" for e in err]
            avisos += [f"Fila {n}: {a}" for a in av]
            if op:
                ops.append(op)
                nuevos[op["id"]] = clase
        if errores:
            return "Tabla rechazada, no se aplicó nada. Corrige y vuelve a enviar la tabla completa:\n" + \
                "\n".join(f"- {e}" for e in errores)
        # las fuentes citadas por primera vez entran al modelo antes que las filas que las citan
        for j in sorted(nuevos.get("_citadas", ())):
            if j not in self.elementos and j in self._candidatas:
                clase_ev, campos_ev, rol_ev = self._candidatas.pop(j)
                ops.insert(0, self._op("crear", clase_ev, j, campos_ev, rol_ev, None))
        for op in ops:
            self._aplicar(op)
            self._pendientes.append(op)
        ops = [o for o in ops if o["clase"] == clase]  # el resumen cuenta solo las filas de la tabla
        cuenta = {a: sum(o["accion"] == a for o in ops) for a in ("crear", "modificar", "eliminar")}
        resumen = f"Aplicadas {len(ops)} filas de {clase}: " + ", ".join(f"{v} {k}" for k, v in cuenta.items() if v) + "."
        return resumen + ("\nAvisos:\n" + "\n".join(f"- {a}" for a in avisos) if avisos else "")

    def _columnas(self, clase: str, encabezado: list[str]) -> tuple[dict, list[str], list[str]]:
        """Indice de columna -> campo. Columnas especiales: id, accion, ecr."""
        esquema, columnas, errores, avisos = CLASES[clase], {}, [], []
        for k, h in enumerate(encabezado):
            h0 = re.sub(r"\(.*?\)", "", sin_tildes(h)).strip()
            if h0 in ("id", "codigo"):
                columnas[k] = "id"
            elif h0 in ("accion",):
                columnas[k] = "accion"
            elif h0 in ("ecr", "ecr que lo motiva"):
                columnas[k] = "ecr"
            else:
                campo = next((c for c, (alias, _, _) in esquema.items() if h0 in alias), None)
                if campo and campo not in columnas.values():
                    columnas[k] = campo
                else:
                    avisos.append(f"la columna «{h}» no es de {clase}: se ignora")
        if "id" not in columnas.values():
            errores.append(f"falta la columna ID. Columnas de {clase}: ID, " + ", ".join(
                a[0].capitalize() for a, _, _ in esquema.values()))
        return columnas, errores, avisos

    def _fila(self, clase: str, columnas: dict, fila: list[str], rol: str, nuevos: dict):
        errores, avisos = [], []
        celda = {campo: (fila[k] if k < len(fila) else "").strip() for k, campo in columnas.items()}
        bruto = celda.pop("id", "")
        i = normalizar_id(bruto)
        if not i:
            return None, [f"el ID «{bruto}» no tiene un formato válido (p. ej. RF-01, CP-03)"], []
        if i != bruto.strip("*` "):
            avisos.append(f"el ID «{bruto}» se normalizó a {i}")
        if clase_de_id(i) != clase:
            return None, [f"{i} es un ID de {clase_de_id(i)}, no de {clase}"], []
        accion_texto = sin_tildes(celda.pop("accion", ""))
        ecr = normalizar_id(celda.pop("ecr", "")) if celda.get("ecr") else None
        existe = i in self.elementos
        # en el ciclo cada cambio de un agente (no las ECR del cliente) lleva la ECR que lo motiva
        if self.iteracion and rol != "cliente" and clase != "ecr":
            if not ecr:
                errores.append("en el ciclo cada cambio lleva su ECR: agrega la columna ECR con la que lo motiva")
            elif ecr not in self.elementos:
                errores.append(f"la ECR {ecr} no existe en el modelo")
        if accion_texto.startswith("elimin"):
            if not existe:
                return None, errores + [f"{i} no existe: no se puede eliminar"], []
            err = errores + self._propiedad(clase, i, {}, rol)
            # las ECR que lo afectan no impiden eliminarlo: quedan como historia del cambio; las decisiones y los
            # modos de falla que lo citan tampoco (son de otro dueno): quedan senalados por K-13 y K-16
            citan = {e.id for e in self.elementos.values() if e.clase in ("decision", "modo_falla") and i in self._referencias(e)}
            dependientes = [e.id for e in self.elementos.values()
                            if e.clase not in ("ecr", "decision", "modo_falla") and i in self._referencias(e)]
            if dependientes:
                err.append(f"no se puede eliminar {i}: lo usan {', '.join(dependientes)}; cámbialos primero")
            if citan:
                avisos.append(f"{', '.join(sorted(citan))} citan {i}: quedan señalados en las comprobaciones")
            op = None if err else self._op("eliminar", clase, i, {}, rol, ecr)
            return op, err, avisos
        campos = {}
        for campo, texto in celda.items():
            # unidad «—» explicita en un requerimiento: adimensional o cualitativo a proposito (K-01 no avisa); al
            # modificar, «-» o «—» siguen siendo «sin cambio» para no borrar la unidad de un requerimiento existente
            if (clase, campo) == ("requerimiento", "unidad") and texto and sin_tildes(texto).strip().lower() in SIN_UNIDAD                     and not (existe and texto in ("-", "—")):
                campos[campo] = "—"
                continue
            if not texto or texto in ("-", "—"):
                continue
            # la Fase 1 (01) registra las funciones antes de que exista el SIS (se crea en la sintesis, 01b): el
            # Modulo SIS se omite con aviso en vez de rechazar la fila; la sintesis asigna los modulos
            if (clase, campo) == ("funcion", "modulo") and normalizar_id(texto) == "SIS" \
                    and "SIS" not in self.elementos and "SIS" not in nuevos:
                aviso = "Módulo SIS omitido: el sistema aún no existe; la síntesis (01b) asigna el módulo de cada función"
                if aviso not in avisos:
                    avisos.append(aviso)
                continue
            try:
                campos[campo] = self._convertir(clase, campo, texto, nuevos, avisos)
            except ValueError as e:
                errores.append(f"{CLASES[clase][campo][0][0]}: {e}")
        # dominio por defecto de un componente: el del agente
        if clase == "componente" and "dominio" not in campos and not existe and rol in DOMINIOS:
            campos["dominio"] = rol
        if not existe:
            faltan = [CLASES[clase][c][0][0] for c, (_, _, obl) in CLASES[clase].items() if obl and c not in campos]
            if faltan:
                errores.append(f"{i} es nuevo y le faltan: {', '.join(faltan)}")
        if clase in ("componente", "parametro") and not existe:
            if not campos.get("fuente"):
                campos["fuente"] = "Supuesto"
                campos.setdefault("nivel", "N1")
            campos.setdefault("nivel", "N1" if "supuesto" in sin_tildes(campos.get("fuente", "")) else "N2")
        if clase == "ecr" and not existe:
            campos.setdefault("estado", "abierta")
            campos["iteracion"] = self.iteracion
        if clase == "decision" and not existe and ecr and "motivada_por" not in campos:
            campos["motivada_por"] = [ecr]  # en el ciclo, la ECR que la provoca
        if clase == "decision" and campos.get("reemplaza") == i:
            errores.append(f"{i} no puede reemplazarse a sí misma")
        if clase == "requerimiento" and "limita" in campos:
            nuevos_limita = nuevos.setdefault("_limita", {})  # entre filas de la misma tabla
            final = {**(self.elementos[i].campos if existe else {}), **campos}
            dim = final["limita"]
            try:
                unidad_ok = cantidad(1, final.get("unidad", "")).check(DIMENSION[dim])
            except Exception:
                unidad_ok = False
            if not (unidad_ok and final.get("comparador") == "≤" and isinstance(final.get("valor"), (int, float))):
                errores.append(f"un requerimiento que limita el total de {dim} necesita comparador ≤, un valor y una "
                               f"unidad de {dim} (p. ej. ≤ | 3 | {UNIDAD_BASE[dim]})")
            otro = next((r.id for r in self.de_clase("requerimiento") if r.campos.get("limita") == dim and r.id != i), None)
            if otro or nuevos_limita.get(dim, i) != i:
                errores.append(f"ya hay un requerimiento que limita el total de {dim}: {otro or nuevos_limita[dim]}")
            nuevos_limita[dim] = i
        errores += self._propiedad(clase, i, campos, rol)
        if errores:
            return None, errores, avisos
        return self._op("modificar" if existe else "crear", clase, i, campos, rol, ecr), [], avisos

    def _op(self, accion, clase, i, campos, rol, ecr) -> dict:
        return {"tarea": self.tarea, "iteracion": self.iteracion, "rol": rol, "accion": accion, "clase": clase,
                "id": i, "campos": campos, "ecr": ecr, "hora": datetime.now().astimezone().isoformat(timespec="seconds")}

    def _convertir(self, clase: str, campo: str, texto: str, nuevos: dict, avisos: list):
        tipo = CLASES[clase][campo][1]
        if tipo == "texto":
            if campo == "fuente":  # las citas a evidencia dentro del texto deben existir (o ser fuentes candidatas)
                for j in ids_en_texto(texto):
                    if clase_de_id(j) not in EVIDENCIA:
                        continue
                    if j in self._candidatas:
                        nuevos.setdefault("_citadas", set()).add(j)
                    elif j not in self.elementos:
                        raise ValueError(f"{j} no existe: cita un ID que te haya devuelto una herramienta")
            return texto
        if tipo in ("entero", "entero15", "entero110"):
            v = numero(texto)
            tope = {"entero15": 5, "entero110": 10}.get(tipo)
            if v != int(v) or v < 1 or (tope and v > tope):
                raise ValueError(f"«{texto}» debe ser un entero {f'de 1 a {tope}' if tope else 'mayor que 0'}")
            return int(v)
        if tipo.startswith("enum:"):
            opciones = tipo[5:].split("|")
            t = COMPARADORES.get(texto.strip().lower(), texto) if campo == "comparador" else sin_tildes(texto).replace(" ", "_")
            t = next((o for o in opciones if sin_tildes(o) == sin_tildes(t)), None)
            if not t:
                raise ValueError(f"«{texto}» no es válido; opciones: {', '.join(opciones)}")
            return t
        if tipo == "nivel":
            t = texto.strip().upper()[:2]
            if t not in NIVELES:
                raise ValueError(f"«{texto}» no es un nivel; usa N1 (supuesto) o N2 (fuente o cálculo)")
            return t
        if tipo == "dominio":
            t = sin_tildes(texto).strip()
            if t not in DOMINIOS:
                raise ValueError(f"«{texto}» no es un dominio; opciones: {', '.join(DOMINIOS)}")
            return t
        if tipo == "dominios":
            ds = [sin_tildes(x).strip() for x in re.split(r"[;,/]| y ", texto) if x.strip()]
            malos = [d for d in ds if d not in DOMINIOS]
            if malos:
                raise ValueError(f"{', '.join(malos)} no son dominios; opciones: {', '.join(DOMINIOS)}")
            return ds
        if tipo.startswith("cantidad:"):
            dim = tipo.split(":")[1]
            valor, unidad = separar_cantidad(texto)
            if not cantidad(valor, unidad).check(DIMENSION[dim]):
                raise ValueError(f"«{texto}» no es {dim}: usa una unidad de {dim} ({UNIDAD_BASE[dim]})")
            return f"{valor:g} {unidad}"
        if tipo == "valor":
            partes = re.split(r"\s*(?:–|—|\.\.\.|…| a |-(?=\s*\d))\s*", texto.strip())
            try:
                return [numero(p) for p in partes][:2] if len(partes) > 1 else numero(texto)
            except ValueError:
                raise ValueError(f"«{texto}» no es un número ni un rango (p. ej. 3.0 o 2–4)") from None
        if tipo == "unidad":
            if sin_tildes(texto).strip() in SIN_UNIDAD:
                return ""
            nuevas = conteos_nuevos(texto)
            if nuevas:
                avisos.append(f"«{', '.join(nuevas)}» de la unidad «{texto.strip()}» no es una unidad física: se "
                              "registra como conteo (adimensional); si es masa, tiempo u otra magnitud, corrige la unidad")
            try:
                cantidad(1, texto)
            except Exception:
                raise ValueError(f"la unidad «{texto}» no se reconoce") from None
            return texto.strip()
        if tipo.startswith("id:") or tipo.startswith("ids:"):
            permitidas = tipo.split(":")[1].split("|")
            ids = []
            for parte in [p for p in re.split(r"[;,]|\s+y\s+", texto) if p.strip()]:
                j = normalizar_id(parte)
                if not j:
                    raise ValueError(f"«{parte.strip()}» no es un ID válido")
                if j != parte.strip().strip("*` "):
                    avisos.append(f"«{parte.strip()}» se normalizó a {j}")
                if "*" not in permitidas and clase_de_id(j) not in permitidas:
                    pista = "; SIS no es un módulo: usa el M-xx que contiene la función" if j == "SIS" else ""
                    raise ValueError(f"{j} es {clase_de_id(j)}; aquí va {' o '.join(permitidas)}{pista}")
                if j in self._candidatas and j not in self.elementos:
                    nuevos.setdefault("_citadas", set()).add(j)
                elif j not in self.elementos and j not in nuevos:
                    if clase_de_id(j) in EVIDENCIA:
                        raise ValueError(f"{j} no existe: cita un ID que te haya devuelto una herramienta")
                    existentes = sorted(e for e in self.elementos if clase_de_id(e) == clase_de_id(j))
                    lista = f"; existen: {', '.join(existentes)}" if existentes else f"; aún no hay {clase_de_id(j)}"
                    raise ValueError(f"{j} no existe{lista}")
                ids.append(j)
            if tipo.startswith("id:") and len(ids) != 1:
                raise ValueError(f"va un solo ID, no «{texto}»")
            return ids[0] if tipo.startswith("id:") else ids
        raise ValueError(f"tipo {tipo} sin conversión")

    def _dueno(self, clase: str, i: str, campos: dict) -> set[str]:
        if clase == "componente":
            dominio = campos.get("dominio") or (self.elementos[i].campos.get("dominio") if i in self.elementos else None)
            return {dominio} if dominio else set(DOMINIOS)
        if clase == "parametro":
            elem = campos.get("elemento") or (self.elementos[i].campos.get("elemento") if i in self.elementos else "SIS")
            e = self.elementos.get(elem)
            if e and e.clase == "componente":
                base = {e.campos.get("dominio")}
            elif e and e.clase == "modulo":  # los dominios del modulo fijan sus parametros (software no tiene BOM)
                base = {"lider", *e.campos.get("dominios", [])}
            else:
                base = {"lider"}
            return base | {"rams"}  # RAMS agrega parametros de confiabilidad a cualquier elemento
        if clase == "decision":
            # el lider decide sobre cualquier elemento; un dominio, solo si es dueno de todo lo que la decision justifica
            just = campos.get("justifica") or (self.elementos[i].campos.get("justifica", []) if i in self.elementos else [])
            comunes = None
            for j in just:
                e = self.elementos.get(j)
                d = self._dueno(e.clase, j, {}) if e else set()
                comunes = d if comunes is None else comunes & d
            return {"lider"} | (comunes or set())
        return DUENOS[clase]

    def _propiedad(self, clase: str, i: str, campos: dict, rol: str) -> list[str]:
        """Capa 3: cada clase y campo tiene duenos; un cambio ajeno se pide con una ECR."""
        errores = []
        duenos = self._dueno(clase, i, campos)
        for campo in campos or {"_": None}:
            permitidos = DUENOS_CAMPO.get((clase, campo), duenos)
            if clase == "ecr" and campo not in ("disciplina", "tipo"):
                permitidos = {"cliente"}
            if rol not in permitidos:
                quien = " o ".join(sorted(permitidos))
                errores.append(f"{i}{'' if campo == '_' else ' (' + campo + ')'} corresponde a {quien}, no a {rol}; "
                               "si hace falta cambiarlo, indícalo en tu informe")
        return sorted(set(errores))

    @staticmethod
    def _referencias(e: Elemento) -> set[str]:
        salida = set()
        for v in e.campos.values():
            for x in (v if isinstance(v, list) else [v]):
                if isinstance(x, str) and normalizar_id(x) == x:
                    salida.add(x)
        return salida - {e.id}

    # --- valores derivados ---

    def de_clase(self, clase: str) -> list[Elemento]:
        return [e for _, e in sorted(self.elementos.items(), key=lambda x: _orden_id(x[0])) if e.clase == clase]

    def _q(self, texto: str | None, dim: str) -> float | None:
        if not texto:
            return None
        valor, unidad = separar_cantidad(texto)
        return cantidad(valor, unidad).to(UNIDAD_BASE[dim]).magnitude

    def total_modulo(self, m: str, dim: str) -> float:
        campo = "masa_unitaria" if dim == "masa" else "costo_unitario"
        return sum(self._q(c.campos.get(campo), dim) * c.campos.get("cantidad", 1)
                   for c in self.de_clase("componente") if c.campos.get("modulo") == m and c.campos.get(campo))

    def total_sistema(self, dim: str) -> float:
        return sum(self.total_modulo(m.id, dim) for m in self.de_clase("modulo"))

    def requerimiento_de(self, dim: str) -> Elemento | None:
        """El requerimiento que el lider marco como limite del total del sistema (columna Limita: masa o costo)."""
        return next((r for r in self.de_clase("requerimiento") if r.campos.get("limita") == dim), None)

    def cumple(self, valor: float, unidad: str, r: Elemento) -> tuple[bool, float] | None:
        """¿valor cumple el requerimiento r? Devuelve (cumple, fraccion del limite usada) o None si no se pueden
        comparar (r sin valor, comparador o unidad, o dimensiones distintas)."""
        comp, objetivo, u_r = r.campos.get("comparador"), r.campos.get("valor"), r.campos.get("unidad", "")
        if not comp or objetivo is None:
            return None
        try:
            v = cantidad(valor, unidad).to(_texto_unidad(u_r) if u_r else "").magnitude
        except Exception:
            return None
        if comp == "rango" or isinstance(objetivo, list):
            lo, hi = (objetivo if isinstance(objetivo, list) else [objetivo, objetivo])[:2]
            return lo <= v <= hi, (v / hi if hi else 0)
        if comp == "≤":
            return v <= objetivo * 1.000001, (v / objetivo if objetivo else 0)
        if comp == "≥":
            return v >= objetivo * 0.999999, (objetivo / v if v else float("inf"))
        return abs(v - objetivo) <= abs(objetivo) * 0.005, 1.0

    def totales_texto(self) -> str:
        filas = ["| Módulo | Masa (kg) | Presupuesto de masa | Costo (USD) | Presupuesto de costo |", "|---|---|---|---|---|"]
        for m in self.de_clase("modulo"):
            filas.append(f"| {m.id} {m.campos.get('nombre', '')} | {self.total_modulo(m.id, 'masa'):.3f} | "
                         f"{m.campos.get('presupuesto_masa', '—')} | {self.total_modulo(m.id, 'costo'):.2f} | "
                         f"{m.campos.get('presupuesto_costo', '—')} |")
        limite = {}
        for dim in ("masa", "costo"):  # limite del sistema: el requerimiento marcado con Limita
            r = self.requerimiento_de(dim)
            limite[dim] = f"{r.id}: ≤ {r.campos.get('valor')} {r.campos.get('unidad', '')}".strip() if r else "—"
        filas.append(f"| Sistema | {self.total_sistema('masa'):.3f} | {limite['masa']} | {self.total_sistema('costo'):.2f} | "
                     f"{limite['costo']} |")
        return "\n".join(filas)

    # --- evidencia y decisiones (F2) ---

    def nueva_evidencia(self, clase: str, campos: dict, rol: str) -> str:
        """La llaman las herramientas: una MAT entra al modelo (en la transaccion de la tarea); un CAL o una FT quedan
        como candidatos y entran cuando un agente los cita (una llamada a la calculadora con todo el BOM no llena el
        modelo de calculos que nadie usa). La misma evidencia repetida conserva su ID. Devuelve el ID."""
        pref = next(p for p, c in PREFIJOS.items() if c == clase)
        campos = {k: v for k, v in campos.items() if v not in (None, "")}
        clave = {k: campos.get(k) for k in ("expresion", "resultado", "documento", "ubicacion", "extracto", "tabla")}
        for j, (c, cs, _) in self._candidatas.items():
            if c == clase and {k: cs.get(k) for k in clave} == clave:
                return j
        for e in self.de_clase(clase):
            if {k: e.campos.get(k) for k in clave} == clave:
                return e.id
        usados = [int(i.split("-")[1]) for i in [*self.elementos, *self._candidatas] if i.startswith(pref + "-")]
        i = f"{pref}-{max(usados, default=0) + 1:02d}"
        if clase in ("fuente", "calculo"):
            self._candidatas[i] = (clase, campos, rol)
        else:
            op = self._op("crear", clase, i, campos, rol, None)
            self._aplicar(op)
            self._pendientes.append(op)
        return i

    def reemplazada_por(self, d: str) -> str | None:
        return next((x.id for x in self.de_clase("decision") if x.campos.get("reemplaza") == d), None)

    def vigentes(self) -> list[Elemento]:
        return [d for d in self.de_clase("decision") if not self.reemplazada_por(d.id)]

    @staticmethod
    def rpn(mf: Elemento) -> int | None:
        s, o, d = (mf.campos.get(k) for k in ("s", "o", "d"))
        return s * o * d if all(isinstance(x, int) for x in (s, o, d)) else None

    @staticmethod
    def cuantitativo(r: Elemento) -> bool:
        """Requerimiento con meta numerica que se puede medir (no un «= 1 —» cualitativo, que se cierra con evidencia)."""
        c = r.campos
        if not c.get("comparador") or not isinstance(c.get("valor"), (int, float, list)):
            return False
        return not (c["comparador"] == "=" and c.get("unidad") == "—")

    # --- comprobaciones de coherencia (capa 4) ---

    def comprobar(self, cuales: list[str] | None = None) -> list[tuple[str, str]]:
        """(K, mensaje) de las comprobaciones pedidas (todas por defecto). Solo avisan; no cambian nada."""
        salida = []
        reqs = self.de_clase("requerimiento")  # RF, RNF, R y E (las especificaciones no derivan de una necesidad: K-02)
        comps = self.de_clase("componente")
        modulos = self.de_clase("modulo")
        todas = cuales or [f"K-{n:02d}" for n in range(1, 17)]
        if "K-01" in todas:
            for r in reqs:
                faltan = [c for c in ("valor", "unidad", "comparador", "verificacion") if r.campos.get(c) in (None, "")]
                if faltan:
                    salida.append(("K-01", f"{r.id} sin {', '.join(faltan)}" +
                                   (" (escribe — en Unidad si es adimensional o cualitativo)" if "unidad" in faltan else "")))
        if "K-02" in todas:
            salida += [("K-02", f"{r.id} no deriva de ninguna necesidad") for r in reqs
                       if not r.campos.get("deriva_de") and not r.id.startswith("E-")]
        if "K-03" in todas:
            salida += [("K-03", f"{f.id} no está asignada a un módulo") for f in self.de_clase("funcion") if not f.campos.get("modulo")]
            salida += [("K-03", f"{m.id} sin presupuesto de {d}") for m in modulos for d in ("masa", "costo")
                       if not m.campos.get(f"presupuesto_{d}")]
        for dim in ("masa", "costo"):
            req = self.requerimiento_de(dim)
            limite = self._q(f"{req.campos['valor']} {req.campos['unidad']}", dim) if req else None
            if "K-04" in todas and limite is not None:
                suma = sum(self._q(m.campos.get(f"presupuesto_{dim}"), dim) or 0 for m in modulos)
                if suma > limite * 1.000001:
                    salida.append(("K-04", f"la suma de presupuestos de {dim} ({suma:g} {UNIDAD_BASE[dim]}) supera {req.id} ({limite:g})"))
            if "K-05" in todas:
                # un modulo puede pasarse de su presupuesto si otro le cede margen: solo avisa cuando el total del
                # sistema ya usa el 90 % de su limite; con margen, el exceso de un modulo es contabilidad (en F1 y F1b
                # abria ECR que solo reasignaban presupuestos) y queda visible en la consulta de presupuestos
                justo = limite is not None and self.total_sistema(dim) >= 0.9 * limite
                for m in modulos:
                    pres = self._q(m.campos.get(f"presupuesto_{dim}"), dim)
                    real = self.total_modulo(m.id, dim)
                    if justo and pres is not None and real > pres * 1.000001:
                        salida.append(("K-05", f"{m.id}: {dim} {real:.4g} {UNIDAD_BASE[dim]} supera su presupuesto de {pres:g}"))
            if "K-06" in todas and limite is not None:
                total = self.total_sistema(dim)
                if total > limite * 1.000001:
                    salida.append(("K-06", f"{dim} total {total:.4g} {UNIDAD_BASE[dim]} supera {req.id} ({limite:g})"))
                elif total >= 0.9 * limite:
                    salida.append(("K-06", f"{dim} total {total:.4g} {UNIDAD_BASE[dim]} usa el {total / limite:.0%} de {req.id}"))
        if "K-06" in todas:
            # el parametro que mide un requerimiento se compara con su meta, con unidades (los que solo contribuyen
            # no: un tiempo parcial no cumple ni incumple el tiempo de ciclo)
            medidos = {r for p in self.de_clase("parametro") for r in p.campos.get("mide", [])}
            sin_medida = [r.id for r in reqs if self.cuantitativo(r) and r.id not in medidos and not r.campos.get("limita")]
            if sin_medida:
                salida.append(("K-06", f"con meta numérica y sin un parámetro que los mida (columna Mide), su cumplimiento "
                                       f"no se puede calcular: {', '.join(sin_medida)}"))
            for p in self.de_clase("parametro"):
                if isinstance(p.campos.get("valor"), list):
                    continue
                for rid in p.campos.get("mide", []):
                    r = self.elementos.get(rid)
                    res = self.cumple(p.campos.get("valor"), p.campos.get("unidad", ""), r) if r else None
                    if res is None:
                        continue
                    ok, uso = res
                    texto = f"{p.id} {p.campos.get('nombre', '')} = {p.campos.get('valor'):g} {p.campos.get('unidad', '')}".strip()
                    if not ok:
                        salida.append(("K-06", f"{texto} no cumple {rid} ({r.campos.get('comparador')} {r.campos.get('valor')} {r.campos.get('unidad', '')})"))
                    elif uso >= 0.9 and r.campos.get("comparador") != "=":
                        salida.append(("K-06", f"{texto} usa el {uso:.0%} de {rid}"))
        if "K-07" in todas:
            for a in comps:
                for b in comps:
                    if a.id < b.id and a.campos.get("dominio") != b.campos.get("dominio"):
                        mismo_modelo = a.campos.get("modelo") and sin_tildes(a.campos["modelo"]) == sin_tildes(b.campos.get("modelo", ""))
                        parecido = difflib.SequenceMatcher(None, sin_tildes(a.campos.get("nombre", "")),
                                                           sin_tildes(b.campos.get("nombre", ""))).ratio() >= 0.85
                        if mismo_modelo or parecido:
                            salida.append(("K-07", f"posible doble conteo: {a.id} «{a.campos.get('nombre')}» ({a.campos.get('dominio')}) "
                                                   f"y {b.id} «{b.campos.get('nombre')}» ({b.campos.get('dominio')})"))
        if "K-08" in todas:
            # portador: un componente o modulo que lo satisface, un parametro que lo determina o, si limita un
            # total del sistema, el propio total (lo compara el codigo)
            portados = {r for e in comps + modulos for r in e.campos.get("satisface", [])}
            portados |= {r for p in self.de_clase("parametro") for r in p.campos.get("determina", []) + p.campos.get("mide", [])}
            portados |= {r.id for r in reqs if r.campos.get("limita")}
            salida += [("K-08", f"{r.id} sin componente, módulo ni parámetro que lo satisfaga o determine")
                       for r in reqs if r.id not in portados]
        if "K-09" in todas:
            for i in self.de_clase("interfaz"):
                doms = {self._dominio_de(i.campos.get("extremo_a")), self._dominio_de(i.campos.get("extremo_b"))} - {None}
                if len(doms) > 1 and not i.campos.get("protocolo"):
                    salida.append(("K-09", f"{i.id} entre dominios sin protocolo ni niveles"))
        if "K-10" in todas:
            for d in DOMINIOS:
                datos = [e for e in self.elementos.values() if e.clase in ("componente", "parametro") and self._dominio_de(e.id) == d]
                n1 = sum(e.campos.get("nivel") == "N1" for e in datos)
                if datos:
                    salida.append(("K-10", f"{d}: {n1} de {len(datos)} datos son supuestos (N1)"))
        vigentes = self.vigentes()
        if "K-11" in todas:
            for d in vigentes:
                c, falta = d.campos, []
                alts = [a.strip() for a in c.get("alternativas", "").split(";") if a.strip()]
                if len(alts) < 2:
                    falta.append("menos de dos alternativas (sepáralas con «;»)")
                elif not any(sin_tildes(c.get("elegida", "")) in sin_tildes(a) or sin_tildes(a) in sin_tildes(c.get("elegida", ""))
                             for a in alts):
                    falta.append("la elegida no está entre las alternativas")
                if not c.get("criterios"):
                    falta.append("sin criterios")
                if not c.get("evidencia"):
                    falta.append("sin evidencia")
                perdida = [j for j in c.get("evidencia", []) if j not in self.elementos]
                if perdida:
                    falta.append(f"su evidencia {', '.join(perdida)} ya no existe")
                if falta:
                    salida.append(("K-11", f"{d.id} ({c.get('asunto', '')}): " + "; ".join(falta)))
        if "K-12" in todas:
            # elementos clave: el sistema, los modulos y los componentes propios (los parametros no: serian demasiados
            # avisos y su valor ya se respalda con Fuente y Nivel)
            justificados = {j for d in vigentes for j in d.campos.get("justifica", [])}
            claves = {"sistema": [e for e in self.de_clase("sistema")], "modulo": modulos,
                      "componente": [c for c in comps if c.campos.get("tipo") == "propio"]}
            for es in claves.values():
                faltan = [e.id for e in es if e.id not in justificados]
                if faltan:
                    salida.append(("K-12", f"sin una decisión vigente que los justifique: {', '.join(faltan)}"))
        if "K-13" in todas:
            for d in vigentes:
                idos = [j for j in d.campos.get("justifica", []) if j not in self.elementos]
                cambiados = [j for j in d.campos.get("justifica", []) if j in self.elementos and self.elementos[j].cambio > d.cambio]
                if idos or cambiados:
                    salida.append(("K-13", f"{d.id} ({d.campos.get('asunto', '')}) justifica " +
                                   "; ".join(x for x in (f"{', '.join(cambiados)}, que cambió después" if cambiados else "",
                                                         f"{', '.join(idos)}, que ya no existe" if idos else "") if x) +
                                   ": revísala (modifícala) o reemplázala con una decisión nueva"))
        if "K-14" in todas:
            # el valor de un parametro que cita una fuente debe aparecer en el extracto que devolvio la herramienta
            for p in self.de_clase("parametro"):
                fts = [j for j in ids_en_texto(p.campos.get("fuente", "")) if j.startswith("FT-") and j in self.elementos]
                v = p.campos.get("valor")
                if not fts or not isinstance(v, (int, float)):
                    continue
                if not any(numero_en_texto(v, self.elementos[j].campos.get("extracto", "")) for j in fts):
                    salida.append(("K-14", f"{p.id} {p.campos.get('nombre', '')} = {v:g} {p.campos.get('unidad', '')} cita "
                                           f"{', '.join(fts)}, pero ese valor no aparece en el extracto de la fuente"))
        if "K-15" in todas:
            for d in vigentes:
                ev = [self.elementos[j] for j in d.campos.get("evidencia", []) if j in self.elementos]
                if ev and all(e.clase == "parametro" and e.campos.get("nivel") == "N1" for e in ev):
                    salida.append(("K-15", f"{d.id} ({d.campos.get('asunto', '')}) se apoya solo en supuestos (N1): "
                                           f"{', '.join(e.id for e in ev)}"))
        if "K-16" in todas:
            salida += [("K-16", f"{mf.id} ({mf.campos.get('modo', '')}) es de {mf.campos.get('elemento')}, que ya no existe")
                       for mf in self.de_clase("modo_falla") if mf.campos.get("elemento") not in self.elementos]
        return salida

    def _dominio_de(self, i: str | None) -> str | None:
        e = self.elementos.get(i or "")
        if not e:
            return None
        if e.clase == "componente":
            return e.campos.get("dominio")
        if e.clase == "parametro":
            return self._dominio_de(e.campos.get("elemento"))
        if e.clase == "modulo":
            ds = e.campos.get("dominios", [])
            return ds[0] if len(ds) == 1 else None
        return None

    # --- lectura ---

    def _fila_texto(self, e: Elemento) -> str:
        partes = [f"{k}: {', '.join(map(str, v)) if isinstance(v, list) else v}" for k, v in e.campos.items() if v not in ("", None, [])]
        if e.clase == "decision" and (por := self.reemplazada_por(e.id)):
            partes.append(f"reemplazada por {por}")
        if e.clase == "modo_falla" and self.rpn(e):
            partes.append(f"RPN: {self.rpn(e)}")
        return f"- {e.id}: " + "; ".join(partes)

    def resumen_evidencia(self) -> str:
        n = {c: len(self.de_clase(c)) for c in EVIDENCIA}
        if not any(n.values()):
            return ""
        return (f"Evidencia registrada por las herramientas: {n['calculo']} cálculos (CAL), {n['fuente']} fuentes citadas (FT) "
                f"y {n['matriz']} matrices (MAT); consulta una con «elemento <ID>».")

    def vista(self, rol: str) -> str:
        """Lo que una tarea necesita del modelo, como texto para su descripcion."""
        if not self.elementos:
            return "El modelo de diseño está vacío."
        partes = []
        if rol in ("lider", "cliente", "integrador"):
            for clase in ("sistema", "externo", "necesidad", "requerimiento", "funcion", "modulo", "interfaz", "componente",
                          "parametro", "decision", "modo_falla", "ecr"):
                es = self.de_clase(clase)
                if es:
                    partes.append(f"{clase.capitalize()}:\n" + "\n".join(self._fila_texto(e) for e in es))
            partes.append(self.resumen_evidencia())
        else:
            partes.append("Requerimientos:\n" + "\n".join(self._fila_texto(e) for e in self.de_clase("requerimiento")))
            mods = [m for m in self.de_clase("modulo") if rol in m.campos.get("dominios", []) or rol == "rams"]
            partes.append("Módulos de tu dominio:\n" + "\n".join(self._fila_texto(m) for m in mods))
            ids_mods = {m.id for m in mods}
            ifs = [i for i in self.de_clase("interfaz") if {i.campos.get("extremo_a"), i.campos.get("extremo_b")} & ids_mods
                   or rol in {self._dominio_de(i.campos.get("extremo_a")), self._dominio_de(i.campos.get("extremo_b"))}]
            partes.append("Interfaces que te tocan:\n" + "\n".join(self._fila_texto(i) for i in ifs))
            if self.de_clase("externo"):
                partes.append("Elementos externos:\n" + "\n".join(self._fila_texto(x) for x in self.de_clase("externo")))
            propios = [c for c in self.de_clase("componente") if c.campos.get("dominio") == rol or rol == "rams"]
            partes.append(("Componentes" if rol == "rams" else "Tus componentes") + ":\n" +
                          ("\n".join(self._fila_texto(c) for c in propios) or "ninguno"))
            # decisiones vigentes que fijan algo tuyo (o de tus modulos) y modos de falla de tus componentes
            mios = {c.id for c in propios} | ids_mods | {p.id for p in self.de_clase("parametro")
                                                        if p.campos.get("elemento") in {c.id for c in propios} | ids_mods}
            ds = [d for d in self.vigentes() if rol == "rams" or set(d.campos.get("justifica", [])) & mios]
            if ds:
                partes.append("Decisiones vigentes que te tocan:\n" + "\n".join(self._fila_texto(d) for d in ds))
            mfs = [f for f in self.de_clase("modo_falla") if rol == "rams" or f.campos.get("elemento") in mios]
            if mfs:
                partes.append("Modos de falla de tus elementos:\n" + "\n".join(self._fila_texto(f) for f in mfs))
            partes.append(self.resumen_evidencia())
        partes.append("Totales calculados por el código:\n" + self.totales_texto())
        if rol in ("cliente", "integrador"):
            avisos = self.comprobar()
            partes.append("Comprobaciones automáticas (K-01 a K-16):\n" + ("\n".join(f"- {k}: {m}" for k, m in avisos) or "- sin avisos"))
        return "\n\n".join(p for p in partes if p)

    def consultar(self, consulta: str) -> str:
        """Consultas fijas: elemento <ID>, trazabilidad <ID>, componentes <dominio>, presupuestos,
        requerimientos sin portador, ecr abiertas."""
        c = sin_tildes(consulta).strip()
        m = re.match(r"(elemento|trazabilidad)\s+(\S+)$", c)
        if m:
            i = normalizar_id(m.group(2))
            if i not in self.elementos:
                return f"{i} no existe en el modelo."
            if m.group(1) == "elemento":
                return self._fila_texto(self.elementos[i])
            arriba = [self._fila_texto(self.elementos[j]) for j in sorted(self._referencias(self.elementos[i])) if j in self.elementos]
            abajo = [self._fila_texto(e) for e in self.elementos.values() if i in self._referencias(e)]
            return (f"{self._fila_texto(self.elementos[i])}\nUsa o deriva de:\n" + ("\n".join(arriba) or "- nada") +
                    "\nLo usan:\n" + ("\n".join(abajo) or "- nada"))
        m = re.match(r"componentes\s+(\S+)$", c)
        if m:
            cs = [self._fila_texto(x) for x in self.de_clase("componente") if x.campos.get("dominio") == m.group(1)]
            return "\n".join(cs) or f"No hay componentes de {m.group(1)}."
        if c == "presupuestos":
            return self.totales_texto()
        if c == "requerimientos sin portador":
            return "\n".join(f"- {msg}" for _, msg in self.comprobar(["K-08"])) or "Todos los requerimientos tienen portador."
        if c == "ecr abiertas":
            es = [self._fila_texto(e) for e in self.de_clase("ecr") if e.campos.get("estado") in ("abierta", "no_cumple")]
            return "\n".join(es) or "No hay ECR abiertas."
        if c == "decisiones":
            return "\n".join(self._fila_texto(d) for d in self.vigentes()) or "No hay decisiones vigentes."
        if c == "modos de falla":
            return "\n".join(self._fila_texto(f) for f in self.de_clase("modo_falla")) or "No hay modos de falla."
        return ("Consulta no reconocida. Usa: elemento <ID>, trazabilidad <ID>, componentes <dominio>, presupuestos, "
                "requerimientos sin portador, ecr abiertas, decisiones, modos de falla.")

    # --- apoyo al ciclo de correccion ---

    def disciplina_de(self, i: str) -> str | None:
        """Disciplina del ciclo duena de un elemento: requerimientos (01), sintesis (01b) o el dominio."""
        e = self.elementos.get(i)
        if not e:
            return None
        if e.clase in ("necesidad", "requerimiento", "externo"):  # los externos salen de la caja negra (Fase 1)
            return "requerimientos"
        if (e.clase == "parametro" and e.creado_por == "rams") or e.clase == "modo_falla":
            return "rams"
        if e.clase in ("componente", "parametro"):
            return self._dominio_de(i) or "sintesis"
        if e.clase == "decision":  # la disciplina de lo que justifica, si es una sola
            ds = {self.disciplina_de(j) for j in e.campos.get("justifica", [])} - {None}
            return ds.pop() if len(ds) == 1 else "sintesis"
        return None if e.clase in ("ecr", *EVIDENCIA) else "sintesis"  # funcion, modulo, interfaz, sistema

    def sugerencias_triaje(self, ecrs: list[str] | None = None) -> str:
        """Para cada ECR abierta, las disciplinas duenas de los elementos que afecta (sugerencia, decide el lider)."""
        lineas = []
        for e in self.de_clase("ecr"):
            if (ecrs is not None and e.id not in ecrs) or e.campos.get("estado") == "cumple":
                continue
            afecta = [j for j in e.campos.get("afecta", []) if j in self.elementos]
            ds = sorted({d for j in afecta if (d := self.disciplina_de(j))})
            lineas.append(f"- {e.id}: afecta {', '.join(afecta) or 'ningún elemento registrado'}"
                          f" -> {', '.join(ds) if ds else 'sin sugerencia'}")
        return "\n".join(lineas) or "- No hay ECR abiertas en el modelo."

    def vista_ecr(self, ecrs: list[str]) -> str:
        """Las ECR asignadas a una correccion y las filas de los elementos que afectan (evita consultarlos uno a uno)."""
        lineas = []
        for i in ecrs:
            e = self.elementos.get(i)
            if not e:
                continue
            lineas.append(self._fila_texto(e))
            lineas += ["  " + self._fila_texto(self.elementos[j]) for j in e.campos.get("afecta", []) if j in self.elementos]
        return "\n".join(lineas)

    def impacto_pendiente(self, disciplina: str) -> list[str]:
        """Disciplinas, distintas de la que corrige, con elementos que usan lo que cambio la tarea en curso (impacto
        calculado desde las relaciones, para compararlo con el que declara la tabla de cambios)."""
        cambiados = {op["id"] for op in self._pendientes}
        afectadas = set()
        for e in self.elementos.values():
            if e.clase != "ecr" and self._referencias(e) & cambiados:
                afectadas.add(self.disciplina_de(e.id))
        return sorted(d for d in afectadas - {disciplina, None})

    # --- exportacion ---

    def exportar_rdf(self, ruta: Path | None = None) -> Path:
        """Turtle con una clase por tipo de elemento y una propiedad por campo; las relaciones apuntan a nodos."""
        from rdflib import Graph, Literal, Namespace, RDF, RDFS
        vdi = Namespace("urn:vdi2206:esquema#")
        el = Namespace("urn:vdi2206:elemento#")
        g = Graph()
        g.bind("vdi", vdi)
        g.bind("el", el)
        relaciones = {"deriva_de", "modulo", "realiza", "satisface", "mide", "determina", "extremo_a", "extremo_b",
                      "elemento", "afecta", "padre", "criterios", "evidencia", "justifica", "motivada_por", "reemplaza"}
        for e in self.elementos.values():
            nodo = el[e.id]
            g.add((nodo, RDF.type, vdi[e.clase.capitalize()]))
            g.add((nodo, RDFS.label, Literal(e.id)))
            for campo, v in e.campos.items():
                for x in (v if isinstance(v, list) else [v]):
                    if campo in relaciones and isinstance(x, str) and x in self.elementos:
                        g.add((nodo, vdi[campo], el[x]))
                    elif x not in ("", None):
                        g.add((nodo, vdi[campo], Literal(x)))
        ruta = ruta or self.carpeta / "_modelo.ttl"
        g.serialize(destination=str(ruta), format="turtle")
        return ruta


def _orden_id(i: str) -> tuple:
    p, _, n = i.partition("-")
    return (list(PREFIJOS).index(p) if p in PREFIJOS else 99, int(n) if n.isdigit() else 0)


# ---------- prueba ----------

if __name__ == "__main__":
    import sys
    import tempfile
    sys.stdout.reconfigure(encoding="utf-8")
    carpeta = Path(tempfile.mkdtemp())
    mod = Modelo(carpeta)
    fallos = []

    def debe(cond: bool, nombre: str, detalle: str = "") -> None:
        print(("ok    " if cond else "FALLA ") + nombre)
        if not cond:
            fallos.append(nombre)
            print("      " + detalle.replace("\n", "\n      "))

    # 01: el lider registra necesidades y requerimientos
    mod.iniciar_tarea("requirements_ingestion_task")
    r = mod.registrar("necesidades", """
| ID | Necesidad | Stakeholder | Tipo | Importancia |
|---|---|---|---|---|
| N-01 | Sujetar la lámina sin que caiga | Operador | exigencia | 5 |
| N-02 | Que sea ligero | Integración | exigencia | 4 |""", "lider")
    debe(r.startswith("Aplicadas 2"), "crea necesidades", r)
    r = mod.registrar("requerimiento", """
| ID | Requerimiento | Magnitud | Comparador | Valor | Unidad | Verificación | Deriva de | Limita |
|---|---|---|---|---|---|---|---|---|
| RF01 | Fuerza de sujeción | fuerza | ≥ | 28.9 | N | Cálculo + ensayo | N-01 | |
| RNF-01 | Masa del efector | masa | ≤ | 3,0 | kg | Pesaje | N-02 | masa |
| R-01 | Costo de fabricación | costo | ≤ | 500 | USD | BOM | N-02 | costo |
| RNF-02 | Tiempo de ciclo | tiempo | ≤ | 4 | s | Ensayo | | |""", "lider")
    debe(r.startswith("Aplicadas 4") and "RF01" in r, "crea requerimientos y avisa la normalización de RF01", r)
    r = mod.registrar("requerimiento", "| ID | Requerimiento | Comparador | Valor | Unidad | Limita |\n|---|---|---|---|---|---|\n"
                      "| RNF-03 | Masa mínima | ≥ | 1 | kg | masa |", "lider")
    debe("comparador ≤" in r and "ya hay un requerimiento" in r, "Limita exige ≤ y un solo requerimiento por total", r)
    r = mod.registrar("requerimiento", "| ID | Requerimiento | Valor | Unidad | Deriva de |\n|---|---|---|---|---|\n"
                      "| RF-02 | Liberar | 300 | ms | N-09 |\n| RF-03 | Detectar | diez | ms | N-01 |", "lider")
    debe("rechazada" in r and "N-09 no existe" in r and "diez" in r and "RF-02" not in mod.elementos,
         "rechaza la tabla entera si una fila tiene errores", r)
    mod.confirmar()

    # 01b: modulos, funciones y concepto
    mod.iniciar_tarea("system_synthesis_task")
    r = mod.registrar("modulo", """
| ID | Nombre | Dominios | Presupuesto de masa | Presupuesto de costo |
|---|---|---|---|---|
| M-01 | Estructura y sujeción | mecánica | 2.0 kg | 300 USD |
| M-02 | Control y sensado | electrónica | 0.4 kg | 150 USD |""", "lider")
    debe(r.startswith("Aplicadas 2"), "crea módulos con presupuestos", r)
    r = mod.registrar("modulo", "| ID | Nombre | Dominios | Presupuesto de masa |\n|---|---|---|---|\n| M-03 | Otro | mecanica | 3 N |", "lider")
    debe("no es masa" in r, "rechaza una unidad de otra dimensión", r)
    r = mod.registrar("funcion", "| ID | Función | Módulo |\n|---|---|---|\n| F-01 | Generar sujeción | M-01 |\n| F-02 | Detectar pieza | M-02 |", "lider")
    debe(r.startswith("Aplicadas 2"), "crea funciones asignadas a módulos", r)
    r = mod.registrar("interfaz", "| ID | Extremo A | Extremo B | Tipo |\n|---|---|---|---|\n| I-01 | M-01 | M-02 | señal |", "lider")
    debe(r.startswith("Aplicadas 1"), "crea una interfaz", r)
    mod.confirmar()

    # 02: el mecanico registra su BOM; intenta tocar un presupuesto (no es suyo)
    mod.iniciar_tarea("mechanical_subsystem_design_task")
    r = mod.registrar("componente", """
| ID | Nombre | Módulo | Cantidad | Masa unitaria | Costo unitario | Modelo candidato | Satisface | Fuente | Nivel |
|---|---|---|---|---|---|---|---|---|---|
| CP-01 | Placa base | M-01 | 1 | 0.9 kg | 40 USD | Propia | RNF-01 | cálculo volumen × densidad | N2 |
| CP-02 | Ventosa | M-01 | 4 | 12 g | 6 USD | Schmalz SPB1 | RF-01 | | |
| CP-03 | Válvula 5/2 | M-01 | 1 | 0.15 kg | 30 USD | SMC SY3120 | | | |""", "mecanica")
    debe(r.startswith("Aplicadas 3"), "el mecánico registra su BOM (g se convierte)", r)
    debe(mod.elementos["CP-02"].campos["nivel"] == "N1", "sin fuente queda como supuesto N1")
    r = mod.registrar("modulo", "| ID | Presupuesto de masa |\n|---|---|\n| M-01 | 2.5 kg |", "mecanica")
    debe("corresponde a lider" in r, "capa 3: un dominio no cambia un presupuesto", r)
    mod.confirmar()

    # 03: el electronico; la valvula repetida debe dar K-07; luego una tarea que falla se descarta
    mod.iniciar_tarea("electronic_subsystem_design_task")
    r = mod.registrar("componente", """
| ID | Nombre | Módulo | Cantidad | Masa unitaria | Costo unitario | Modelo candidato |
|---|---|---|---|---|---|---|
| CP-04 | Válvula 5/2 | M-02 | 1 | 0.15 kg | 30 USD | SMC SY3120 |
| CP-05 | Sensor de presión | M-02 | 1 | 0.1 kg | 45 USD | SMC ZSE30A |""", "electronica")
    debe(r.startswith("Aplicadas 2"), "el electrónico registra su BOM", r)
    r = mod.registrar("componente", "| ID | Masa unitaria |\n|---|---|\n| CP-01 | 1 kg |", "electronica")
    debe("corresponde a mecanica" in r, "capa 3: el electrónico no modifica un componente mecánico", r)
    mod.confirmar()
    mod.iniciar_tarea("software_subsystem_design_task")
    mod.registrar("componente", "| ID | Nombre | Módulo | Cantidad | Masa unitaria | Costo unitario |\n|---|---|---|---|---|---|\n"
                  "| CP-06 | PLC extra | M-02 | 1 | 1 kg | 400 USD |", "electronica")
    mod.descartar()
    debe("CP-06" not in mod.elementos, "descartar devuelve el modelo al último estado confirmado")

    mod.iniciar_tarea("prueba_presupuestos")
    mod.registrar("modulo", "| ID | Presupuesto de masa |\n|---|---|\n| M-01 | 2.9 kg |\n| M-02 | 0.2 kg |", "lider")
    k = mod.comprobar(["K-04", "K-05"])
    debe(any(x == "K-04" for x, _ in k) and not any(x == "K-05" and "masa" in m for x, m in k),
         "K-04 (presupuestos sobre el límite); K-05 no avisa con margen en el sistema", str(k))
    mod.registrar("componente", "| ID | Nombre | Módulo | Cantidad | Masa unitaria | Costo unitario |\n|---|---|---|---|---|---|\n"
                  f"| CP-09 | Lastre | M-01 | 1 | {2.8 - mod.total_sistema('masa'):.3f} kg | 1 USD |", "mecanica")
    k = mod.comprobar(["K-05"])
    debe(any(x == "K-05" and "M-02" in m and "masa" in m for x, m in k),
         "K-05 avisa del módulo sobre su presupuesto con el sistema al 90 % de su límite", str(k))
    mod.descartar()
    mod.iniciar_tarea("prueba_parametros")
    r = mod.registrar("parametro", "| ID | Nombre | Valor | Unidad | Elemento | Determina |\n|---|---|---|---|---|---|\n"
                      "| DP-01 | Fuerza de retención | 20 | N | CP-02 | RF-01 |\n| DP-02 | Tiempo de ciclo | 3900 | ms | SIS | RNF-02 |", "mecanica")
    debe("DP-02" in r and "corresponde a" in r and "SIS" in r, "un parámetro del sistema es del líder", r)
    r = mod.registrar("parametro", "| ID | Nombre | Valor | Unidad | Elemento | Mide |\n|---|---|---|---|---|---|\n"
                      "| DP-01 | Fuerza de retención | 20 | N | CP-02 | RF-01 |", "mecanica")
    mod.elementos["SIS"] = Elemento(id="SIS", clase="sistema", campos={"concepto": "vacío"})
    mod.registrar("parametro", "| ID | Nombre | Valor | Unidad | Elemento | Mide |\n|---|---|---|---|---|---|\n"
                  "| DP-02 | Tiempo de ciclo | 3900 | ms | SIS | RNF-02 |", "lider")
    k = mod.comprobar(["K-06"])
    debe(any("DP-01" in m and "no cumple RF-01" in m for _, m in k) and any("DP-02" in m and "98%" in m for _, m in k),
         "K-06 compara el parámetro que mide un requerimiento con su meta, con unidades (ms frente a s)", str(k))
    mod.registrar("parametro", "| ID | Nombre | Valor | Unidad | Elemento | Determina |\n|---|---|---|---|---|---|\n"
                  "| DP-03 | Tiempo de soplado | 10 | s | CP-02 | RNF-02 |", "mecanica")
    k = mod.comprobar(["K-06"])
    debe(not any("DP-03" in m for _, m in k), "K-06 no compara un parámetro que solo contribuye (Determina)", str(k))
    r = mod.registrar("parametro", "| ID | Nombre | Valor | Unidad | Elemento |\n|---|---|---|---|---|\n"
                      "| DP-03 | Periodo de muestreo | 10 | ms | M-02 |", "electronica")
    debe(r.startswith("Aplicadas 1"), "un dominio del módulo registra parámetros del módulo", r)
    r = mod.registrar("parametro", "| ID | Nombre | Valor | Unidad | Elemento |\n|---|---|---|---|---|\n"
                      "| DP-04 | Periodo de muestreo | 10 | ms | M-02 |", "software")
    debe("corresponde a" in r, "un dominio ajeno al módulo no registra sus parámetros", r)
    mod.descartar()

    k = mod.comprobar()
    ks = {x for x, _ in k}
    debe("K-07" in ks, "K-07 detecta la válvula en dos dominios", str(k))
    debe(any(x == "K-02" and "RNF-02" in m for x, m in k), "K-02 detecta un requerimiento sin necesidad", str(k))
    debe(any(x == "K-08" and "RNF-02" in m for x, m in k), "K-08 detecta un requerimiento sin portador", str(k))
    debe(any(x == "K-09" and "I-01" in m for x, m in k), "K-09 detecta una interfaz sin protocolo", str(k))
    debe(abs(mod.total_sistema("masa") - (0.9 + 4 * 0.012 + 0.15 + 0.15 + 0.1)) < 1e-9, "suma de masas con unidades")
    debe(abs(mod.total_sistema("costo") - (40 + 24 + 30 + 30 + 45)) < 1e-9, "suma de costos")

    # ciclo: el cliente abre una ECR; el mecanico corrige citandola
    mod.iniciar_tarea("client_acceptance_review_task")
    r = mod.registrar("ecr", "| ID | Descripción | Criterio de aceptación | Afecta |\n|---|---|---|---|\n"
                      "| ECR-01 | Válvula contada dos veces | Una sola válvula en el BOM | CP-03, CP-04 |", "cliente")
    debe(r.startswith("Aplicadas 1"), "el cliente abre una ECR que afecta elementos", r)
    mod.confirmar()
    s = mod.sugerencias_triaje()
    debe("ECR-01: afecta CP-03, CP-04 -> " in s and "mecanica" in s, "sugiere al triaje el dueño de lo afectado", s)
    mod.iniciar_tarea("prueba_impacto", iteracion=1)
    mod.registrar("requerimiento", "| ID | Valor | ECR |\n|---|---|---|\n| RF-01 | 25 | ECR-01 |", "lider")
    imp = mod.impacto_pendiente("requerimientos")
    debe(imp and "requerimientos" not in imp, "calcula el impacto de un cambio sobre otras disciplinas", str(imp))
    v = mod.vista_ecr(["ECR-01"])
    debe(v.startswith("- ECR-01") and "CP-03" in v and "CP-04" in v, "vista de una corrección: la ECR y lo que afecta", v)
    r = mod.registrar("externo", "| ID | Nombre | Tipo | ECR |\n|---|---|---|---|\n| X-01 | Brida del robot | robot | ECR-01 |", "lider")
    r2 = mod.registrar("interfaz", "| ID | Extremo A | Extremo B | Tipo | ECR |\n|---|---|---|---|---|\n"
                       "| I-02 | X-01 | M-01 | mecánica | ECR-01 |", "lider")
    debe(r.startswith("Aplicadas 1") and r2.startswith("Aplicadas 1") and mod.disciplina_de("X-01") == "requerimientos",
         "un elemento externo puede ser extremo de una interfaz", r + r2)
    r = mod.registrar("requerimiento", "| ID | Requerimiento | Comparador | Valor | Unidad | Verificación | ECR |\n|---|---|---|---|---|---|---|\n"
                      "| E-09 | Tensión de control | = | 24 | V | Medición | ECR-01 |\n"
                      "| R-09 | Señal de pieza presente | = | 1 | — | Prueba | ECR-01 |", "lider")
    mod.registrar("parametro", "| ID | Nombre | Valor | Unidad | Elemento | Mide | ECR |\n|---|---|---|---|---|---|---|\n"
                  "| DP-09 | Tensión | 24 | V | M-01 | E-09 | ECR-01 |", "lider")
    k = mod.comprobar(["K-01", "K-06"])
    debe(r.startswith("Aplicadas 2") and not any("DP-09" in m or "R-09" in m for _, m in k),
         "«—» como unidad y sin aviso de uso con «=» si el valor coincide", r + str(k))
    mod.registrar("requerimiento", "| ID | Requerimiento | Comparador | Valor | Unidad | Verificación | ECR |\n|---|---|---|---|---|---|---|\n"
                  "| R-10 | Holgura | ≥ | 2 | | Medición | ECR-01 |\n"
                  "| E-09 | — | — | 24 | — | — | ECR-01 |", "lider")
    k = mod.comprobar(["K-01"])
    debe(any("R-10 sin unidad" in m for _, m in k) and mod.elementos["E-09"].campos["unidad"] == "V",
         "K-01 avisa de una unidad vacía; «—» al modificar no borra la unidad", str(k))
    r = mod.registrar("requerimiento", "| ID | Requerimiento | Comparador | Valor | Unidad | Verificación | ECR |\n|---|---|---|---|---|---|---|\n"
                      "| R-11 | Vida útil | ≥ | 2e6 | ciclos | Ensayo | ECR-01 |\n| R-12 | Cadencia | ≥ | 600 | ciclos/h | Ensayo | ECR-01 |\n"
                      "| R-13 | Caudal | ≤ | 1.5 | Nl/s | Medición | ECR-01 |\n| R-14 | Volumen | ≤ | 20 | cm3 | Cálculo | ECR-01 |\n"
                      "| R-15 | Entradas | ≤ | 4 | señales | Revisión | ECR-01 |", "lider")
    debe(r.startswith("Aplicadas 5") and cantidad(600, "ciclos/h").to("1/s").magnitude > 0.16
         and abs(cantidad(20, "cm3").to("L").magnitude - 0.02) < 1e-9, "unidades de conteo, Nl/s y cm3", r)
    r = mod.registrar("requerimiento", "| ID | Requerimiento | Comparador | Valor | Unidad | Verificación | ECR |\n|---|---|---|---|---|---|---|\n"
                      "| R-16 | Vida | ≥ | 5 | años | Ensayo | ECR-01 |\n| R-17 | Vida en ciclos | ≥ | 2 | millones de ciclos | Ensayo | ECR-01 |\n"
                      "| R-18 | Celdas atendidas | ≥ | 2 | celdas | Revisión | ECR-01 |", "lider")
    debe(r.startswith("Aplicadas 3") and "«celdas» de la unidad" in r and abs(cantidad(5, "años").to("h").magnitude - 43830) < 1
         and cantidad(2, "millones de ciclos").to("").magnitude == 2e6, "años, millones de ciclos y conteo desconocido con aviso", r)
    r = mod.registrar("sistema", "| ID | Concepto | ECR |\n|---|---|---|\n| SIS | Vacío | ECR-01 |", "lider")
    r2 = mod.registrar("interfaz", "| ID | Extremo A | Extremo B | Tipo | ECR |\n|---|---|---|---|---|\n"
                       "| I-03 | SIS | X-01 | mecánica | ECR-01 |", "lider")
    r3 = mod.registrar("funcion", "| ID | Función | Módulo | ECR |\n|---|---|---|---|\n| F-00 | Manipular la lámina | SIS | ECR-01 |", "lider")
    debe(r2.startswith("Aplicadas 1") and r3.startswith("Aplicadas 1"), "SIS como extremo de interfaz y dueño de la función global", r + r2 + r3)
    r = mod.registrar("componente", "| ID | Módulo | ECR |\n|---|---|---|\n| CP-02 | SIS | ECR-01 |", "mecanica")
    debe("SIS no es un módulo" in r, "SIS como módulo de un componente se rechaza con la pista", r)
    mod.descartar()
    mod.iniciar_tarea("domain_correction_task", iteracion=1)
    r = mod.registrar("componente", "| ID | Acción |\n|---|---|\n| CP-03 | eliminar |", "mecanica")
    debe("lleva su ECR" in r, "en el ciclo cada cambio exige su ECR", r)
    r = mod.registrar("modulo", "| ID | Acción | ECR |\n|---|---|---|\n| M-01 | eliminar | ECR-01 |", "lider")
    debe("no se puede eliminar" in r and "CP-01" in r, "no elimina un módulo que usan componentes", r)
    r = mod.registrar("componente", "| ID | Acción | ECR |\n|---|---|---|\n| CP-03 | eliminar | ECR-01 |", "mecanica")
    debe(r.startswith("Aplicadas 1") and "CP-03" not in mod.elementos, "elimina la válvula repetida citando la ECR", r)
    debe(not any(k == "K-07" for k, _ in mod.comprobar(["K-07"])), "sin la válvula repetida K-07 ya no avisa")
    r = mod.registrar("componente", "| ID | Cantidad | ECR |\n|---|---|---|\n| CP-02 | 6 | ECR-01 |", "mecanica")
    debe(r.startswith("Aplicadas 1") and mod.elementos["CP-02"].campos["cantidad"] == 6, "modifica solo el campo enviado", r)
    mod.confirmar()

    # F2: evidencia que crean las herramientas, decisiones y modos de falla
    mod.iniciar_tarea("prueba_f2_dominio")
    cal = mod.nueva_evidencia("calculo", {"expresion": "6 * 5.7", "resultado": "34.2"}, "mecanica")
    ft = mod.nueva_evidencia("fuente", {"documento": "Manual del robot", "ubicacion": "p. 12",
                                        "extracto": "Carga máxima 7 kg; par admisible de la muñeca 16,2 N·m"}, "mecanica")
    ft2 = mod.nueva_evidencia("fuente", {"documento": "Hoja de datos", "extracto": "Caudal 40 L/min"}, "mecanica")
    otra = mod.nueva_evidencia("fuente", {"documento": "Manual del robot", "ubicacion": "p. 12",
                                          "extracto": "Carga máxima 7 kg; par admisible de la muñeca 16,2 N·m"}, "electronica")
    mat = mod.nueva_evidencia("matriz", {"tipo": "evaluacion", "tabla": "A: 3, 4; B: 5, 2", "resultado": "1 B; 2 A"}, "lider")
    debe(cal == "CAL-01" and ft == "FT-01" and ft2 == "FT-02" and otra == "FT-01" and not {cal, ft} & set(mod.elementos)
         and mat == "MAT-01" and mat in mod.elementos,
         "CAL y FT quedan candidatos hasta que los citan (la misma fuente repetida conserva su ID); MAT entra directo")
    r = mod.registrar("calculo", "| ID | Expresión | Resultado |\n|---|---|---|\n| CAL-05 | 1+1 | 2 |", "mecanica")
    debe("registra el código" in r, "los agentes no registran evidencia a mano", r)
    r = mod.registrar("parametro", "| ID | Nombre | Valor | Unidad | Elemento | Mide | Fuente |\n|---|---|---|---|---|---|---|\n"
                      "| DP-05 | Fuerza de retención | 34.2 | N | CP-02 | RF-01 | CAL-01 |\n| DP-06 | Par admisible | 16.2 | N·m | CP-01 | | FT-09 |",
                      "mecanica")
    debe("FT-09 no existe" in r and "DP-05" not in mod.elementos, "rechaza una cita a evidencia que no existe", r)
    r = mod.registrar("parametro", "| ID | Nombre | Valor | Unidad | Elemento | Mide | Fuente |\n|---|---|---|---|---|---|---|\n"
                      "| DP-05 | Fuerza de retención | 34.2 | N | CP-02 | RF-01 | CAL-01 |\n"
                      "| DP-06 | Par admisible | 16.2 | N·m | CP-01 | | Manual, FT-01 |\n"
                      "| DP-07 | Carga útil | 9 | kg | CP-01 | | FT-01 |\n"
                      "| DP-08 | Holgura | 2 | mm | CP-01 | | |", "mecanica")
    debe(r.startswith("Aplicadas 4 filas") and "FT-01" in mod.elementos and "CAL-01" in mod.elementos
         and "FT-02" not in mod.elementos and mod.elementos["DP-06"].campos["nivel"] == "N2",
         "citar evidencia candidata la registra (y solo la citada)", r)
    k = mod.comprobar(["K-14"])
    debe(any("DP-07" in m for _, m in k) and not any("DP-06" in m for _, m in k),
         "K-14 avisa si el valor citado no está en el extracto (16,2 sí; 9 no)", str(k))
    k = mod.comprobar(["K-06"])
    debe(any("RNF-02" in m and "sin un parámetro que los mida" in m for _, m in k) and not any("RF-01" in m for _, m in k),
         "K-06 avisa de un requerimiento numérico sin parámetro que lo mida", str(k))
    r = mod.registrar("decision", """
| ID | Asunto | Alternativas | Criterios | Elegida | Justificación | Evidencia | Justifica |
|---|---|---|---|---|---|---|---|
| D-01 | Número de ventosas | 4 ventosas; 6 ventosas | RF-01; RNF-01 | 6 ventosas | Con 6 la retención supera RF-01 | CAL-01 | CP-02; DP-05 |
| D-02 | Holgura | 2 mm; 5 mm | RF-01 | 2 mm | Menor brazo de palanca | DP-08 | DP-08 |""", "mecanica")
    debe(r.startswith("Aplicadas 2"), "un dominio registra decisiones sobre sus elementos", r)
    r = mod.registrar("decision", "| ID | Asunto | Alternativas | Criterios | Elegida | Justificación | Justifica |\n|---|---|---|---|---|---|---|\n"
                      "| D-03 | Arquitectura | dos módulos | RNF-01 | dos módulos | Separa dominios | M-01; CP-02 |", "mecanica")
    debe("corresponde a lider" in r, "una decisión sobre un módulo es del líder", r)
    r = mod.registrar("decision", "| ID | Asunto | Alternativas | Criterios | Elegida | Justificación | Justifica |\n|---|---|---|---|---|---|---|\n"
                      "| D-03 | Arquitectura | dos módulos | RNF-01 | dos módulos | Separa dominios | M-01; M-02 |", "lider")
    k = mod.comprobar(["K-11", "K-12", "K-15"])
    debe(any(x == "K-11" and "D-03" in m and "menos de dos" in m and "sin evidencia" in m for x, m in k),
         "K-11 avisa de una decisión con una sola alternativa y sin evidencia", str(k))
    debe(any(x == "K-15" and "D-02" in m for x, m in k), "K-15 avisa de una decisión que solo se apoya en supuestos", str(k))
    debe(not any(x == "K-12" and ("DP-05" in m or "M-01" in m) for x, m in k),
         "K-12 no avisa de lo que ya justifica una decisión vigente", str(k))
    r = mod.registrar("modo de falla", "| ID | Elemento | Modo | Efecto | S | O | D |\n|---|---|---|---|---|---|---|\n"
                      "| MF-01 | CP-04 | Bobina abierta | Se suelta la lámina | 9 | 2 | 4 |", "mecanica")
    debe("corresponde a rams" in r, "los modos de falla son de RAMS", r)
    r = mod.registrar("modos de falla", "| ID | Elemento | Modo | Efecto | S | O | D | Acción recomendada |\n|---|---|---|---|---|---|---|---|\n"
                      "| MF-01 | CP-04 | Bobina abierta | Se suelta la lámina | 9 | 2 | 4 | Válvula normalmente cerrada |\n"
                      "| MF-02 | CP-02 | Labio desgastado | Fuga de vacío | 6 | 11 | 3 | |", "rams")
    debe("de 1 a 10" in r, "S, O y D van de 1 a 10", r)
    r = mod.registrar("modos de falla", "| ID | Elemento | Modo | Efecto | S | O | D | Acción recomendada |\n|---|---|---|---|---|---|---|---|\n"
                      "| MF-01 | CP-04 | Bobina abierta | Se suelta la lámina | 9 | 2 | 4 | Válvula normalmente cerrada |", "rams")
    debe(r.startswith("Aplicadas 1") and Modelo.rpn(mod.elementos["MF-01"]) == 72 and "RPN: 72" in mod.consultar("modos de falla"),
         "registra un modo de falla y calcula el RPN", r)
    mod.confirmar()
    mod.iniciar_tarea("prueba_f2_correccion", iteracion=2)
    r = mod.registrar("componente", "| ID | Nombre | Cantidad | Satisface | ECR |\n|---|---|---|---|---|\n"
                      "| CP-02 | Ventosa | 8 | RF-01 | ECR-01 |", "mecanica")
    mod.registrar("parametro", "| ID | Fuente | ECR |\n|---|---|---|\n| DP-08 | Medición en plano | ECR-01 |", "mecanica")
    k = mod.comprobar(["K-13"])
    debe(any("D-01" in m and "CP-02, que cambió después" in m for _, m in k) and not any("D-02" in m for _, m in k),
         "K-13 avisa de una decisión cuyo elemento cambió (un cambio de fuente no cuenta)", str(k))
    r = mod.registrar("decision", "| ID | Asunto | Alternativas | Criterios | Elegida | Justificación | Evidencia | Justifica | Reemplaza | ECR |\n"
                      "|---|---|---|---|---|---|---|---|---|---|\n| D-04 | Número de ventosas | 6 ventosas; 8 ventosas | RF-01 | 8 ventosas | "
                      "La ECR-01 pide más margen | CAL-01 | CP-02; DP-05 | D-01 | ECR-01 |", "mecanica")
    k = mod.comprobar(["K-13"])
    debe(r.startswith("Aplicadas 1") and mod.reemplazada_por("D-01") == "D-04" and not k
         and mod.elementos["D-04"].campos["motivada_por"] == ["ECR-01"],
         "una decisión nueva reemplaza a la anterior (que queda en el modelo) y toma la ECR como motivo", r + str(k))
    r = mod.registrar("componente", "| ID | Acción | ECR |\n|---|---|---|\n| CP-04 | eliminar | ECR-01 |", "electronica")
    k = mod.comprobar(["K-16"])
    debe(r.startswith("Aplicadas 1") and "MF-01 citan CP-04" in r and any("MF-01" in m for _, m in k),
         "eliminar un componente con modo de falla avisa y K-16 lo señala", r + str(k))
    debe(mod.disciplina_de("D-04") == "mecanica" and mod.disciplina_de("MF-01") == "rams" and mod.disciplina_de("CAL-01") is None,
         "disciplina de decisiones, modos de falla y evidencia")
    mod.confirmar()

    # reanudacion y exportacion
    otro = Modelo(carpeta)
    n = otro.reconstruir()
    debe({e: v.campos for e, v in otro.elementos.items()} == {e: v.campos for e, v in mod.elementos.items()},
         f"reconstruir desde el registro reproduce el modelo ({n} operaciones)")
    otro.reconstruir({"requirements_ingestion_task"})
    debe(set(otro.elementos) == {"N-01", "N-02", "RF-01", "RNF-01", "R-01", "RNF-02"}, "reconstruir solo las tareas indicadas")
    ttl = mod.exportar_rdf()
    debe("vdi:satisface el:RF-01" in ttl.read_text(encoding="utf-8"), "exporta RDF con las relaciones")
    debe("CP-01" in mod.consultar("trazabilidad RNF-01"), "consultar trazabilidad")
    print()
    print(mod.vista("mecanica"))
    print()
    print(mod.vista("cliente").split("Comprobaciones")[1])
    print()
    print(f"{'Todo bien' if not fallos else str(len(fallos)) + ' fallas'} (archivos en {carpeta})")
    sys.exit(1 if fallos else 0)
