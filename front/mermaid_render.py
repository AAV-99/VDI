"""
mermaid_render.py — Renderizado a prueba de fallos de diagramas Mermaid en Streamlit.

Los LLM generan Mermaid con errores típicos (paréntesis sin comillas en etiquetas,
"|" o "<=" dentro de nodos, títulos de subgraph con símbolos, etc.). Este módulo
lo resuelve en tres capas, de modo que NUNCA aparezca el recuadro rojo de error:

  1. sanitize_mermaid(): corrige en Python los errores conocidos (pone comillas a
     etiquetas de nodos y flechas, escapa comillas internas, etc.).
  2. En el navegador, mermaid.parse() valida el código ANTES de dibujarlo:
     intenta la versión saneada y, si falla, la original.
  3. Si ninguna versión es válida (o no carga mermaid.js por falta de internet),
     se muestra el código del diagrama como texto legible, con un aviso discreto.

Uso:
    from mermaid_render import render_markdown_with_mermaid
    render_markdown_with_mermaid(contenido_md)
"""
from __future__ import annotations

import hashlib
import json
import re

import streamlit as st
import streamlit.components.v1 as components

MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js"

# Bloques ```mermaid ... ``` o ~~~mermaid ... ~~~ (tolera espacios y mayúsculas)
FENCE_RE = re.compile(
    r"^[ \t]*(?P<fence>```|~~~)[ \t]*mermaid[ \t]*\r?\n(?P<code>.*?)^[ \t]*(?P=fence)[ \t]*$",
    re.S | re.M | re.I,
)

# ----------------------------------------------------------------------------
# Capa 1: saneo determinista
# ----------------------------------------------------------------------------
# Líneas de flowchart que no contienen nodos ni etiquetas: se copian tal cual
_SKIP_LINE_RE = re.compile(
    r"^\s*(%%|classDef\b|class\b|style\b|linkStyle\b|click\b|direction\b|accTitle\b|accDescr\b|end\s*$)",
    re.I,
)
# Flecha justo antes de una etiqueta |...|
_ARROW_END_RE = re.compile(r"(<?(?:-{2,}|={2,}|-\.+-)[->ox]?|<?-\.+->)\s*$")
# Formas de nodo: (apertura, cierre), de la más larga a la más corta
_SHAPES = [
    ("(((", ")))"), ("((", "))"), ("([", "])"), ("[[", "]]"), ("[(", ")]"),
    ("{{", "}}"), ("[/", "/]"), ("[\\", "\\]"), ("(", ")"), ("[", "]"), ("{", "}"),
]
_ID_CHAR_RE = re.compile(r"[\w]", re.UNICODE)


def _quote(label: str) -> str:
    """Devuelve la etiqueta entre comillas, escapando comillas internas."""
    label = label.strip()
    if len(label) >= 2 and label[0] == '"' and label[-1] == '"':
        inner = label[1:-1]
    else:
        inner = label
    inner = inner.replace('"', "#quot;")
    return f'"{inner}"'


def _logical_lines(code: str) -> list[str]:
    """Une líneas físicas cuando una etiqueta entre comillas ocupa varias líneas."""
    out, buf = [], ""
    for line in code.splitlines():
        buf = f"{buf}\n{line}" if buf else line
        if buf.count('"') % 2 == 0:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf + '"')  # comilla huérfana: se cierra para no romper todo
    return out


def _inline_edge_labels(line: str) -> str:
    """A -- texto --> B  ==>  A -->|"texto"| B   (también == ==> y -. .->)."""
    line = re.sub(r"(?<=\s)--\s+(?![->])([^\"|\n]+?)\s+-->", lambda m: f'-->|{_quote(m.group(1))}|', line)
    line = re.sub(r"(?<=\s)==\s+(?![=>])([^\"|\n]+?)\s+==>", lambda m: f'==>|{_quote(m.group(1))}|', line)
    line = re.sub(r"(?<=\s)-\.\s+([^\"|\n]+?)\s+\.->", lambda m: f'-.->|{_quote(m.group(1))}|', line)
    return line


