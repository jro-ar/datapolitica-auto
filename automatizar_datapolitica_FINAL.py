#!/usr/bin/env python3
"""
Automatizador de noticias para datapolitica.com
Scraping RSS -> Reescritura Claude API -> Publicacion panel ASP.NET

IMPORTANTE:
- Solo automatiza Orden 1 (radio=1) y Cinta Testigo (radio=5)
- Editorial (radio=4) NUNCA se toca
- La contrasena viene del secret de GitHub, NUNCA hardcodeada
"""

import os
import re
import json
import io
import xml.etree.ElementTree as ET
import anthropic
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# ── Configuracion ─────────────────────────────────────────────────────────────
PANEL_BASE      = "https://datapolitica.com/panel"
PANEL_LOGIN_URL = f"{PANEL_BASE}/login.aspx"
PANEL_NOTA_URL  = f"{PANEL_BASE}/panel_agregar_nota.aspx"
PANEL_LIST_URL  = f"{PANEL_BASE}/panel_listado.aspx"
PANEL_DEL_URL   = f"{PANEL_BASE}/Function.ashx"

PANEL_USER = "admin"
PANEL_PASS = os.environ["DATAPOLITICA_PANEL_PASS"]

CLAUDE_MODEL = "claude-sonnet-4-6"

RSS_SOURCES = [
    "https://www.infobae.com/arc/outboundfeeds/rss/category/politica/",
    "https://www.perfil.com/feed/politica",
    "https://tn.com.ar/arc/outboundfeeds/rss/category/politica/?outputType=xml",
    "https://www.ambito.com/rss/pages/politica.xml",
    "https://noticiasargentinas.com/rss.xml",
    # Fuentes agregadas (oct-2026) para tener mas noticias nuevas cada hora.
    # Si alguna no responde, se saltea sola sin afectar al resto.
    "https://www.clarin.com/rss/politica/",
    "https://www.lanacion.com.ar/arc/outboundfeeds/rss/category/politica/",
    "https://www.pagina12.com.ar/rss/secciones/el-pais/notas",
    "https://www.eldestapeweb.com/rss/politica",
    "https://www.lavoz.com.ar/arc/outboundfeeds/rss/category/politica/",
    "https://www.losandes.com.ar/arc/outboundfeeds/rss/category/politica/",
    "https://www.cronista.com/files/rss/politica.xml",
    "https://www.mdzol.com/rss/politica.xml",
]

MAX_TITULO = 70
MAX_COPETE = 160
MIN_CUERPO = 900
MAX_CUERPO = 1600

# 1=Orden1  4=Editorial(NUNCA TOCAR)  5=Cinta Testigo
ORDEN_NOTA  = 1
ORDEN_CINTA = 5

# Cuantos articulos publicar en Orden 1 por corrida
N_ORDEN1 = 6

# MODO PRUEBA: con DRY_RUN=1 se arma todo (titulos, fotos, textos) pero NO se borra ni se publica nada,
# y no se guarda el estado. Sirve para revisar una version nueva antes de que llegue al sitio.
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"

# Resumen de la corrida (lo usa el revisor automatico)
RESUMEN = {"publicadas": 0, "cinta": False, "notas": [], "descartes": [], "errores": [], "borrado": ""}

# Palabras que descartan un articulo SIN IMPORTAR el tema
DISCARD_KEYWORDS = [
    # Economia / finanzas
    "dolar", "inflacion", "hipoteca", "uva", "credito hipotecario",
    "tasa de interes", "bolsa", "mercado financiero", "deuda externa",
    "reservas del bcra", "exportaciones", "importaciones",
    "finanzas", "fmi", "fondo monetario",
    "nafta", "combustible", "automotriz", "volkswagen", "toyota", "ford",
    "seat", "renault", "mercedes", "ferrari",
    "pbi", "deficit fiscal", "superavit",
    # Entretenimiento / cultura / deporte
    # Periodistas / conductores de TV/radio (sus notas suelen tener fotos de estudio)
    "fantino", "intratables", "bendita", "mirtha legrand", "susana gimenez",
    "andy kusnetzoff", "viviana canosa", "eduardo feinmann", "jonatan viale",
    "baby etchecopar", "luis majul", "jorge lanata", "diego brancatelli",
    # Entretenimiento / cultura / deporte
    "espectaculo", "concierto", "recital", "festival", "moda",
    "cine", "teatro", "streaming", "netflix", "spotify",
    "futbol", "tenis", "atletismo", "olimpiadas",
    # Turismo / gastronomia
    "turismo", "queso", "gastronomia", "restaurante", "hotel",
    "marketing hotelero", "escapismo",
    # Clima / ciencias
    "ciclon", "huracan", "nino", "nina", "terremoto", "tsunami",
    "volcan", "inundacion", "sequia", "clima", "meteorologico",
    # Politica extranjera
    "reform uk", "laborismo", "reino unido", "trump", "biden", "harris",
    "republica popular", "china popular", "putin", "zelensky", "kremlin",
    "union europea", "otan", "nato", "israel", "palestina", "gaza",
    "estados unidos", "eeuu", "casa blanca", "congreso americano",
    # Espana / politica espanola
    "congreso espanol", "senado espanol", "gobierno espanol",
    "congreso de los diputados", "pedro sanchez", "feijoo",
    "comunidad autonoma", "moncloa",
    # Aeropuertos 2000 / concesion aeroportuaria
    "aeropuertos 2000",
    "red aeroportuaria",
    "concesion aeroportuaria",
    "negocio aeroportuario",
    "privatizacion aeropuertos",
    "aeropuerto privatizado",
]

# El TITULO debe contener al menos una de estas para pasar
ARGENTINA_POLITICS_KEYWORDS = [
    # Politicos clave
    "milei", "kirchner", "macri", "massa", "bullrich", "larreta",
    "kicillof", "cristina", "javier milei", "sergio massa",
    "patricia bullrich", "horacio larreta", "axel kicillof",
    "schiaretti", "llaryora", "passalacqua", "ziliotto",
    # Instituciones politicas
    "gobierno", "congreso", "senado", "diputados", "casa rosada",
    "corte suprema", "poder judicial", "justicia federal",
    "gabinete", "ministerio", "secretaria de",
    # Partidos / movimientos
    "peronismo", "peronista", "kirchnerismo", "kirchnerista",
    "libertad avanza", "pro ", " pro,", "juntos por el cambio",
    "union por la patria", "frente de todos", "oficialismo",
    "oposicion", "bloque", "coalicion",
    # Procesos politicos
    "elecciones", "legislativas", "presidenciales",
    "gobernador", "intendente", "candidato", "campana electoral",
    "voto", "urna", "ballotage",
    # Organismos del estado argentino
    "afip", "anses", "bcra", "conicet", "indec",
    # Politica argentina
    "argentina", "argentino", "argentinos",
    # Mas figuras y cargos (ampliado)
    "karina milei", "adorni", "caputo", "santilli", "espert", "villarruel",
    "pichetto", "scioli", "grabois", "lousteau", "ritondo", "menem",
    "francos", "petri", "cuneo libarona", "lijo", "lorenzetti",
    "vicepresidente", "vicepresidenta", "jefe de gabinete", "ministra", "ministro",
    "senador", "senadora", "diputado", "diputada", "legislatura", "concejal",
    "gobernadores", "gobernadora", "jefe de gobierno", "intendentes",
    "dnu", "decreto", "veto", "reforma", "sesion", "dictamen", "proyecto de ley",
    "partido", "alianza", "campana", "interna", "referente", "dirigente",
    "juez", "jueza", "fiscal", "indagatoria", "procesamiento", "camara federal",
    "paritaria", "gremio", "cgt", "sindicato",
]

STATE_FILE = "state.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; automatizador/1.0)"}


# ── Estado ────────────────────────────────────────────────────────────────────
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception as e:
            print(f"   Aviso: No se pudo leer {STATE_FILE}: {e}")
    return {}


def save_state(state: dict):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
        print(f"   Estado guardado: {state}")
    except Exception as e:
        print(f"   Aviso: No se pudo guardar {STATE_FILE}: {e}")


# ── Normalizacion ──────────────────────────────────────────────────────────────
def _normalize(text: str) -> str:
    return (text.lower()
            .replace('á','a').replace('é','e').replace('í','i')
            .replace('ó','o').replace('ú','u').replace('ü','u')
            .replace('ñ','n'))


# ── Filtros ────────────────────────────────────────────────────────────────────
def should_discard(article: dict) -> bool:
    """Descarta articulos con keywords de economia, entretenimiento, extranjero, etc."""
    text = _normalize(article.get('title', '') + ' ' + article.get('summary', ''))
    # v21: coberturas en vivo / minuto a minuto no sirven como nota (rompen la reescritura)
    _tit = _normalize(article.get('title', ''))
    for kw in ("en vivo", "minuto a minuto", "en directo", "ultimas noticias de"):
        if kw in _tit:
            print(f"   Descartado (cobertura en vivo): {article['title'][:70]}")
            return True
    for kw in DISCARD_KEYWORDS:
        if kw in text:
            print(f"   Descartado ('{kw}'): {article['title'][:70]}")
            return True
    return False


def is_argentina_politics_title(article: dict) -> bool:
    """El TITULO debe contener una keyword especificamente de politica argentina."""
    title = _normalize(article.get('title', ''))
    for kw in ARGENTINA_POLITICS_KEYWORDS:
        if kw in title:
            return True
    print(f"   Titulo sin politica argentina, descartado: {article['title'][:70]}")
    return False


_dedup_client = None


def _get_dedup_client():
    """Singleton del cliente Anthropic para deteccion de duplicados."""
    global _dedup_client
    if _dedup_client is None:
        _dedup_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _dedup_client


def articles_are_similar(art1: dict, art2: dict) -> bool:
    """
    Detecta articulos duplicados o muy similares usando tres niveles:

    1. SequenceMatcher >= 0.55  → duplicado obvio (sin API)
    2. SequenceMatcher < 0.20   → claramente distintos (sin API)
    3. Zona gris 0.20-0.55      → pregunta a Claude Haiku: mismo evento SI/NO

    El nivel 3 es agoístico al tema: funciona para DNU, elecciones, economia
    o cualquier tema futuro sin ajustar ningun umbral ni lista de palabras.
    """
    from difflib import SequenceMatcher

    # Los articulos del RSS y de la homepage usan la clave "title"; los reescritos usan "titulo".
    t1 = (art1.get("titulo") or art1.get("title") or "").strip()
    t2 = (art2.get("titulo") or art2.get("title") or "").strip()

    if not t1 or not t2:
        return False

    # Nivel 1: similitud textual alta → duplicado obvio
    ratio = SequenceMatcher(None, t1.lower(), t2.lower()).ratio()
    if ratio >= 0.55:
        print(f"  [dup-texto={ratio:.2f}] '{t1[:45]}' / '{t2[:45]}'")
        return True

    # Nivel 2: muy distintos → descartamos sin gastar API
    if ratio < 0.20:
        return False

    # Nivel 3: zona gris → verificacion semantica con Haiku
    try:
        resp = _get_dedup_client().messages.create(
            model="claude-haiku-4-5",
            max_tokens=10,
            messages=[{
                "role": "user",
                "content": (
                    "Estos dos titulos de noticias, "
                    "¿describen exactamente el mismo evento noticioso?\n"
                    "Responde SOLO con SI o NO.\n\n"
                    f"Titulo 1: {t1}\n"
                    f"Titulo 2: {t2}"
                )
            }]
        )
        es_dup = resp.content[0].text.strip().upper().startswith("S")
        if es_dup:
            print(f"  [dup-ia] '{t1[:45]}' / '{t2[:45]}'")
        return es_dup
    except Exception as e:
        print(f"  [dup-error] {e}")
        # Fallback conservador: si la API falla, preferimos no bloquear
        return False


_STOP_TEMA = {
    "gobierno", "politica", "argentina", "argentino", "nacional", "oposicion",
    "oficialismo", "presidente", "elecciones", "provincia", "ministro",
    "senado", "diputados", "congreso", "sobre", "entre", "desde", "hasta",
    "tiene", "ahora", "contra", "despues", "antes", "cuando", "donde",
}


