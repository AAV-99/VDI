"""
mock_flow.py — Simulación del flujo de agentes VDI 2206.

Lee el .txt generado por la interfaz y escribe los mismos archivos .md que
produce el flujo real de CrewAI en la carpeta output/. Sirve para probar la
interfaz sin gastar cuota de la API de Gemini.

Uso directo (opcional):  uv run python mock_flow.py
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent
INPUT_FILE = ROOT / "input" / "project_context.txt"
OUTPUT_DIR = ROOT / "output"
DOSSIER_NAME = "00_vdi2206_consolidated_dossier.md"
# Si existe esta carpeta, la simulación devuelve estos reportes de ejemplo
SAMPLES_DIR = ROOT / "ejemplos" / "salida"

# (archivo, agente, título del reporte)
AGENTS = [
    ("01_requirements_and_system.md", "Integración", "Matriz de Requerimientos e Integración VDI 2206"),
    ("02_mechanical_subsystem.md", "Mecánica", "Concepto Mecánico y BOM Preliminar"),
    ("03_electronic_subsystem.md", "Electrónica", "Hardware de Control/Potencia y BOM Electrónico"),
    ("04_software_architecture.md", "Software", "Arquitectura Conceptual de Software/Firmware"),
    ("05_rams_maintenance.md", "Mantenimiento", "Evaluación RAMS y Diagnóstico PHM"),
    ("06_client_acceptance.md", "Validación del Cliente", "Dictamen de Calidad y Criterios de Aceptación"),
]


def _section(text: str, title: str) -> str:
    """Extrae el contenido de una sección '## TITULO' del .txt de entrada."""
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |^=== |\Z)", text, re.S | re.M)
    return match.group(1).strip() if match else ""


def run_mock_flow(
    input_path: Path = INPUT_FILE,
    output_dir: Path = OUTPUT_DIR,
    on_step: Optional[Callable[[int, int, str], None]] = None,
    delay: float = 0.8,
) -> Path:
    """Simula la tripulación. Devuelve la ruta del dossier consolidado."""
    print(f"[MOCK] Leyendo input: {input_path}")
    context = input_path.read_text(encoding="utf-8")
    print(f"[MOCK] Input leído: {len(context)} caracteres")

    system_type = _section(context, "TIPO DE SISTEMA") or "Sistema mecatrónico (sin título)"
    scope = _section(context, "ALCANCE DEL PROYECTO") or "_No especificado_"
    requirements = _section(context, "REQUERIMIENTOS PRIMARIOS") or "_No especificados_"
    n_docs = len(re.findall(r"^--- DOCUMENTO \d+:", context, re.M))
    print(f"[MOCK] Sistema: {system_type!r} | documentos adjuntos detectados: {n_docs}")

    output_dir.mkdir(parents=True, exist_ok=True)
    total = len(AGENTS) + 1
    reports: list[tuple[str, str]] = []

    for i, (filename, agent, title) in enumerate(AGENTS, start=1):
        if on_step:
            on_step(i, total, f"Agente de {agent}")
        print(f"[MOCK] ({i}/{total}) Agente de {agent} trabajando...")
        time.sleep(delay)

        body = (
            f"# {title}\n\n"
            f"> ⚠️ **Reporte simulado** — generado por `mock_flow.py`, no por los agentes reales.\n\n"
            f"**Sistema:** {system_type}\n\n"
            f"**Agente responsable:** {agent}\n\n"
            f"## Resumen\n\n"
            f"Análisis de la disciplina *{agent}* sobre el alcance recibido "
            f"({len(context)} caracteres de contexto, {n_docs} documento(s) adjunto(s)).\n\n"
            f"## Hallazgos (ejemplo)\n\n"
            f"| ID | Hallazgo | Criticidad | Acción propuesta |\n"
            f"|---|---|---|---|\n"
            f"| {agent[:3].upper()}-01 | Hallazgo de ejemplo 1 | Alta | Acción de ejemplo |\n"
            f"| {agent[:3].upper()}-02 | Hallazgo de ejemplo 2 | Media | Acción de ejemplo |\n"
        )
        sample = SAMPLES_DIR / filename
        if sample.exists():
            body = sample.read_text(encoding="utf-8")
            print(f"[MOCK]    usando reporte de ejemplo: {sample.name}")
        (output_dir / filename).write_text(body, encoding="utf-8")
        print(f"[MOCK]    -> escrito {filename} ({len(body)} caracteres)")
        reports.append((title, body))

    if on_step:
        on_step(total, total, "Consolidando dossier")
    print(f"[MOCK] ({total}/{total}) Consolidando dossier...")
    time.sleep(delay)

    dossier = (
        f"# Expediente Consolidado VDI 2206\n\n"
        f"> ⚠️ **Dossier simulado** — generado el {datetime.now():%Y-%m-%d %H:%M:%S}.\n\n"
        f"## 1. Sistema\n\n{system_type}\n\n"
        f"## 2. Alcance\n\n{scope}\n\n"
        f"## 3. Requerimientos primarios\n\n{requirements}\n\n"
        f"## 4. Reportes por disciplina\n\n"
    )
    for title, body in reports:
        # Baja un nivel los encabezados para anidarlos dentro del dossier
        nested = re.sub(r"^(#+) ", r"##\1 ", body, flags=re.M)
        dossier += nested + "\n---\n\n"

    sample = SAMPLES_DIR / DOSSIER_NAME
    if sample.exists():
        dossier = sample.read_text(encoding="utf-8")
        print(f"[MOCK] usando dossier de ejemplo: {sample.name}")
    dossier_path = output_dir / DOSSIER_NAME
    dossier_path.write_text(dossier, encoding="utf-8")
    print(f"[MOCK] Dossier escrito en {dossier_path} ({len(dossier)} caracteres)")
    return dossier_path


if __name__ == "__main__":
    if not INPUT_FILE.exists():
        INPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        INPUT_FILE.write_text(
            "=== CONTEXTO DEL PROYECTO VDI 2206 ===\n\n"
            "## TIPO DE SISTEMA\nGripper de prueba\n\n"
            "## ALCANCE DEL PROYECTO\nAlcance de prueba\n\n"
            "## REQUERIMIENTOS PRIMARIOS\nMasa <= 1.5 kg\n",
            encoding="utf-8",
        )
        print(f"[MOCK] No había input; se creó uno de prueba en {INPUT_FILE}")
    run_mock_flow()
