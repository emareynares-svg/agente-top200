"""Punto de entrada.

Uso:
    python -m agente mensual    # universo de las 200 por capitalización
    python -m agente semanal    # fundamentales: 50 mejores + tesis IA + 20 principales
    python -m agente diario     # técnico de las 20 principales
    python -m agente todo       # los tres en orden (útil la primera vez)
    python -m agente diario --demo   # datos simulados, sin Internet ni IA
"""
from __future__ import annotations

import argparse
import sys

from . import fundamental, tecnico, universo
from .util import cargar_config, configurar_log, leer_json, log


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agente")
    ap.add_argument("modo", choices=["mensual", "semanal", "diario", "todo"])
    ap.add_argument("--demo", action="store_true", help="usar datos simulados (sin Internet)")
    args = ap.parse_args(argv)

    configurar_log()
    cfg = cargar_config()
    if args.demo:
        from .datos import DemoProveedor
        prov = DemoProveedor()
        cfg.setdefault("ia", {})["activada"] = False
    else:
        from .datos import YahooProveedor
        prov = YahooProveedor()

    modo = args.modo
    uni = leer_json("universo.json")
    sem = leer_json("semanal.json")

    # Cada etapa necesita la anterior: si falta, se genera automáticamente.
    if modo in ("mensual", "todo") or uni is None:
        uni = universo.ejecutar(prov, cfg)
    if modo in ("semanal", "todo") or (modo == "diario" and sem is None):
        sem = fundamental.ejecutar(prov, cfg, uni)
    if modo in ("diario", "todo"):
        tecnico.ejecutar(prov, cfg, sem)

    log.info("Listo (%s).", modo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