def _palabras_clave(texto: str) -> set:
    return {w for w in re.findall(r"\w{5,}", _normalize(texto)) if w not in _STOP_TEMA}


def es_tema_repetido(titulo: str, titulos_previos: list) -> tuple:
    """
    Dice si una noticia candidata trata un tema que YA publicamos en los ultimos dias.
    Devuelve (repetido, por_texto):
      - (True, True)   -> titulo casi igual a uno ya publicado (sin usar API)
      - (True, False)  -> mismo tema segun Claude Haiku (mismo protagonista y asunto)
      - (False, False) -> tema nuevo (o no se pudo verificar: ante la duda NO bloquea)
    """
    from difflib import SequenceMatcher

    titulo = (titulo or "").strip()
    previos = [p for p in titulos_previos if p]
    if not titulo or not previos:
        return False, False

    # Paso 1: texto casi igual
    for p in previos:
        if SequenceMatcher(None, titulo.lower(), p.lower()).ratio() >= 0.55:
            return True, True

    # Paso 2: elegir solo los previos que comparten alguna palabra clave (los mas recientes primero)
    claves = _palabras_clave(titulo)
    candidatos = [p for p in reversed(previos) if claves & _palabras_clave(p)][:15]
    if not candidatos:
        return False, False

    lista = "\n".join(f"{i+1}. {p}" for i, p in enumerate(candidatos))
    try:
        resp = _get_dedup_client().messages.create(
            model="claude-haiku-4-5",
            max_tokens=10,
            messages=[{
                "role": "user",
                "content": (
                    "Un sitio de noticias ya publico los titulos de la lista. "
                    "¿Alguno trata el MISMO tema o hecho noticioso (mismo protagonista y mismo asunto) "
                    "que el titulo nuevo? Si varios coinciden, responde el numero del primero. "
                    "Responde SOLO con el numero o con NO.\n\n"
                    f"Titulos ya publicados:\n{lista}\n\n"
                    f"Titulo nuevo: {titulo}"
                )
            }]
        )
        rta = resp.content[0].text.strip().upper()
        num = re.match(r"^\d+", rta)
        if num:
            pos = min(max(int(num.group()) - 1, 0), len(candidatos) - 1)
            parecido = candidatos[pos][:50]
            print(f"  [tema-repetido-ia] '{titulo[:50]}' ~ '{parecido}'")
            return True, False
        return False, False
    except Exception as e:
        print(f"  [tema-repetido-error] {e}")
        return False, False


def titulo_final_repetido(titulo: str, titulos_previos: list) -> bool:
    """Control final: el titulo YA reescrito no puede ser casi igual a uno ya publicado."""
    from difflib import SequenceMatcher
    t = (titulo or "").strip().lower()
    if not t:
        return False
    for p in titulos_previos:
        if p and SequenceMatcher(None, t, p.strip().lower()).ratio() >= 0.65:
            return True
    return False


def patch_css_tipografia():
    """Parchea el CSS del sitio para eliminar 'Patua One' de .patua_tit_ppal.
    Intenta dos métodos: Plesk REST API via HTTP, luego FTP como fallback.
    Se ejecuta en cada corrida: si el CSS se resetea, el siguiente run lo corrige."""
    import ftplib
    import io
    import base64

    host = "datapolitica.com"
    user = "admin"
    # Intentar con PLESK_PASS primero, sino DATAPOLITICA_PANEL_PASS
    passwd = os.environ.get("PLESK_PASS") or os.environ.get("DATAPOLITICA_PANEL_PASS", "")

    # Cadenas a reemplazar (variantes posibles del font-family con Patua One)
    REEMPLAZOS = [
        ('"Patua One", cursive, Arial, "Trebuchet MS", Tahoma', 'arial, "Trebuchet MS", Tahoma, sans-serif'),
        ("'Patua One', cursive, Arial, 'Trebuchet MS', Tahoma", 'arial, "Trebuchet MS", Tahoma, sans-serif'),
        ('"Patua One",cursive,Arial,"Trebuchet MS",Tahoma', 'arial,"Trebuchet MS",Tahoma,sans-serif'),
        ('"Patua One", cursive,', 'arial,'),
        ("'Patua One', cursive,", "arial,"),
        ('"Patua One"', "arial"),
        ("'Patua One'", "arial"),
    ]

    def apply_patch(css_content):
        """Aplica los reemplazos al CSS. Retorna (nuevo_contenido, cambios_hechos)."""
        patched = False
        for old, new in REEMPLAZOS:
            if old in css_content:
                css_content = css_content.replace(old, new)
                patched = True
                print(f"   Reemplazado: {repr(old[:50])} -> {repr(new[:40])}")
        return css_content, patched

    print("\n>> Parcheando CSS tipografia...")

    # ── Método 1: Plesk REST API ──────────────────────────────────────────────
    # Plesk REST API: HTTPS en 8443 (principal) o HTTP en 8880 (fallback)
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    CSS_FILES = ["estilos/estilos.css", "styles/styles.css"]
    plesk_bases = [f"https://{host}:8443", f"http://{host}:8880"]
    plesk_ok = False
    for plesk_base in plesk_bases:
        try:
            sess = requests.Session()
            sess.auth = (user, passwd)
            sess.verify = False
            sess.headers.update({"User-Agent": "Mozilla/5.0"})

            # Buscar dominio
            r = sess.get(f"{plesk_base}/api/v2/domains", timeout=15)
            print(f"   [Plesk API] {plesk_base} -> status={r.status_code}, tipo={r.headers.get('Content-Type','?')[:40]}")
            if r.status_code == 200 and "json" in r.headers.get("Content-Type", ""):
                domains = r.json()
                domain_id = None
                for d in domains:
                    if "datapolitica" in d.get("name", "").lower():
                        domain_id = d.get("id")
                        break
                if domain_id:
                    for css_file in CSS_FILES:
                        r2 = sess.get(
                            f"{plesk_base}/api/v2/domains/{domain_id}/files/content",
                            params={"filename": css_file},
                            timeout=15,
                        )
                        if r2.status_code == 200:
                            css_content = r2.text
                            print(f"   [Plesk API] CSS leído: {css_file} ({len(css_content)} bytes)")
                            new_css, patched = apply_patch(css_content)
                            if patched:
                                r3 = sess.put(
                                    f"{plesk_base}/api/v2/domains/{domain_id}/files/content",
                                    params={"filename": css_file},
                                    data=new_css.encode("utf-8"),
                                    timeout=15,
                                )
                                if r3.status_code in (200, 204):
                                    print("   [Plesk API] CSS guardado OK")
                                    plesk_ok = True
                                    return
                                else:
                                    print(f"   [Plesk API] Error al guardar: {r3.status_code}")
                            else:
                                print("   [Plesk API] CSS ya correcto")
                                return
                else:
                    print(f"   [Plesk API] Dominio no encontrado en respuesta")
            else:
                print(f"   [Plesk API] Respuesta no JSON: {r.text[:150]}")
        except Exception as e:
            print(f"   [Plesk API] Error con {plesk_base}: {type(e).__name__}: {e}")
    if not plesk_ok:
        print("   [Plesk API] Ambos endpoints fallaron. Intentando FTP...")

    # ── Método 2: FTP ─────────────────────────────────────────────────────────
    FTP_PATHS = [
        "httpdocs/estilos/estilos.css",
        "httpdocs/styles/styles.css",
        "estilos/estilos.css",
        "styles/styles.css",
    ]
    try:
        ftp = ftplib.FTP()
        ftp.connect(host, 21, timeout=20)
        ftp.login(user, passwd)
        ftp.set_pasv(True)
        print(f"   [FTP] Conectado OK")

        css_content = None
        css_path = None
        for path in FTP_PATHS:
            try:
                buf = io.BytesIO()
                ftp.retrbinary(f"RETR {path}", buf.write)
                css_content = buf.getvalue().decode("utf-8", errors="replace")
                css_path = path
                print(f"   [FTP] CSS encontrado: {path} ({len(css_content)} bytes)")
                break
            except ftplib.error_perm:
                continue

        if css_content is None:
            print("   [FTP] No se encontró el CSS en ninguna ruta conocida.")
            ftp.quit()
            return

        new_css, patched = apply_patch(css_content)
        if patched:
            ftp.storbinary(f"STOR {css_path}", io.BytesIO(new_css.encode("utf-8")))
            print("   [FTP] CSS guardado OK — Patua One eliminada")
        else:
            print("   [FTP] CSS ya correcto")

        ftp.quit()

    except ftplib.all_errors as e:
        print(f"   [FTP] Error: {e}")
    except Exception as e:
        print(f"   Error inesperado en parche CSS: {e}")


def delete_note(session: requests.Session, note_id: int) -> bool:
    url = f"{PANEL_DEL_URL}?mod=deletenews&ids={note_id}"
    try:
        resp = session.get(url, timeout=20)
        result = resp.text.strip()
        ok = result.lower().startswith("ok")
        print(f"   Borrar nota {note_id}: {'OK' if ok else 'ERROR'} ({result[:80]})")
        return ok
    except Exception as e:
        print(f"   Borrar nota {note_id}: excepcion: {e}")
        return False


def delete_all_orden1(session: requests.Session) -> int:
    """Scrapes panel_listado.aspx y borra TODOS los articulos de radio=1 (Orden 1).
    No depende del state.json - elimina todo lo que encuentre en el panel."""
    try:
        resp = session.get(PANEL_LIST_URL, timeout=30)
        soup = BeautifulSoup(resp.text, "html.parser")
        borrados = 0
        intentados = 0

        for a in soup.find_all("a", href=True):
            href = a["href"]
            m = re.search(r'panel_agregar_nota\.aspx\?id=(\d+)', href)
            if not m:
                continue
            note_id = int(m.group(1))

            # Buscar la fila de la tabla para ver el radio
            row = a.find_parent("tr")
            if row:
                cells = row.find_all("td")
                es_orden1 = False
                for cell in cells:
                    txt = cell.get_text(strip=True)
                    if txt == "1":
                        es_orden1 = True
                        break
                if not es_orden1:
                    continue

            intentados += 1
            if delete_note(session, note_id):
                borrados += 1

        print(f"   delete_all_orden1: intentados={intentados}, borrados={borrados}")
        return borrados
    except Exception as e:
        print(f"   Aviso: No se pudo scrapear panel para borrar: {e}")
        return 0


# ── Login ──────────────────────────────────────────────────────────────────────
def login(session: requests.Session) -> bool:
    print(f"   GET {PANEL_LOGIN_URL}")
    resp = session.get(PANEL_LOGIN_URL, timeout=30)
    print(f"   Status: {resp.status_code} | URL: {resp.url}")

    soup = BeautifulSoup(resp.text, "html.parser")

    vs  = soup.find("input", {"id": "__VIEWSTATE"})
    vsg = soup.find("input", {"id": "__VIEWSTATEGENERATOR"})
    ev  = soup.find("input", {"id": "__EVENTVALIDATION"})

    if not vs:
        print(f"   ERROR: No se encontro __VIEWSTATE. HTML: {resp.text[:300]}")
        return False

    btn = soup.find("input", {"type": "submit"})
    btn_name  = btn["name"]  if btn and btn.get("name")  else "btnIngresar"
    btn_value = btn["value"] if btn and btn.get("value") else "ingresar"

    payload = {
        "__VIEWSTATE":          vs["value"],
        "__VIEWSTATEGENERATOR": vsg["value"] if vsg else "",
        "__EVENTVALIDATION":    ev["value"]  if ev  else "",
        "txtUsuario":           PANEL_USER,
        "txtClave":             PANEL_PASS,
        btn_name:               btn_value,
    }

    resp2 = session.post(PANEL_LOGIN_URL, data=payload, timeout=30, allow_redirects=True)
    print(f"   Status: {resp2.status_code} | URL final: {resp2.url}")

    ok = "login" not in resp2.url.lower()
    if not ok:
        print(f"   ERROR: Login fallo. HTML preview: {resp2.text[:500]}")
    else:
        print(f"   Login exitoso -> {resp2.url}")

    return ok


