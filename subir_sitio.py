# -*- coding: utf-8 -*-
"""
Sube arreglos a datapolitica.com por FTP cifrado (cuenta github_deploy, solo /httpdocs).

Reglas de seguridad:
  - NUNCA borra nada del sitio.
  - Antes de reemplazar un archivo, guarda una copia del original en
    App_Data/respaldos_github/<fecha>/  (carpeta que IIS no muestra al publico).
  - Despues de subir, revisa que el sitio responda. Si no responde bien,
    vuelve a poner los originales automaticamente.

Modos (variable MODO):
  probar -> solo se conecta, mira la carpeta y prueba escribir un archivito de prueba. No toca el sitio.
  subir  -> sube los archivos de la carpeta "sitio/" del repo (la misma estructura que httpdocs).
"""
import ftplib
import io
import os
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta

HOST = os.environ.get("FTP_HOST", "ar109.xvserver.com")
USER = os.environ.get("FTP_USER", "github_deploy")
PASSWORD = os.environ.get("FTP_PASSWORD", "")
MODO = (os.environ.get("MODO") or "probar").strip().lower()
ARCHIVOS = os.environ.get("ARCHIVOS", "").split()  # rutas dentro de sitio/ (vacio = todos)
CARPETA_LOCAL = "sitio"
SITIO = "https://datapolitica.com/"
ARGENTINA = timezone(timedelta(hours=-3))


def log(msg):
    print(msg, flush=True)


def conectar():
    if not PASSWORD:
        log("ERROR: falta el secreto FTP_PASSWORD en GitHub.")
        sys.exit(1)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False          # el certificado del servidor compartido puede no coincidir con el nombre
    ctx.verify_mode = ssl.CERT_NONE
    # El sitio tiene su propia direccion (datapolitica.com = 190.105.238.109), distinta de la del
    # servidor general (ar109). Se prueba primero la del sitio. En servidores Windows (IIS) el usuario
    # a veces va con el dominio adelante ("datapolitica.com|github_deploy"), por eso hay 2 formatos.
    intentos = [("datapolitica.com", USER),
                ("datapolitica.com", f"datapolitica.com|{USER}"),
                (HOST, f"datapolitica.com|{USER}")]
    ftp = None
    ultimo_error = None
    for servidor, usuario in intentos:
        ftp = ftplib.FTP_TLS(context=ctx, timeout=60)
        try:
            ftp.connect(servidor, 21)
        except ftplib.all_errors as e:
            log(f"  no conecta con {servidor}: {e}")
            ftp = None
            continue
        try:
            ftp.login(usuario, PASSWORD)    # FTP_TLS cifra el login antes de mandar la clave
            log(f"Login OK: servidor {servidor}, usuario {usuario}")
            break
        except ftplib.error_perm as e:
            ultimo_error = e
            log(f"  no entra en {servidor} con el usuario '{usuario}': {e}")
            try:
                ftp.close()
            except Exception:
                pass
            ftp = None
    if ftp is None:
        log("ERROR: el servidor no acepta el usuario/clave. Revisar que la clave guardada en GitHub "
            "(FTP_PASSWORD) sea exactamente la de la cuenta github_deploy en Plesk.")
        raise SystemExit(f"Login rechazado: {ultimo_error}")
    ftp.prot_p()                        # cifra tambien los archivos
    ftp.set_pasv(True)
    ftp.voidcmd("TYPE I")               # modo binario: necesario para medir tamanos y comparar archivos
    log("Conexion cifrada lista.")
    return ftp


def existe(ftp, ruta):
    try:
        ftp.size(ruta)
        return True
    except ftplib.all_errors:
        pass
    # plan B por si el servidor no responde SIZE: buscar el nombre en su carpeta
    carpeta, _, nombre = ruta.rpartition("/")
    try:
        nombres = ftp.nlst(carpeta) if carpeta else ftp.nlst()
    except ftplib.all_errors:
        return False
    return any(n.replace("\\", "/").split("/")[-1].lower() == nombre.lower() for n in nombres)


def crear_carpetas(ftp, ruta_archivo):
    partes = ruta_archivo.split("/")[:-1]
    actual = ""
    for p in partes:
        actual = f"{actual}/{p}" if actual else p
        try:
            ftp.mkd(actual)
        except ftplib.all_errors:
            pass  # ya existe


def bajar(ftp, ruta):
    buf = io.BytesIO()
    ftp.retrbinary(f"RETR {ruta}", buf.write)
    return buf.getvalue()


