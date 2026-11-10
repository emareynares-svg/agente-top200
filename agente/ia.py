"""Capa de IA: Claude escribe las tesis semanales y el comentario técnico diario."""
from __future__ import annotations

import json
import os
import re
import time

from .util import log

SISTEMA = """Sos un analista de inversiones de renta variable, riguroso y escéptico, que escribe en
español rioplatense claro y sin jerga innecesaria. Trabajás para una inversora particular con este perfil:
{perfil}
Reglas: basate SOLO en los datos provistos; si algo falta, decilo. No inventes cifras ni noticias.
No des órdenes de compra/venta: das análisis para que ella decida. Respondé ÚNICAMENTE con JSON válido."""

PLANTILLA_TESIS = """Analizá esta empresa (seleccionada por un screener de calidad + valor entre las 200
mayores del mundo por capitalización).

DATOS (ratios en decimales; 0.25 = 25%):
{datos}

TITULARES RECIENTES:
{noticias}

Devolvé este JSON:
{{
 "resumen": "1-2 frases: qué hace y por qué aparece en el screener",
 "tesis": "3-5 frases con la tesis de inversión a largo plazo",
 "fortalezas": ["...", "...", "..."],
 "riesgos": ["...", "...", "..."],
 "catalizadores": ["...", "..."],
 "calidad": "Alta | Media | Baja",
 "valuacion": "Barata | Razonable | Cara",
 "veredicto": "Candidata para el satélite | Seguir de cerca | No encaja",
 "confianza": 1-5,
 "que_vigilar": "la métrica o evento que cambiaría la tesis"
}}"""

PLANTILLA_DIARIO = """Estas son las 20 empresas principales de la semana (elegidas por fundamentales) con su
análisis técnico al cierre de hoy. Indicadores: precio, SMA50/200, RSI(14), histograma MACD, %B de Bollinger,
distancia al máximo de 52 semanas, rendimientos y una etiqueta automática.

{datos}

Escribí un comentario para una inversora de largo plazo que usa el análisis técnico solo para elegir
MEJORES MOMENTOS DE ENTRADA (no para hacer trading). Devolvé este JSON:
{{
 "titular": "una frase con lo más importante del día",
 "resumen": "3-5 frases sobre el panorama técnico del grupo",
 "oportunidades": [{{"ticker": "...", "comentario": "por qué podría ser buen momento de entrada"}}],
 "cuidado": [{{"ticker": "...", "comentario": "por qué conviene esperar"}}],
 "por_empresa": {{"TICKER": "una línea breve", "...": "..."}}
}}
Máximo 4 oportunidades y 4 de cuidado. Si no hay oportunidades claras, dejá la lista vacía."""

_CLIENTE = None


def disponible(cfg: dict) -> bool:
    return bool(cfg.get("ia", {}).get("activada")) and bool(os.environ.get("ANTHROPIC_API_KEY"))


def _cliente():
    global _CLIENTE
    if _CLIENTE is None:
        import anthropic
        _CLIENTE = anthropic.Anthropic(max_retries=4)
    return _CLIENTE


def _extraer_json(texto: str) -> dict:
    texto = texto.strip()
    texto = re.sub(r"^```(?:json)?|```$", "", texto, flags=re.MULTILINE).strip()
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        ini, fin = texto.find("{"), texto.rfind("}")
        if ini >= 0 and fin > ini:
            return json.loads(texto[ini:fin + 1])
        raise


def _llamar(cfg: dict, modelo: str, prompt: str, max_tokens: int) -> dict | None:
    sistema = SISTEMA.format(perfil=cfg.get("perfil_inversor", "").strip())
    for intento in range(3):
        try:
            resp = _cliente().messages.create(
                model=modelo, max_tokens=max_tokens, system=sistema,
                messages=[{"role": "user", "content": prompt}],
            )
            texto = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            return _extraer_json(texto)
        except json.JSONDecodeError:
            log.warning("Respuesta de IA no era JSON válido (intento %d)", intento + 1)
        except Exception as e:  # noqa: BLE001
            log.warning("Error de IA (intento %d): %s", intento + 1, e)
            time.sleep(5 * (intento + 1))
    return None


def _fmt(v):
    if isinstance(v, float):
        return round(v, 4)
    return v


def tesis_empresa(cfg: dict, r: dict) -> dict | None:
    campos = ["ticker", "nombre", "sector", "industria", "pais", "market_cap", "precio", "moneda",
              "roic", "roe", "margen_bruto", "margen_operativo", "margen_neto", "fcf_consistencia",
              "deuda_neta_ebitda", "crecimiento_ventas", "crecimiento_ganancias", "fcf_yield",
              "pe_forward", "pe_trailing", "ev_ebitda", "peg", "pb", "dividend_yield", "beta",
              "upside_analistas", "recomendacion", "max_52s", "min_52s", "score", "score_calidad",
              "score_valor", "alertas", "descripcion"]
    datos = json.dumps({k: _fmt(r.get(k)) for k in campos}, ensure_ascii=False, indent=0)
    noticias = "\n".join(f"- {n}" for n in r.get("noticias") or []) or "(sin titulares)"
    prompt = PLANTILLA_TESIS.format(datos=datos, noticias=noticias)
    return _llamar(cfg, cfg["ia"].get("modelo", "claude-sonnet-5-5"), prompt, 1200)


def comentario_diario(cfg: dict, filas: list[dict]) -> dict | None:
    campos = ["ticker", "nombre", "precio", "sma50", "sma200", "rsi", "macd_hist", "bb_pct",
              "dist_max52", "rend_1d", "rend_1s", "rend_1m", "rend_3m", "rend_ytd", "vol_rel",
              "cruce", "tendencia", "etiqueta", "puntaje_tecnico", "score_fundamental",
              "veredicto_semanal"]
    datos = "\n".join(json.dumps({k: _fmt(f.get(k)) for k in campos}, ensure_ascii=False) for f in filas)
    prompt = PLANTILLA_DIARIO.format(datos=datos)
    return _llamar(cfg, cfg["ia"].get("modelo_diario", "claude-sonnet-5-5"), prompt, 2500)