# ── ViewState ──────────────────────────────────────────────────────────────────
def get_viewstate(session: requests.Session) -> dict | None:
    resp = session.get(PANEL_NOTA_URL, timeout=30)

    if "login" in resp.url.lower():
        print(f"   ERROR: get_viewstate redireciono al login")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    vs  = soup.find("input", {"id": "__VIEWSTATE"})
    if not vs:
        print(f"   ERROR: __VIEWSTATE no encontrado en {resp.url}")
        return None

    return {
        "__VIEWSTATE":          vs["value"],
        "__VIEWSTATEGENERATOR": soup.find("input", {"id": "__VIEWSTATEGENERATOR"})["value"],
        "__EVENTVALIDATION":    soup.find("input", {"id": "__EVENTVALIDATION"})["value"],
    }


# ── RSS ────────────────────────────────────────────────────────────────────────
def _parse_rss(xml_text: str) -> list[dict]:
    articles = []
    try:
        root = ET.fromstring(xml_text)
        ns = {"atom": "http://www.w3.org/2005/Atom",
              "media": "http://search.yahoo.com/mrss/"}
        items = root.findall(".//item")
        for item in items:
            title = (item.findtext("title") or "").strip()
            desc  = (item.findtext("description") or
                     item.findtext("content") or "").strip()
            link  = (item.findtext("link") or "").strip()
            img_url = ""
            media_content = item.find("media:content", ns)
            if media_content is not None:
                img_url = media_content.get("url", "")
            if not img_url:
                enclosure = item.find("enclosure")
                if enclosure is not None and "image" in enclosure.get("type", ""):
                    img_url = enclosure.get("url", "")
            if title:
                articles.append({"title": title, "summary": desc,
                                  "link": link, "img_url": img_url})
        if not articles:
            for entry in root.findall(".//atom:entry", ns):
                title   = (entry.findtext("atom:title", namespaces=ns) or "").strip()
                summary = (entry.findtext("atom:summary", namespaces=ns) or
                           entry.findtext("atom:content", namespaces=ns) or "").strip()
                link_el = entry.find("atom:link[@rel='alternate']", ns)
                link    = link_el.get("href", "") if link_el is not None else ""
                if title:
                    articles.append({"title": title, "summary": summary,
                                     "link": link, "img_url": ""})
    except Exception:
        pass
    return articles


def fetch_articles(n: int = 40) -> list[dict]:
    from collections import defaultdict

    # Paso 1: recolectar todos los articulos con su fuente (sin deduplicar)
    all_items = []
    conteo_fuentes = {}
    for url in RSS_SOURCES:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            items = _parse_rss(resp.text)
            for art in items:
                if art.get("title"):
                    art["_fuente"] = url
                    all_items.append(art)
            print(f"   RSS {url[:50]}: {len(items)} articulos")
            conteo_fuentes[url] = len(items)
        except Exception as e:
            print(f"   Error al obtener {url}: {e}")
            conteo_fuentes[url] = "no responde"

    # Paso 2: contar en cuantas fuentes aparece cada titulo (normalizado)
    titulo_fuentes = defaultdict(set)
    for art in all_items:
        clave = _normalize(art["title"])[:80]
        titulo_fuentes[clave].add(art["_fuente"])

    # Paso 3: deduplicar por titulo exacto, guardar conteo de fuentes
    seen_titles = set()
    articles = []
    for art in all_items:
        t = art["title"]
        if t in seen_titles:
            continue
        seen_titles.add(t)
        clave = _normalize(t)[:80]
        art["_n_fuentes"] = len(titulo_fuentes[clave])
        articles.append(art)

    print(f"   Total articulos en pool: {len(articles)}")
    # Intercalar las fuentes (1 de cada una por turno) para que no quede todo el pool
    # ocupado por la primera fuente (antes Infobae llenaba los 40 lugares)
    por_fuente = defaultdict(list)
    for art in articles:
        por_fuente[art["_fuente"]].append(art)
    mezcla = []
    while any(por_fuente.values()):
        for f in list(por_fuente.keys()):
            if por_fuente[f]:
                mezcla.append(por_fuente[f].pop(0))
    try:
        RESUMEN["fuentes"] = [f"{u.split('/')[2]}: {c}" for u, c in conteo_fuentes.items()]
        RESUMEN["fuentes"].append(f"Total en el pool: {len(articles)} (se usan {min(n, len(mezcla))})")
    except Exception:
        pass
    return mezcla[:n]


def get_current_homepage_articles() -> list[dict]:
    try:
        resp = requests.get("http://datapolitica.com/index.aspx",
                            headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        titles = set()
        for a in soup.find_all("a"):
            text = a.get_text(strip=True)
            if 25 <= len(text) <= 200:
                titles.add(text)
        result = [{"title": t, "summary": ""} for t in titles]
        print(f"   Titulos actuales en homepage: {len(result)}")
        return result
    except Exception as e:
        print(f"   Aviso: No se pudo scrapear homepage: {e}")
        return []


# ── Imagen ─────────────────────────────────────────────────────────────────────
_CASCADAS = {}


def _cascada(nombre):
    """Carga el detector de caras de OpenCV (si el paquete no trae el archivo, lo baja una vez)."""
    if nombre in _CASCADAS:
        return _CASCADAS[nombre]
    import cv2, os, requests as _rq
    ruta = os.path.join(getattr(cv2, "data", None) and cv2.data.haarcascades or "", nombre)
    if not os.path.exists(ruta):
        ruta = os.path.join("/tmp", nombre)
        if not os.path.exists(ruta):
            url = "https://raw.githubusercontent.com/opencv/opencv/4.x/data/haarcascades/" + nombre
            r = _rq.get(url, timeout=30)
            r.raise_for_status()
            open(ruta, "wb").write(r.content)
    cas = cv2.CascadeClassifier(ruta)
    _CASCADAS[nombre] = cas if not cas.empty() else None
    return _CASCADAS[nombre]


def _caras(img):
    """Devuelve lista de (x, y, w, h) de caras detectadas (solo deteccion, no reconoce a nadie)."""
    try:
        import cv2, numpy as np
        arr = np.array(img.convert("L"))
        cas = _cascada("haarcascade_frontalface_default.xml")
        if cas is None:
            return []
        h, w = arr.shape
        m = max(24, int(min(w, h) * 0.06))
        caras = cas.detectMultiScale(arr, scaleFactor=1.1, minNeighbors=6, minSize=(m, m))
        if len(caras) == 0:
            prof = _cascada("haarcascade_profileface.xml")
            if prof is not None:
                caras = prof.detectMultiScale(arr, scaleFactor=1.1, minNeighbors=6, minSize=(m, m))
        caras = [tuple(int(v) for v in c) for c in caras]
        if not caras:
            return []
        # quedarse con las caras importantes (al menos 40% del tamano de la mas grande)
        mayor = max(c[2] for c in caras)
        return [c for c in caras if c[2] >= mayor * 0.4]
    except Exception:
        return []


# Calidad de encuadre de cada foto ya recortada (por su huella): True = bien encuadrada
_ENCUADRE: dict = {}


def encuadre_ok(data: bytes) -> bool:
    """False si la foto original era un primer plano exagerado o la cara estaba cortada por un borde."""
    import hashlib
    return _ENCUADRE.get(hashlib.md5(data).hexdigest(), True)


def _evaluar_encuadre(caras, w, h) -> bool:
    if not caras:
        return True  # sin caras detectadas no se puede juzgar
    mayor = max(caras, key=lambda c: c[2] * c[3])
    x, y, cw, ch = mayor
    if ch > h * 0.50 or cw > w * 0.45:
        return False  # primer plano: la cara ocupa casi toda la foto
    margen_x, margen_y = w * 0.02, h * 0.02
    if x < margen_x or x + cw > w - margen_x or y < margen_y:
        return False  # cara pegada o cortada por un borde
    return True


def _crop_to_16_9(data: bytes) -> bytes:
    """Recorta a 16:9 (1200x675) centrando las CARAS de la foto, para que ninguna miniatura
    de la portada (que recorta los costados) deje a la persona cortada o fuera de cuadro.
    Si la cara esta pegada a un borde, completa el costado con un fondo difuminado."""
    try:
        from PIL import Image, ImageFilter
        img = Image.open(io.BytesIO(data)).convert("RGB")
        w, h = img.size
        R = 16 / 9
        caras = _caras(img)
        if caras:
            x0 = min(c[0] for c in caras); x1 = max(c[0] + c[2] for c in caras)
            y0 = min(c[1] for c in caras); y1 = max(c[1] + c[3] for c in caras)
            cx = (x0 + x1) / 2
            cy = (y0 + y1) / 2
            ancho_caras = x1 - x0
            alto_cara = max(c[3] for c in caras)
            # tamano del recorte: lo mas grande posible, pero que la persona no quede diminuta
            # y que las caras entren en el 70% central (zona segura de las miniaturas)
            cw = min(w, h * R)
            cw = max(cw, ancho_caras / 0.70)
            ch = cw / R
            # que la cara no ocupe mas de ~45% de alto ni quede cortada arriba
            ch = max(ch, alto_cara / 0.45)
            cw = ch * R
            # posicion: caras centradas a lo ancho y en el tercio superior-medio
            left = cx - cw / 2
            top = cy - ch * 0.40
            # si el recorte se sale de la foto, ampliar el lienzo con fondo difuminado
            pad_l = max(0, -left); pad_r = max(0, left + cw - w)
            pad_t = max(0, -top); pad_b = max(0, top + ch - h)
            # arriba/abajo preferimos mover el recorte antes que rellenar
            if pad_t or pad_b:
                if ch <= h:
                    top = min(max(0, top), h - ch); pad_t = pad_b = 0
            if pad_l or pad_r or pad_t or pad_b:
                W = int(w + pad_l + pad_r) + 1; H = int(h + pad_t + pad_b) + 1
                fondo = img.resize((W, H)).filter(ImageFilter.GaussianBlur(40))
                fondo.paste(img, (int(pad_l), int(pad_t)))
                img = fondo
                left += pad_l; top += pad_t
            img = img.crop((int(left), int(top), int(left + cw), int(top + ch)))
        else:
            cur = w / h
            if cur > R:
                nw = int(h * R); left = (w - nw) // 2
                img = img.crop((left, 0, left + nw, h))
            elif cur < R:
                nh = int(w / R)
                img = img.crop((0, 0, w, nh))  # sin caras detectadas: desde arriba
        img = img.resize((1200, 675), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        out = buf.getvalue()
        import hashlib
        _ENCUADRE[hashlib.md5(out).hexdigest()] = _evaluar_encuadre(caras, w, h)
        return out
    except Exception as e:
        print(f"   Aviso: No se pudo recortar imagen: {e}")
        return data


def _is_journalist_studio_image(img) -> bool:
    """Usa Claude Haiku para detectar si la imagen es de periodista/conductor en estudio.
    Retorna True si ES imagen de periodista (debe descartarse)."""
    try:
        import base64, io as _io
        buf = _io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=60)
        b64 = base64.b64encode(buf.getvalue()).decode()
        cli = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        resp = cli.messages.create(
            model="claude-haiku-4-5",
            max_tokens=5,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": "¿Es esta imagen un periodista o conductor en estudio de radio/TV (microfono, auriculares, fondo de estudio)? Responde solo SI o NO."}
                ]
            }]
        )
        answer = resp.content[0].text.strip().upper().replace("Í","I").replace("É","E")
        return answer.startswith("SI") or answer.startswith("SÍ")
    except Exception as e:
        print(f"   Aviso clasificacion Claude: {e}")
        return False  # si falla, no descartar


_URL_BLACKLIST = (
    "via.placeholder.com",
    "scorecardresearch.com",
    "e-planning.net",
    "doubleclick.net",
    "googlesyndication.com",
    "ads.",
)

