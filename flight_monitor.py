"""
=============================================================================
 flight_monitor.py -- Monitor de precios de vuelos con alertas por Telegram
=============================================================================
Rutas monitorizadas:
 IDA    : Nuremberg (NUE) -> Bucharest (OTP)   -- Viernes 12 Jun 2026
 Aerolinea: Wizz Air
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
# CREDENCIALES
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
        "airlines":    {"wizz air"}, # Filtro estricto para Wizz Air
    },
]

PRECIO_MAXIMO   = float(os.environ.get("MAX_PRICE", "0") or 0)
ARCHIVO_PRECIOS = Path("precios.json")
ARCHIVO_HIST    = Path("historial.json")

# ... (El resto de las funciones: logging, cargar_json, registrar_historial, etc., permanecen iguales)

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
    # ... (Resto de la lógica de consulta igual)
    # El filtro funcionará correctamente con {"wizz air"}
    
    # ... [Resto del script sin cambios para mantener la integridad]
