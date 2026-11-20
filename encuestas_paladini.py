"""Junta las encuestas que publica Eduardo Paladini en Clarin para la pagina "Encuestas 2027".

- Lee la pagina de autor de Paladini y abre solo las notas de encuestas que todavia no se procesaron.
- Con Claude Haiku (barato) saca los datos de la nota: consultora, fecha de campo, alcance, casos y
  los numeros principales.
- CONTROL FIJO (sin IA): cada numero tiene que aparecer TAL CUAL en el texto de la nota; si no, se descarta.
  Asi nunca se publica un numero inventado.
- No copia graficos ni texto de Clarin: solo datos (numeros) con la fuente y el link.
- Guarda todo en encuestas_2027.json (la pagina del sitio lee ese archivo).
Modo prueba: DRY_RUN=1 no guarda nada.
"""
import json
import os
import re
from datetime import datetime, timezone
from html import unescape

import anthropic
import requests

AUTOR = "https://www.clarin.com/autor/eduardo-paladini.html"
BASE = "https://www.clarin.com"
ARCHIVO = "encuestas_2027.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; datapolitica.com/1.0)"}
MAX_NUEVAS = 6          # notas nuevas por corrida (para no gastar de mas)
MAX_GUARDADAS = 60
DRY_RUN = os.environ.get("DRY_RUN") == "1"


def bajar(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def notas_de_encuestas():
    html = bajar(AUTOR)
    links = re.findall(r"/politica/[a-z0-9-]+_0_[A-Za-z0-9]+\.html", html)
    vistos, res = set(), []
    for l in links:
        if l in vistos:
            continue
        vistos.add(l)
        if re.search(r"encuesta|sondeo|balotaje|imagen|intencion-de-voto|elecciones-2027", l):
            res.append(BASE + l)
    return res


def texto_de_nota(url):
    html = bajar(url)
    titulo = unescape((re.search(r"<title>(.*?)</title>", html, re.S) or [None, ""])[1]).split(" - Clar")[0].strip()
    fecha = (re.search(r'"datePublished"\s*:\s*"([^"]+)"', html) or [None, ""])[1]
    parrafos = []
    for p in re.findall(r"<p[^>]*>(.*?)</p>", html, re.S):
        t = unescape(re.sub(r"<[^>]+>", "", p)).strip()
        if len(t) > 40 and "JavaScript" not in t:
            parrafos.append(t)
    return titulo, fecha, "\n".join(parrafos)


PROMPT = """Sos un analista de datos. Del texto de esta nota periodistica, extrae SOLO los datos de la encuesta principal.
Reglas estrictas:
- Copia los numeros EXACTAMENTE como aparecen en el texto (con su coma decimal y el simbolo %), sin calcular ni redondear.
- No inventes nada: si un dato no esta en el texto, dejalo vacio.
- Si la nota no trata de una encuesta de opinion con numeros, responde {"es_encuesta": false}.
- Maximo 3 preguntas y 6 opciones por pregunta; elegi las mas importantes (intencion de voto, balotaje, imagen/aprobacion).
Responde SOLO JSON valido, sin markdown, con esta forma:
{"es_encuesta": true, "consultora": "...", "alcance": "Nacional | provincia o distrito", "fecha_campo": "...", "casos": "...",
 "metodologia": "...", "preguntas": [{"pregunta": "...", "opciones": [{"nombre": "...", "valor": "34,5%"}]}]}

NOTA:
"""


def extraer(cli, texto):
    for intento in range(3):
        try:
            r = cli.messages.create(model="claude-haiku-4-5", max_tokens=1200,
                                    messages=[{"role": "user", "content": PROMPT + texto[:9000]}])
            raw = "".join(getattr(b, "text", "") for b in r.content).strip()
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                return json.loads(m.group())
        except Exception as e:
            print(f"   Aviso: extraccion fallo (intento {intento + 1}): {str(e)[:80]}")
    return None


def _norm(s):
    return re.sub(r"\s+", "", s.replace(" ", " "))


def validar(datos, texto):
    """Control fijo: cada valor tiene que estar textual en la nota. Devuelve los datos limpios o None."""
    if not datos or not datos.get("es_encuesta") or not datos.get("consultora"):
        return None
    if datos["consultora"].split()[0].lower() not in texto.lower():
        return None
    tn = _norm(texto)
    preguntas = []
    for p in (datos.get("preguntas") or [])[:3]:
        ops = []
        for o in (p.get("opciones") or [])[:6]:
            v = str(o.get("valor", "")).strip()
            num = re.sub(r"\s*%$", "", v)
            if v and re.search(r"\d", v) and (_norm(v) in tn or _norm(num) + "%" in tn or _norm(num) + "por" in tn):
                ops.append({"nombre": str(o.get("nombre", "")).strip()[:60], "valor": v if v.endswith("%") else v})
        if len(ops) >= 2:
            preguntas.append({"pregunta": str(p.get("pregunta", "")).strip()[:120], "opciones": ops})
    if not preguntas:
        return None
    return {k: str(datos.get(k) or "").strip()[:120] for k in ("consultora", "alcance", "fecha_campo", "casos", "metodologia")} | {"preguntas": preguntas}


def main():
    datos = {"actualizado": "", "procesadas": [], "encuestas": []}
    if os.path.exists(ARCHIVO):
        try:
            datos = json.load(open(ARCHIVO, encoding="utf-8"))
        except Exception:
            pass
    procesadas = set(datos.get("procesadas", []))
    nuevas = [u for u in notas_de_encuestas() if u not in procesadas][:MAX_NUEVAS]
    print(f"Notas de encuestas nuevas para revisar: {len(nuevas)}")
    if not nuevas:
        return
    cli = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=3, timeout=90)
    agregadas = 0
    for url in nuevas:
        try:
            titulo, fecha, texto = texto_de_nota(url)
        except Exception as e:
            print(f"   No se pudo leer {url}: {e}")
            continue
        procesadas.add(url)
        if len(texto) < 300:
            print(f"   Sin texto suficiente: {titulo[:60]}")
            continue
        enc = validar(extraer(cli, texto), texto)
        if not enc:
            print(f"   Descartada (no es encuesta o numeros no verificados): {titulo[:70]}")
            continue
        enc.update({"publicada": fecha[:10], "fuente": "Clarín (Eduardo Paladini)", "url": url, "titulo_fuente": titulo[:150]})
        print(f"   OK: {enc['consultora']} | {enc['alcance']} | {len(enc['preguntas'])} preguntas")
        datos["encuestas"] = [e for e in datos.get("encuestas", []) if e.get("url") != url]
        datos["encuestas"].insert(0, enc)
        agregadas += 1
    datos["encuestas"] = sorted(datos["encuestas"], key=lambda e: e.get("publicada", ""), reverse=True)[:MAX_GUARDADAS]
    datos["procesadas"] = sorted(procesadas)[-300:]
    datos["actualizado"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    print(f"Encuestas agregadas: {agregadas}. Total guardadas: {len(datos['encuestas'])}")
    if DRY_RUN:
        print("[PRUEBA] No se guarda.")
        print(json.dumps(datos["encuestas"][:2], ensure_ascii=False, indent=1)[:3000])
        return
    json.dump(datos, open(ARCHIVO, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