def _download_image(url: str) -> bytes | None:
    if any(b in url for b in _URL_BLACKLIST):
        return None
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, stream=True)
        content_type = resp.headers.get("content-type", "")
        if "image" not in content_type:
            print(f"   Imagen descartada (content-type={content_type[:40]}): {url[:60]}")
            return None
        data = resp.content
        if len(data) < 3000:
            print(f"   Imagen descartada por tamano ({len(data)//1024}KB < 3KB): {url[:60]}")
            return None
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(data))
            w, h = img.size
            if w < 200 or h < 100:
                print(f"   Imagen descartada por dimensiones ({w}x{h}): {url[:60]}")
                return None
            ratio = w / h
            if ratio < 0.3:
                print(f"   Imagen descartada (banner vertical extremo, ratio={ratio:.2f}): {url[:60]}")
                return None
            if ratio > 6.0:
                print(f"   Imagen descartada (banner horizontal extremo, ratio={ratio:.2f}): {url[:60]}")
                return None
        except Exception:
            if len(data) < 5000:
                return None

        data = _crop_to_16_9(data)
        print(f"   Imagen descargada y recortada a 16:9: {len(data)//1024}KB")
        return data
    except Exception as e:
        print(f"   Error descargando imagen {url[:80]}: {e}")
    return None


def _es_url_logo(url: str) -> bool:
    """Rechaza URLs que parezcan logos, íconos o imágenes de marca."""
    u = url.lower()
    if any(kw in u for kw in ['logo', 'icon', 'favicon', 'brand']):
        return True
    return False


def _scrape_best_image(link: str) -> bytes | None:
    """Extrae la mejor imagen disponible del artículo: og:image, twitter:image, primera img grande."""
    try:
        resp = requests.get(link, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")

        # 1) og:image
        og = (soup.find("meta", {"property": "og:image"}) or
              soup.find("meta", {"name": "og:image"}))
        if og:
            url = og.get("content", "").strip()
            if url and not _es_url_logo(url):
                data = _download_image(url)
                if data:
                    print(f"   Imagen desde og:image")
                    return data

        # 2) twitter:image
        tw = (soup.find("meta", {"name": "twitter:image"}) or
              soup.find("meta", {"property": "twitter:image"}))
        if tw:
            url = tw.get("content", "").strip()
            if url and not _es_url_logo(url):
                data = _download_image(url)
                if data:
                    print(f"   Imagen desde twitter:image")
                    return data

        # 3) Primera <img> grande dentro del artículo
        for img_tag in soup.find_all("img", src=True):
            src = img_tag.get("src", "").strip()
            if not src or src.startswith("data:") or _es_url_logo(src):
                continue
            if not src.startswith("http"):
                from urllib.parse import urljoin
                src = urljoin(link, src)
            data = _download_image(src)
            if data:
                print(f"   Imagen desde <img> del artículo")
                return data

    except Exception as e:
        print(f"   No se pudo scrapear imagen de {link}: {e}")
    return None


# ── Foto de la persona nombrada en el titulo ───────────────────────────────────
def _nombre_del_titulo(titulo: str) -> str:
    """Si el titulo empieza con 'Nombre Apellido: ...', devuelve 'Nombre Apellido'. Si no, ''."""
    if ":" not in (titulo or ""):
        return ""
    cand = titulo.split(":", 1)[0].strip()
    palabras = cand.split()
    if not (2 <= len(palabras) <= 5):
        return ""
    if palabras[0].lower() in {"el", "la", "los", "las", "un", "una", "unos", "unas", "ante", "tras", "sin"}:
        return ""  # 'El Gobierno: ...', 'La oposicion: ...' no son personas
    conectores = {"de", "del", "la", "las", "los", "y", "da", "di", "van", "von"}
    for w in palabras:
        if w.lower() in conectores:
            continue
        if not w[0].isupper():
            return ""
    return cand


def persona_del_titulo(client, titulo: str, copete: str, art: dict) -> str:
    """Devuelve el NOMBRE COMPLETO de la persona que nombra el titulo (aunque el titulo traiga solo
    el apellido), o '' si no nombra a ninguna persona. Usa el copete y el articulo para completar."""
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=60,
            messages=[{"role": "user", "content": (
                "¿Este titulo nombra a una PERSONA concreta (por nombre o apellido)? Si nombra a varias, "
                "elegi la principal. Responde SOLO con su nombre y apellido completos tal como figuran en "
                "el copete o el articulo (ej: 'Patricia Bullrich'), o con NINGUNA si no nombra a ninguna persona "
                "(instituciones, partidos o 'el Gobierno' NO son personas).\n\n"
                f"Titulo: {titulo}\nCopete: {copete}\nTitulo original del articulo: {art.get('title','')}"
            )}]
        )
        r = resp.content[0].text.strip().strip('".\'')
        if not r or r.upper().startswith("NINGUN") or len(r.split()) < 2 or len(r.split()) > 5:
            return ""
        # el apellido tiene que aparecer en el titulo (si no, no es la persona del titulo)
        if _normalize(r.split()[-1]) not in _normalize(titulo):
            return ""
        return r
    except Exception as e:
        print(f"    Aviso: no se pudo identificar la persona del titulo: {e}")
        # ante la duda, usar la deteccion simple 'Nombre Apellido: ...'
        return _nombre_del_titulo(titulo)


def _imagen_con_apellido(link: str, nombre: str) -> bytes | None:
    """Busca, entre las fotos del articulo original, una cuyo epigrafe / texto alternativo /
    nombre de archivo mencione el apellido de la persona. Devuelve esa foto o None."""
    from urllib.parse import urljoin
    partes = [_normalize(x) for x in nombre.split() if len(x) > 1]
    if len(partes) < 2 or not link:
        return None  # sin nombre y apellido no hay forma segura de confirmar quien es
    apellido = partes[-1]
    nombre_pila = partes[0]
    if len(apellido) < 4:
        return None
    try:
        resp = requests.get(link, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        cands = []
        for prop in ("og:image", "twitter:image"):
            m = soup.find("meta", {"property": prop}) or soup.find("meta", {"name": prop})
            if m and m.get("content"):
                alt = (soup.find("meta", {"property": prop + ":alt"}) or
                       soup.find("meta", {"name": prop + ":alt"}))
                cands.append((m["content"].strip(), alt.get("content", "") if alt else ""))
        for tag in soup.find_all("img")[:80]:
            src = (tag.get("src") or tag.get("data-src") or "").strip()
            if not src or src.startswith("data:"):
                continue
            # ignorar fotos de notas relacionadas, barras laterales, menus, etc.
            if tag.find_parent(["aside", "nav", "footer", "header"]) is not None:
                continue
            _ancestros = " ".join(" ".join(a.get("class", [])) + " " + (a.get("id") or "")
                                  for a in tag.parents if getattr(a, "get", None))
            if re.search(r"related|relacionad|recomend|sidebar|trending|masleidas|mas-leidas|widget|carousel|newsletter",
                         _ancestros, re.I):
                continue
            texto = (tag.get("alt") or "") + " " + (tag.get("title") or "")
            fig = tag.find_parent("figure")
            if fig is not None and fig.find("figcaption") is not None:
                texto += " " + fig.find("figcaption").get_text(" ", strip=True)
            cands.append((urljoin(link, src), texto))
        vistos = set()
        for url, texto in cands:
            if url in vistos or _es_url_logo(url):
                continue
            vistos.add(url)
            archivo = url.split("?")[0].rsplit("/", 1)[-1]
            _t = _normalize(texto + " " + archivo)
            # nombre de pila Y apellido (evita confundir a Maximo con Cristina Kirchner, etc.)
            if apellido in _t and nombre_pila in _t:
                data = _download_image(url)
                if data:
                    try:
                        from PIL import Image as _PILImage
                        if _is_journalist_studio_image(_PILImage.open(io.BytesIO(data))):
                            print("   Foto del articulo descartada: es una toma de estudio de TV/radio (con conductores)")
                            continue
                    except Exception:
                        pass
                    print(f"   Foto de {nombre}: tomada del articulo (el epigrafe/archivo lo nombra)")
                    return data
    except Exception as e:
        print(f"   Aviso: busqueda de foto por apellido fallo: {e}")
    return None


def _foto_wikipedia(nombre: str) -> bytes | None:
    """Retrato de la persona desde Wikipedia (solo si la pagina es de una figura politica/publica argentina)."""
    try:
        resp = requests.get(
            "https://es.wikipedia.org/w/api.php",
            params={"action": "query", "titles": nombre, "prop": "pageimages|extracts",
                    "piprop": "thumbnail", "pithumbsize": 900, "exintro": 1, "explaintext": 1,
                    "exchars": 500, "redirects": 1, "format": "json"},
            headers={"User-Agent": "datapolitica-bot/1.0 (https://datapolitica.com)"},
            timeout=15,
        )
        pages = resp.json().get("query", {}).get("pages", {})
        claves = ("argentin", "politic", "senador", "diputad", "gobernador", "intendente",
                  "president", "ministr", "dirigente", "legislador", "juez", "jueza", "fiscal", "sindical")
        for pg in pages.values():
            if "missing" in pg:
                continue
            ext = _normalize(pg.get("extract", ""))
            if not any(k in ext for k in claves):
                print(f"   Wikipedia: '{nombre}' no parece una figura politica argentina, se ignora")
                continue
            thumb = pg.get("thumbnail", {}).get("source", "")
            if thumb:
                data = _download_image(thumb)
                if data:
                    print(f"   Foto de {nombre}: retrato de Wikipedia")
                    return data
    except Exception as e:
        print(f"   Aviso: foto de Wikipedia fallo: {e}")
    return None


def _foto_commons(nombre: str) -> bytes | None:
    """Foto de Wikimedia Commons cuyo NOMBRE DE ARCHIVO contiene el nombre completo de la persona."""
    try:
        resp = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={"action": "query", "generator": "search", "gsrsearch": nombre,
                    "gsrnamespace": 6, "gsrlimit": 15, "prop": "imageinfo",
                    "iiprop": "url|size|mime", "iiurlwidth": 900, "format": "json"},
            headers={"User-Agent": "datapolitica-bot/1.0 (https://datapolitica.com)"},
            timeout=15,
        )
        pages = resp.json().get("query", {}).get("pages", {})
        partes = [_normalize(w) for w in nombre.split() if len(w) > 2]
        for pg in sorted(pages.values(), key=lambda x: x.get("index", 99)):
            info = (pg.get("imageinfo") or [{}])[0]
            titulo = _normalize(pg.get("title", ""))
            if info.get("mime") != "image/jpeg" or info.get("width", 0) < 400:
                continue
            if not all(w in titulo for w in partes):
                continue
            data = _download_image(info.get("thumburl") or info.get("url", ""))
            if data:
                print(f"   Foto de {nombre}: de Wikimedia Commons")
                return data
    except Exception as e:
        print(f"   Aviso: foto de Commons fallo: {e}")
    return None


def titulo_sin_nombre(client, titulo: str, copete: str) -> str:
    """Reescribe el titulo SIN nombre propio al comienzo (usa el cargo o el tema). Si algo falla, devuelve ''."""
    try:
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=150,
            messages=[{"role": "user", "content": (
                "Reescribi este titulo de una noticia politica argentina para que NO empiece con el nombre de ninguna persona: "
                "empezalo con su cargo, la institucion o el tema. No inventes datos ni agregues nombres. "
                "Debe entenderse con una sola lectura, sin jerga, metaforas, abreviaturas ni fechas sueltas, diciendo claramente quien hizo que. "
                f"Frase COMPLETA de maximo {MAX_TITULO} caracteres, estilo periodistico argentino. "
                "Responde SOLO con el titulo, sin comillas.\n\n"
                f"Titulo: {titulo}\nCopete: {copete}"
            )}]
        )
        nuevo = resp.content[0].text.strip().strip('"').strip()
        nuevo = corregir_conjunciones(nuevo)
        if not nuevo or len(nuevo) > MAX_TITULO or len(nuevo.split()) < 4:
            return ""
        if nuevo.split()[-1].lower().rstrip(".,;:") in {"de", "del", "en", "al", "a", "con", "por", "para", "y", "e", "o", "u", "que", "la", "el", "los", "las"}:
            return ""
        return nuevo
    except Exception as e:
        print(f"   Aviso: no se pudo reescribir el titulo sin nombre: {e}")
        return ""


