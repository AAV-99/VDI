"""Registro de cada respuesta del modelo, con un interceptor HTTP de CrewAI.

Escribe una linea por peticion en RUN_DIR/_llm.log. Cuando la respuesta llega sin texto
(content vacio y sin llamadas a herramientas), guarda la peticion y la respuesta completas en RUN_DIR/llm_vacias/ para
poder analizarlas y repetirlas. Con VDI_GUARDAR_RESPUESTAS=1 guarda ademas el texto y el
razonamiento de todas las respuestas en RUN_DIR/llm_respuestas/. El registro nunca debe tumbar
la corrida: cualquier error interno se anota y la respuesta sigue su curso sin cambios.
"""
import itertools
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx
from crewai.llms.hooks.base import BaseInterceptor

COLUMNAS = ("hora", "n", "http", "finish_reason", "tokens_prompt", "tokens_respuesta", "segundos",
            "chars_content", "chars_razonamiento", "marca_fin_en_content", "marca_fin_en_razonamiento",
            "final_answer_en_razonamiento", "llamadas_herramientas")


class RegistroLLM(BaseInterceptor[httpx.Request, httpx.Response]):
    def __init__(self, carpeta: Path, marca_fin: str):
        self.carpeta = Path(carpeta)
        self.marca_fin = marca_fin
        self._contador = itertools.count(1)
        self._local = threading.local()
        self._lock = threading.Lock()

    def on_outbound(self, message: httpx.Request) -> httpx.Request:
        try:
            self._local.peticion = message.content
        except Exception:
            self._local.peticion = b""
        self._local.t0 = time.time()
        return message

    def on_inbound(self, message: httpx.Response) -> httpx.Response:
        try:
            message.read()
            self._registrar(message)
        except Exception as error:  # el registro nunca interrumpe la corrida
            self._escribir_linea(f"{datetime.now().isoformat(timespec='seconds')}\tERROR_REGISTRO\t{type(error).__name__}: {error}")
        return message

    def _registrar(self, respuesta: httpx.Response) -> None:
        n = next(self._contador)
        segundos = round(time.time() - getattr(self._local, "t0", time.time()))
        datos = respuesta.json() if respuesta.status_code == 200 else {}
        eleccion = (datos.get("choices") or [{}])[0]
        mensaje = eleccion.get("message") or {}
        content = mensaje.get("content") or ""
        razonamiento = mensaje.get("reasoning") or mensaje.get("reasoning_content") or ""
        llamadas = mensaje.get("tool_calls") or []
        uso = datos.get("usage") or {}
        fila = (
            datetime.now().isoformat(timespec="seconds"), n, respuesta.status_code, eleccion.get("finish_reason"),
            uso.get("prompt_tokens"), uso.get("completion_tokens"), segundos, len(content), len(razonamiento),
            self.marca_fin in content, self.marca_fin in razonamiento, "Final Answer" in razonamiento,
            ",".join((c.get("function") or {}).get("name", "?") for c in llamadas) or "-",
        )
        self._escribir_linea("\t".join(str(x) for x in fila))

        if os.environ.get("VDI_GUARDAR_RESPUESTAS") == "1" and respuesta.status_code == 200:
            destino = self.carpeta / "llm_respuestas"
            destino.mkdir(parents=True, exist_ok=True)
            (destino / f"{n:02d}_razonamiento.md").write_text(razonamiento, encoding="utf-8")
            (destino / f"{n:02d}_respuesta.md").write_text(content, encoding="utf-8")

        if respuesta.status_code == 200 and not content.strip() and not llamadas:
            destino = self.carpeta / "llm_vacias"
            destino.mkdir(parents=True, exist_ok=True)
            (destino / f"{n:02d}_peticion.json").write_bytes(getattr(self._local, "peticion", b"") or b"")
            (destino / f"{n:02d}_respuesta.json").write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")

    def _escribir_linea(self, linea: str) -> None:
        archivo = self.carpeta / "_llm.log"
        with self._lock:
            self.carpeta.mkdir(parents=True, exist_ok=True)
            nuevo = not archivo.exists()
            with open(archivo, "a", encoding="utf-8") as f:
                if nuevo:
                    f.write("\t".join(COLUMNAS) + "\n")
                f.write(linea + "\n")
