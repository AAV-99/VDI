"""Informe para el ingeniero (F2): prosa del agente + graficos y tablas que arma el codigo desde el modelo.

El dossier tecnico (00_vdi2206_consolidated_dossier.md) son tablas generadas desde el modelo: sirve al proceso y a
la auditoria. Este informe es el que lee una persona para decidir si aprueba el concepto. El agente escribe solo la
prosa de siete secciones (tarea engineer_report_task); el codigo agrega despues de cada una sus graficos (SVG
escritos a mano, sin dependencias) y tablas, y comprueba que toda cifra de la prosa este en el dossier o el
historial (cifras_sin_respaldo), para que el texto no invente numeros.

Render sin LLM sobre una corrida existente (usa la prosa de _analisis/00_informe_ingeniero.md si existe):
    uv run python -m vdi.informe_final <carpeta de la corrida>
"""
import html
import re
from pathlib import Path

from vdi.informes import ESTADO_ECR, modelo_hasta
from vdi.modelo import UNIDAD_BASE, Modelo, ids_en_texto, sin_tildes

ARCHIVO_PROSA = "00_informe_ingeniero.md"  # respuesta del agente, en _analisis/
ARCHIVO_HTML = "00_informe_ingeniero.html"
SECCIONES = ["Resumen ejecutivo", "Concepto y funcionamiento", "Cumplimiento de los requerimientos", "Decisiones clave",
             "Masa y costo", "Riesgos", "Qué falta para aprobar"]
ESTADO_TEXTO = {"cumple": "Cumple", "no_cumple": "No cumple", "pendiente": "Pendiente de prueba", "abierta": "Abierta"}


# ---------- prosa ----------

def secciones_prosa(texto: str) -> dict[str, str]:
    """Texto de cada seccion pedida, por su titulo (sin numeracion ni tildes al comparar)."""
    salida = {}
    partes = re.split(r"(?m)^##\s+(.+?)\s*$", texto or "")
    for titulo, cuerpo in zip(partes[1::2], partes[2::2]):
        t = sin_tildes(re.sub(r"^[\d.\s]+", "", titulo)).strip(" :*")
        for s in SECCIONES:
            if t.startswith(sin_tildes(s)) and s not in salida:
                salida[s] = cuerpo.replace("<!-- FIN DEL INFORME -->", "").strip()
    return salida


def faltan_secciones(texto: str) -> list[str]:
    return [s for s in SECCIONES if not secciones_prosa(texto).get(s)]


def _numeros(texto: str) -> list[float]:
    vistos = set()
    for t in re.findall(r"\d[\d.,]*\d|\d", texto or ""):
        for v in (t.replace(",", "."), t.replace(",", ""), t.replace(".", "").replace(",", ".")):
            try:
                vistos.add(float(v))
            except ValueError:
                pass
    return sorted(vistos)


def cifras_sin_respaldo(prosa: str, referencia: str) -> list[str]:
    """Cifras de la prosa que no estan en la referencia (dossier e historial), ni redondeadas ni con un factor 1000
    (g y kg) o 100 (fraccion y %). No cuentan los ID, los codigos de producto (letras pegadas), los enteros hasta 12
    (conteos, numeracion) ni los anos."""
    texto = prosa
    for i in ids_en_texto(prosa):
        texto = re.sub(re.escape(i).replace(r"\-", r"[\-‐‑‒–]?0*"), " ", texto)
    ref = _numeros(referencia)
    faltan = []
    for m in re.finditer(r"(?<![\w.,\-])(\d+(?:[.,]\d+)?)(?![\w]|[.,]\d)", texto):
        tok = m.group(1)
        v = float(tok.replace(",", "."))
        decimales = len(re.split(r"[.,]", tok)[1]) if re.search(r"[.,]", tok) else 0
        if (decimales == 0 and v <= 12) or (decimales == 0 and 1900 <= v <= 2100):
            continue
        tol = 0.5 * 10 ** -decimales + 1e-9
        if not any(abs(x * f - v) <= tol * max(1, f / 1000) for x in ref for f in (1, 1000, 0.001, 100)):
            faltan.append(tok)
    return list(dict.fromkeys(faltan))


