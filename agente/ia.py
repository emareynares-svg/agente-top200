"""Capa de IA: Claude escribe las tesis mensuales y la evaluación semanal del momento de entrada."""
from __future__ import annotations

import json
import os
import re
import threading
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

PLANTILLA_SEMANAL = """Estas son las empresas elegidas esta semana entre las 50 mejores del mes por
fundamentales. Para cada una tenés el análisis técnico al cierre (precio, SMA50/200, RSI(14), histograma
MACD, %B de Bollinger, distancia al máximo de 52 semanas, rendimientos, etiqueta automática) y un resumen
de la tesis fundamental del mes (veredicto, valuación, qué vigilar). "motivo" indica por qué entró esta semana.
"noticias_semana" trae los titulares de los últimos días (fecha y título; puede estar vacío).

{datos}

La inversora compra para mantener años; el técnico sirve solo para elegir MEJORES MOMENTOS DE ENTRADA.
Cruzá tesis y técnico: una empresa con buena tesis en retroceso ordenado puede ser "Entrar ahora" o
"Entrar de a poco"; una sobreextendida, "Esperar"; una con tesis floja o tendencia rota, "Evitar".
NOTICIAS: si algún titular afecta de forma material la tesis del mes (resultados o guidance muy distintos
a lo esperado, regulación, juicios, fusiones/adquisiciones, cambio de CEO, problemas contables, pérdida de
un cliente clave), completá "alerta_tesis" explicando en 1-2 frases qué cambia y si refuerza o debilita la
tesis, y tenelo en cuenta en el "momento". Si no hay nada material, usá null. Basate SOLO en los titulares
provistos: no inventes noticias ni supongas el contenido más allá del título.
Devolvé este JSON:
{{
 "titular": "una frase con lo más importante de la semana",
 "resumen": "3-5 frases sobre el panorama del grupo",
 "por_empresa": {{
   "TICKER": {{"momento": "Entrar ahora | Entrar de a poco | Esperar | Evitar",
              "comentario": "2-3 frases que crucen tesis y técnico",
              "nivel": "precio o condición concreta a vigilar para entrar (ej. 'cerca de la SMA50, ~185')",
              "alerta_tesis": null}}
 }}
}}
Incluí todas las empresas en por_empresa."""

_CLIENTE = None


def disponible(cfg: dict) -> bool:
    return bool(cfg.get("ia", {}).get("activada")) and bool(os.environ.get("ANTHROPIC_API_KEY"))


def _cliente():
    global _CLIENTE
    if _CLIENTE is None:
        import anthropic
        _CLIENTE = anthropic.Anthropic(max_retries=2)
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


# Control de gasto y fallas (compartido entre hilos)
_LOCK = threading.Lock()
_ESTADO = {"fallas_seguidas": 0, "cortado": False, "tokens_in": 0, "tokens_out": 0, "llamadas": 0,
           "sin_pensamiento": False, "por_modelo": {}}
MAX_FALLAS_SEGUIDAS = 3