def subir_bytes(ftp, ruta, datos):
    crear_carpetas(ftp, ruta)
    ftp.storbinary(f"STOR {ruta}", io.BytesIO(datos))
    tam = ftp.size(ruta)
    if tam is not None and tam != len(datos):
        raise RuntimeError(f"{ruta}: el tamano subido ({tam}) no coincide ({len(datos)})")


def sitio_ok():
    for intento in range(3):
        try:
            req = urllib.request.Request(SITIO, headers={"User-Agent": "Mozilla/5.0 (revision GitHub)"})
            with urllib.request.urlopen(req, timeout=30) as r:
                if r.status == 200:
                    return True
        except Exception as e:
            log(f"  revision del sitio, intento {intento + 1}: {e}")
        time.sleep(10)
    return False


def probar(ftp):
    nombres = ftp.nlst()
    log(f"Archivos y carpetas en httpdocs: {len(nombres)}")
    if not any(n.lower().endswith("index.aspx") for n in nombres):
        log("AVISO: no veo index.aspx; la carpeta podria no ser httpdocs.")
    else:
        log("OK: veo index.aspx (es la carpeta del sitio).")
    prueba = "_prueba_github.txt"
    ftp.storbinary(f"STOR {prueba}", io.BytesIO(b"prueba de escritura de GitHub - se puede borrar"))
    log("OK: puedo escribir archivos.")
    try:
        ftp.delete(prueba)
        log("OK: archivo de prueba borrado.")
    except ftplib.all_errors:
        log("Aviso: no pude borrar el archivo de prueba (no molesta a nadie).")
    log("PRUEBA OK: la conexion funciona. No se cambio nada del sitio.")


def lista_a_subir():
    if not os.path.isdir(CARPETA_LOCAL):
        return []
    if ARCHIVOS:
        rutas = [a[len(CARPETA_LOCAL) + 1:] if a.startswith(CARPETA_LOCAL + "/") else a for a in ARCHIVOS]
    else:
        rutas = []
        for raiz, _, archivos in os.walk(CARPETA_LOCAL):
            for a in archivos:
                rutas.append(os.path.relpath(os.path.join(raiz, a), CARPETA_LOCAL).replace("\\", "/"))
    salida = []
    for r in sorted(set(rutas)):
        nombre = r.split("/")[-1]
        if nombre.upper().startswith("LEEME") or nombre.startswith("."):
            continue
        if ".." in r.split("/"):
            continue
        if os.path.isfile(os.path.join(CARPETA_LOCAL, r)):
            salida.append(r)
    return salida


def subir(ftp):
    rutas = lista_a_subir()
    if not rutas:
        log("No hay archivos para subir en la carpeta sitio/. No se cambio nada.")
        return
    sello = datetime.now(ARGENTINA).strftime("%Y-%m-%d_%H%M")
    carpeta_resp = f"App_Data/respaldos_github/{sello}"
    respaldos = {}
    log(f"Archivos a subir: {len(rutas)}")
    # 1) respaldo de los originales
    for r in rutas:
        if existe(ftp, r):
            original = bajar(ftp, r)
            subir_bytes(ftp, f"{carpeta_resp}/{r}", original)
            respaldos[r] = original
            log(f"  respaldo: {r} -> {carpeta_resp}/{r}")
        else:
            respaldos[r] = None
            log(f"  {r} es nuevo (no habia original)")
    # 2) subida
    for r in rutas:
        with open(os.path.join(CARPETA_LOCAL, r), "rb") as f:
            subir_bytes(ftp, r, f.read())
        log(f"  subido: {r}")
    # 3) revision del sitio y vuelta atras si falla
    time.sleep(15)
    if sitio_ok():
        log(f"LISTO: {len(rutas)} archivo(s) subidos y el sitio responde bien.")
        log(f"Respaldos en httpdocs/{carpeta_resp}")
        return
    log("PROBLEMA: el sitio no responde bien despues de subir. Vuelvo a poner los originales...")
    for r, original in respaldos.items():
        if original is not None:
            subir_bytes(ftp, r, original)
            log(f"  restaurado: {r}")
        else:
            log(f"  {r} era nuevo; queda en el servidor pero no reemplazo nada")
    time.sleep(15)
    log("Sitio OK despues de restaurar." if sitio_ok() else "ATENCION: el sitio sigue sin responder bien.")
    sys.exit(1)


def main():
    log(f"Modo: {MODO}")
    if MODO == "nada":
        log("No hay archivos nuevos o cambiados en sitio/. No se cambio nada.")
        return
    ftp = conectar()
    try:
        if MODO == "subir":
            subir(ftp)
        else:
            probar(ftp)
    finally:
        try:
            ftp.quit()
        except ftplib.all_errors:
            pass


if __name__ == "__main__":
    main()
