"""Prueba completa sin Internet: datos simulados + IA simulada.
Ejecutar:  python tests/test_offline.py  [--escribir-docs]"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agente import costos, fundamental, historial, ia, tecnico, universo, util  # noqa: E402
from agente.datos import DemoProveedor  # noqa: E402


class FalsoCliente:
    """Imita anthropic.Anthropic().messages.create y devuelve JSON plausible."""

    def __init__(self):
        self.llamadas = 0
        self.messages = self

    def create(self, model, max_tokens, system, messages, extra_body=None):
        assert extra_body["output_config"]["effort"] == "low"
        self.llamadas += 1
        p = messages[0]["content"]
        assert "Respondé ÚNICAMENTE con JSON" in system
        if "elegidas esta semana" in p:
            tickers = [json.loads(l)["ticker"] for l in p.splitlines() if l.startswith("{\"ticker\"")]
            momentos = ["Entrar ahora", "Entrar de a poco", "Esperar", "Evitar"]
            out = {"titular": "Semana mixta: varias de calidad retroceden hacia su media de 50 ruedas",
                   "resumen": "Texto de ejemplo generado por la IA simulada.",
                   "por_empresa": {t: {"momento": momentos[i % 4], "comentario": "Comentario de ejemplo.",
                                       "nivel": "cerca de la SMA50"} for i, t in enumerate(tickers)}}
            texto = "```json\n" + json.dumps(out, ensure_ascii=False) + "\n```"
        else:
            out = {"resumen": "Empresa de ejemplo.", "tesis": "Tesis de ejemplo.",
                   "fortalezas": ["Márgenes altos", "Caja neta", "Marca"],
                   "riesgos": ["Regulación", "Ciclo", "Competencia"],
                   "catalizadores": ["Nuevo producto", "Recompras"], "calidad": "Alta",
                   "valuacion": "Razonable", "veredicto": "Candidata para el satélite",
                   "confianza": 3, "que_vigilar": "Margen operativo"}
            texto = json.dumps(out, ensure_ascii=False)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=texto)], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=1500, output_tokens=600))


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

    m = fundamental.ejecutar(prov, cfg, u)
    assert len(m["empresas"]) == 50
    scores = [e["score"] for e in m["empresas"]]
    assert scores == sorted(scores, reverse=True), "las 50 deben venir ordenadas por score"
    assert all(0 <= x <= 100 for x in scores)
    assert all(e["ia"]["veredicto"] for e in m["empresas"])
    assert len(m["ranking_completo"]) == 200
    assert (destino / "mensual.json").exists()

    # Semana 1
    s1 = tecnico.ejecutar(prov, cfg, m)
    assert len(s1["empresas"]) == 20
    assert s1["resumen_ia"]["titular"]
    for e in s1["empresas"]:
        assert -100 <= e["puntaje_tecnico"] <= 100
        assert e["rsi"] is None or 0 <= e["rsi"] <= 100
        assert e["motivo"] == "Primera vez en el ciclo"
    series = json.loads((destino / "series.json").read_text())["series"]
    assert set(series) == {e["ticker"] for e in s1["empresas"]}

    # Semana 2: deben entrar empresas nuevas; solo se repiten las de seguimiento (máx. 5)
    t1 = {e["ticker"] for e in s1["empresas"]}
    s2 = tecnico.ejecutar(prov, cfg, m)
    t2 = {e["ticker"] for e in s2["empresas"]}
    repetidas = t1 & t2
    seg = {e["ticker"] for e in s2["empresas"] if e["motivo"].startswith("Seguimiento")}
    assert repetidas == seg and 0 < len(seg) <= 5, (repetidas, seg)
    assert len(t2) == 20

    # Semana 3: con 3 semanas se cubre todo el mes
    tecnico.ejecutar(prov, cfg, m)
    s4 = tecnico.ejecutar(prov, cfg, m)
    cob = s4["cobertura_mes"]
    operables = sum(1 for e in m["empresas"] if e["operable"])
    assert cob["analizadas"] == operables, (cob, operables)
    assert falso.llamadas == 54, falso.llamadas

    # Historial: simulamos evaluaciones pasadas copiando los snapshots con fechas anteriores
    import shutil
    hdir = destino / "historial"
    hoy_ = util.hoy()
    shutil.copy(hdir / f"mensual-{hoy_}.json", hdir / "mensual-2026-08-03.json")
    shutil.copy(hdir / f"semanal-{hoy_}.json", hdir / "semanal-2026-08-09.json")
    shutil.copy(hdir / f"semanal-{hoy_}.json", hdir / "semanal-2026-09-13.json")
    shutil.copy(hdir / f"mensual-{hoy_}.json", hdir / "mensual-2026-10-01.json")
    h = historial.ejecutar(prov, cfg)
    ev = h["evaluaciones"]
    assert {e["tipo"] for e in ev} == {"mensual", "semanal"}
    viejas = [e for e in ev if e["fecha"] == "2026-08-03"]
    assert len(viejas) == 50 and all(e["rend"] is not None and e["exceso"] is not None for e in viejas)
    assert h["senales"]["veredicto"] and h["senales"]["momento"]
    assert "VOO" in h["series"] and all(t in h["series"] for t in {e["ticker"] for e in ev})
    assert len(h["fechas"]) == 5, h["fechas"]

    # Costos: el uso de IA se acumula por modelo y se persiste ejecución tras ejecución
    u = ia.uso(cfg)
    assert u["llamadas"] == 54 and u["por_modelo"][0]["modelo"] == "claude-sonnet-5-5"
    esperado = (54 * 1500 * 2 + 54 * 600 * 10) / 1e6
    assert abs(u["costo"] - esperado) < 1e-6, (u["costo"], esperado)
    costos.registrar(cfg, "todo", u, util.ahora_iso(), 120, True)
    costos.registrar(cfg, "semanal", u, util.ahora_iso(), 60, False)
    c = json.loads((destino / "costos.json").read_text())
    assert len(c["registros"]) == 2 and abs(c["total"] - 2 * esperado) < 1e-3
    assert c["registros"][1]["resultado"] == "con error"
    print(f"OK: universo 200, mensual 50 (+50 tesis), semanal 20 con rotación "
          f"(semana 2 repite solo {len(seg)} en seguimiento; cobertura {cob['analizadas']}/{cob['total']} "
          f"en 4 semanas), {falso.llamadas} llamadas IA; historial {len(ev)} evaluaciones en {len(h['fechas'])} fechas")


def test_indicadores() -> None:
    import pandas as pd
    sube = pd.Series(range(1, 101), dtype=float)
    assert tecnico.rsi(sube).iloc[-1] > 99
    baja = pd.Series(range(100, 0, -1), dtype=float)
    assert tecnico.rsi(baja).iloc[-1] < 1
    print("OK: indicadores")


def test_disyuntor() -> None:
    """Si las respuestas vienen cortadas, la IA se corta sola tras 3 fallas (no gasta de más)."""
    class Cortado:
        llamadas = 0
        def __init__(self):
            self.messages = self
        def create(self, **kw):
            Cortado.llamadas += 1
            return SimpleNamespace(content=[SimpleNamespace(type="text", text='{"resumen": "inc')],
                                   stop_reason="max_tokens", usage=SimpleNamespace(input_tokens=10, output_tokens=10))
    import copy
    viejo = copy.deepcopy(ia._ESTADO)
    ia._CLIENTE = Cortado()
    cfg = util.cargar_config()
    for _ in range(10):
        assert ia.tesis_empresa(cfg, {"ticker": "X"}) is None
    assert ia._ESTADO["cortado"] and Cortado.llamadas == 3, Cortado.llamadas
    ia._ESTADO.clear(); ia._ESTADO.update(viejo); ia._CLIENTE = None
    print("OK: disyuntor de IA (3 llamadas y corta)")


def test_json() -> None:
    assert ia._extraer_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert ia._extraer_json('texto {"a": 2} más') == {"a": 2}
    assert util.es_operable("AAPL") and util.es_operable("BRK-B") and not util.es_operable("2222.SR") and not util.es_operable("TCEHY")
    print("OK: parser JSON y operables")


if __name__ == "__main__":
    test_indicadores()
    test_json()
    test_disyuntor()
    if "--escribir-docs" in sys.argv:
        correr(util.RAIZ / "docs" / "data")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            correr(Path(tmp))