def foto_de_persona(art: dict, nombre: str, actual: bytes | None = None) -> bytes | None:
    """Foto que SI muestra a la persona nombrada en el titulo (epigrafe del articulo, Wikipedia o
    Commons). Entre las verificadas prefiere una BIEN ENCUADRADA (cara entera, ni pegada al borde
    ni primer plano exagerado). None = no se encontro ninguna verificada."""
    candidatas = []
    for fuente in (lambda: _imagen_con_apellido(art.get("link", ""), nombre),
                   lambda: _foto_wikipedia(nombre),
                   lambda: _foto_commons(nombre)):
        try:
            data = fuente()
        except Exception:
            data = None
        if data:
            if encuadre_ok(data):
                return data
            print("   Foto verificada pero mal encuadrada (primer plano o cara cortada): busco otra")
            candidatas.append(data)
    return candidatas[0] if candidatas else None


def fetch_article_image(art: dict) -> bytes | None:
    # Primero: scraping completo del artículo (og:image > twitter:image > img tag)
    link = art.get("link", "")
    if link:
        data = _scrape_best_image(link)
        if data:
            return data

    # Fallback: imagen del RSS
    img_url = art.get("img_url", "")
    if img_url and not _es_url_logo(img_url):
        data = _download_image(img_url)
        if data:
            print(f"   Imagen obtenida del RSS (fallback)")
            return data

    print("   Sin imagen disponible para esta nota")
    return None


def _image_extension(data: bytes) -> str:
    if data[:4] == b'\x89PNG':
        return "png"
    if data[:3] in (b'GIF', b'gif'):
        return "gif"
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return "webp"
    return "jpg"


# Fotos ya elegidas en esta corrida (firma de colores) para no repetir fotos casi iguales
# aunque sean archivos distintos (ej.: dos fotos del recinto de Diputados desde otro angulo)
_FIRMAS_FOTOS_USADAS: list = []
UMBRAL_FOTO_PARECIDA = 0.82  # probado: recinto vs recinto 0.86; fotos distintas <= 0.75


def _firma_foto(data: bytes):
    try:
        from PIL import Image as _I
        import collections
        im = _I.open(io.BytesIO(data)).convert("RGB").resize((32, 32))
        bins = collections.Counter((r // 64, g // 64, b // 64) for r, g, b in im.getdata())
        tot = sum(bins.values())
        return {k: v / tot for k, v in bins.items()}
    except Exception:
        return None


def _parecido(f1, f2) -> float:
    if not f1 or not f2:
        return 0.0
    return sum(min(f1.get(k, 0), f2.get(k, 0)) for k in set(f1) | set(f2))


def foto_muy_parecida(data: bytes) -> bool:
    f = _firma_foto(data)
    return any(_parecido(f, u) >= UMBRAL_FOTO_PARECIDA for u in _FIRMAS_FOTOS_USADAS)


def registrar_foto_usada(data: bytes):
    f = _firma_foto(data)
    if f:
        _FIRMAS_FOTOS_USADAS.append(f)


def find_article_with_photo(articles: list[dict],
                            exclude_similar_to: list[dict] | None = None,
                            used_indices: set | None = None,
                            require_argentina_politics: bool = True,
                            used_img_hashes: set | None = None) -> tuple[dict | None, bytes | None, int]:
    """
    Busca el primer articulo que:
    - No este en used_indices
    - No sea descartado por DISCARD_KEYWORDS
    - Tenga keyword de politica argentina en el titulo (si require_argentina_politics)
    - No sea similar a articulos ya seleccionados
    - Tenga foto disponible y NO repetida (hash de imagen)
    """
    import hashlib
    if used_indices is None:
        used_indices = set()
    if exclude_similar_to is None:
        exclude_similar_to = []
    if used_img_hashes is None:
        used_img_hashes = set()

    for i, art in enumerate(articles):
        if i in used_indices:
            continue
        if should_discard(art):
            continue
        if require_argentina_politics and not is_argentina_politics_title(art):
            continue
        if any(articles_are_similar(prev, art) for prev in exclude_similar_to):
            continue
        img = fetch_article_image(art)
        if img is not None:
            img_hash = hashlib.md5(img).hexdigest()
            if img_hash in used_img_hashes:
                print(f"   Foto repetida, descartado: {art['title'][:60]}")
                continue
            if foto_muy_parecida(img):
                print(f"   Foto casi igual a otra de esta actualizacion, descartado: {art['title'][:60]}")
                continue
            used_img_hashes.add(img_hash)
            registrar_foto_usada(img)
            print(f"   Articulo seleccionado [{i}]: {art['title'][:70]}")
            return art, img, i
        else:
            print(f"   Sin foto, descartado: {art['title'][:60]}")
    return None, None, -1


# ── Claude ─────────────────────────────────────────────────────────────────────
def corregir_conjunciones(texto: str) -> str:
    """
    Corrige conjunciones segun ortografia espanola:
    - 'o' -> 'u' antes de palabras que empiezan con 'o' u 'ho'
    - 'y' -> 'e' antes de palabras que empiezan con 'i' u 'hi'
    """
    texto = re.sub(r'\bo\b(?=\s+[oO])', 'u', texto)
    texto = re.sub(r'\bO\b(?=\s+[oO])', 'U', texto)
    texto = re.sub(r'\bo\b(?=\s+[hH][oO])', 'u', texto)
    texto = re.sub(r'\bO\b(?=\s+[hH][oO])', 'U', texto)
    texto = re.sub(r'\by\b(?=\s+[iI])', 'e', texto)
    texto = re.sub(r'\bY\b(?=\s+[iI])', 'E', texto)
    texto = re.sub(r'\by\b(?=\s+[hH][iI])', 'e', texto)
    texto = re.sub(r'\bY\b(?=\s+[hH][iI])', 'E', texto)
    # Al reves: 'e' solo corresponde antes de 'i' / 'hi' (no antes de 'hie' ni 'hia').
    # Si el modelo escribio 'e' antes de cualquier otra palabra (ej: 'Kirchner e Kicillof'), vuelve a 'y'.
    def _e_a_y(m):
        sig = m.group(2)
        n = _normalize(sig)
        if n.startswith("i") or (n.startswith("hi") and not n.startswith(("hie", "hia"))):
            return m.group(0)
        return m.group(1) + "y " + sig
    texto = re.sub(r'(\s)e\s+([A-Za-zÁÉÍÓÚáéíóúÑñ]\w*)', _e_a_y, texto)
    return texto


def rewrite(client: anthropic.Anthropic, art: dict, tipo: str) -> dict:
    if tipo == "cinta":
        prompt = f"""Sos editor de DATAPOLITICA, sitio argentino de analisis politico. \
Reescribi esta noticia como item de CINTA TESTIGO (ticker de noticias corto).

Articulo:
Titulo: {art['title']}
Resumen: {art['summary'][:400]}

Responde SOLO con JSON valido, sin markdown:
{{"titulo": "...", "copete": "...", "cuerpo": "..."}}

REGLAS:
- titulo: entre 45 y {MAX_TITULO - 8} caracteres (NUNCA mas de {MAX_TITULO}; conta los caracteres antes de responder). Frase COMPLETA. Estilo telegrafico.
- CLARIDAD DEL TITULO: el titulo se debe entender con UNA sola lectura, por un lector comun que no sigue la politica a diario y sin tener que buscar nada. Prohibido: jerga interna, metaforas, abreviaturas, fechas sueltas ("el 17", "el 3"), palabras como 'relato', 'proscripcion', 'ola', 'arena', 'tablero', 'juego' usadas en sentido figurado, y titulos que dejan la accion sin decir. Debe responder QUE hizo QUIEN, con verbo claro y datos concretos (nombres, cifras, lugar). Si una fecha es clave, escribila completa ("17 de Octubre"). Malo: 'Cristina Kirchner: el PJ activa el relato de la proscripcion en el 17'. Bien: 'El PJ dice que Cristina Kirchner esta proscripta en el acto del 17 de Octubre'.
- copete: max 90 caracteres. El dato clave en una frase.
- cuerpo: max 200 caracteres. Una sola oracion de contexto.
- No uses verbos de cita. Narra el hecho.
- Tono: directo, politico, argentino.
- ORTOGRAFIA: Usa 'u' en lugar de 'o' ante palabras que empiecen con 'o' u 'ho'. Usa 'e' en lugar de 'y' ante palabras que empiecen con 'i' u 'hi'."""

    else:
        prompt = f"""Sos editor jefe de DATAPOLITICA, sitio argentino de analisis politico.

Reescribi esta noticia con perspectiva analitica propia, angulo DIFERENTE al original.

Articulo:
Titulo: {art['title']}
Resumen: {art['summary'][:600]}

Responde SOLO con JSON valido, sin markdown:
{{"titulo": "...", "copete": "...", "cuerpo": "..."}}

REGLAS ESTRICTAS:
- titulo: entre 45 y {MAX_TITULO - 8} caracteres (NUNCA mas de {MAX_TITULO}; conta los caracteres antes de responder). Frase completa, angulo propio, sin copiar el original.
- CLARIDAD DEL TITULO: el titulo se debe entender con UNA sola lectura, por un lector comun que no sigue la politica a diario y sin tener que buscar nada. Prohibido: jerga interna, metaforas, abreviaturas, fechas sueltas ("el 17", "el 3"), palabras como 'relato', 'proscripcion', 'ola', 'arena', 'tablero', 'juego' usadas en sentido figurado, y titulos que dejan la accion sin decir. Debe responder QUE hizo QUIEN, con verbo claro y datos concretos (nombres, cifras, lugar). Si una fecha es clave, escribila completa ("17 de Octubre"). Malo: 'Cristina Kirchner: el PJ activa el relato de la proscripcion en el 17'. Bien: 'El PJ dice que Cristina Kirchner esta proscripta en el acto del 17 de Octubre'.
- NOMBRE PROPIO EN EL TITULO: si la noticia tiene un protagonista politico identificable (una persona), el titulo DEBE empezar con su NOMBRE Y APELLIDO COMPLETOS (por ejemplo 'Miguel Siciliano: ...' o 'Axel Kicillof: ...'), seguidos de dos puntos o de un verbo. Usa SOLO nombres que figuren en el articulo; nunca inventes nombres ni cargos. Si el articulo solo trae el apellido y no estas seguro del nombre, usa el apellido solo. Si no hay una persona protagonista clara, no fuerces ningun nombre.
- FECHAS Y ANOS: hoy es {datetime.now().strftime('%d/%m/%Y')} (ano {datetime.now().year}). NUNCA cambies ni inventes anos, fechas ni cifras del articulo: si el articulo habla de 2027, el titulo dice 2027. Si no estas seguro de un ano, no lo menciones.
- copete: max {MAX_COPETE} caracteres. Lead periodistico que amplia el titulo.
- cuerpo: OBLIGATORIO entre {MIN_CUERPO} y {MAX_CUERPO} caracteres. NO menos. ESTRUCTURA OBLIGATORIA: usa piramide invertida. El PRIMER PARRAFO concentra que paso, quienes son los protagonistas y que significa politicamente. El SEGUNDO PARRAFO da contexto. Los siguientes desarrollan el analisis. Escribi 6-7 parrafos analiticos densos, separados por \\n. Cada parrafo debe tener minimo 3 oraciones. Desarrolla: contexto politico, actores y sus posiciones, antecedentes historicos, tensiones en juego, implicancias para la coyuntura. El cuerpo es la parte mas importante: no lo recortes.
- No uses verbos de cita. Narra los hechos directamente.
- Tono: analitico, critico, con voz editorial propia.
- ORTOGRAFIA: Usa 'u' en lugar de 'o' ante palabras que empiecen con 'o' u 'ho'. Usa 'e' en lugar de 'y' ante palabras que empiecen con 'i' u 'hi'."""

    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = resp.content[0].text.strip()

    if "```" in raw:
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group())
            except json.JSONDecodeError:
                # Tercer intento: pedir a Claude que corrija el JSON malformado
                fix_resp = client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=3500,
                    messages=[{"role": "user", "content":
                        f"El siguiente texto deberia ser JSON valido con campos titulo, copete y cuerpo. "
                        f"Corregilo y devolvelo SOLO como JSON valido sin markdown. "
                        f"Escapa correctamente todas las comillas dobles dentro de los strings:\n\n{m.group()[:3000]}"
                    }]
                )
                fix_raw = fix_resp.content[0].text.strip()
                if "```" in fix_raw:
                    parts2 = fix_raw.split("```")
                    fix_raw = parts2[1] if len(parts2) > 1 else fix_raw
                    if fix_raw.startswith("json"):
                        fix_raw = fix_raw[4:].strip()
                data = json.loads(fix_raw)
        else:
            raise

    # Nunca cortar el titulo a la fuerza (eso dejaba titulos truncos como "... y evalua").
    # Si algo se pasa del limite se pide reescribirlo; el control final de calidad hace el resto.
    if len(data.get("titulo", "")) > MAX_TITULO or len(data.get("copete", "")) > MAX_COPETE:
        try:
            data = enforce_lengths(client, data)
        except Exception as e:
            print(f"   Aviso: no se pudo acortar con Claude: {e}")
    if len(data.get("copete", "")) > MAX_COPETE:
        cop = data["copete"][:MAX_COPETE]
        corte = max(cop.rfind(". "), cop.rfind("; "))
        data["copete"] = cop[:corte + 1] if corte > 40 else cop.rsplit(" ", 1)[0]

    # Reintentar si el cuerpo es demasiado corto
    if tipo != "cinta":
        for _intento_cuerpo in range(2):
            if len(data.get("cuerpo", "")) >= MIN_CUERPO:
                break
            print(f"   Cuerpo demasiado corto ({len(data.get('cuerpo',''))} chars < {MIN_CUERPO}), reintentando...")
            expand_resp = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=2500,
                messages=[{"role": "user", "content": (
                    f"El siguiente cuerpo de nota periodistica es muy corto ({len(data.get('cuerpo',''))} caracteres). "
                    f"Expandilo a entre {MIN_CUERPO} y {MAX_CUERPO} caracteres con mas contexto y analisis politico. "
                    f"Responde SOLO con el texto del cuerpo (sin JSON, sin comillas, sin titulo):\n\n{data.get('cuerpo','')}"
                )}]
            )
            cuerpo_expandido = expand_resp.content[0].text.strip()
            if len(cuerpo_expandido) >= MIN_CUERPO:
                data["cuerpo"] = cuerpo_expandido

    STOP_WORDS = {"de", "del", "en", "al", "a", "con", "por", "para", "sin",
                  "sobre", "bajo", "entre", "desde", "hasta", "ante", "tras",
                  "el", "la", "los", "las", "un", "una", "unos", "unas",
                  "y", "e", "o", "u", "que", "ni", "pero", "sino", "como",
                  "si", "su", "sus", "mi", "tu", "lo", "se", "es", "fue"}
    for _intento in range(3):
        tit = data.get("titulo", "").strip().rstrip(".,;:")
        ultima = tit.split()[-1].lower().rstrip(".,;:") if tit else ""
        if ultima not in STOP_WORDS:
            break
        fix_resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": (
                f"El titulo '{tit}' esta cortado (termina en '{ultima}'). "
                f"Reescribilo como frase COMPLETA de max {MAX_TITULO} caracteres, "
                f"estilo telegrafico argentino. Solo el titulo, sin comillas."
            )}]
        )
        data["titulo"] = fix_resp.content[0].text.strip().strip('"')

    data["titulo"] = corregir_conjunciones(data["titulo"])
    data["copete"] = corregir_conjunciones(data["copete"])
    data["cuerpo"] = corregir_conjunciones(data["cuerpo"])
    return data