def _find_shape_end(s: str, start: int, opener: str, closer: str) -> int | None:
    """Índice donde termina el cierre de la forma que empieza en `start` (exclusivo)."""
    open_ch = opener[0]
    close_ch = {"(": ")", "[": "]", "{": "}"}[open_ch]
    depth, j, in_q = 0, start, False
    while j < len(s):
        ch = s[j]
        if ch == '"':
            in_q = not in_q
        elif not in_q:
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    end = j + 1
                    if s[:end].endswith(closer) and end - len(closer) >= start + len(opener):
                        return end
                    return None
        j += 1
    return None


def _sanitize_flowchart_line(line: str, stats: dict) -> str:
    if _SKIP_LINE_RE.match(line):
        return line

    # Títulos de subgraph: "subgraph Título (x)" -> subgraph SG_n["Título (x)"]
    m = re.match(r"^(\s*subgraph\s+)(.+?)\s*$", line, re.I)
    if m:
        rest = m.group(2)
        if re.fullmatch(r"[\w]+", rest) or re.fullmatch(r"\"[^\"]*\"", rest):
            return line
        m2 = re.fullmatch(r"([\w]+)\s*\[(.*)\]", rest, re.S)
        if m2:
            stats["nodes"] += 0 if m2.group(2).strip().startswith('"') else 1
            return f"{m.group(1)}{m2.group(1)}[{_quote(m2.group(2))}]"
        stats["subgraphs"] += 1
        sid = "SG_" + hashlib.md5(rest.encode("utf-8")).hexdigest()[:6]
        return f"{m.group(1)}{sid}[{_quote(rest)}]"

    before = line
    line = _inline_edge_labels(line)
    if line != before:
        stats["edges"] += 1

    out: list[str] = []
    i, n = 0, len(line)
    while i < n:
        ch = line[i]

        # Texto ya entre comillas: se copia intacto
        if ch == '"':
            j = line.find('"', i + 1)
            j = n - 1 if j == -1 else j
            out.append(line[i : j + 1])
            i = j + 1
            continue

        # Etiqueta de flecha |...|
        if ch == "|" and _ARROW_END_RE.search("".join(out)):
            j = i + 1
            in_q = False
            while j < n and (line[j] != "|" or in_q):
                if line[j] == '"':
                    in_q = not in_q
                j += 1
            if j < n:
                label = line[i + 1 : j]
                quoted = _quote(label)
                if quoted != label:
                    stats["edges"] += 1
                out.append(f"|{quoted}|")
                i = j + 1
                continue

        # Identificador de nodo seguido de una forma: ID[...], ID(...), ID{...}
        if _ID_CHAR_RE.match(ch) and (i == 0 or not _ID_CHAR_RE.match(line[i - 1])):
            j = i
            while j < n and _ID_CHAR_RE.match(line[j]):
                j += 1
            node_id = line[i:j]
            handled = False
            for opener, closer in _SHAPES:
                if line.startswith(opener, j):
                    end = _find_shape_end(line, j, opener, closer)
                    if end is None:
                        # Desbalanceado: se toma el último cierre de la línea
                        k = line.rfind(closer)
                        if k <= j + len(opener) - 1:
                            continue
                        end = k + len(closer)
                    label = line[j + len(opener) : end - len(closer)]
                    quoted = _quote(label)
                    if quoted != label:
                        stats["nodes"] += 1
                    out.append(f"{node_id}{opener}{quoted}{closer}")
                    i = end
                    handled = True
                    break
            if handled:
                continue
            out.append(node_id)
            i = j
            continue

        out.append(ch)
        i += 1
    return "".join(out)


def _sanitize_quadrant(code: str, stats: dict) -> str:
    """quadrantChart: los textos de ejes y cuadrantes con tildes o símbolos van entre comillas."""
    def q(txt: str) -> str:
        txt = txt.strip()
        if len(txt) >= 2 and txt[0] == txt[-1] == '"':
            return txt
        stats["quadrant"] += 1
        return '"' + txt.replace('"', "'") + '"'

    lines = []
    for line in code.splitlines():
        m = re.match(r"^(\s*)(x-axis|y-axis)\s+(.*)$", line)
        if m:
            parts = [q(p) for p in re.split(r"-->", m.group(3), maxsplit=1) if p.strip()]
            line = f"{m.group(1)}{m.group(2)} " + " --> ".join(parts)
        else:
            m = re.match(r"^(\s*)(quadrant-[1-4])\s+(.*)$", line)
            if m:
                line = f"{m.group(1)}{m.group(2)} {q(m.group(3))}"
        lines.append(line)
    return "\n".join(lines)