# Precios por defecto (US$ por millón de tokens: entrada, salida). Se pueden pisar en config.yaml → ia.precios
PRECIOS = {"claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0), "claude-fable-5-2": (8.0, 40.0)}


def _precio(cfg: dict, modelo: str) -> tuple[float, float]:
    ia_cfg = cfg.get("ia", {})
    p = (ia_cfg.get("precios") or {}).get(modelo) or PRECIOS.get(modelo)
    if p:
        return float(p[0]), float(p[1])
    return float(ia_cfg.get("precio_entrada", 2)), float(ia_cfg.get("precio_salida", 10))


def uso(cfg: dict) -> dict:
    """Consumo de IA de esta ejecución, con costo estimado por modelo."""
    modelos, total = [], 0.0
    for m, d in _ESTADO["por_modelo"].items():
        pin, pout = _precio(cfg, m)
        c = (d["in"] * pin + d["out"] * pout) / 1e6
        total += c
        modelos.append({"modelo": m, "llamadas": d["llamadas"], "tokens_in": d["in"],
                        "tokens_out": d["out"], "costo": round(c, 4)})
    return {"llamadas": _ESTADO["llamadas"], "tokens_in": _ESTADO["tokens_in"],
            "tokens_out": _ESTADO["tokens_out"], "costo": round(total, 4),
            "cortada": _ESTADO["cortado"], "por_modelo": modelos}


def resumen_uso(cfg: dict) -> str:
    u = uso(cfg)
    return (f"IA: {u['llamadas']} llamadas, {u['tokens_in']:,} tokens de entrada, "
            f"{u['tokens_out']:,} de salida, costo estimado US$ {u['costo']:.2f}"
            + (" (CORTADA por fallas repetidas)" if u["cortada"] else ""))


def _llamar(cfg: dict, modelo: str, prompt: str, max_tokens: int) -> dict | None:
    """Una llamada a Claude que devuelve JSON. Tiene un 'disyuntor': si fallan
    MAX_FALLAS_SEGUIDAS llamadas seguidas, deja de llamar en esta corrida para no gastar crédito."""
    if _ESTADO["cortado"]:
        return None
    ia_cfg = cfg.get("ia", {})
    sistema = SISTEMA.format(perfil=cfg.get("perfil_inversor", "").strip())
    for _intento in range(2):
        if _ESTADO["cortado"]:
            return None
        extra = {"output_config": {"effort": ia_cfg.get("esfuerzo", "low")}}
        pens = ia_cfg.get("pensamiento")
        if pens and not _ESTADO["sin_pensamiento"]:
            extra["thinking"] = {"type": pens}
        try:
            resp = _cliente().messages.create(
                model=modelo, max_tokens=max_tokens, system=sistema,
                messages=[{"role": "user", "content": prompt}], extra_body=extra,
            )
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "thinking" in msg.lower() and not _ESTADO["sin_pensamiento"]:
                log.warning("El modelo no acepta thinking=%s; sigo sin ese parámetro.", pens)
                _ESTADO["sin_pensamiento"] = True
                continue
            log.warning("Error de IA: %s", msg[:300])
            _registrar_falla()
            if any(x in msg.lower() for x in ("credit", "authentication", "api key", "not_found", "permission")):
                _cortar(f"error no recuperable: {msg[:200]}")
                return None
            time.sleep(5)
            continue
        with _LOCK:
            tin = getattr(resp.usage, "input_tokens", 0) or 0
            tout = getattr(resp.usage, "output_tokens", 0) or 0
            _ESTADO["llamadas"] += 1
            _ESTADO["tokens_in"] += tin
            _ESTADO["tokens_out"] += tout
            pm = _ESTADO["por_modelo"].setdefault(modelo, {"llamadas": 0, "in": 0, "out": 0})
            pm["llamadas"] += 1
            pm["in"] += tin
            pm["out"] += tout
        texto = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        try:
            datos = _extraer_json(texto)
        except (json.JSONDecodeError, ValueError):
            log.warning("Respuesta no era JSON válido (stop_reason=%s, %d caracteres de texto)",
                        getattr(resp, "stop_reason", "?"), len(texto))
            _registrar_falla()
            if getattr(resp, "stop_reason", "") == "max_tokens":
                max_tokens *= 2  # se quedó corto: un único reintento con más margen
                continue
            return None
        with _LOCK:
            _ESTADO["fallas_seguidas"] = 0
        return datos
    return None


def _registrar_falla() -> None:
    with _LOCK:
        _ESTADO["fallas_seguidas"] += 1
        if _ESTADO["fallas_seguidas"] >= MAX_FALLAS_SEGUIDAS:
            _cortar(f"{MAX_FALLAS_SEGUIDAS} fallas seguidas")


def _cortar(motivo: str) -> None:
    if not _ESTADO["cortado"]:
        log.error("IA desactivada para esta corrida (%s). Revisá crédito, API key o modelo.", motivo)
    _ESTADO["cortado"] = True


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
    return _llamar(cfg, cfg["ia"].get("modelo", "claude-sonnet-5-5"), prompt, int(cfg["ia"].get("max_tokens_tesis", 4000)))


def comentario_semanal(cfg: dict, filas: list[dict]) -> dict | None:
    campos = ["ticker", "nombre", "motivo", "precio", "sma50", "sma200", "rsi", "macd_hist", "bb_pct",
              "dist_max52", "rend_1s", "rend_1m", "rend_3m", "rend_ytd", "cruce", "tendencia", "etiqueta",
              "puntaje_tecnico", "score_fundamental", "veredicto_mensual", "valuacion_mensual",
              "tesis_mensual", "vigilar_mensual", "noticias_semana"]
    datos = "\n".join(json.dumps({k: _fmt(f.get(k)) for k in campos}, ensure_ascii=False) for f in filas)
    prompt = PLANTILLA_SEMANAL.format(datos=datos)
    return _llamar(cfg, cfg["ia"].get("modelo_semanal", "claude-sonnet-5-5"), prompt,
                   int(cfg["ia"].get("max_tokens_semanal", 10000)))
