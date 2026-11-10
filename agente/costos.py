"""Registro persistente del gasto de IA de cada ejecución (docs/data/costos.json)."""
from __future__ import annotations

import os

from .util import ahora_iso, guardar_json, leer_json, log


def registrar(cfg: dict, modo: str, uso: dict, inicio: str, duracion_s: float, ok: bool) -> None:
    datos = leer_json("costos.json") or {"registros": []}
    run_url = None
    if os.environ.get("GITHUB_RUN_ID"):
        run_url = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
                   f"{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{os.environ['GITHUB_RUN_ID']}")
    evento = os.environ.get("GITHUB_EVENT_NAME")
    datos["registros"].append({
        "fecha": inicio,
        "modo": modo,
        "origen": {"schedule": "automática", "workflow_dispatch": "manual"}.get(evento, evento or "local"),
        "resultado": "ok" if ok else "con error",
        "duracion_min": round(duracion_s / 60, 1),
        "llamadas": uso["llamadas"],
        "tokens_in": uso["tokens_in"],
        "tokens_out": uso["tokens_out"],
        "costo": uso["costo"],
        "cortada": uso["cortada"],
        "por_modelo": uso["por_modelo"],
        "fuente": "estimado por el agente",
        "run": run_url,
    })
    cc = cfg.get("costos") or {}
    datos["cargas"] = [{"fecha": str(x.get("fecha")), "monto": float(x.get("monto") or 0)}
                       for x in (cc.get("cargas") or [])]
    datos["alerta_saldo"] = float(cc.get("alerta_saldo", 1.5))
    datos["actualizado"] = ahora_iso()
    datos["total"] = round(sum(r.get("costo") or 0 for r in datos["registros"]), 4)
    guardar_json("costos.json", datos, historial=False)
    log.info("Costo registrado: US$ %.2f (acumulado US$ %.2f)", uso["costo"], datos["total"])