_PALABRAS_FINALES_INVALIDAS = {"de", "del", "en", "al", "a", "con", "por", "para", "sin",
    "sobre", "bajo", "entre", "desde", "hasta", "ante", "tras", "el", "la", "los", "las",
    "un", "una", "unos", "unas", "y", "e", "o", "u", "que", "ni", "pero", "sino", "como",
    "si", "su", "sus", "lo", "se", "es", "fue", "evalua", "evalúa", "busca", "analiza",
    "prepara", "define", "decide", "planea", "estudia", "pide", "quiere", "intenta"}


def _titulo_pasa_reglas_basicas(t: str) -> str:
    """Devuelve '' si el titulo pasa los controles automaticos; si no, el motivo."""
    t = (t or "").strip()
    if not t:
        return "vacio"
    if len(t) > MAX_TITULO:
        return f"largo ({len(t)} > {MAX_TITULO})"
    if len(t.split()) < 4:
        return "muy corto"
    if t.endswith(("...", "…", ",", ":", ";", "-")):
        return "termina cortado"
    ultima = t.split()[-1].lower().strip(".,;:!?\"'")
    if ultima in _PALABRAS_FINALES_INVALIDAS:
        return f"termina en '{ultima}' (queda incompleto)"
    if re.search(r"\bel \d{1,2}(?=$|[ ,.;:])(?! de\b)(?! por\b)", t):
        return "fecha suelta"
    return ""


def control_calidad_titulo(client, titulo: str, copete: str) -> str | None:
    """Ultimo filtro antes de publicar. Devuelve un titulo correcto, o None si no se logra
    (en ese caso la nota NO se publica: es preferible una nota menos que un titulo malo)."""
    t = (titulo or "").strip().strip('"')
    for ronda in range(1, 4):
        motivo = _titulo_pasa_reglas_basicas(t)
        try:
            resp = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=300,
                messages=[{"role": "user", "content": (
                    "Sos el corrector final de titulos de DATAPOLITICA (sitio de politica argentina). "
                    "Revisa este titulo con criterio estricto. Es CORRECTO solo si cumple TODO:\n"
                    "1) Es una frase completa y cerrada: no queda nada en suspenso ni cortado "
                    "(MAL: 'El Gobierno negocia sin los votos para eliminar las PASO y evalua').\n"
                    "2) Un lector comun lo entiende con UNA sola lectura, sin explicacion: dice quien hizo que, "
                    "sin jerga, metaforas, abreviaturas ni fechas sueltas como 'el 17'.\n"
                    "3) Ortografia perfecta en español, con todas las tildes (MAL: 'reunio'; BIEN: 'reunió').\n"
                    f"4) Tiene como maximo {MAX_TITULO} caracteres (apunta a {MAX_TITULO - 8} o menos; conta bien los caracteres).\n"
                    "5) No inventa datos: todo lo que dice esta en el titulo o en el copete.\n"
                    + (f"Control automatico detecto: {motivo}.\n" if motivo else "")
                    + "Si NO cumple, escribi un titulo corregido que SI cumpla todo, usando solo la informacion dada.\n"
                    "Responde SOLO con JSON valido, sin markdown: "
                    '{"ok": true|false, "problema": "...", "titulo": "titulo final"}\n\n'
                    f"Titulo: {t}\nCopete: {copete}"
                )}]
            )
            raw = resp.content[0].text.strip()
            m = re.search(r"\{.*\}", raw, re.S)
            res = json.loads(m.group() if m else raw)
        except Exception as e:
            print(f"    Aviso: control de titulo no disponible ({e}).")
            return t if not motivo else None
        nuevo = corregir_conjunciones(str(res.get("titulo", "")).strip().strip('"'))
        if res.get("ok") and not motivo:
            return t
        if not nuevo:
            return None
        print(f"    Control de titulo (ronda {ronda}): {res.get('problema', motivo)} -> {nuevo}")
        t = nuevo
        # el titulo corregido se vuelve a revisar en la ronda siguiente
    return t if not _titulo_pasa_reglas_basicas(t) else None


def rewrite_con_reintento(client, art: dict, tipo: str) -> dict:
    """Si la respuesta de Claude llega incompleta o mal armada, se pide de nuevo (hasta 2 veces mas)."""
    ultimo = None
    for intento in range(3):
        try:
            return rewrite(client, art, tipo)
        except Exception as e:
            ultimo = e
            print(f"    Respuesta de Claude invalida (intento {intento + 1}/3): {str(e)[:80]}")
    raise ultimo


def enforce_lengths(client: anthropic.Anthropic, data: dict) -> dict:
    """Acorta titulo y copete reescribiendolos (nunca cortando). El cuerpo no se toca."""
    prompt = f"""Reescribi estos textos para que entren en el limite, como frases COMPLETAS y claras, sin perder el dato principal.
Responde SOLO con JSON valido, sin markdown: {{"titulo": "...", "copete": "..."}}

titulo actual ({len(data['titulo'])} caracteres; limite {MAX_TITULO - 5}): {data['titulo']}
copete actual ({len(data['copete'])} caracteres; limite {MAX_COPETE}): {data['copete']}"""
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = resp.content[0].text.strip()
    m = re.search(r"\{.*\}", raw, re.S)
    nuevo = json.loads(m.group() if m else raw)
    out = dict(data)
    if nuevo.get("titulo"):
        out["titulo"] = str(nuevo["titulo"]).strip().strip('"')
    if nuevo.get("copete"):
        out["copete"] = str(nuevo["copete"]).strip()
    return out


# ── Publicar ───────────────────────────────────────────────────────────────────
def publicar(session: requests.Session, orden: int,
             titulo: str, copete: str, cuerpo: str,
             img_data: bytes | None = None) -> tuple[bool, int | None]:
    if DRY_RUN:
        print(f"    [PRUEBA] No se publica. Orden {orden} | Titulo: {titulo} | Foto: {'si' if img_data else 'NO'}")
        return True, None
    state = get_viewstate(session)
    if not state:
        print("    ERROR: No se pudo obtener ViewState")
        return False, None

    hoy = datetime.now()
    cuerpo_plain = cuerpo.strip()

    payload = {
        **state,
        "radio":               str(orden),
        "drpDay":              str(hoy.day).zfill(2),
        "drpMonth":            str(hoy.month).zfill(2),
        "drpYear":             str(hoy.year),
        "txtTitle":            titulo,
        "txtSubTitle":         copete,
        "txtDescriptionLong":  cuerpo_plain,
        "imgPublicar.x":       "1",
        "imgPublicar.y":       "1",
    }

    if img_data:
        ext = _image_extension(img_data)
        mime = "image/jpeg" if ext == "jpg" else f"image/{ext}"
        print(f"    Subiendo imagen: {len(img_data)//1024}KB, ext={ext}, mime={mime}")
        files = {"flUpload": (f"imagen.{ext}", img_data, mime)}
        resp = session.post(PANEL_NOTA_URL, data=payload, files=files,
                            headers={"Referer": PANEL_NOTA_URL},
                            timeout=60, allow_redirects=True)
    else:
        print(f"    Sin imagen para subir")
        resp = session.post(PANEL_NOTA_URL, data=payload,
                            timeout=30, allow_redirects=True)

    print(f"    POST nota -> Status: {resp.status_code} | URL final: {resp.url}")

    ok = "panel_listado" in resp.url.lower()
    if not ok:
        print(f"    Respuesta inesperada. HTML preview: {resp.text[:400]}")
        return False, None

    ids = re.findall(r'panel_agregar_nota\.aspx\?id=(\d+)', resp.text)
    note_id = int(ids[0]) if ids else None
    if note_id:
        print(f"    ID nota publicada: {note_id}")
        # Verificar si la imagen fue aceptada
        if img_data:
            try:
                r2 = session.get(f"{PANEL_BASE}/panel_agregar_nota.aspx?id={note_id}", timeout=20)
                from bs4 import BeautifulSoup as _BS
                s2 = _BS(r2.text, "html.parser")
                img_el = s2.find("img", {"id": "imgFoto"}) or s2.find("img", src=lambda x: x and "upload" in x)
                if img_el:
                    print(f"    [CHECK] Imagen confirmada en panel: {img_el.get('src','')[:80]}")
                else:
                    print(f"    [CHECK] ATENCION: No se encontro imagen en el panel para esta nota")
            except Exception as _e:
                print(f"    [CHECK] No se pudo verificar imagen: {_e}")

    return True, note_id


# ── Main ───────────────────────────────────────────────────────────────────────


