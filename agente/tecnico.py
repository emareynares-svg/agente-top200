"""Proceso SEMANAL: elige 20 de las 50 del mes (rotación + seguimiento), hace el análisis
técnico y Claude evalúa el momento de entrada cruzándolo con la tesis mensual."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import ia
from .util import ahora_iso, guardar_json, hoy, leer_json, log


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    sube = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    baja = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = sube / baja.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.mask((baja == 0) & (sube > 0), 100.0)


def macd(close: pd.Series, r: int = 12, l: int = 26, s: int = 9):
    linea = close.ewm(span=r, adjust=False).mean() - close.ewm(span=l, adjust=False).mean()
    senal = linea.ewm(span=s, adjust=False).mean()
    return linea, senal, linea - senal


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["Close"].shift()
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - prev).abs(), (df["Low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def _rend(close: pd.Series, dias: int) -> float | None:
    if len(close) <= dias:
        return None
    return float(close.iloc[-1] / close.iloc[-1 - dias] - 1)


def analizar(df: pd.DataFrame) -> dict:
    c = df["Close"]
    precio = float(c.iloc[-1])
    sma20, sma50, sma200 = (c.rolling(n).mean() for n in (20, 50, 200))
    r = rsi(c)
    m, s, h = macd(c)
    bb_mid, bb_std = sma20, c.rolling(20).std()
    bb_sup, bb_inf = bb_mid + 2 * bb_std, bb_mid - 2 * bb_std
    a = atr(df)
    ult = lambda x: (float(x.iloc[-1]) if pd.notna(x.iloc[-1]) else None)  # noqa: E731
    v20 = df["Volume"].rolling(20).mean()
    max52, min52 = float(c.tail(252).max()), float(c.tail(252).min())
    inicio_anio = c[c.index >= pd.Timestamp(year=c.index[-1].year, month=1, day=1)]

    ind = {
        "precio": precio,
        "sma20": ult(sma20), "sma50": ult(sma50), "sma200": ult(sma200),
        "rsi": ult(r), "macd": ult(m), "macd_senal": ult(s), "macd_hist": ult(h),
        "macd_hist_prev": float(h.iloc[-2]) if len(h) > 1 else None,
        "bb_pct": ult((c - bb_inf) / (bb_sup - bb_inf)),
        "atr_pct": ult(a / c),
        "vol_rel": ult(df["Volume"] / v20),
        "dist_max52": precio / max52 - 1, "dist_min52": precio / min52 - 1,
        "max52": max52, "min52": min52,
        "rend_1d": _rend(c, 1), "rend_1s": _rend(c, 5), "rend_1m": _rend(c, 21),
        "rend_3m": _rend(c, 63), "rend_1a": _rend(c, 252),
        "rend_ytd": float(precio / inicio_anio.iloc[0] - 1) if len(inicio_anio) else None,
    }
    # Cruces recientes (últimos 5 días) de SMA50 vs SMA200
    dif = (sma50 - sma200).dropna()
    cruce = None
    if len(dif) > 6:
        ventana = np.sign(dif.tail(6))
        if ventana.iloc[0] < 0 < ventana.iloc[-1]:
            cruce = "Cruce dorado (SMA50 sobre SMA200)"
        elif ventana.iloc[0] > 0 > ventana.iloc[-1]:
            cruce = "Cruce de la muerte (SMA50 bajo SMA200)"
    ind["cruce"] = cruce
    ind.update(_senal(ind))
    ind["serie"] = {
        "fechas": [d.strftime("%Y-%m-%d") for d in c.tail(180).index],
        "close": [round(float(x), 2) for x in c.tail(180)],
        "sma50": [None if pd.isna(x) else round(float(x), 2) for x in sma50.tail(180)],
        "sma200": [None if pd.isna(x) else round(float(x), 2) for x in sma200.tail(180)],
    }
    return ind


def _senal(i: dict) -> dict:
    """Puntaje técnico de -100 a +100 y una etiqueta legible."""
    p, pts, motivos = i["precio"], 0, []

    def suma(cond, val, txt):
        nonlocal pts
        if cond:
            pts += val
            motivos.append(txt)

    s50, s200, rs = i["sma50"], i["sma200"], i["rsi"]
    if s200:
        suma(p > s200, 25, "Precio sobre SMA200")
        suma(p < s200, -25, "Precio bajo SMA200")
    if s50 and s200:
        suma(s50 > s200, 15, "SMA50 > SMA200")
        suma(s50 < s200, -15, "SMA50 < SMA200")
    if s50:
        suma(p > s50, 10, "Precio sobre SMA50")
        suma(p < s50, -10, "Precio bajo SMA50")
    if i["macd_hist"] is not None:
        suma(i["macd_hist"] > 0, 10, "MACD positivo")
        suma(i["macd_hist"] < 0, -10, "MACD negativo")
        if i["macd_hist_prev"] is not None:
            suma(i["macd_hist"] > 0 >= i["macd_hist_prev"], 10, "MACD cruzó al alza hoy")
            suma(i["macd_hist"] < 0 <= i["macd_hist_prev"], -10, "MACD cruzó a la baja hoy")
    if rs is not None:
        suma(rs >= 70, -10, f"RSI sobrecomprado ({rs:.0f})")
        suma(rs <= 30, 10, f"RSI sobrevendido ({rs:.0f})")
    if i["rend_3m"] is not None:
        suma(i["rend_3m"] > 0.10, 10, "Momentum 3m fuerte")
        suma(i["rend_3m"] < -0.10, -10, "Momentum 3m débil")
    pts = max(-100, min(100, pts))

    alcista = bool(s200 and p > s200 and s50 and s50 > s200)
    bajista = bool(s200 and p < s200 and s50 and s50 < s200)
    tendencia = "Alcista" if alcista else "Bajista" if bajista else "Lateral / mixta"

    # Etiqueta pensada para un inversor de largo plazo: ¿es buen momento para entrar?
    if alcista and rs is not None and rs < 45 and s50 and p <= s50 * 1.03:
        etiqueta = "Zona de compra (retroceso en tendencia alcista)"
    elif rs is not None and rs >= 70:
        etiqueta = "Sobrecomprada: esperar"
    elif bajista and rs is not None and rs <= 30:
        etiqueta = "Sobrevendida en tendencia bajista: cautela"
    elif bajista:
        etiqueta = "Debilidad: no apurarse"
    elif alcista:
        etiqueta = "Tendencia sana"
    else:
        etiqueta = "Neutral"
    return {"puntaje_tecnico": pts, "tendencia": tendencia, "etiqueta": etiqueta, "motivos": motivos}


def seleccionar(cfg: dict, mensual: dict, rotacion: dict) -> list[dict]:
    """Elige las empresas de la semana.

    1. Seguimiento: hasta `seguimiento_max` que la semana anterior estaban en "Zona de compra".
    2. Rotación: el resto, priorizando las que hace más tiempo que no se analizan
       (las nunca analizadas primero) y, a igualdad, las de mejor score mensual.
    Así en ~3 semanas se recorren las 50 y las oportunidades no se pierden de vista."""
    s = cfg["semanal"]
    n = int(s.get("cantidad", 20))
    candidatas = [e for e in mensual.get("empresas", [])
                  if e.get("operable") or not s.get("solo_operables", True)]
    hist = rotacion.get("historial", {})
    semanas = rotacion.get("semanas", [])
    anterior = set(semanas[-1]["tickers"]) if semanas else set()

    seguimiento = [e for e in candidatas if e["ticker"] in anterior
                   and hist.get(e["ticker"], {}).get("zona_compra")]
    seguimiento = seguimiento[: int(s.get("seguimiento_max", 5))]
    elegidas = [dict(e, motivo="Seguimiento: zona de compra la semana pasada") for e in seguimiento]
    ya = {e["ticker"] for e in elegidas}

    resto = [e for e in candidatas if e["ticker"] not in ya]
    resto.sort(key=lambda e: (hist.get(e["ticker"], {}).get("ultima", ""), -(e.get("score") or 0)))
    for e in resto[: n - len(elegidas)]:
        ult = hist.get(e["ticker"], {}).get("ultima")
        elegidas.append(dict(e, motivo=f"Rotación (último análisis {ult})" if ult else "Primera vez en el ciclo"))
    return elegidas


def ejecutar(prov, cfg: dict, mensual: dict) -> dict:
    rotacion = leer_json("rotacion.json") or {"historial": {}, "semanas": []}
    elegidas = seleccionar(cfg, mensual, rotacion)
    tickers = [e["ticker"] for e in elegidas]
    log.info("Semana: %d empresas (%s)", len(tickers), ", ".join(tickers))
    historias = prov.historia(tickers, "2y")
    filas = []
    for e in elegidas:
        t = e["ticker"]
        if t not in historias:
            log.warning("Sin precios para %s", t)
            continue
        try:
            ind = analizar(historias[t])
        except Exception as ex:  # noqa: BLE001
            log.warning("Fallo técnico %s: %s", t, ex)
            continue
        tesis = e.get("ia") or {}
        ind.update({"ticker": t, "nombre": e.get("nombre", t), "sector": e.get("sector"),
                    "motivo": e["motivo"], "score_fundamental": e.get("score"),
                    "puesto_mensual": e.get("puesto"), "veredicto_mensual": tesis.get("veredicto"),
                    "valuacion_mensual": tesis.get("valuacion"),
                    "tesis_mensual": (tesis.get("resumen") or "")[:300],
                    "vigilar_mensual": tesis.get("que_vigilar")})
        filas.append(ind)
    filas.sort(key=lambda x: x["puntaje_tecnico"], reverse=True)

    resumen = None
    if ia.disponible(cfg) and filas:
        log.info("Pidiendo a Claude la evaluación del momento de entrada...")
        resumen = ia.comentario_semanal(cfg, filas)

    # Actualiza el historial de rotación
    fecha = hoy()
    hist = rotacion.setdefault("historial", {})
    for f in filas:
        h = hist.setdefault(f["ticker"], {"veces": 0})
        h.update({"ultima": fecha, "veces": h.get("veces", 0) + 1, "etiqueta": f["etiqueta"],
                  "zona_compra": f["etiqueta"].startswith("Zona de compra")
                  or ((resumen or {}).get("por_empresa", {}).get(f["ticker"], {}) or {}).get("momento") == "Entrar ahora"})
    rotacion.setdefault("semanas", []).append({"fecha": fecha, "tickers": [f["ticker"] for f in filas]})
    rotacion["semanas"] = rotacion["semanas"][-26:]
    del_mes = {e["ticker"] for e in mensual.get("empresas", [])}
    cubiertas = [t for t, h in hist.items() if t in del_mes and h.get("ultima", "") >= (mensual.get("actualizado") or "")[:10]]
    guardar_json("rotacion.json", rotacion, historial=False)

    # Las series de precios van en un archivo aparte (sin historial, para no inflar el repo)
    series = {f["ticker"]: f.pop("serie") for f in filas}
    guardar_json("series.json", {"actualizado": ahora_iso(), "series": series}, historial=False)
    resultado = {"actualizado": ahora_iso(), "mes_de": mensual.get("actualizado"),
                 "cobertura_mes": {"analizadas": len(cubiertas), "total": len(del_mes)},
                 "resumen_ia": resumen, "empresas": filas}
    guardar_json("semanal.json", resultado)
    return resultado