def sanitize_mermaid(code: str, debug: bool = True) -> str:
    """Corrige los errores de sintaxis más comunes de Mermaid generado por LLM."""
    stats = {"nodes": 0, "edges": 0, "subgraphs": 0, "quadrant": 0}
    code = code.replace("\r\n", "\n").replace("\t", "    ").strip("\n")
    # Comillas tipográficas -> normales (Mermaid solo reconoce ")
    code = code.replace("“", '"').replace("”", '"')

    first = next((l.strip() for l in code.splitlines() if l.strip() and not l.strip().startswith("%%")), "")
    kind = first.split()[0].lower() if first else ""

    try:
        if kind in ("flowchart", "graph"):
            code = "\n".join(_sanitize_flowchart_line(l, stats) for l in _logical_lines(code))
        elif kind == "quadrantchart":
            code = _sanitize_quadrant(code, stats)
    except Exception as exc:  # el saneo nunca debe tumbar la interfaz
        print(f"[mermaid] ERROR en el saneo ({exc}); se usa el código original")

    if debug:
        print(
            f"[mermaid] tipo={kind or '?'} | nodos corregidos={stats['nodes']} | "
            f"flechas corregidas={stats['edges']} | subgraphs={stats['subgraphs']} | "
            f"textos quadrant={stats['quadrant']}"
        )
    return code


