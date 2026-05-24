"""
=============================================================================
 flight_monitor.py -- Monitor de precios de vuelos con alertas por Telegram
=============================================================================
Rutas monitorizadas:
  IDA    : Nuremberg (NUE) -> Bucharest (OTP)   -- Viernes 12 Jun 2026
  VUELTA : Bucharest (BBU) -> Memmingen (FMM)   -- Domingo 14 Jun 2026
Aerolineas : Ryanair - Wizz Air
Fuente     : SerpApi -- Google Flights
Alertas    : Bot de Telegram
Historial  : historial.json  (alimenta el dashboard chart.html)
Scheduler  : GitHub Actions (.github/workflows/monitor.yml)
=============================================================================
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# CREDENCIALES -- GitHub Secrets / variables de entorno locales
# ---------------------------------------------------------------------------

SERPAPI_KEY        = os.environ.get("SERPAPI_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

# ---------------------------------------------------------------------------
# RUTAS A MONITORIZAR
# ---------------------------------------------------------------------------

RUTAS = [
    {
        "id":          "ida_NUE_OTP",
        "label":       "IDA  NUE -> OTP",
        "origin":      "NUE",
        "destination": "OTP",
        "date":        "2026-06-12",
        "type":        "2",
        "airlines":    {"ryanair", "wizz air"},
    },
    {
        "id":          "vuelta_BBU_FMM",
        "label":       "VUELTA  BBU -> FMM",
        "origin":      "BBU",
        "destination": "FMM",
        "date":        "2026-06-14",
        "type":        "2",
        "airlines":    {"ryanair", "wizz air"},
    },
]

PRECIO_MAXIMO   = float(os.environ.get("MAX_PRICE", "0") or 0)
ARCHIVO_PRECIOS = Path("precios.json")
ARCHIVO_HIST    = Path("historial.json")

# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# VALIDACION
# ---------------------------------------------------------------------------

def validar_config() -> bool:
    errores = []
    if not SERPAPI_KEY:        errores.append("SERPAPI_KEY no definida")
    if not TELEGRAM_BOT_TOKEN: errores.append("TELEGRAM_BOT_TOKEN no definida")
    if not TELEGRAM_CHAT_ID:   errores.append("TELEGRAM_CHAT_ID no definida")
    for e in errores:
        log.error("Config incompleta: %s", e)
    return len(errores) == 0


# ---------------------------------------------------------------------------
# ESTADO LOCAL
# ---------------------------------------------------------------------------

def cargar_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("No se pudo leer %s: %s", path, exc)
    return {}


def guardar_json(path: Path, datos: dict) -> None:
    try:
        path.write_text(json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info("Guardado: %s", path)
    except OSError as exc:
        log.error("Error guardando %s: %s", path, exc)


def registrar_historial(clave: str, airline: str, ruta_label: str, precio: float) -> None:
    historial = cargar_json(ARCHIVO_HIST)
    if clave not in historial:
        historial[clave] = []
    historial[clave].append({
        "ts":      datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "airline": airline,
        "route":   ruta_label,
        "price":   precio,
    })
    guardar_json(ARCHIVO_HIST, historial)


# ---------------------------------------------------------------------------
# CONSULTA SERPAPI
# ---------------------------------------------------------------------------

def consultar_ruta(ruta: dict) -> list:
    params = {
        "engine":        "google_flights",
        "departure_id":  ruta["origin"],
        "arrival_id":    ruta["destination"],
        "outbound_date": ruta["date"],
        "type":          ruta["type"],
        "currency":      "EUR",
        "hl":            "es",
        "stops":         "1",
        "api_key":       SERPAPI_KEY,
    }

    try:
        resp = requests.get("https://serpapi.com/search.json", params=params, timeout=30)
        resp.raise_for_status()
        datos = resp.json()
    except requests.exceptions.Timeout:
        log.error("[%s] Timeout con SerpApi.", ruta["id"])
        return []
    except requests.exceptions.HTTPError as exc:
        log.error("[%s] HTTP error SerpApi: %s", ruta["id"], exc)
        return []
    except requests.exceptions.RequestException as exc:
        log.error("[%s] Red error SerpApi: %s", ruta["id"], exc)
        return []
    except ValueError:
        log.error("[%s] Respuesta no-JSON.", ruta["id"])
        return []

    todos = datos.get("best_flights", []) + datos.get("other_flights", [])
    if not todos:
        log.warning("[%s] Sin resultados de SerpApi.", ruta["id"])
        return []

    resultados = []
    for vuelo in todos:
        flights = vuelo.get("flights", [])
        if not flights:
            continue
        airline_raw = flights[0].get("airline", "").lower().strip()
        if not any(obj in airline_raw for obj in ruta["airlines"]):
            continue
        precio = vuelo.get("price")
        if precio is None:
            continue

        if "ryanair" in airline_raw:
            display = "Ryanair"
        elif "wizz" in airline_raw:
            display = "Wizz Air"
        else:
            display = airline_raw.title()

        token = vuelo.get("booking_token", "")
        deep_link = (
            "https://www.google.com/travel/flights?tfs=" + token
            if token else ""
        )

        resultados.append({
            "airline":   display,
            "price":     float(precio),
            "deep_link": deep_link,
        })

    log.info("[%s] %d vuelo(s) encontrado(s).", ruta["id"], len(resultados))
    return resultados


# ---------------------------------------------------------------------------
# TELEGRAM
# Nota: los emojis van como escape Unicode para evitar problemas de encoding
# ---------------------------------------------------------------------------

# Prefijos de aerolinea en el mensaje
PREFIJOS = {
    "Ryanair":  "\U0001f7e1",   # circulo amarillo
    "Wizz Air": "\U0001f49c",   # corazon morado
}


def sanitizar(texto: str) -> str:
    """Escapa caracteres especiales de HTML que rompen parse_mode HTML."""
    return (texto
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


def enviar_telegram(texto: str) -> bool:
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage"
    payload = {
        "chat_id":                  TELEGRAM_CHAT_ID,
        "text":                     texto,
        "parse_mode":               "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        log.info("Telegram OK.")
        return True
    except requests.exceptions.HTTPError as exc:
        log.error("HTTP Telegram: %s -- %s", exc, resp.text)
    except requests.exceptions.RequestException as exc:
        log.error("Red Telegram: %s", exc)
    return False


def construir_mensaje(ruta, airline, precio_anterior, precio_nuevo, deep_link):
    prefijo   = PREFIJOS.get(airline, "\u2708")
    fecha_str = datetime.strptime(ruta["date"], "%Y-%m-%d").strftime("%a %d %b %Y")
    ahora     = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M")

    # Enlace limpio -- solo si existe y es una URL basica
    if deep_link and deep_link.startswith("https://"):
        enlace = '\n\n<a href="' + deep_link[:200] + '">[Reservar en Google Flights]</a>'
    else:
        enlace = ""

    if precio_anterior is None:
        titulo = "\U0001f195 <b>Vuelo registrado</b>"
        cuerpo = "Precio inicial: <b>" + str(int(precio_nuevo)) + " EUR</b>"
    elif precio_nuevo < precio_anterior:
        diff   = precio_anterior - precio_nuevo
        titulo = "\U0001f4c9 <b>El precio ha BAJADO</b>"
        cuerpo = (
            "Antes: <s>" + str(int(precio_anterior)) + " EUR</s>\n"
            "Ahora: <b>" + str(int(precio_nuevo)) + " EUR</b>  "
            "(ahorras <b>" + str(int(diff)) + " EUR</b>)"
        )
    else:
        diff   = precio_nuevo - precio_anterior
        titulo = "\U0001f4c8 El precio ha subido"
        cuerpo = (
            "Antes: " + str(int(precio_anterior)) + " EUR\n"
            "Ahora: <b>" + str(int(precio_nuevo)) + " EUR</b>  "
            "(+" + str(int(diff)) + " EUR)"
        )

    return (
        titulo + "\n\n"
        + prefijo + " <b>" + sanitizar(airline) + "</b>\n"
        + "\U0001f5fa <b>" + sanitizar(ruta["label"]) + "</b>\n"
        + "\U0001f4c5 " + fecha_str + "\n\n"
        + cuerpo
        + enlace + "\n\n"
        + "<i>" + ahora + " UTC</i>"
    )


# ---------------------------------------------------------------------------
# LOGICA PRINCIPAL
# ---------------------------------------------------------------------------

def monitorizar_ruta(ruta: dict, precios: dict) -> bool:
    log.info("-- %s  %s -> %s  (%s)",
             ruta["id"], ruta["origin"], ruta["destination"], ruta["date"])
    vuelos = consultar_ruta(ruta)
    if not vuelos:
        return False

    mejor = {}
    for v in vuelos:
        k = v["airline"]
        if k not in mejor or v["price"] < mejor[k]["price"]:
            mejor[k] = v

    hubo_cambios = False
    for airline, vuelo in mejor.items():
        precio_nuevo = vuelo["price"]
        clave        = ruta["id"] + "_" + airline.lower().replace(" ", "_")
        precio_ant   = precios.get(clave)

        registrar_historial(clave, airline, ruta["label"], precio_nuevo)

        if PRECIO_MAXIMO > 0 and precio_nuevo > PRECIO_MAXIMO:
            log.info("[%s][%s] %.0f EUR > maximo %.0f EUR -- sin alerta.",
                     ruta["id"], airline, precio_nuevo, PRECIO_MAXIMO)
            precios[clave] = precio_nuevo
            hubo_cambios   = True
            continue

        if precio_ant is None or abs(precio_nuevo - precio_ant) > 0.01:
            log.info("[%s][%s] Cambio: %s -> %.0f EUR",
                     ruta["id"], airline,
                     str(int(precio_ant)) if precio_ant else "--",
                     precio_nuevo)
            msg = construir_mensaje(
                ruta, airline, precio_ant, precio_nuevo, vuelo["deep_link"]
            )
            if enviar_telegram(msg):
                precios[clave] = precio_nuevo
                hubo_cambios   = True
        else:
            log.info("[%s][%s] Sin cambios: %.0f EUR.",
                     ruta["id"], airline, precio_nuevo)

    return hubo_cambios


def monitorizar_todo() -> None:
    log.info("=" * 55)
    log.info("Flight Monitor -- %s UTC",
             datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
    precios      = cargar_json(ARCHIVO_PRECIOS)
    hubo_cambios = False
    for ruta in RUTAS:
        if monitorizar_ruta(ruta, precios):
            hubo_cambios = True
    if hubo_cambios:
        guardar_json(ARCHIVO_PRECIOS, precios)
    log.info("=" * 55)


if __name__ == "__main__":
    if not validar_config():
        sys.exit(1)
    monitorizar_todo()
