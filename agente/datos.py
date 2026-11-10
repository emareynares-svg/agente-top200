"""Proveedores de datos de mercado.

- YahooProveedor: datos reales (companiesmarketcap.com + Yahoo Finance vía yfinance). Gratis.
- DemoProveedor: datos simulados para probar el programa sin internet (--demo).
"""
from __future__ import annotations

import io
import random
import time
import zlib
from functools import lru_cache

import numpy as np
import pandas as pd

from .util import log, num

# Lista de respaldo: grandes empresas globales (formato Yahoo). Si companiesmarketcap.com
# no responde, el programa consulta la capitalización de estas y se queda con las mayores.
SEMILLA = """
AAPL MSFT NVDA AMZN GOOGL META AVGO TSLA BRK-B LLY JPM V WMT ORCL MA XOM UNH COST NFLX JNJ
HD PG ABBV BAC CRM KO CVX PLTR AMD TMUS CSCO WFC ABT IBM MRK PM GE LIN MCD ACN DIS INTU PEP
TMO NOW GS ISRG T AXP UBER RTX VZ MS QCOM AMGN BKNG ADBE TXN CAT SPGI BX PGR SCHW C BSX NEE
DHR PFE HON UNP AMAT LOW BA SYK ETN TJX GILD KKR COP DE ADP PANW ANET MU CMCSA VRTX LRCX MDT
ADI KLAC CB MMC SBUX LMT APH CRWD INTC MO SO ICE BMY PLD CME AMT ELV HCA WM NKE DUK MCO CEG
TT SHW UPS GEV APP MCK EQIX CTAS PH ABNB CI AJG CVS ORLY MSI WELL TDG COF PYPL MMM ZTS RCL
ITW CDNS SNPS AON CL USB PNC GD EMR NOC HWM DASH COIN SPOT MRVL HOOD NET SNOW
TSM ASML SAP NVO AZN TM HSBC SHEL NVS BABA SONY UL RY TD BHP RIO TTE BUD MUFG SMFG HDB IBN
PDD SAN BBVA UBS DEO BP SNY GSK ENB CNQ SHOP ARM BTI ING NTES JD TCOM MELI SE CP CNI BCS LYG
BN INFY RACE
2222.SR 1120.SR 005930.KS 000660.KS 0700.HK 1398.HK 0939.HK 3988.HK 1288.HK 601288.SS 600519.SS
1211.HK 3690.HK 1810.HK 2318.HK 0857.HK 0941.HK MC.PA RMS.PA OR.PA SU.PA AIR.PA SAF.PA EL.PA
ROG.SW NESN.SW SIE.DE ALV.DE DTE.DE RHM.DE ITX.MC IBE.MC RELIANCE.NS TCS.NS BHARTIARTL.NS
9984.T 6861.T 8035.T 9983.T 4063.T 6501.T 8058.T CBA.AX 2454.TW 2317.TW ADYEN.AS PRX.AS LSEG.L RR.L
""".split()

URL_CMC = "https://companiesmarketcap.com/?download=csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

# Campos que se piden a Yahoo (Ticker.info)
CAMPOS_INFO = [
    "longName", "shortName", "sector", "industry", "country", "currency", "financialCurrency",
    "marketCap", "enterpriseValue", "currentPrice", "regularMarketPrice",
    "returnOnEquity", "returnOnAssets", "grossMargins", "operatingMargins", "profitMargins",
    "ebitdaMargins", "debtToEquity", "totalDebt", "totalCash", "ebitda", "freeCashflow",
    "operatingCashflow", "trailingPE", "forwardPE", "priceToBook", "enterpriseToEbitda",
    "pegRatio", "trailingPegRatio", "revenueGrowth", "earningsGrowth", "currentRatio",
    "dividendYield", "payoutRatio", "beta", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
    "recommendationKey", "targetMeanPrice", "numberOfAnalystOpinions", "longBusinessSummary",
]


