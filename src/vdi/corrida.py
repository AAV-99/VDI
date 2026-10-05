"""Carpeta y registro de trazabilidad de cada corrida.

Cada corrida escribe en su propia subcarpeta de output/ (ignorada por Git) y deja
un corrida.json con el commit del codigo, el modelo y los parametros usados.
"""
import json
import os
import platform
import subprocess
import urllib.request
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

INICIO = datetime.now().astimezone()

# VDI_RUN_DIR permite que correr.sh fije la carpeta y guarde ahi tambien el log.
RUN_DIR = Path(os.environ.get("VDI_RUN_DIR") or Path("output") / INICIO.strftime("%Y-%m-%d_%H%M"))
ARCHIVO = RUN_DIR / "corrida.json"
_RAIZ = Path(__file__).resolve().parents[2]


def _git(*args: str) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=_RAIZ, capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _version(paquete: str) -> str | None:
    try:
        return version(paquete)
    except PackageNotFoundError:
        return None


def _modelo_en_ollama(base_url: str, modelo: str) -> dict | None:
    """Parametros reales del modelo en el servidor (el contexto vive en el Modelfile, no en el codigo)."""
    try:
        req = urllib.request.Request(
            base_url.rstrip("/") + "/api/show",
            data=json.dumps({"model": modelo}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            info = json.load(resp)
        return {"parameters": info.get("parameters"), "details": info.get("details")}
    except Exception:
        return None


def _escribir(datos: dict) -> None:
    try:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        ARCHIVO.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # el registro nunca debe tumbar la corrida


def registrar_inicio(llm_config: dict, inputs: dict, parametros: dict) -> None:
    estado_git = _git("status", "--porcelain", "--untracked-files=no")
    modelo = str(llm_config.get("model", "")).split("/", 1)[-1]
    _escribir({
        "inicio": INICIO.isoformat(timespec="seconds"),
        "fin": None,
        "duracion_horas": None,
        "estado": "en curso",
        "carpeta": str(RUN_DIR),
        "servidor": platform.node(),
        "codigo": {
            "commit": _git("rev-parse", "HEAD"),
            "rama": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "cambios_sin_commit": None if estado_git is None else bool(estado_git),
        },
        "versiones": {"python": platform.python_version(), "crewai": _version("crewai")},
        "llm": llm_config,
        "modelo_en_ollama": _modelo_en_ollama(str(llm_config.get("base_url", "")), modelo),
        "parametros": parametros,
        "inputs": inputs,
    })


def registrar_fin(estado: str) -> None:
    try:
        datos = json.loads(ARCHIVO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    fin = datetime.now().astimezone()
    datos["fin"] = fin.isoformat(timespec="seconds")
    datos["duracion_horas"] = round((fin - INICIO).total_seconds() / 3600, 2)
    datos["estado"] = estado
    _escribir(datos)
