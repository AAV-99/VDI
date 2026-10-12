"""RAG de los documentos de consulta con RagTool (crewai_tools).

Para cambiar los documentos basta con agregar, reemplazar o borrar archivos en knowledge/
(PDF, DOCX, TXT o MD). En la siguiente corrida se detecta el cambio y se reindexa todo.
RagTool lee cada archivo y lo trocea con su propio chunker. Embeddings ONNX locales: no usan
API key ni gastan cuota de Gemini.
"""
import json
from functools import lru_cache
from pathlib import Path

from chromadb.config import Settings
from crewai_tools import RagTool

ROOT = Path(__file__).resolve().parents[4]
KNOWLEDGE_DIR = ROOT / "knowledge"
RAG_DB_DIR = ROOT / ".rag_db"
HUELLA_FILE = RAG_DB_DIR / "documentos_indexados.json"
EXTENSIONES = {".pdf", ".docx", ".txt", ".md"}
LIMITE_RESULTADOS = 4
UMBRAL_SIMILITUD = 0.3  # con 0.6 (valor por defecto de RagTool) quedan fuera fragmentos pertinentes

# Lo que el agente lee para decidir cuando usar la herramienta: actualizarlo si cambia el tema de los documentos
DESCRIPCION = (
    "Knowledge base written in ENGLISH with the CRP industrial robot manuals: robot operation, "
    "programming instructions, G4-CD60 electric cabinet, PLC user manual, welding technology and "
    "RA09A-07 arm specifications. Use it for technical data (I/O, wiring, safety, payload, maintenance, "
    "programming). ALWAYS write the query in English."
)


def documentos() -> list[Path]:
    return sorted(p for p in KNOWLEDGE_DIR.iterdir() if p.suffix.lower() in EXTENSIONES)


def _huella(docs: list[Path]) -> dict[str, list[int]]:
    """Nombre, tamano y fecha de modificacion de cada documento: si algo cambia, se reindexa."""
    return {p.name: [p.stat().st_size, int(p.stat().st_mtime)] for p in docs}


@lru_cache
def herramienta_rag() -> RagTool:
    """RagTool compartido por los agentes. El indice se guarda en .rag_db/ y solo se reconstruye
    cuando cambian los documentos de knowledge/ (o si se borra esa carpeta)."""
    tool = RagTool(
        name="buscar_manuales_crp",
        description=DESCRIPCION,
        similarity_threshold=UMBRAL_SIMILITUD,
        limit=LIMITE_RESULTADOS,
        config={
            "embedding_model": {"provider": "onnx"},
            "vectordb": {
                "provider": "chromadb",
                # CrewAI siempre crea un PersistentClient: se fija la carpeta del proyecto (no ./chroma)
                "config": {"settings": Settings(persist_directory=str(RAG_DB_DIR), is_persistent=True,
                                                allow_reset=True, anonymized_telemetry=False)},
            },
        },
    )
    docs = documentos()
    huella = _huella(docs)
    anterior = json.loads(HUELLA_FILE.read_text(encoding="utf-8")) if HUELLA_FILE.exists() else None
    if huella != anterior:
        print(f"[VDI] Indexando {len(docs)} documentos de {KNOWLEDGE_DIR} (tarda unos minutos)...", flush=True)
        # Se vacia la coleccion para que no queden fragmentos de documentos borrados o reemplazados
        cliente = tool.adapter._client
        cliente.delete_collection(collection_name=tool.collection_name)
        cliente.get_or_create_collection(collection_name=tool.collection_name)
        for doc in docs:
            # adapter.add y no tool.add: tool.add rechaza rutas fuera de la carpeta desde donde se ejecuta
            tool.adapter.add(str(doc))
            print(f"[VDI]   {doc.name}", flush=True)
        HUELLA_FILE.write_text(json.dumps(huella, ensure_ascii=False, indent=1), encoding="utf-8")
        print("[VDI] Indice listo", flush=True)
    return tool


def buscar(consulta: str) -> str:
    """Consulta directa al RagTool, sin LLM (para probar la recuperacion)."""
    return herramienta_rag().run(query=consulta)