class YahooProveedor:
    """Datos reales desde Internet."""

    def __init__(self) -> None:
        import yfinance as yf  # import diferido: el modo demo no lo necesita
        self.yf = yf

    # ---------- Universo ----------
    def universo(self, cantidad: int) -> list[dict]:
        try:
            return self._universo_cmc(cantidad)
        except Exception as e:  # noqa: BLE001
            log.warning("companiesmarketcap.com falló (%s). Uso la lista de respaldo.", e)
            return self._universo_semilla(cantidad)

    def _universo_cmc(self, cantidad: int) -> list[dict]:
        import requests
        r = requests.get(URL_CMC, headers=HEADERS, timeout=30)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        cols = {c.lower().strip(): c for c in df.columns}
        sym, name = cols.get("symbol"), cols.get("name")
        cap = next((cols[c] for c in cols if "marketcap" in c.replace(" ", "")), None)
        pais = cols.get("country")
        if not (sym and cap):
            raise ValueError(f"Columnas inesperadas: {list(df.columns)}")
        df = df.dropna(subset=[sym, cap]).sort_values(cap, ascending=False).head(cantidad)
        filas = []
        for i, row in enumerate(df.to_dict("records"), 1):
            filas.append({
                "rank": i, "ticker": str(row[sym]).strip(), "nombre": str(row.get(name, "")),
                "market_cap": num(row[cap]), "pais": str(row.get(pais, "") or ""),
            })
        log.info("Universo desde companiesmarketcap.com: %d empresas", len(filas))
        return filas

    def _universo_semilla(self, cantidad: int) -> list[dict]:
        filas = []
        for t in SEMILLA:
            try:
                fi = self.yf.Ticker(t).fast_info
                cap = num(fi.get("marketCap"))
                cur = fi.get("currency") or "USD"
                if cap:
                    filas.append({"ticker": t, "nombre": t, "market_cap": cap * self.fx(cur), "pais": ""})
            except Exception as e:  # noqa: BLE001
                log.debug("Sin datos para %s: %s", t, e)
        filas.sort(key=lambda x: x["market_cap"], reverse=True)
        filas = filas[:cantidad]
        for i, f in enumerate(filas, 1):
            f["rank"] = i
        log.info("Universo desde lista de respaldo: %d empresas", len(filas))
        return filas

    # ---------- Moneda ----------
    @lru_cache(maxsize=64)
    def fx(self, moneda: str) -> float:
        """Cuántos USD vale 1 unidad de `moneda`."""
        if not moneda or moneda.upper() == "USD":
            return 1.0
        div = 1.0
        if moneda in ("GBp", "GBX"):
            moneda, div = "GBP", 100.0
        elif moneda == "ILA":
            moneda, div = "ILS", 100.0
        elif moneda == "ZAc":
            moneda, div = "ZAR", 100.0
        try:
            h = self.yf.Ticker(f"{moneda.upper()}USD=X").history(period="5d")
            return float(h["Close"].dropna().iloc[-1]) / div
        except Exception:  # noqa: BLE001
            log.warning("No pude obtener tipo de cambio %s→USD; uso 1", moneda)
            return 1.0 / div

    # ---------- Fundamentales ----------
    def fundamentales(self, ticker: str) -> dict:
        t = self.yf.Ticker(ticker)
        info = {}
        for intento in range(3):
            try:
                info = t.info or {}
                break
            except Exception as e:  # noqa: BLE001
                log.debug("Reintento %s info (%s)", ticker, e)
                time.sleep(2 * (intento + 1))
        datos = {k: info.get(k) for k in CAMPOS_INFO}
        datos.update(self._estados(t))
        return datos

    def _estados(self, t) -> dict:
        """ROIC aproximado y consistencia de flujo de caja libre a partir de los balances."""
        out: dict = {"roic": None, "fcf_anios_positivos": None, "fcf_anios": None,
                     "crecimiento_ventas_3a": None}
        try:
            inc, bal, cf = t.income_stmt, t.balance_sheet, t.cashflow

            def fila(df, *nombres):
                for n in nombres:
                    if df is not None and n in df.index:
                        return df.loc[n]
                return None

            ebit = fila(inc, "EBIT", "Operating Income")
            tax = fila(inc, "Tax Rate For Calcs")
            capital = fila(bal, "Invested Capital")
            if ebit is not None and capital is not None:
                tasa = num(tax.iloc[0]) if tax is not None else 0.21
                tasa = 0.21 if tasa is None or not (0 <= tasa <= 0.5) else tasa
                e, c = num(ebit.iloc[0]), num(capital.iloc[0])
                if e is not None and c and c > 0:
                    out["roic"] = e * (1 - tasa) / c
            fcf = fila(cf, "Free Cash Flow")
            if fcf is not None:
                serie = fcf.dropna()
                out["fcf_anios"] = int(len(serie))
                out["fcf_anios_positivos"] = int((serie > 0).sum())
            ventas = fila(inc, "Total Revenue")
            if ventas is not None:
                v = ventas.dropna()
                if len(v) >= 4 and num(v.iloc[3]) and v.iloc[3] > 0 and v.iloc[0] > 0:
                    out["crecimiento_ventas_3a"] = (v.iloc[0] / v.iloc[3]) ** (1 / 3) - 1
        except Exception as e:  # noqa: BLE001
            log.debug("Estados contables incompletos: %s", e)
        return out

    def noticias(self, ticker: str, maximo: int = 6) -> list[str]:
        try:
            items = self.yf.Ticker(ticker).news or []
        except Exception:  # noqa: BLE001
            return []
        titulos = []
        for it in items[:maximo]:
            c = it.get("content", it)
            titulo = c.get("title")
            fecha = (c.get("pubDate") or "")[:10]
            if titulo:
                titulos.append(f"{fecha} {titulo}".strip())
        return titulos

    # ---------- Precios ----------
    def historia(self, tickers: list[str], periodo: str = "2y") -> dict[str, pd.DataFrame]:
        df = self.yf.download(tickers, period=periodo, interval="1d", group_by="ticker",
                              auto_adjust=True, progress=False, threads=True)
        out = {}
        for t in tickers:
            try:
                sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
                sub = sub.dropna(subset=["Close"])
                if len(sub) > 30:
                    out[t] = sub
            except KeyError:
                log.warning("Sin historia de precios para %s", t)
        return out