# ----------------------------------------------------------------------------
# Capas 2 y 3: validación en el navegador + respaldo legible
# ----------------------------------------------------------------------------
_HTML_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<style>
  html, body { margin:0; padding:0; background:transparent; font-family: "Source Sans Pro", sans-serif; }
  #wrap { padding: 4px 2px 8px; }
  #diagram { display:flex; justify-content:center; overflow-x:auto; }
  #diagram svg { max-width:100%; height:auto; }
  #fallback { display:none; }
  .note { font-size: 13px; opacity: .75; margin: 0 0 6px; color: var(--fg); }
  pre { margin:0; padding:12px; border-radius:8px; overflow-x:auto; white-space:pre-wrap;
        font-size:13px; line-height:1.4; background: var(--code-bg); color: var(--fg); }
  details { margin-top:6px; font-size:12px; color: var(--fg); opacity:.7; }
  details pre { margin-top:6px; }
  :root { --fg:#31333F; --code-bg:#f0f2f6; }
  @media (prefers-color-scheme: dark) { :root { --fg:#FAFAFA; --code-bg:#262730; } }
</style></head>
<body><div id="wrap">
  <div id="diagram"></div>
  <div id="fallback">
    <p class="note">⚠️ Este diagrama no se pudo dibujar; se muestra su contenido:</p>
    <pre id="fallback-code"></pre>
  </div>
  <details id="src"><summary>Ver código Mermaid</summary><pre id="src-code"></pre></details>
</div>
<script>
  const CANDIDATES = __CANDIDATES__;
  const DARK = __DARK__;
  // Handshake con Streamlit: sin él se ignora el ajuste de altura
  window.parent.postMessage({isStreamlitMessage: true, type: "streamlit:componentReady", apiVersion: 1}, "*");
  function resize() {
    const h = Math.ceil(document.getElementById('wrap').getBoundingClientRect().height) + 16;
    window.parent.postMessage({isStreamlitMessage: true, type: "streamlit:setFrameHeight", height: h}, "*");
  }
  function showFallback(reason) {
    console.log("[mermaid] respaldo:", reason);
    document.getElementById('diagram').style.display = 'none';
    document.getElementById('src').style.display = 'none';
    document.getElementById('fallback').style.display = 'block';
    document.getElementById('fallback-code').textContent = CANDIDATES[CANDIDATES.length - 1];
    resize();
  }
  document.getElementById('src-code').textContent = CANDIDATES[0];
  new ResizeObserver(resize).observe(document.getElementById('wrap'));
  window.addEventListener('load', resize);

  async function run() {
    if (typeof mermaid === 'undefined') return showFallback('mermaid.js no cargó');
    const dark = DARK === null ? window.matchMedia('(prefers-color-scheme: dark)').matches : DARK;
    mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: dark ? 'dark' : 'default',
                         suppressErrorRendering: true, flowchart: { htmlLabels: true } });
    for (let k = 0; k < CANDIDATES.length; k++) {
      const code = CANDIDATES[k];
      try {
        await mermaid.parse(code);                          // valida sin dibujar
        const { svg } = await mermaid.render('m' + Date.now() + k, code);
        document.getElementById('diagram').innerHTML = svg;
        document.getElementById('diagram').style.display = 'flex';    // por si el respaldo ya se mostró
        document.getElementById('fallback').style.display = 'none';
        document.getElementById('src').style.display = 'block';
        document.getElementById('src-code').textContent = code;
        console.log('[mermaid] dibujado con la versión', k);
        resize();
        return;
      } catch (e) {
        console.log('[mermaid] versión', k, 'inválida:', String(e && e.message || e).slice(0, 300));
      }
    }
    showFallback('ninguna versión es válida');
  }
  // Si el CDN no responde en 8 s, se muestra el respaldo
  const timer = setTimeout(() => { if (typeof mermaid === 'undefined') showFallback('timeout CDN'); }, 8000);
  const s = document.createElement('script');
  s.src = "__CDN__";
  s.onload = () => { clearTimeout(timer); run(); };
  s.onerror = () => { clearTimeout(timer); showFallback('error al cargar el CDN'); };
  document.head.appendChild(s);
</script></body></html>
"""


def _estimate_height(code: str) -> int:
    """Altura inicial aproximada (luego el iframe se ajusta solo al contenido)."""
    lines = [l for l in code.splitlines() if l.strip()]
    return max(250, min(1400, 120 + 28 * len(lines)))


def _streamlit_dark() -> bool | None:
    try:
        base = st.get_option("theme.base")
        return {"dark": True, "light": False}.get(base)
    except Exception:
        return None


def render_mermaid(code: str, debug: bool = True) -> None:
    """Dibuja un diagrama Mermaid sin que nunca aparezca un error de sintaxis."""
    original = code.strip("\n")
    fixed = sanitize_mermaid(original, debug=debug)
    candidates = [fixed] if fixed == original else [fixed, original]
    page = (
        _HTML_TEMPLATE.replace("__CANDIDATES__", json.dumps(candidates, ensure_ascii=False).replace("</", "<\\/"))
        .replace("__DARK__", json.dumps(_streamlit_dark()))
        .replace("__CDN__", MERMAID_CDN)
    )
    try:
        if hasattr(st, "iframe"):
            # Streamlit >= 1.5x: la altura se ajusta sola al diagrama
            st.iframe(page, height="content")
        else:
            components.html(page, height=_estimate_height(fixed), scrolling=True)
    except Exception as exc:  # último recurso: el código como texto
        print(f"[mermaid] ERROR al crear el componente ({exc}); se muestra el código")
        st.code(original, language="text")


def render_markdown_with_mermaid(content: str, debug: bool = True) -> None:
    """Muestra un Markdown dibujando aparte cada bloque ```mermaid```."""
    pos, n_diagrams = 0, 0
    for m in FENCE_RE.finditer(content):
        text = content[pos : m.start()]
        if text.strip():
            st.markdown(text)
        render_mermaid(m.group("code"), debug=debug)
        n_diagrams += 1
        pos = m.end()
    tail = content[pos:]
    if tail.strip():
        st.markdown(tail)
    if debug:
        print(f"[mermaid] {n_diagrams} diagramas renderizados en este documento")


__all__ = ["sanitize_mermaid", "render_mermaid", "render_markdown_with_mermaid"]
