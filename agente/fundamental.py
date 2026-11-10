"""Proceso MENSUAL (2ª parte): puntúa las 200 por calidad + valor, elige las 50 mejores
y Claude escribe una tesis para cada una."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from . import ia
from .util import ahora_iso, guardar_json, log, num

FINANCIERAS = {"Financial Services", "Financial"}

# (métrica, bloque, mayor_es_mejor, relativa_al_sector, peso)
METRICAS = [
    ("roic",               "calidad", True,  False, 1.5),
    ("margen_operativo",   "calidad", True,  True,  1.0),
    ("margen_bruto",       "calidad", True,  True,  0.5),
    ("fcf_consistencia",   "calidad", True,  False, 1.0),
    ("deuda_neta_ebitda",  "calidad", False, False, 1.0),
    ("crecimiento_ventas", "calidad", True,  False, 1.0),
    ("fcf_yield",          "valor",   True,  False, 1.5),
    ("earnings_yield",     "valor",   True,  True,  1.5),
    ("ev_ebitda",          "valor",   False, True,  1.0),
    ("peg",                "valor",   False, False, 0.75),
    ("pb_inv",             "valor",   True,  True,  0.75),
    ("upside_analistas",   "valor",   True,  False, 0.5),
]

NOMBRES_METRICAS = {
    "roic": "ROIC", "margen_operativo": "Margen operativo", "margen_bruto": "Margen bruto",
    "fcf_consistencia": "Años con FCF positivo", "deuda_neta_ebitda": "Deuda neta / EBITDA",
    "crecimiento_ventas": "Crecimiento de ventas", "fcf_yield": "FCF yield",
    "earnings_yield": "Earnings yield (1/PE fwd)", "ev_ebitda": "EV / EBITDA", "peg": "PEG",
    "pb_inv": "Libro / Precio (financieras)", "upside_analistas": "Upside precio objetivo",
}


def _metricas(raw: dict, prov) -> dict:
    """Calcula las métricas normalizadas a partir de los datos crudos de Yahoo."""
    sector = raw.get("sector") or "Sin sector"
    financiera = sector in FINANCIERAS
    cap = num(raw.get("marketCap"))
    # Ajuste si los estados contables están en otra moneda que la cotización (ej. ADRs)
    fin, cot = raw.get("financialCurrency") or "USD", raw.get("currency") or "USD"
    conv = 1.0 if fin == cot else prov.fx(fin) / max(prov.fx(cot), 1e-12)

    roe = num(raw.get("returnOnEquity"))
    roic = num(raw.get("roic"))
    if financiera or roic is None:
        roic = roe
    ebitda = num(raw.get("ebitda"))
    deuda, caja = num(raw.get("totalDebt")) or 0.0, num(raw.get("totalCash")) or 0.0
    dne = None
    if not financiera and ebitda and ebitda > 0:
        dne = (deuda - caja) / ebitda
    elif not financiera and ebitda is not None and ebitda <= 0:
        dne = 10.0  # EBITDA negativo: penaliza
    fcf = num(raw.get("freeCashflow"))
    fcf_yield = (fcf * conv / cap) if (fcf is not None and cap and not financiera) else None
    pe = num(raw.get("forwardPE")) or num(raw.get("trailingPE"))
    ey = (1 / pe) if pe else None
    ev_e = num(raw.get("enterpriseToEbitda"))
    ev_e = ev_e if (ev_e is not None and 0 < ev_e < 200 and not financiera) else None
    peg = num(raw.get("trailingPegRatio")) or num(raw.get("pegRatio"))
    peg = peg if (peg is not None and 0 < peg < 10) else None
    pb = num(raw.get("priceToBook"))
    pb_inv = (1 / pb) if (financiera and pb and pb > 0) else None
    precio = num(raw.get("currentPrice")) or num(raw.get("regularMarketPrice"))
    objetivo = num(raw.get("targetMeanPrice"))
    upside = (objetivo / precio - 1) if (precio and objetivo and (raw.get("numberOfAnalystOpinions") or 0) >= 3) else None
    anios, pos = raw.get("fcf_anios"), raw.get("fcf_anios_positivos")
    fcf_cons = (pos / anios) if anios else None
    crec = num(raw.get("crecimiento_ventas_3a"))
    if crec is None:
        crec = num(raw.get("revenueGrowth"))

    return {
        "sector": sector, "industria": raw.get("industry"), "financiera": financiera,
        "precio": precio, "moneda": cot,
        "roic": roic, "roe": roe, "margen_operativo": num(raw.get("operatingMargins")),
        "margen_bruto": None if financiera else num(raw.get("grossMargins")),
        "margen_neto": num(raw.get("profitMargins")),
        "fcf_consistencia": fcf_cons, "deuda_neta_ebitda": dne, "crecimiento_ventas": crec,
        "crecimiento_ganancias": num(raw.get("earningsGrowth")),
        "fcf_yield": fcf_yield, "earnings_yield": ey, "pe_forward": num(raw.get("forwardPE")),
        "pe_trailing": num(raw.get("trailingPE")), "ev_ebitda": ev_e, "peg": peg,
        "pb": pb, "pb_inv": pb_inv, "upside_analistas": upside,
        "dividend_yield": num(raw.get("dividendYield")), "beta": num(raw.get("beta")),
        "recomendacion": raw.get("recommendationKey"),
        "max_52s": num(raw.get("fiftyTwoWeekHigh")), "min_52s": num(raw.get("fiftyTwoWeekLow")),
        "descripcion": (raw.get("longBusinessSummary") or "")[:600],
    }


def _alertas(m: dict) -> list[str]:
    a = []
    if m.get("fcf_yield") is not None and m["fcf_yield"] < 0:
        a.append("FCF negativo")
    if m.get("deuda_neta_ebitda") is not None and m["deuda_neta_ebitda"] > 3:
        a.append("Deuda alta")
    if m.get("pe_forward") is not None and m["pe_forward"] > 45:
        a.append("Múltiplo exigente")
    if m.get("crecimiento_ventas") is not None and m["crecimiento_ventas"] < 0:
        a.append("Ventas en caída")
    if m.get("pe_forward") is not None and m["pe_forward"] <= 0:
        a.append("Pérdidas esperadas")
    return a


def puntuar(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Percentiles (0-1) por métrica, a nivel global o dentro del sector; luego promedio ponderado."""
    df = df.copy()
    tam_sector = df.groupby("sector")["ticker"].transform("count")
    for met, _bloque, mayor, relativa, _peso in METRICAS:
        serie = pd.to_numeric(df[met], errors="coerce")
        glob = serie.rank(pct=True, ascending=mayor)
        if relativa:
            sect = serie.groupby(df["sector"]).rank(pct=True, ascending=mayor)
            df[f"p_{met}"] = sect.where(tam_sector >= 5, glob)
        else:
            df[f"p_{met}"] = glob

    def bloque(nombre: str) -> tuple[pd.Series, pd.Series]:
        mets = [(m, w) for m, b, _, _, w in METRICAS if b == nombre]
        num_ = sum(df[f"p_{m}"].fillna(0) * w for m, w in mets)
        den = sum(df[f"p_{m}"].notna() * w for m, w in mets)
        total = sum(w for _, w in mets)
        return (num_ / den.replace(0, pd.NA)) * 100, den / total

    df["score_calidad"], cob_c = bloque("calidad")
    df["score_valor"], cob_v = bloque("valor")
    s = cfg["mensual"]
    wq, wv = float(s.get("peso_calidad", 0.55)), float(s.get("peso_valor", 0.45))
    df["cobertura"] = (cob_c * wq + cob_v * wv) / (wq + wv)
    df["score"] = (df["score_calidad"].fillna(0) * wq + df["score_valor"].fillna(0) * wv) / (wq + wv)
    df.loc[df["cobertura"] < float(s.get("cobertura_minima", 0.5)), "score"] = pd.NA
    df["score"] = pd.to_numeric(df["score"], errors="coerce")
    return df.sort_values("score", ascending=False, na_position="last")