def diagnostico_panel(session) -> list:
    """SOLO LECTURA (modo prueba): mira el listado del panel para entender por que no se sacan
    de la portada las notas viejas de Orden 1. No borra ni cambia nada."""
    lineas = []
    try:
        resp = session.get(PANEL_LIST_URL, timeout=30)
        soup = BeautifulSoup(resp.text, "html.parser")
        links = [a for a in soup.find_all("a", href=True) if re.search(r"panel_agregar_nota\.aspx\?id=\d+", a["href"])]
        lineas.append(f"Listado: HTTP {resp.status_code}, {len(links)} enlaces a notas, url final {resp.url.split('?')[0]}")
        filas = 0
        for a in links[:10]:
            row = a.find_parent("tr")
            celdas = [c.get_text(" ", strip=True)[:40] for c in row.find_all("td")] if row else []
            nid = re.search(r"id=(\d+)", a["href"]).group(1)
            lineas.append(f"id {nid}: " + " | ".join(celdas)[:220])
            filas += 1
        # otros enlaces de accion (borrar, activar, etc.) que tenga el listado
        acciones = sorted({re.sub(r"\d+", "N", x["href"])[:80] for x in soup.find_all("a", href=True)
                           if "id=" in x["href"] or "ids=" in x["href"] or "javascript" in x["href"].lower()})[:8]
        if acciones:
            lineas.append("Acciones en el listado: " + " ; ".join(acciones))
        onclicks = sorted({(x.get("onclick") or "")[:80] for x in soup.find_all(onclick=True)})[:6]
        if onclicks:
            lineas.append("onclick: " + " ; ".join(onclicks))
    except Exception as e:
        lineas.append(f"Diagnostico no disponible: {e}")
    for l in lineas:
        print("   [diag] " + l)
    return lineas


def evaluar_corrida() -> str:
    """OK = 6 notas + cinta sin errores. SIN NOVEDADES = salieron menos porque no habia noticias nuevas.
    PROBLEMA = hubo errores, o se descartaron notas por calidad y no se pudo completar."""
    graves = [e for e in RESUMEN["errores"] if not e.startswith("Error armando")]
    if graves or (RESUMEN["errores"] and RESUMEN["publicadas"] < 3):
        return "PROBLEMA"
    if RESUMEN["publicadas"] >= N_ORDEN1 and RESUMEN["cinta"]:
        return "OK"
    malos = [d for d in RESUMEN["descartes"] if d.startswith("Titulo malo")]
    if len(malos) >= 2:
        return "PROBLEMA"
    return "SIN NOVEDADES"


def escribir_resumen(estado: str):
    """Deja el resumen en la pagina de la corrida (GitHub) y, si hay un problema, el archivo alerta.txt
    que el workflow convierte en un aviso por mail."""
    try:
        modo = "PRUEBA (no se publico nada)" if DRY_RUN else "REAL"
        lineas = [f"## Corrida datapolitica — {estado} — modo {modo}", "",
                  f"- Notas de Orden 1 publicadas: **{RESUMEN['publicadas']} de {N_ORDEN1}**",
                  f"- Cinta Testigo: **{'sí' if RESUMEN['cinta'] else 'no'}**"]
        if RESUMEN["borrado"]:
            lineas.append(f"- Borrado previo: {RESUMEN['borrado']}")
        if RESUMEN["notas"]:
            lineas += ["", "| Tipo | Título | Foto | Persona del título |", "|---|---|---|---|"]
            for n in RESUMEN["notas"]:
                lineas.append(f"| {n['tipo']} | {n['titulo']} | {'sí' if n['foto'] else 'NO'} | {n['persona'] or '-'} |")
        if RESUMEN["descartes"]:
            lineas += ["", "**Descartadas:**"] + [f"- {d}" for d in RESUMEN["descartes"]]
        if RESUMEN["errores"]:
            lineas += ["", "**Errores:**"] + [f"- {e}" for e in RESUMEN["errores"]]
        if RESUMEN.get("fuentes"):
            lineas += ["", "**Fuentes (noticias leidas):**"] + [f"- {x}" for x in RESUMEN["fuentes"]]
        if RESUMEN.get("diag"):
            lineas += ["", "**Diagnostico del panel (solo lectura):**"] + [f"- {d}" for d in RESUMEN["diag"]]
        texto = "\n".join(lineas) + "\n"
        print("\n" + texto)
        ruta = os.environ.get("GITHUB_STEP_SUMMARY")
        if ruta:
            with open(ruta, "a", encoding="utf-8") as f:
                f.write(texto)
        if estado == "PROBLEMA" and not DRY_RUN:
            with open("alerta.txt", "w", encoding="utf-8") as f:
                f.write(texto)
    except Exception as e:
        print(f"Aviso: no se pudo escribir el resumen: {e}")



