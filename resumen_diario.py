"""Arma el resumen de las ultimas 24 horas de datapolitica.com a partir de state.json (run_log)."""
import json
from datetime import datetime, timedelta

try:
    state = json.load(open("state.json", encoding="utf-8"))
except Exception:
    state = {}
log = state.get("run_log", [])
desde = datetime.utcnow() - timedelta(hours=24)
corridas = []
for r in log:
    try:
        if datetime.strptime(r["fecha"], "%Y-%m-%d %H:%M") >= desde:
            corridas.append(r)
    except Exception:
        pass


def hora_ba(f):
    return (datetime.strptime(f, "%Y-%m-%d %H:%M") - timedelta(hours=3)).strftime("%d/%m %H:%M")


ok = sum(1 for r in corridas if r.get("estado") == "OK")
sin = sum(1 for r in corridas if r.get("estado") == "SIN NOVEDADES")
prob = [r for r in corridas if r.get("estado") == "PROBLEMA"]
notas = sum(int(r.get("publicadas", 0)) for r in corridas)
cintas = sum(1 for r in corridas if r.get("cinta"))

print(f"## datapolitica.com — últimas 24 horas\n")
if not corridas:
    print("**Atención: no hubo ninguna actualización en las últimas 24 horas.** "
          "Revisar https://github.com/jro-ar/automatizador-noticias/actions\n")
else:
    print("| | Cantidad |\n|---|---|")
    print(f"| Actualizaciones (lo esperado son 24) | {len(corridas)} |")
    print(f"| Completas (6 notas + cinta) | {ok} |")
    print(f"| Con menos notas porque no había noticias nuevas | {sin} |")
    print(f"| Con problemas | {len(prob)} |")
    print(f"| Notas publicadas | {notas} |")
    print(f"| Cintas Testigo publicadas | {cintas} |\n")
    if len(corridas) < 18:
        print(f"- Hubo menos actualizaciones de lo esperado ({len(corridas)} de 24): GitHub salteó corridas.\n")
    if prob:
        print("**Problemas:**")
        for r in prob[-5:]:
            print(f"- {hora_ba(r['fecha'])}: " + "; ".join(r.get("detalle", [])[:3]))
        print()
    else:
        print("Sin problemas en el día.\n")
    ultima = corridas[-1]
    print(f"Última actualización: {hora_ba(ultima['fecha'])} (hora Argentina). Títulos:")
    for t in ultima.get("titulos", []):
        print(f"- {t}")
print("\nSitio: https://datapolitica.com/")
