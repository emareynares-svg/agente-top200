"""Proceso MENSUAL: arma el universo de las N empresas con mayor capitalización."""
from __future__ import annotations

from .util import ahora_iso, es_operable, guardar_json, leer_json, log


def ejecutar(prov, cfg: dict) -> dict:
    c = cfg["universo"]
    cantidad = int(c.get("cantidad", 200))
    excluir = set(c.get("excluir") or [])

    filas = [f for f in prov.universo(cantidad + len(excluir)) if f["ticker"] not in excluir]
    filas = filas[:cantidad]
    for t in c.get("incluir_extra") or []:
        if t not in {f["ticker"] for f in filas}:
            filas.append({"rank": None, "ticker": t, "nombre": t, "market_cap": None,
                          "pais": "", "extra": True})

    anterior = leer_json("universo.json")
    previos = {f["ticker"] for f in (anterior or {}).get("empresas", [])}
    actuales = {f["ticker"] for f in filas}

    for f in filas:
        f["operable"] = es_operable(f["ticker"])
        f["nueva"] = bool(previos) and f["ticker"] not in previos

    resultado = {
        "actualizado": ahora_iso(),
        "cantidad": len(filas),
        "operables": sum(f["operable"] for f in filas),
        "entraron": sorted(actuales - previos) if previos else [],
        "salieron": sorted(previos - actuales) if previos else [],
        "empresas": filas,
    }
    log.info("Universo: %d empresas (%d operables desde EE.UU.). Entraron %d, salieron %d.",
             len(filas), resultado["operables"], len(resultado["entraron"]), len(resultado["salieron"]))
    guardar_json("universo.json", resultado)
    return resultado
