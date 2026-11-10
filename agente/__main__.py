"""Punto de entrada.

Uso:
    python -m agente mensual    # top 200 por capitalización + score + 50 mejores con tesis IA
    python -m agente semanal    # 20 de las 50 (rotación + seguimiento): técnico + momento de entrada
    python -m agente todo       # ambos en orden (útil la primera vez)
    python -m agente semanal --demo   # datos simulados, sin Internet ni IA
"""
from __future__ import annotations

import argparse
import sys

import os

from . import fundamental, ia, tecnico, universo
from .util import cargar_config, configurar_log, leer_json, log


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agente")
    ap.add_argument("modo", choices=["mensual", "semanal", "todo"])
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
    mensual = leer_json("mensual.json")

    # El semanal necesita el mensual: si todavía no existe, se genera primero.
    if modo in ("mensual", "todo") or mensual is None:
        uni = universo.ejecutar(prov, cfg)
        mensual = fundamental.ejecutar(prov, cfg, uni)
    if modo in ("semanal", "todo"):
        tecnico.ejecutar(prov, cfg, mensual)

    uso = ia.resumen_uso(cfg)
    log.info(uso)
    log.info("Listo (%s).", modo)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(f"### Agente Top 200 — {modo}\n\n- {uso}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
