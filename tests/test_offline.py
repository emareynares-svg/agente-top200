"""Prueba completa sin Internet: datos simulados + IA simulada.
Ejecutar:  python tests/test_offline.py  [--escribir-docs]"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agente import fundamental, ia, tecnico, universo, util  # noqa: E402
from agente.datos import DemoProveedor  # noqa: E402


class FalsoCliente:
    """Imita anthropic.Anthropic().messages.create y devuelve JSON plausible."""

    def __init__(self):
        self.llamadas = 0
        self.messages = self

    def create(self, model, max_tokens, system, messages):
        self.llamadas += 1
        p = messages[0]["content"]
        assert "Respondé ÚNICAMENTE con JSON" in system
        if "20 empresas principales" in p:
            tickers = [json.loads(l)["ticker"] for l in p.splitlines() if l.startswith("{\"ticker\"")]
            out = {"titular": "Mercado mixto: varias de calidad retroceden hacia su media de 50 ruedas",
                   "resumen": "Texto de ejemplo generado por la IA simulada.",
                   "oportunidades": [{"ticker": tickers[0], "comentario": "Retroceso ordenado en tendencia alcista."}],
                   "cuidado": [{"ticker": tickers[-1], "comentario": "RSI alto, conviene esperar."}],
                   "por_empresa": {t: "Comentario breve de ejemplo." for t in tickers}}
            texto = "```json\n" + json.dumps(out, ensure_ascii=False) + "\n```"
        else:
            out = {"resumen": "Empresa de ejemplo.", "tesis": "Tesis de ejemplo.",
                   "fortalezas": ["Márgenes altos", "Caja neta", "Marca"],
                   "riesgos": ["Regulación", "Ciclo", "Competencia"],
                   "catalizadores": ["Nuevo producto", "Recompras"], "calidad": "Alta",
                   "valuacion": "Razonable", "veredicto": "Candidata para el satélite",
                   "confianza": 3, "que_vigilar": "Margen operativo"}
            texto = json.dumps(out, ensure_ascii=False)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=texto)])


def correr(destino: Path) -> None:
    util.DATA, util.HISTORIAL = destino, destino / "historial"
    cfg = util.cargar_config()
    cfg["ia"]["activada"] = True
    falso = FalsoCliente()
    ia._CLIENTE = falso
    ia.disponible = lambda c: True  # type: ignore[assignment]
    prov = DemoProveedor()

    u = universo.ejecutar(prov, cfg)
    assert u["cantidad"] == 200, u["cantidad"]

    s = fundamental.ejecutar(prov, cfg, u)
    assert len(s["empresas"]) == 50
    assert len(s["top20"]) == 20
    scores = [e["score"] for e in s["empresas"]]
    assert scores == sorted(scores, reverse=True), "las 50 deben venir ordenadas por score"
    assert all(0 <= x <= 100 for x in scores)
    assert all(e["ia"]["veredicto"] for e in s["empresas"])
    assert len(s["ranking_completo"]) == 200

    d = tecnico.ejecutar(prov, cfg, s)
    assert len(d["empresas"]) == 20
    assert d["resumen_ia"]["titular"]
    for e in d["empresas"]:
        assert -100 <= e["puntaje_tecnico"] <= 100
        assert e["rsi"] is None or 0 <= e["rsi"] <= 100
    series = json.loads((destino / "series.json").read_text())["series"]
    assert set(series) == {e["ticker"] for e in d["empresas"]}
    assert falso.llamadas == 51, falso.llamadas
    print(f"OK: universo 200, semanal 50 (+{falso.llamadas - 1} tesis IA), diario 20, archivos en {destino}")


def test_indicadores() -> None:
    import pandas as pd
    sube = pd.Series(range(1, 101), dtype=float)
    assert tecnico.rsi(sube).iloc[-1] > 99
    baja = pd.Series(range(100, 0, -1), dtype=float)
    assert tecnico.rsi(baja).iloc[-1] < 1
    print("OK: indicadores")


def test_json() -> None:
    assert ia._extraer_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert ia._extraer_json('texto {"a": 2} más') == {"a": 2}
    assert util.es_operable("AAPL") and util.es_operable("BRK-B") and not util.es_operable("2222.SR")
    print("OK: parser JSON y operables")


if __name__ == "__main__":
    test_indicadores()
    test_json()
    if "--escribir-docs" in sys.argv:
        correr(util.RAIZ / "docs" / "data")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            correr(Path(tmp))