def _md_html(texto: str) -> str:
    """Markdown basico de la prosa (parrafos, listas, negritas, cursivas, codigo, subtitulos) a HTML."""
    def linea(t):
        t = html.escape(t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", t)
        return re.sub(r"`(.+?)`", r"<code>\1</code>", t)
    salida, lista, parrafo = [], None, []

    def cerrar():
        nonlocal lista, parrafo
        if parrafo:
            salida.append("<p>" + " ".join(parrafo) + "</p>")
            parrafo = []
        if lista:
            salida.append(f"</{lista}>")
            lista = None
    for l in (texto or "").split("\n"):
        s = l.strip()
        m = re.match(r"^([-*•]|\d+[.)])\s+(.*)", s)
        if not s or s.startswith("|"):
            cerrar()
        elif s.startswith("#"):
            cerrar()
            salida.append(f"<h3>{linea(s.lstrip('#').strip())}</h3>")
        elif m:
            tipo = "ol" if m.group(1)[0].isdigit() else "ul"
            if parrafo or lista != tipo:
                cerrar()
                salida.append(f"<{tipo}>")
                lista = tipo
            salida.append(f"<li>{linea(m.group(2))}</li>")
        else:
            if lista:
                cerrar()
            parrafo.append(linea(s))
    cerrar()
    return "\n".join(salida)


# ---------- datos para los graficos ----------

def uso_de_limites(m: Modelo) -> list[dict]:
    """Requerimientos cuyo cumplimiento calcula el codigo: el total del sistema (Limita) o el parametro que lo mide."""
    filas = []
    for r in m.de_clase("requerimiento"):
        dim = r.campos.get("limita")
        candidatos = [(f"total de {dim}", m.total_sistema(dim), UNIDAD_BASE[dim])] if dim else [
            (p.id, p.campos.get("valor"), p.campos.get("unidad", "")) for p in m.de_clase("parametro")
            if r.id in p.campos.get("mide", []) and isinstance(p.campos.get("valor"), (int, float))]
        for quien, valor, unidad in candidatos:
            res = m.cumple(valor, unidad, r)
            if res:
                filas.append({"req": r, "quien": quien, "valor": valor, "unidad": unidad, "cumple": res[0],
                              "uso": res[1], "igual": r.campos.get("comparador") == "="})
    return filas


def _txt(v, n: int = 60) -> str:
    v = " ".join(str(v or "").split())
    return v if len(v) <= n else v[:n - 1] + "…"


def _esc(v) -> str:
    return html.escape(str(v), quote=True)


# ---------- graficos SVG ----------

def svg_limites(filas: list[dict]) -> str:
    if not filas:
        return "<p class='nota'>Ningún requerimiento tiene un valor calculable todavía (falta el parámetro que lo mide).</p>"
    ancho, eti, barra, alto = 760, 300, 300, 30
    tope = max(1.5, min(3.0, max(f["uso"] for f in filas) * 1.05))
    x = lambda u: eti + barra * min(u, tope) / tope  # noqa: E731
    h = alto * len(filas) + 40
    s = [f"<svg viewBox='0 0 {ancho} {h}' role='img' aria-label='Uso de cada límite'>"]
    for u, cls, ancla in ((0.9, "guia", "end"), (1.0, "meta", "start")):
        s.append(f"<line class='{cls}' x1='{x(u):.1f}' x2='{x(u):.1f}' y1='6' y2='{h - 26}'/>")
        s.append(f"<text class='eje' x='{x(u) + (-3 if ancla == 'end' else 3):.1f}' y='{h - 10}' text-anchor='{ancla}'>"
                 f"{'90 %' if u < 1 else 'meta'}</text>")
    for k, f in enumerate(filas):
        y = 10 + k * alto
        r = f["req"]
        estado, icono = (("bien", "✓ cumple") if f["cumple"] and (f["uso"] < 0.9 or f["igual"]) else
                         ("cerca", "! cerca del límite") if f["cumple"] else ("mal", "✗ no cumple"))
        meta = f"{r.campos.get('comparador')} {r.campos.get('valor')} {r.campos.get('unidad', '')}".strip()
        valor = f"{f['valor']:.4g} {f['unidad']}".strip()
        s.append(f"<g><title>{_esc(r.id)} {_esc(r.campos.get('texto', ''))}: {_esc(f['quien'])} = {_esc(valor)} "
                 f"frente a {_esc(meta)} ({f['uso']:.0%})</title>")
        s.append(f"<text class='eti' x='{eti - 8}' y='{y + 18}' text-anchor='end'>{_esc(r.id)} {_esc(_txt(r.campos.get('texto'), 34))}</text>")
        s.append(f"<rect class='{estado}' x='{eti}' y='{y + 6}' width='{max(2, x(f['uso']) - eti):.1f}' height='16' rx='4'/>")
        s.append(f"<text class='val' x='{max(x(f['uso']), x(1.0)) + 6:.1f}' y='{y + 18}'>{f['uso']:.0%} {icono}</text></g>")
    s.append("</svg>")
    return "\n".join(s)


def svg_presupuesto(m: Modelo, dim: str) -> str:
    unidad = UNIDAD_BASE[dim]
    filas = [(f"{mo.id} {_txt(mo.campos.get('nombre'), 28)}", m.total_modulo(mo.id, dim), m._q(mo.campos.get(f"presupuesto_{dim}"), dim))
             for mo in m.de_clase("modulo")]
    req = m.requerimiento_de(dim)
    limite = m._q(f"{req.campos['valor']} {req.campos['unidad']}", dim) if req else None
    filas.append((f"Sistema{' (' + req.id + ')' if req else ''}", m.total_sistema(dim), limite))
    if not any(r for _, r, _ in filas):
        return f"<p class='nota'>No hay componentes con {dim} registrada.</p>"
    ancho, eti, barra, alto = 760, 260, 330, 30
    tope = max(max(r, b or 0) for _, r, b in filas) * 1.1 or 1
    x = lambda v: eti + barra * v / tope  # noqa: E731
    h = alto * len(filas) + 16
    s = [f"<svg viewBox='0 0 {ancho} {h}' role='img' aria-label='{dim.capitalize()} por módulo frente a su presupuesto'>"]
    for k, (nombre, real, pres) in enumerate(filas):
        y = 6 + k * alto
        sobre = pres is not None and real > pres * 1.000001
        texto_pres = f" de {pres:.4g}" if pres is not None else " (sin presupuesto)"
        s.append(f"<g><title>{_esc(nombre)}: {real:.4g}{texto_pres} {unidad}</title>")
        s.append(f"<text class='eti{' fuerte' if nombre.startswith('Sistema') else ''}' x='{eti - 8}' y='{y + 18}' "
                 f"text-anchor='end'>{_esc(nombre)}</text>")
        s.append(f"<rect class='{'mal' if sobre else 'serie'}' x='{eti}' y='{y + 6}' width='{max(2, x(real) - eti):.1f}' height='16' rx='4'/>")
        if pres is not None:
            s.append(f"<line class='meta' x1='{x(pres):.1f}' x2='{x(pres):.1f}' y1='{y + 2}' y2='{y + 26}'/>")
        s.append(f"<text class='val' x='{max(x(real), x(pres or 0)) + 6:.1f}' y='{y + 18}'>{real:.4g}{texto_pres} {unidad}"
                 f"{' ✗ sobre el presupuesto' if sobre else ''}</text></g>")
    s.append("</svg>")
    return "\n".join(s)


def svg_fmea(m: Modelo, n: int = 10) -> str:
    mfs = sorted((f for f in m.de_clase("modo_falla") if m.rpn(f)), key=lambda f: -m.rpn(f))[:n]
    if not mfs:
        return "<p class='nota'>No hay modos de falla registrados en el modelo.</p>"
    ancho, eti, barra, alto = 760, 330, 260, 30
    tope = max(m.rpn(f) for f in mfs)
    h = alto * len(mfs) + 10
    s = [f"<svg viewBox='0 0 {ancho} {h}' role='img' aria-label='Modos de falla por RPN'>"]
    for k, f in enumerate(mfs):
        y = 4 + k * alto
        c = f.campos
        w = barra * m.rpn(f) / tope
        s.append(f"<g><title>{_esc(f.id)} {_esc(c.get('elemento'))}: {_esc(c.get('modo'))} → {_esc(c.get('efecto'))}. "
                 f"Acción: {_esc(c.get('mitigacion') or '—')}</title>")
        s.append(f"<text class='eti' x='{eti - 8}' y='{y + 18}' text-anchor='end'>{_esc(f.id)} {_esc(c.get('elemento'))} "
                 f"{_esc(_txt(c.get('modo'), 30))}</text>")
        s.append(f"<rect class='{'mal' if c.get('s', 0) >= 9 else 'serie'}' x='{eti}' y='{y + 6}' width='{max(2, w):.1f}' height='16' rx='4'/>")
        s.append(f"<text class='val' x='{eti + w + 6:.1f}' y='{y + 18}'>RPN {m.rpn(f)} (S {c.get('s')}, O {c.get('o')}, "
                 f"D {c.get('d')}){' ✗ severidad crítica' if c.get('s', 0) >= 9 else ''}</text></g>")
    s.append("</svg>")
    return "\n".join(s)


def svg_arquitectura(m: Modelo) -> str:
    """Caja negra y arquitectura: el sistema con sus modulos dentro, los elementos externos fuera y las interfaces."""
    mods, externos = m.de_clase("modulo"), m.de_clase("externo")
    if not mods:
        return "<p class='nota'>No hay módulos registrados en el modelo.</p>"
    ancho, col_x, sis_x = 780, 16, 230
    mw, mh, gap = 245, 62, 26
    filas_mod = (len(mods) + 1) // 2
    sis_h = 50 + filas_mod * (mh + gap)
    ext_h = 50
    h = max(sis_h, len(externos) * (ext_h + 14)) + 30
    caja = {"SIS": (sis_x, 10, ancho - sis_x - 10, h - 30)}
    for k, mo in enumerate(mods):
        caja[mo.id] = (sis_x + 20 + (k % 2) * (mw + 20), 50 + (k // 2) * (mh + gap), mw, mh)
    paso_ext = (h - 30) / max(1, len(externos))
    for k, xe in enumerate(externos):
        caja[xe.id] = (col_x, 10 + k * paso_ext + (paso_ext - ext_h) / 2, 170, ext_h)

    def ancla(i):
        e = m.elementos.get(i)
        if e and e.clase == "componente":
            i = e.campos.get("modulo")
        return i if i in caja else None
    enlaces = {}
    for itf in m.de_clase("interfaz"):
        a, b = ancla(itf.campos.get("extremo_a")), ancla(itf.campos.get("extremo_b"))
        if a and b and a != b:
            enlaces.setdefault(tuple(sorted((a, b))), []).append(f"{itf.id} {itf.campos.get('tipo', '')}")

    def centro(i):
        x, y, w, hh = caja[i]
        return x + w / 2, y + hh / 2

    def borde(i, hacia):
        # punto del borde de la caja i en direccion a otro punto (para que las lineas no crucen las cajas)
        x, y, w, hh = caja[i]
        cx, cy = centro(i)
        dx, dy = hacia[0] - cx, hacia[1] - cy
        if i == "SIS":  # al sistema se llega por su borde izquierdo
            return x, min(max(hacia[1], y + 20), y + hh - 20)
        if not dx and not dy:
            return cx, cy
        t = min(w / 2 / abs(dx) if dx else 1e9, hh / 2 / abs(dy) if dy else 1e9)
        return cx + dx * t, cy + dy * t
    s = [f"<svg viewBox='0 0 {ancho} {h}' role='img' aria-label='Arquitectura del sistema'>"]
    sis = m.elementos.get("SIS")
    x, y, w, hh = caja["SIS"]
    s.append(f"<rect class='sistema' x='{x}' y='{y}' width='{w}' height='{hh}' rx='10'/>")
    s.append(f"<text class='eti fuerte' x='{x + 14}' y='{y + 24}'>SIS {_esc(_txt(sis.campos.get('concepto') if sis else 'Sistema', 70))}</text>")
    for (a, b), etiquetas in enlaces.items():
        pa, pb = borde(a, centro(b)), borde(b, centro(a))
        s.append(f"<g><title>{_esc('; '.join(etiquetas))}</title><line class='enlace' x1='{pa[0]:.1f}' y1='{pa[1]:.1f}' "
                 f"x2='{pb[0]:.1f}' y2='{pb[1]:.1f}'/>")
        mx, my = (pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2
        s.append(f"<text class='mini' x='{mx:.1f}' y='{my - 4:.1f}' text-anchor='middle'>{_esc(_txt(', '.join(e.split()[0] for e in etiquetas), 24))}</text></g>")
    for mo in mods:
        x, y, w, hh = caja[mo.id]
        s.append(f"<g><title>{_esc(mo.id)} {_esc(mo.campos.get('nombre'))} ({_esc(', '.join(mo.campos.get('dominios', [])))})</title>"
                 f"<rect class='modulo' x='{x}' y='{y}' width='{w}' height='{hh}' rx='6'/>")
        s.append(f"<text class='eti fuerte' x='{x + 10}' y='{y + 24}'>{_esc(mo.id)} {_esc(_txt(mo.campos.get('nombre'), 26))}</text>")
        s.append(f"<text class='mini' x='{x + 10}' y='{y + 44}'>{_esc(', '.join(mo.campos.get('dominios', [])))}</text></g>")
    for xe in externos:
        x, y, w, hh = caja[xe.id]
        s.append(f"<g><title>{_esc(xe.id)} {_esc(xe.campos.get('nombre'))}</title><rect class='externo' x='{x}' y='{y}' "
                 f"width='{w}' height='{hh}' rx='6'/>")
        s.append(f"<text class='eti' x='{x + 10}' y='{y + 22}'>{_esc(xe.id)}</text>")
        s.append(f"<text class='mini' x='{x + 10}' y='{y + 40}'>{_esc(_txt(xe.campos.get('nombre'), 26))}</text></g>")
    s.append("</svg>")
    return "\n".join(s)


# ---------- bloques HTML ----------

def _tabla(filas: list[list], encabezado: list[str]) -> str:
    if not filas:
        return "<p class='nota'>Ninguno.</p>"
    cab = "".join(f"<th>{_esc(c)}</th>" for c in encabezado)
    cuerpo = "".join("<tr>" + "".join(f"<td>{_esc(_txt(v, 160)) if v not in (None, '', []) else '—'}</td>"
                                       for v in (", ".join(map(str, x)) if isinstance(x, list) else x for x in f)) + "</tr>"
                     for f in filas)
    return f"<table><thead><tr>{cab}</tr></thead><tbody>{cuerpo}</tbody></table>"


def veredicto(m: Modelo) -> str:
    usos = uso_de_limites(m)
    reqs = m.de_clase("requerimiento")
    medidos = {f["req"].id for f in usos}
    no_cumplen = sorted({f["req"].id for f in usos if not f["cumple"]})
    cerca = sorted({f["req"].id for f in usos if f["cumple"] and f["uso"] >= 0.9 and not f["igual"]} - set(no_cumplen))
    sin_medida = [r.id for r in reqs if m.cuantitativo(r) and r.id not in medidos]
    ecr = {}
    for e in m.de_clase("ecr"):
        ecr.setdefault(e.campos.get("estado", "abierta"), []).append(e.id)
    tiles = [("Requerimientos", f"{len(medidos) - len(no_cumplen)} de {len(reqs)}", "cumplen con valor calculado"),
             ("No cumplen", str(len(no_cumplen)), ", ".join(no_cumplen) or "ninguno"),
             ("Cerca del límite (≥ 90 %)", str(len(cerca)), ", ".join(cerca) or "ninguno"),
             ("Sin valor que los mida", str(len(sin_medida)), _txt(", ".join(sin_medida), 60) or "ninguno")]
    for dim in ("masa", "costo"):
        req = m.requerimiento_de(dim)
        total = m.total_sistema(dim)
        if req:
            lim = m._q(f"{req.campos['valor']} {req.campos['unidad']}", dim)
            tiles.append((f"{dim.capitalize()} total", f"{total:.4g} {UNIDAD_BASE[dim]}", f"{total / lim:.0%} de {req.id} ({lim:g})"))
    tiles.append(("ECR", " · ".join(f"{len(v)} {ESTADO_TEXTO.get(k, k).lower()}" for k, v in sorted(ecr.items())) or "ninguna",
                  "estado que dejó el cliente"))
    return "<div class='tiles'>" + "".join(f"<div class='tile'><div class='t'>{_esc(a)}</div><div class='v'>{_esc(b)}</div>"
                                           f"<div class='d'>{_esc(c)}</div></div>" for a, b, c in tiles) + "</div>"


def tabla_decisiones(m: Modelo) -> str:
    filas = []
    for d in m.vigentes():
        c = d.campos
        otras = [a.strip() for a in c.get("alternativas", "").split(";") if a.strip()
                 and sin_tildes(a.strip()) != sin_tildes(c.get("elegida", ""))]
        filas.append([d.id, c.get("asunto"), c.get("elegida"), "; ".join(otras), c.get("justifica")])
    return _tabla(filas, ["ID", "Asunto", "Elegida", "Descartadas", "Fija"])


def tabla_ecr_abiertas(m: Modelo) -> str:
    filas = [[e.id, e.campos.get("descripcion"), ESTADO_ECR.get(e.campos.get("estado"), e.campos.get("estado"))]
             for e in m.de_clase("ecr") if e.campos.get("estado") != "cumple"]
    return _tabla(filas, ["ECR", "Hallazgo", "Estado"])


def tabla_pendientes(m: Modelo) -> str:
    filas = [[e.id, "Ensayo o medición", e.campos.get("criterio")] for e in m.de_clase("ecr") if e.campos.get("estado") == "pendiente"]
    filas += [[p.id, "Confirmar supuesto (N1)", f"{p.campos.get('nombre')} = {p.campos.get('valor')} {p.campos.get('unidad', '')}"
               f" (mide {', '.join(p.campos.get('mide', []))})"]
              for p in m.de_clase("parametro") if p.campos.get("nivel") == "N1" and p.campos.get("mide")]
    comerciales = [c.id for c in m.de_clase("componente") if c.campos.get("tipo") == "comercial"]
    if comerciales:
        filas.append(["BOM", "Cotización formal", f"{len(comerciales)} componentes comerciales ({_txt(', '.join(comerciales), 80)}); "
                      "los precios del BOM son estimaciones"])
    return _tabla(filas, ["Elemento", "Qué falta", "Detalle"])


ESTILO = """
:root { color-scheme: light; --fondo:#fcfcfb; --superficie:#ffffff; --texto:#0b0b0b; --texto2:#52514e; --borde:#dedcd6;
  --serie:#2a78d6; --bien:#0ca30c; --cerca:#fab219; --mal:#d03b3b; --sis:#f3f6fb; --mod:#ffffff; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { color-scheme: dark; --fondo:#1a1a19;
  --superficie:#242423; --texto:#f0efec; --texto2:#c3c2b7; --borde:#3d3d3a; --serie:#3987e5; --sis:#20262f; --mod:#242423; } }
:root[data-theme="dark"] { color-scheme: dark; --fondo:#1a1a19; --superficie:#242423; --texto:#f0efec; --texto2:#c3c2b7;
  --borde:#3d3d3a; --serie:#3987e5; --sis:#20262f; --mod:#242423; }
body { margin:0; background:var(--fondo); color:var(--texto); font:16px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width:900px; margin:0 auto; padding:24px 16px 64px; }
h1 { font-size:1.7rem; margin:0 0 4px; } h2 { margin-top:2.2rem; border-bottom:1px solid var(--borde); padding-bottom:4px; }
h3 { font-size:1.05rem; } .sub { color:var(--texto2); margin:0 0 1.5rem; } .nota { color:var(--texto2); font-style:italic; }
figure { margin:1rem 0; background:var(--superficie); border:1px solid var(--borde); border-radius:8px; padding:12px; overflow-x:auto; }
figcaption { color:var(--texto2); font-size:.85rem; margin-top:6px; } svg { width:100%; height:auto; min-width:560px; }
svg text { fill:var(--texto); font-size:13px; } svg .eti { fill:var(--texto); } svg .fuerte { font-weight:600; }
svg .val, svg .eje, svg .mini { fill:var(--texto2); font-size:12px; } svg .mini { font-size:11px; }
svg .serie { fill:var(--serie); } svg .bien { fill:var(--bien); } svg .cerca { fill:var(--cerca); } svg .mal { fill:var(--mal); }
svg .meta { stroke:var(--texto); stroke-width:2; } svg .guia { stroke:var(--texto2); stroke-width:1; stroke-dasharray:4 3; }
svg .sistema { fill:var(--sis); stroke:var(--serie); stroke-width:1.5; } svg .modulo { fill:var(--mod); stroke:var(--borde); stroke-width:1.5; }
svg .externo { fill:var(--superficie); stroke:var(--texto2); stroke-width:1.5; stroke-dasharray:5 3; }
svg .enlace { stroke:var(--texto2); stroke-width:2; }
table { border-collapse:collapse; width:100%; font-size:.9rem; margin:1rem 0; display:block; overflow-x:auto; }
th, td { border:1px solid var(--borde); padding:6px 8px; text-align:left; vertical-align:top; } th { background:var(--superficie); }
.tiles { display:grid; grid-template-columns:repeat(auto-fit, minmax(190px, 1fr)); gap:10px; margin:1rem 0; }
.tile { background:var(--superficie); border:1px solid var(--borde); border-radius:8px; padding:10px 12px; }
.tile .t { color:var(--texto2); font-size:.8rem; } .tile .v { font-size:1.35rem; font-weight:600; } .tile .d { color:var(--texto2); font-size:.8rem; }
@media print { body { background:#fff; } figure, .tile { break-inside:avoid; } }
"""


def render_html(carpeta: Path, prosa: str, titulo: str = "Informe del diseño conceptual") -> str:
    carpeta = Path(carpeta)
    m = modelo_hasta(carpeta)
    texto = secciones_prosa(prosa)
    sis = m.elementos.get("SIS")

    def fig(svg, pie):
        return f"<figure>{svg}<figcaption>{_esc(pie)}</figcaption></figure>"
    extra = {
        "Resumen ejecutivo": veredicto(m),
        "Concepto y funcionamiento": fig(svg_arquitectura(m), "Arquitectura: el sistema con sus módulos, los elementos externos "
                                         "(borde discontinuo) y las interfaces (pasa el cursor para ver su tipo)."),
        "Cumplimiento de los requerimientos": fig(svg_limites(uso_de_limites(m)), "Uso de cada límite calculado por el código "
                                                  "(línea continua: la meta; discontinua: 90 %). Para un «≥» el uso es meta / valor."),
        "Decisiones clave": tabla_decisiones(m),
        "Masa y costo": fig(svg_presupuesto(m, "masa"), "Masa por módulo; la línea es su presupuesto.") +
                        fig(svg_presupuesto(m, "costo"), "Costo estimado por módulo; la línea es su presupuesto. Los precios "
                            "son estimaciones del modelo: la cotización formal es tarea del diseñador."),
        "Riesgos": fig(svg_fmea(m), "Modos de falla con mayor RPN (S × O × D).") + "<h3>ECR que no cerraron</h3>" + tabla_ecr_abiertas(m),
        "Qué falta para aprobar": tabla_pendientes(m),
    }
    partes = [f"<h1>{_esc(titulo)}</h1>",
              f"<p class='sub'>{_esc(sis.campos.get('concepto', '')) if sis else ''}</p>"]
    for k, s in enumerate(SECCIONES, 1):
        cuerpo = _md_html(texto[s]) if texto.get(s) else "<p class='nota'>Sin texto del agente para esta sección.</p>"
        partes.append(f"<section><h2>{k}. {_esc(s)}</h2>{cuerpo}{extra[s]}</section>")
    partes.append("<section><h2>Anexos</h2><ul><li><a href='00_vdi2206_consolidated_dossier.md'>Dossier técnico</a> "
                  "(tablas generadas desde el modelo de diseño)</li><li><a href='historial_de_diseno.md'>Historial del diseño</a> "
                  "(cada ECR: apertura, triaje, cambios y verificación)</li></ul></section>")
    return (f"<!doctype html><html lang='es'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, "
            f"initial-scale=1'><title>{_esc(titulo)}</title><style>{ESTILO}</style></head><body><main>"
            + "\n".join(partes) + "</main></body></html>\n")


def escribir(carpeta: Path) -> Path:
    """Escribe el HTML con la prosa de _analisis/ (o sin prosa si la tarea no corrio o fallo)."""
    carpeta = Path(carpeta)
    ruta_prosa = carpeta / "_analisis" / ARCHIVO_PROSA
    prosa = ruta_prosa.read_text(encoding="utf-8") if ruta_prosa.exists() else ""
    ruta = carpeta / ARCHIVO_HTML
    ruta.write_text(render_html(carpeta, prosa), encoding="utf-8")
    return ruta


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"Informe en {escribir(Path(sys.argv[1]))}")