def main():
    print("=" * 55)
    print("  Automatizador datapolitica.com")
    print(f"  {datetime.now().strftime('%d/%m/%Y %H:%M')} (hora UTC)")
    print("=" * 55)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # 0. Estado anterior
    state = load_state()
    published_links = set(state.get("published_links", []))
    published_titles = list(state.get("published_titles", []))
    print(f"Titulos ya publicados en historial: {len(published_titles)}")
    if published_links:
        print(f"URLs ya publicadas en historial: {len(published_links)}")

    # 1. Obtener articulos (pool amplio)
    print("\n>> Obteniendo articulos de RSS...")
    articulos = fetch_articles(n=160)

    if len(articulos) < N_ORDEN1 + 1:
        print(f"ERROR: No se obtuvieron suficientes articulos (necesito al menos {N_ORDEN1+1}). Abortando.")
        RESUMEN["errores"].append("No llegaron suficientes noticias de los diarios (fuentes RSS)")
        escribir_resumen("PROBLEMA")
        raise SystemExit(1)

    # 2. Pre-filtrar: descartar keywords prohibidos y sin politica argentina
    print("\n>> Pre-filtrando articulos...")
    articulos_filtrados = []
    for art in articulos:
        if should_discard(art):
            continue
        if not is_argentina_politics_title(art):
            # Permitir si aparece en >=4 de 5 fuentes (noticia extranjera muy relevante)
            if art.get("_n_fuentes", 0) >= 4:
                print(f"   [EXTRANJERO RELEVANTE {art['_n_fuentes']}/5] {art['title'][:60]}")
            else:
                continue
        articulos_filtrados.append(art)
    print(f"   {len(articulos_filtrados)} articulos pasaron el filtro (de {len(articulos)})")

    # Filtrar ya publicados
    if published_links:
        antes = len(articulos_filtrados)
        sin_repetidos = [a for a in articulos_filtrados if a.get("link") not in published_links]
        print(f"   {antes - len(sin_repetidos)} descartados por ya publicados")
        # Fallback: si quedan muy pocos, ignorar historial para garantizar N_ORDEN1 articulos
        if len(sin_repetidos) >= N_ORDEN1:
            articulos_filtrados = sin_repetidos
        else:
            print(f"   Fallback: insuficientes articulos nuevos ({len(sin_repetidos)}), usando pool completo")

    if len(articulos_filtrados) < N_ORDEN1 + 1:
        print(f"   Advertencia: solo {len(articulos_filtrados)} articulos disponibles. Intentando igual.")

    # 3. Scrapear homepage ANTES de borrar (para no repetir lo que estaba)
    print("\n>> Guardando articulos actuales de la homepage...")
    homepage_arts = get_current_homepage_articles()

    # Excluir articulos similares a lo que YA estaba en la homepage
    if homepage_arts:
        antes = len(articulos_filtrados)
        articulos_filtrados = [
            art for art in articulos_filtrados
            if not any(articles_are_similar(art, h) for h in homepage_arts)
        ]
        print(f"   {antes - len(articulos_filtrados)} descartados por similitud con homepage actual")

    # ── Deduplicación por figura política ─────────────────────────────
    # Evitar que 2 artículos del mismo batch mencionen al mismo personaje
    _FIGURAS_DEDUP = [
        "milei", "kicillof", "cristina", "kirchner", "massa", "macri", "bullrich",
        "larreta", "berni", "scioli", "moyano", "baradel", "grabois",
        "randazzo", "manes", "morales", "schiaretti", "llaryora", "francos",
        "caputo", "sturzenegger", "adorni", "lijo", "villarruel", "quirno",
        "pullaro", "passalacqua", "cornejo", "figueroa",
    ]

    def _figura_en_titulo(titulo: str):
        t = titulo.lower()
        for f in _FIGURAS_DEDUP:
            if f in t:
                return f
        return None

    # v21: ya NO se descartan; las noticias de una figura que ya aparece pasan al final
    # (el control de "mismo tema" con IA evita las notas realmente repetidas)
    figuras_vistas: set = set()
    arts_primero, arts_despues = [], []
    for art in articulos_filtrados:
        fig = _figura_en_titulo(art.get("title", ""))
        if fig is None or fig not in figuras_vistas:
            arts_primero.append(art)
            if fig:
                figuras_vistas.add(fig)
        else:
            arts_despues.append(art)
    print(f"   [dedup-figura] {len(arts_despues)} noticias de figuras repetidas pasan al final (no se descartan)")
    articulos_filtrados = arts_primero + arts_despues

    # ── Memoria de temas ya publicados (evita notas repetidas entre dias) ──
    if published_titles:
        print("\n>> Revisando temas ya publicados...")
        sin_repetir, relleno, reserva = [], [], []
        recientes = published_titles[-60:]   # aprox. las ultimas 12-24 horas
        for art in articulos_filtrados:
            repetido, por_texto = es_tema_repetido(art.get('title', ''), published_titles)
            if not repetido:
                sin_repetir.append(art)
            elif por_texto:
                print(f"   [repetido-texto] Descartado: {art.get('title','')[:60]}")
            elif not es_tema_repetido(art.get('title', ''), recientes)[0]:
                # tema que tratamos hace mas de un dia: puede volver como NOVEDAD si hace falta
                print(f"   [tema-viejo] En reserva (novedad de un tema de dias atras): {art.get('title','')[:60]}")
                reserva.append(art)
            else:
                print(f"   [repetido-tema] Descartado: {art.get('title','')[:60]}")
                relleno.append(art)
        objetivo = N_ORDEN1 + 1
        if len(sin_repetir) < objetivo and relleno:
            faltan = objetivo - len(sin_repetir)
            print(f"   AVISO: pocos temas nuevos ({len(sin_repetir)}). Se reponen {min(faltan, len(relleno))} del mismo tema para no dejar la portada vacia.")
            sin_repetir += relleno[:faltan]
        # La reserva va al final: solo se usa si con las noticias nuevas no se llega a 6
        sin_repetir += reserva
        print(f"   {len(articulos_filtrados) - len(sin_repetir)} descartados por tema ya publicado ({len(reserva)} en reserva)")
        articulos_filtrados = sin_repetir

    # 3b. Parche CSS tipografia (elimina Patua One de .patua_tit_ppal)
    # patch_css_tipografia()  # desactivado: no tocar CSS tipografia

    # 4. Login
    print("\n>> Iniciando sesion en el panel...")
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; automatizador/1.0)"})

    if not login(session):
        print("ERROR: Fallo el login. Abortando.")
        RESUMEN["errores"].append("No se pudo entrar al panel del sitio (login)")
        escribir_resumen("PROBLEMA")
        raise SystemExit(1)

    # 5. Borrar TODOS los articulos de Orden 1 del panel (no depende del state)
    print("\n>> Borrando TODOS los articulos Orden 1 del panel...")
    if DRY_RUN:
        print("   [PRUEBA] No se borra nada.")
        RESUMEN["diag"] = diagnostico_panel(session)
    else:
        _ids_previos = list(state.get("orden1_ids", []))
        _b = delete_all_orden1(session)
        RESUMEN["borrado"] = f"{_b} notas de Orden 1 borradas"
        # Control: una nota borrada, ¿sigue existiendo en el sitio? (importante para Google)
        if _ids_previos:
            try:
                _r = requests.get(f"https://datapolitica.com/{_ids_previos[0]}~x", headers=HEADERS, timeout=20)
                _sigue = _r.status_code == 200 and "Datapol" in _r.text and len(_r.text) > 5000
                print(f"   Control: la nota borrada {_ids_previos[0]} {'SIGUE visible' if _sigue else 'YA NO existe'} en el sitio (HTTP {_r.status_code})")
                RESUMEN["borrado"] += f"; nota borrada {_ids_previos[0]}: {'sigue visible' if _sigue else 'ya no existe en el sitio'}"
            except Exception as _e:
                print(f"   Control de borrado no disponible: {_e}")

    # Borrar cinta anterior del estado
    prev_cinta_id = None if DRY_RUN else state.get("cinta_id")
    if prev_cinta_id:
        print(f"\n>> Borrando cinta anterior (ID {prev_cinta_id})...")
        delete_note(session, prev_cinta_id)

    # 6. Seleccionar y publicar N_ORDEN1 articulos en Orden 1
    print(f"\n>> Publicando {N_ORDEN1} articulos en Orden {ORDEN_NOTA}...")
    orden1_ids = []
    selected_arts = []
    used_indices = set()
    # Cargar hashes de imagenes usadas en corridas anteriores (evita repetir la misma foto)
    used_img_hashes = set(state.get("used_img_hashes", []))
    print(f"   Hashes de imagenes anteriores cargados: {len(used_img_hashes)}")
    nuevos_links = []
    nuevos_img_hashes = []
    nuevos_titulos = []

    publicadas = 0
    intentos = 0
    # Si una nota se descarta (titulo malo, repetida, etc.) se prueba con la siguiente noticia
    while publicadas < N_ORDEN1 and intentos < N_ORDEN1 + 14:
        intentos += 1
        n = publicadas
        print(f"\n  [Orden {ORDEN_NOTA} - Nota {n+1}/{N_ORDEN1}] (intento {intentos})")
        art, img, idx = find_article_with_photo(
            articulos_filtrados,
            exclude_similar_to=selected_arts,
            used_indices=used_indices,
            require_argentina_politics=False,  # ya filtrado arriba
            used_img_hashes=used_img_hashes,
        )
        if art is None:
            print(f"   No hay mas noticias disponibles para Orden 1.")
            break

        used_indices.add(idx)
        selected_arts.append(art)
        import hashlib as _hl
        nuevos_img_hashes.append(_hl.md5(img).hexdigest())

        try:
            contenido = rewrite_con_reintento(client, art, "nota")
            t = contenido["titulo"]
            c = contenido["copete"]
            b = contenido["cuerpo"]
            print(f"    Titulo  ({len(t):3d} chars): {t}")
            print(f"    Copete  ({len(c):3d} chars): {c[:80]}{'...' if len(c)>80 else ''}")
            # 1) Primero el titulo queda en su version FINAL (control de calidad)
            t_ok = control_calidad_titulo(client, t, c)
            if not t_ok:
                print("    Titulo sin la calidad necesaria tras 3 revisiones. NO se publica.")
                RESUMEN["descartes"].append(f"Titulo malo: {t[:60]}")
                continue
            if t_ok != t:
                print(f"    Titulo final: {t_ok}")
            t = t_ok
            # 2) Despues, con el titulo final: si nombra a una persona, la foto tiene que ser de ella
            nombre_t = persona_del_titulo(client, t, c, art)
            if nombre_t:
                img_p = foto_de_persona(art, nombre_t)
                if img_p:
                    img = img_p
                    nuevos_img_hashes[-1] = _hl.md5(img).hexdigest()
                else:
                    # Sin foto verificada NO se nombra a la persona (evita un nombre con la cara de otro)
                    t_alt = titulo_sin_nombre(client, t, c)
                    t_alt = control_calidad_titulo(client, t_alt, c) if t_alt else None
                    if t_alt and not persona_del_titulo(client, t_alt, c, art):
                        print(f"    Sin foto verificada de {nombre_t}. Titulo sin nombre: {t_alt}")
                        t = t_alt
                        nombre_t = ""
                    else:
                        print(f"    Sin foto verificada de {nombre_t} y no se logro un titulo sin nombre. NO se publica.")
                        RESUMEN["descartes"].append(f"Sin foto de {nombre_t}: {t[:50]}")
                        continue
            if titulo_final_repetido(t, published_titles + nuevos_titulos):
                print("    Titulo final casi igual a una nota ya publicada. NO se publica.")
                RESUMEN["descartes"].append(f"Repetida: {t[:60]}")
                continue
            # Tampoco dos notas del MISMO tema en la misma actualizacion (ej.: dos sobre la causa Hotesur)
            _fig_t = _figura_en_titulo(t)
            if _fig_t and sum(1 for _x in nuevos_titulos[::2] if _figura_en_titulo(_x) == _fig_t) >= 2:
                print(f"    Ya hay 2 notas de '{_fig_t}' en esta actualizacion. NO se publica.")
                RESUMEN["descartes"].append(f"Tercera nota de {_fig_t}: {t[:50]}")
                continue
            if nuevos_titulos and es_tema_repetido(t, nuevos_titulos)[0]:
                print("    Mismo tema que otra nota de esta actualizacion. NO se publica.")
                RESUMEN["descartes"].append(f"Mismo tema que otra nota: {t[:55]}")
                continue
            ok, note_id = publicar(session, ORDEN_NOTA, t, c, b, img_data=img)
            print(f"    {'OK Publicado' if ok else 'ERROR al publicar'}")
            if ok:
                publicadas += 1
                RESUMEN["publicadas"] += 1
                RESUMEN["notas"].append({"tipo": "Orden 1", "titulo": t, "foto": bool(img),
                                         "persona": nombre_t or ""})
                nuevos_titulos += [t, art.get('title', '')]
            else:
                RESUMEN["errores"].append(f"El panel no acepto la nota: {t[:60]}")
            if ok and note_id:
                orden1_ids.append(note_id)
            if art.get("link"):
                nuevos_links.append(art["link"])
        except Exception as e:
            print(f"    Excepcion: {e}")
            RESUMEN["errores"].append(f"Error armando una nota: {str(e)[:120]}")
            import traceback
            traceback.print_exc()

    # 7. Seleccionar y publicar Cinta Testigo
    print(f"\n  [Cinta Testigo]")
    # La cinta NO puede repetir un tema ya publicado ni uno que este en la portada
    _previos_cinta = (list(published_titles) + list(nuevos_titulos) +
                      [h.get("title", "") for h in (homepage_arts or []) if isinstance(h, dict)])
    _pools = [(articulos_filtrados, used_indices, True),
              ([a for a in articulos if not should_discard(a)], set(), False)]

    def _siguiente_candidato_cinta():
        for _pool, _usados, _es_principal in _pools:
            for _intento in range(8):
                cand, img_c, idx_c = find_article_with_photo(
                    _pool,
                    exclude_similar_to=selected_arts,
                    used_indices=_usados,
                    require_argentina_politics=False,
                    used_img_hashes=used_img_hashes,
                )
                if cand is None:
                    break
                _usados.add(idx_c)
                rep, _ = es_tema_repetido(cand.get("title", ""), _previos_cinta)
                if rep:
                    print(f"   [cinta] Tema ya publicado o en portada, descartado: {cand.get('title','')[:60]}")
                    continue
                return cand, img_c
        return None, None

    cinta_id = None
    motivos_cinta = []
    for _cinta_intento in range(5):
        art_cinta, img_cinta = _siguiente_candidato_cinta()
        if art_cinta is None:
            break
        try:
            ct = rewrite_con_reintento(client, art_cinta, "cinta")
            t2 = ct["titulo"]
            c2 = ct["copete"]
            b2 = ct["cuerpo"]
            print(f"    Titulo  ({len(t2):3d} chars): {t2}")
            t2_ok = control_calidad_titulo(client, t2, c2)
            if not t2_ok:
                raise RuntimeError("Titulo de cinta sin la calidad necesaria. NO se publica.")
            if t2_ok != t2:
                print(f"    Titulo final: {t2_ok}")
            t2 = t2_ok
            # Si la cinta nombra a una persona, la foto tiene que ser de ella
            nombre_c = persona_del_titulo(client, t2, c2, art_cinta)
            if nombre_c:
                img_pc = foto_de_persona(art_cinta, nombre_c)
                if img_pc:
                    img_cinta = img_pc
                else:
                    t2_alt = titulo_sin_nombre(client, t2, c2)
                    t2_alt = control_calidad_titulo(client, t2_alt, c2) if t2_alt else None
                    if t2_alt and not persona_del_titulo(client, t2_alt, c2, art_cinta):
                        print(f"    Sin foto verificada de {nombre_c}. Titulo sin nombre: {t2_alt}")
                        t2 = t2_alt
                    else:
                        raise RuntimeError(f"Cinta sin foto verificada de {nombre_c}. NO se publica.")
            if titulo_final_repetido(t2, _previos_cinta) or es_tema_repetido(t2, _previos_cinta)[0]:
                raise RuntimeError("La cinta repite un tema ya publicado. NO se publica.")
            ok2, cinta_id = publicar(session, ORDEN_CINTA, t2, c2, b2, img_data=img_cinta)
            print(f"    {'OK Publicado en cinta testigo' if ok2 else 'ERROR al publicar'}")
            if art_cinta.get("link"):
                nuevos_links.append(art_cinta["link"])
            if ok2:
                RESUMEN["cinta"] = True
                RESUMEN["notas"].append({"tipo": "Cinta", "titulo": t2, "foto": bool(img_cinta), "persona": ""})
                nuevos_titulos += [t2, art_cinta.get('title', '')]
                break
            else:
                RESUMEN["errores"].append(f"El panel no acepto la cinta: {t2[:60]}")
                break
        except Exception as e:
            print(f"    Excepcion: {e}")
            # v21: cualquier falla con una noticia (titulo, foto, reescritura) -> se prueba otra
            motivos_cinta.append(str(e)[:100])
            if _cinta_intento < 4:
                print("    Pruebo con otra noticia para la cinta...")
                continue
            RESUMEN["errores"].append(f"Error armando la cinta: {str(e)[:120]}")
            import traceback
            traceback.print_exc()
            break
    if not RESUMEN["cinta"]:
        if motivos_cinta:
            RESUMEN["descartes"].append("Cinta: " + " / ".join(m.replace(" NO se publica.", "") for m in motivos_cinta[:3]))
        else:
            print("   No se encontro articulo para Cinta Testigo")
            RESUMEN["descartes"].append("Cinta: no hubo noticia nueva para la cinta")

    # 8. Guardar estado
    # Conservar claves que no maneja este script (por ejemplo las del posteo en X)
    _mias = ("orden1_ids", "cinta_id", "published_links", "used_img_hashes", "published_titles")
    nuevo_estado = {k: v for k, v in state.items() if k not in _mias}
    nuevo_estado["orden1_ids"] = orden1_ids
    if cinta_id:
        nuevo_estado["cinta_id"] = cinta_id

    # Solo recordar los articulos de la corrida ACTUAL para evitar
    # acumular historial que agota el RSS en runs seguidos
    all_links = list(published_links) + nuevos_links
    nuevo_estado["published_links"] = all_links[-12:]

    # Persistir hashes de imagenes usadas (ultimas 40) para no repetir fotos entre corridas
    all_hashes = list(used_img_hashes) + nuevos_img_hashes
    nuevo_estado["used_img_hashes"] = all_hashes[-40:]
    print(f"   Hashes de imagenes guardados: {len(nuevo_estado['used_img_hashes'])}")

    # Historial de titulos publicados (ultimos 200) para no repetir temas entre dias
    nuevo_estado["published_titles"] = (published_titles + [t for t in nuevos_titulos if t])[-200:]
    print(f"   Titulos guardados en historial: {len(nuevo_estado['published_titles'])}")

    # Registro de corridas (para el resumen diario): ultimas 72
    estado_corrida = evaluar_corrida()
    log = list(state.get("run_log", []))
    log.append({"fecha": datetime.utcnow().strftime("%Y-%m-%d %H:%M"), "publicadas": RESUMEN["publicadas"],
                "cinta": RESUMEN["cinta"], "estado": estado_corrida,
                "detalle": (RESUMEN["errores"] + RESUMEN["descartes"])[:6],
                "titulos": [n["titulo"] for n in RESUMEN["notas"]]})
    nuevo_estado["run_log"] = log[-72:]

    if DRY_RUN:
        print("\n>> [PRUEBA] No se guarda el estado.")
    else:
        print("\n>> Guardando estado...")
        save_state(nuevo_estado)
    escribir_resumen(estado_corrida)

    print(f"\nAutomatizacion completada: {len(orden1_ids)} notas en Orden 1 + {'1 cinta' if cinta_id else '0 cintas'}")


if __name__ == "__main__":
    main()