class DemoProveedor:
    """Datos simulados, reproducibles, para probar sin internet."""

    SECTORES = ["Technology", "Healthcare", "Financial Services", "Consumer Defensive",
                "Industrials", "Energy", "Communication Services", "Consumer Cyclical"]

    def __init__(self, semilla: int = 7) -> None:
        self.rng = random.Random(semilla)

    def universo(self, cantidad: int) -> list[dict]:
        tickers = SEMILLA[:cantidad]
        caps = sorted((self.rng.lognormvariate(26.5, 0.9) for _ in tickers), reverse=True)
        return [{"rank": i + 1, "ticker": t, "nombre": f"Empresa {t}", "market_cap": c,
                 "pais": "United States" if "." not in t else "Otro"}
                for i, (t, c) in enumerate(zip(tickers, caps))]

    def fx(self, moneda: str) -> float:
        return 1.0

    def fundamentales(self, ticker: str) -> dict:
        r = random.Random(zlib.crc32(ticker.encode()))
        sector = r.choice(self.SECTORES)
        cap = r.lognormvariate(26.5, 0.9)
        pe = r.uniform(8, 60)
        return {
            "longName": f"Empresa {ticker}", "sector": sector, "industry": sector + " - demo",
            "country": "United States", "currency": "USD", "financialCurrency": "USD",
            "marketCap": cap, "enterpriseValue": cap * r.uniform(0.9, 1.3),
            "currentPrice": r.uniform(20, 900),
            "returnOnEquity": r.uniform(-0.05, 0.6), "returnOnAssets": r.uniform(0, 0.25),
            "grossMargins": r.uniform(0.2, 0.85), "operatingMargins": r.uniform(0.02, 0.5),
            "profitMargins": r.uniform(0.01, 0.4), "debtToEquity": r.uniform(0, 250),
            "totalDebt": cap * r.uniform(0, 0.3), "totalCash": cap * r.uniform(0, 0.15),
            "ebitda": cap * r.uniform(0.02, 0.12), "freeCashflow": cap * r.uniform(-0.01, 0.07),
            "trailingPE": pe, "forwardPE": pe * r.uniform(0.7, 1.05),
            "priceToBook": r.uniform(1, 20), "enterpriseToEbitda": r.uniform(5, 40),
            "revenueGrowth": r.uniform(-0.1, 0.5), "earningsGrowth": r.uniform(-0.3, 0.8),
            "dividendYield": r.uniform(0, 4), "beta": r.uniform(0.5, 1.8),
            "recommendationKey": r.choice(["buy", "hold", "strong_buy"]),
            "targetMeanPrice": None, "numberOfAnalystOpinions": r.randint(5, 50),
            "roic": r.uniform(-0.02, 0.45), "fcf_anios": 4, "fcf_anios_positivos": r.randint(1, 4),
            "crecimiento_ventas_3a": r.uniform(-0.05, 0.35),
            "longBusinessSummary": "Empresa simulada para la demo.",
        }

    def noticias(self, ticker: str, maximo: int = 6) -> list[str]:
        return [f"Titular simulado {i} sobre {ticker}" for i in range(2)]

    def historia(self, tickers: list[str], periodo: str = "2y") -> dict[str, pd.DataFrame]:
        out = {}
        idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=500)
        for t in tickers:
            r = np.random.default_rng(zlib.crc32(t.encode()))
            ret = r.normal(r.uniform(-0.0005, 0.0012), r.uniform(0.01, 0.025), len(idx))
            close = 100 * np.exp(np.cumsum(ret))
            high = close * (1 + np.abs(r.normal(0, 0.008, len(idx))))
            low = close * (1 - np.abs(r.normal(0, 0.008, len(idx))))
            vol = r.integers(5_000_000, 40_000_000, len(idx)).astype(float)
            out[t] = pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close,
                                   "Volume": vol}, index=idx)
        return out