def ejecutar(prov, cfg: dict, universo: dict) -> dict:
    empresas = universo["empresas"]
    tickers = [e["ticker"] for e in empresas]
    log.info("Descargando fundamentales de %d empresas...", len(tickers))

    def bajar(t: str):
        try:
            return t, prov.fundamentales(t)
        except Exception as e:  # noqa: BLE001
            log.warning("Fallo fundamentales %s: %s", t, e)
            return t, {}

    with ThreadPoolExecutor(max_workers=3) as pool:
        crudos = dict(pool.map(bajar, tickers))

    filas = []
    for e in empresas:
        raw = crudos.get(e["ticker"]) or {}
        if not raw:
            continue
        m = _metricas(raw, prov)
        m.update({"ticker": e["ticker"], "nombre": raw.get("longName") or raw.get("shortName") or e["nombre"],
                  "rank_cap": e.get("rank"), "market_cap": e.get("market_cap"),
                  "operable": e.get("operable"), "pais": raw.get("country") or e.get("pais")})
        m["alertas"] = _alertas(m)
        filas.append(m)
    sin_datos = [t for t in tickers if not crudos.get(t)]
    log.info("Fundamentales: %d con datos, %d sin datos%s", len(filas), len(sin_datos),
             f" ({', '.join(sin_datos[:15])}{'…' if len(sin_datos) > 15 else ''})" if sin_datos else "")
    if not filas:
        raise RuntimeError("Yahoo Finance no devolvió datos fundamentales (posible bloqueo o límite de consultas).")

    df = puntuar(pd.DataFrame(filas), cfg)
    n50 = int(cfg["mensual"].get("cantidad", 50))
    top = df[df["score"].notna()].head(n50)


    registros = top.to_dict("records")
    for i, r in enumerate(registros, 1):
        r["puesto"] = i

    # Análisis con IA
    if ia.disponible(cfg):
        log.info("Pidiendo a Claude el análisis fundamental de %d empresas...", len(registros))
        maxn = int(cfg["ia"].get("max_noticias", 6))

        def analizar(r: dict) -> dict:
            r["noticias"] = prov.noticias(r["ticker"], maxn)
            r["ia"] = ia.tesis_empresa(cfg, r)
            return r

        with ThreadPoolExecutor(max_workers=int(cfg["ia"].get("hilos", 4))) as pool:
            registros = list(pool.map(analizar, registros))
    else:
        log.info("IA desactivada o sin ANTHROPIC_API_KEY: se omiten las tesis.")

    columnas_rank = ["ticker", "nombre", "sector", "rank_cap", "market_cap", "operable",
                     "score", "score_calidad", "score_valor", "cobertura", "alertas"]
    resultado = {
        "actualizado": ahora_iso(),
        "criterio": {"peso_calidad": cfg["mensual"]["peso_calidad"],
                     "peso_valor": cfg["mensual"]["peso_valor"],
                     "metricas": [{"id": m, "nombre": NOMBRES_METRICAS[m], "bloque": b,
                                   "mayor_es_mejor": may, "relativa_sector": rel, "peso": w}
                                  for m, b, may, rel, w in METRICAS]},
        "empresas": registros,
        "ranking_completo": df[columnas_rank].to_dict("records"),
    }
    guardar_json("mensual.json", resultado)
    log.info("Mensual listo. Top 5: %s", ", ".join(top["ticker"].head(5)))
    return resultado
