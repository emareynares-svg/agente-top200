"""HISTORIAL: junta todas las evaluaciones pasadas (mensuales y semanales) y mide cómo
le fue a cada acción desde la fecha en que se analizó, comparado con un índice de referencia.

No usa IA: es gratis. Se recalcula al final de cada ejecución (o con `python -m agente historial`)."""
from __future__ import annotations

import json
import re
from collections import defaultdict

import pandas as pd

from . import util
from .util import ahora_iso, guardar_json, log

PATRON = re.compile(r"^(?P<base>[a-z]+)-(?P<fecha>\d{4}-\d{2}-\d{2})\.json$")


def _evaluaciones() -> list[dict]:
    """Lee los snapshots de docs/data/historial/ y los convierte en filas uniformes.
    Detecta el tipo por el contenido (sirve también para archivos de versiones anteriores)."""
    filas: list[dict] = []
    if not util.HISTORIAL.exists():
        return filas
    for ruta in sorted(util.HISTORIAL.glob("*.json")):
        m = PATRON.match(ruta.name)
        if not m or m["base"] == "universo":
            continue
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        fecha = m["fecha"]
        empresas = datos.get("empresas") or []
        if not empresas:
            continue
        if "puntaje_tecnico" in empresas[0]:  # evaluación técnica (semanal / diaria antigua)
            pe = (datos.get("resumen_ia") or {}).get("por_empresa") or {}
            for e in empresas:
                w = pe.get(e["ticker"])
                w = w if isinstance(w, dict) else {}
                filas.append({
                    "fecha": fecha, "tipo": "semanal", "ticker": e["ticker"], "nombre": e.get("nombre"),
                    "precio_reportado": e.get("precio"), "puntaje_tecnico": e.get("puntaje_tecnico"),
                    "etiqueta": e.get("etiqueta"), "tendencia": e.get("tendencia"), "rsi": e.get("rsi"),
                    "momento": w.get("momento"), "comentario": w.get("comentario"),
                    "score": e.get("score_fundamental"), "motivo": e.get("motivo"),
                })
        elif "score" in empresas[0]:  # evaluación fundamental (mensual / semanal antigua)
            for e in empresas:
                ia = e.get("ia") or {}
                filas.append({
                    "fecha": fecha, "tipo": "mensual", "ticker": e["ticker"], "nombre": e.get("nombre"),
                    "sector": e.get("sector"), "precio_reportado": e.get("precio"),
                    "puesto": e.get("puesto"), "score": e.get("score"),
                    "score_calidad": e.get("score_calidad"), "score_valor": e.get("score_valor"),
                    "roic": e.get("roic"), "fcf_yield": e.get("fcf_yield"), "pe_forward": e.get("pe_forward"),
                    "veredicto": ia.get("veredicto"), "valuacion": ia.get("valuacion"),
                    "resumen": ia.get("resumen"),
                })
    # Si el mismo día hubo varias corridas del mismo tipo, el archivo ya guarda solo la última.
    return filas


def _cierre_en(serie: pd.Series, fecha: str) -> float | None:
    """Cierre del día de la evaluación (o el último anterior)."""
    s = serie[serie.index <= pd.Timestamp(fecha)]
    return float(s.iloc[-1]) if len(s) else None


def ejecutar(prov, cfg: dict) -> dict | None:
    filas = _evaluaciones()
    if not filas:
        log.info("Historial: todavía no hay evaluaciones guardadas.")
        return None
    bench = (cfg.get("historial") or {}).get("referencia", "VOO")
    inicio = (pd.Timestamp(min(f["fecha"] for f in filas)) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    tickers = sorted({f["ticker"] for f in filas} | {bench})
    log.info("Historial: %d evaluaciones de %d empresas desde %s", len(filas), len(tickers) - 1, inicio)
    try:
        precios = prov.historia(tickers, inicio=inicio, minimo=1)
    except Exception as e:  # noqa: BLE001
        log.warning("Historial: no pude bajar precios (%s)", e)
        precios = {}
    cierres = {t: df["Close"].dropna() for t, df in precios.items()}
    ref = cierres.get(bench)
    hoy_ts = max((s.index[-1] for s in cierres.values()), default=pd.Timestamp.today())

    for f in filas:
        s = cierres.get(f["ticker"])
        f["precio_eval"] = _cierre_en(s, f["fecha"]) if s is not None else None
        f["precio_actual"] = float(s.iloc[-1]) if s is not None and len(s) else None
        f["rend"] = (f["precio_actual"] / f["precio_eval"] - 1) if f["precio_eval"] and f["precio_actual"] else None
        r0 = _cierre_en(ref, f["fecha"]) if ref is not None else None
        f["rend_ref"] = (float(ref.iloc[-1]) / r0 - 1) if r0 else None
        f["exceso"] = (f["rend"] - f["rend_ref"]) if f["rend"] is not None and f["rend_ref"] is not None else None
        f["dias"] = int((hoy_ts - pd.Timestamp(f["fecha"])).days)

    # ¿Funcionan las señales? Rendimiento promedio por veredicto (mensual) y momento (semanal)
    min_dias = int((cfg.get("historial") or {}).get("dias_minimos", 7))

    def agrupar(tipo: str, clave: str) -> list[dict]:
        g: dict[str, list[dict]] = defaultdict(list)
        for f in filas:
            if f["tipo"] == tipo and f.get(clave) and f["exceso"] is not None and f["dias"] >= min_dias:
                g[f[clave]].append(f)
        out = []
        for k, v in g.items():
            ex = [x["exceso"] for x in v]
            out.append({"grupo": k, "n": len(v),
                        "rend_prom": sum(x["rend"] for x in v) / len(v),
                        "ref_prom": sum(x["rend_ref"] for x in v) / len(v),
                        "exceso_prom": sum(ex) / len(ex),
                        "le_gano_pct": sum(1 for x in ex if x > 0) / len(ex)})
        return sorted(out, key=lambda x: -x["exceso_prom"])

    # Series semanales de precio (livianas) para los gráficos
    series = {}
    for t, s in cierres.items():
        w = s.resample("W-FRI").last().dropna()
        # la última semana puede estar incompleta: se etiqueta con la fecha real del último cierre
        w.index = w.index.where(w.index <= s.index[-1], s.index[-1])
        series[t] = {"fechas": [d.strftime("%Y-%m-%d") for d in w.index],
                     "close": [round(float(x), 2) for x in w]}

    resultado = {
        "actualizado": ahora_iso(), "referencia": bench, "dias_minimos": min_dias,
        "fechas": sorted({f["fecha"] for f in filas}),
        "senales": {"veredicto": agrupar("mensual", "veredicto"), "momento": agrupar("semanal", "momento"),
                    "etiqueta": agrupar("semanal", "etiqueta")},
        "evaluaciones": filas, "series": series,
    }
    guardar_json("historial.json", resultado, historial=False)
    return resultado
