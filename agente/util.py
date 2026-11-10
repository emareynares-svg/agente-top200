"""Utilidades comunes: rutas, configuración, guardado de JSON."""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
from pathlib import Path
from typing import Any

import yaml

RAIZ = Path(__file__).resolve().parent.parent
DATA = RAIZ / "docs" / "data"
HISTORIAL = DATA / "historial"

log = logging.getLogger("agente")


def configurar_log() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")


def cargar_config() -> dict:
    with open(RAIZ / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def ahora_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def hoy() -> str:
    return dt.date.today().isoformat()


def _limpiar(obj: Any) -> Any:
    """Convierte NaN/inf y tipos numpy a valores JSON válidos."""
    if isinstance(obj, dict):
        return {str(k): _limpiar(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_limpiar(v) for v in obj]
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        try:
            obj = obj.item()
        except Exception:  # noqa: BLE001
            pass
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return round(obj, 6)
    return obj


def guardar_json(nombre: str, contenido: Any, historial: bool = True) -> Path:
    DATA.mkdir(parents=True, exist_ok=True)
    ruta = DATA / nombre
    datos = _limpiar(contenido)
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    if historial:
        HISTORIAL.mkdir(parents=True, exist_ok=True)
        base = nombre.removesuffix(".json")
        (HISTORIAL / f"{base}-{hoy()}.json").write_text(
            json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    log.info("Guardado %s", ruta.name)
    return ruta


def leer_json(nombre: str) -> Any | None:
    ruta = DATA / nombre
    if not ruta.exists():
        return None
    return json.loads(ruta.read_text(encoding="utf-8"))


def es_operable(ticker: str) -> bool:
    """Heurística: los tickers de Yahoo sin sufijo de mercado ('.XX') cotizan en EE.UU.
    (acciones locales o ADRs), por lo que se pueden comprar desde Hapi."""
    if "." in ticker:
        return False
    # ADRs de mercado OTC (5 letras terminadas en Y o F, ej. TCEHY, NSRGY): suelen no estar en Hapi
    if len(ticker) == 5 and ticker[-1] in "YF":
        return False
    return True


def num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v
