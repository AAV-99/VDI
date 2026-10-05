"""
app.py — Interfaz Streamlit para el framework multi-agente VDI 2206.

Flujo:
  1. El usuario escribe el contexto del problema y (opcional) adjunta documentos.
  2. Se genera input/project_context.txt (contexto + texto extraído de los adjuntos).
  3. Se ejecutan los agentes (simulados o reales).
  4. Se muestran los .md de output/ en pantalla.

Ejecutar:  uv run streamlit run app.py
"""
from __future__ import annotations

import io
import subprocess
from datetime import datetime
from pathlib import Path

import streamlit as st

from mock_flow import DOSSIER_NAME, run_mock_flow

ROOT = Path(__file__).resolve().parent
INPUT_DIR = ROOT / "input"
INPUT_FILE = INPUT_DIR / "project_context.txt"
OUTPUT_DIR = ROOT / "output"

# Comando del flujo real (ver README, paso 7)
REAL_FLOW_CMD = ["uv", "run", "kickoff"]

ALLOWED_TYPES = ["txt", "md", "csv", "pdf", "docx"]


# ----------------------------------------------------------------------------
# Extracción de texto de documentos adjuntos
# ----------------------------------------------------------------------------
def extract_text(uploaded_file) -> str:
    """Devuelve el texto de un archivo subido (txt, md, csv, pdf, docx)."""
    name = uploaded_file.name
    ext = Path(name).suffix.lower()
    data = uploaded_file.getvalue()
    print(f"[UI] Extrayendo texto de {name} ({len(data)} bytes, tipo {ext})")

    if ext in (".txt", ".md", ".csv"):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            print(f"[UI]    {name}: no es UTF-8, se usa latin-1")
            text = data.decode("latin-1")

    elif ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
        print(f"[UI]    {name}: {len(pages)} páginas")
        text = "\n\n".join(pages)

    elif ext == ".docx":
        import docx

        document = docx.Document(io.BytesIO(data))
        parts = [p.text for p in document.paragraphs if p.text.strip()]
        for table in document.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text.strip() for cell in row.cells))
        text = "\n".join(parts)

    else:
        raise ValueError(f"Tipo de archivo no soportado: {ext}")

    print(f"[UI]    {name}: {len(text)} caracteres extraídos")
    return text.strip()


# ----------------------------------------------------------------------------
# Construcción del .txt de entrada para los agentes
# ----------------------------------------------------------------------------
def build_input_txt(system_type, scope, requirements, extra, documents) -> str:
    """documents: lista de tuplas (nombre, texto)."""
    lines = [
        "=== CONTEXTO DEL PROYECTO VDI 2206 ===",
        f"Generado: {datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        "## TIPO DE SISTEMA",
        system_type.strip(),
        "",
        "## ALCANCE DEL PROYECTO",
        scope.strip(),
        "",
        "## REQUERIMIENTOS PRIMARIOS",
        requirements.strip() or "(no especificados)",
        "",
        "## CONTEXTO ADICIONAL",
        extra.strip() or "(ninguno)",
        "",
    ]
    if documents:
        lines += ["=== DOCUMENTOS ADJUNTOS ===", ""]
        for i, (name, text) in enumerate(documents, start=1):
            lines += [
                f"--- DOCUMENTO {i}: {name} ---",
                text or "(sin texto extraíble)",
                f"--- FIN DOCUMENTO {i} ---",
                "",
            ]
    return "\n".join(lines)


def run_real_flow() -> str:
    """Lanza el flujo real de CrewAI y devuelve su salida de consola."""
    print(f"[UI] Ejecutando flujo real: {' '.join(REAL_FLOW_CMD)}")
    result = subprocess.run(
        REAL_FLOW_CMD, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    print(f"[UI] Flujo real terminó con código {result.returncode}")
    log = (result.stdout or "") + "\n" + (result.stderr or "")
    if result.returncode != 0:
        raise RuntimeError(f"El flujo terminó con código {result.returncode}:\n{log[-3000:]}")
    return log


# ----------------------------------------------------------------------------
# Interfaz
# ----------------------------------------------------------------------------
st.set_page_config(page_title="VDI 2206 — Revisión de Diseño", page_icon="⚙️", layout="wide")
st.title("⚙️ VDI 2206 — Revisión de Diseño Mecatrónico")
st.caption("Describe el problema, adjunta documentos de soporte y ejecuta la tripulación de agentes.")

with st.sidebar:
    st.header("Configuración")
    mode = st.radio(
        "Modo de ejecución",
        ["Simulación (mock)", "Agentes reales (CrewAI)"],
        help="La simulación no consume cuota de Gemini.",
    )
    st.divider()
    st.markdown(f"**Input:** `{INPUT_FILE.relative_to(ROOT)}`")
    st.markdown(f"**Output:** `{OUTPUT_DIR.relative_to(ROOT)}/`")

st.subheader("1. Contexto del problema")
system_type = st.text_input(
    "Tipo de sistema *",
    placeholder="Ej.: Efector final (gripper) para manipulación de láminas de acero",
)
scope = st.text_area(
    "Alcance del proyecto *",
    height=160,
    placeholder="Qué se va a diseñar, dónde opera, con qué equipos se integra, qué piezas maneja...",
)
requirements = st.text_area(
    "Requerimientos primarios",
    height=120,
    placeholder="Ej.: Masa <= 1.5 kg. Tiempo de ciclo <= 4.0 s. Alimentación 24V DC...",
)
extra = st.text_area(
    "Contexto adicional",
    height=100,
    placeholder="Restricciones, normas aplicables, presupuesto, antecedentes...",
)

st.subheader("2. Documentos de soporte (opcional)")
uploads = st.file_uploader(
    "Adjunta especificaciones, fichas técnicas, normas, etc.",
    type=ALLOWED_TYPES,
    accept_multiple_files=True,
)

st.subheader("3. Ejecutar")
if st.button("🚀 Generar input y ejecutar agentes", type="primary"):
    if not system_type.strip() or not scope.strip():
        st.error("Completa al menos el tipo de sistema y el alcance del proyecto.")
        st.stop()

    # --- 3.1 Extraer texto de los adjuntos ---
    documents = []
    for up in uploads or []:
        try:
            documents.append((up.name, extract_text(up)))
        except Exception as exc:  # un adjunto dañado no debe tumbar todo
            print(f"[UI] ERROR extrayendo {up.name}: {exc}")
            st.warning(f"No se pudo leer **{up.name}**: {exc}")
    for name, text in documents:
        if not text:
            st.warning(f"**{name}** no tiene texto extraíble (¿PDF escaneado?).")

    # --- 3.2 Escribir el .txt ---
    input_txt = build_input_txt(system_type, scope, requirements, extra, documents)
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    INPUT_FILE.write_text(input_txt, encoding="utf-8")
    print(f"[UI] Input escrito en {INPUT_FILE} ({len(input_txt)} caracteres, {len(documents)} adjuntos)")
    st.session_state["input_txt"] = input_txt

    # --- 3.3 Limpiar resultados de la corrida anterior ---
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUTPUT_DIR.glob("*.md"):
        old.unlink()
        print(f"[UI] Eliminado resultado anterior: {old.name}")

    # --- 3.4 Ejecutar agentes ---
    try:
        if mode.startswith("Simulación"):
            progress = st.progress(0.0, text="Iniciando agentes...")
            run_mock_flow(
                INPUT_FILE,
                OUTPUT_DIR,
                on_step=lambda i, total, label: progress.progress(i / total, text=f"{label} ({i}/{total})"),
            )
            progress.empty()
        else:
            with st.spinner("Ejecutando agentes reales... esto puede tardar varios minutos."):
                st.session_state["run_log"] = run_real_flow()
        st.session_state["run_ok"] = True
        st.success("Ejecución completada.")
    except Exception as exc:
        print(f"[UI] ERROR en la ejecución: {exc}")
        st.session_state["run_ok"] = False
        st.error(f"Falló la ejecución de los agentes:\n\n```\n{exc}\n```")

# ----------------------------------------------------------------------------
# Resultados (persisten entre recargas gracias a session_state)
# ----------------------------------------------------------------------------
if "input_txt" in st.session_state:
    with st.expander("📄 Ver el .txt enviado a los agentes"):
        st.download_button(
            "Descargar project_context.txt",
            st.session_state["input_txt"],
            file_name="project_context.txt",
            mime="text/plain",
        )
        st.code(st.session_state["input_txt"], language="text")

if st.session_state.get("run_log"):
    with st.expander("🖥️ Log de consola de los agentes"):
        st.code(st.session_state["run_log"][-10000:], language="text")

if st.session_state.get("run_ok"):
    st.subheader("4. Resultados")
    md_files = sorted(OUTPUT_DIR.glob("*.md"))
    print(f"[UI] Archivos .md encontrados en output/: {[f.name for f in md_files]}")

    if not md_files:
        st.warning("La ejecución terminó pero no hay archivos .md en `output/`.")
    else:
        # El dossier (00_...) queda primero por orden alfabético
        tabs = st.tabs(["📘 Dossier" if f.name == DOSSIER_NAME else f.stem for f in md_files])
        for tab, md_file in zip(tabs, md_files):
            with tab:
                content = md_file.read_text(encoding="utf-8")
                st.download_button(
                    f"Descargar {md_file.name}",
                    content,
                    file_name=md_file.name,
                    mime="text/markdown",
                    key=f"dl_{md_file.name}",
                )
                st.markdown(content)
