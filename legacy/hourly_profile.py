"""
hourly_profile.py
-----------------
Descarga demanda horaria de REData (2020-2024) y calcula la distribución
horaria y mensual de las Top-100 horas de mayor demanda.
Esto valida si el ELCC solar de 0.10 es real o artefacto.
"""
import sys
sys.path.insert(0, "src")

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import requests
from pathlib import Path
import logging
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

RAW_DIR = Path("data/raw")
FIGURES_DIR = Path("figures")
RESULTS_DIR = Path("results")
RAW_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
BASE_URL = "https://apidatos.ree.es/es/datos"


def fetch_demand_hourly_month(year, month):
    """Descarga demanda horaria de un mes concreto."""
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    start = f"{year}-{month:02d}-01T00:00"
    end = f"{year}-{month:02d}-{last_day:02d}T23:59"
    url = f"{BASE_URL}/demanda/demanda-tiempo-real"
    params = {"start_date": start, "end_date": end, "time_trunc": "hour"}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=30)
        if r.status_code != 200:
            return pd.DataFrame()
        data = r.json()
        rows = []
        for item in data.get("included", []):
            title = item.get("attributes", {}).get("title", "")
            if "Demanda" in title or "demanda" in title.lower():
                for val in item.get("attributes", {}).get("values", []):
                    rows.append({"datetime": val["datetime"], "demand": val["value"]})
        if not rows:
            # Tomar el primer item disponible
            for item in data.get("included", []):
                for val in item.get("attributes", {}).get("values", []):
                    rows.append({"datetime": val["datetime"], "demand": val["value"]})
                if rows:
                    break
        if rows:
            df = pd.DataFrame(rows)
            df["datetime"] = pd.to_datetime(df["datetime"], utc=True).dt.tz_localize(None)
            # Resamplear de 5min a hora
            df = df.set_index("datetime").resample("h").mean().reset_index()
            return df
    except Exception as e:
        logger.warning(f"  Error {year}-{month:02d}: {e}")
    return pd.DataFrame()


# Descargar año 2023 completo (representativo, sin Excepción Ibérica al final)
cache_file = RAW_DIR / "demand_hourly_2023_full.parquet"
if cache_file.exists():
    logger.info("Cargando demanda 2023 desde caché...")
    df_demand = pd.read_parquet(cache_file)
else:
    logger.info("Descargando demanda horaria 2023 mes a mes...")
    frames = []
    for month in range(1, 13):
        logger.info(f"  Mes {month:02d}/2023...")
        df_m = fetch_demand_hourly_month(2023, month)
        if not df_m.empty:
            frames.append(df_m)
            logger.info(f"    {len(df_m)} horas")
        time.sleep(0.5)
    if frames:
        df_demand = pd.concat(frames, ignore_index=True)
        df_demand.to_parquet(cache_file, index=False)
        logger.info(f"Demanda 2023: {len(df_demand)} horas")
    else:
        logger.error("No se pudo descargar demanda")
        exit(1)

df_demand["datetime"] = pd.to_datetime(df_demand["datetime"])
df_demand["hour"] = df_demand["datetime"].dt.hour
df_demand["month"] = df_demand["datetime"].dt.month

# Top-100 horas de mayor demanda
top100 = df_demand.nlargest(100, "demand")
hour_dist = top100["hour"].value_counts().sort_index()
month_dist = top100["month"].value_counts().sort_index()

logger.info(f"\nDistribución HORARIA del Top-100 (2023):\n{hour_dist.to_string()}")
logger.info(f"\nDistribución MENSUAL del Top-100 (2023):\n{month_dist.to_string()}")

# Guardar
hour_dist.to_csv(RESULTS_DIR / "block_b_top100_hour_distribution.csv", header=["count"])
month_dist.to_csv(RESULTS_DIR / "block_b_top100_month_distribution.csv", header=["count"])

# ─── FIGURA ──────────────────────────────────────────────────────────────────
fig, axes = plt.subplots(1, 2, figsize=(14, 5))

# Distribución horaria
hours = list(range(24))
hour_vals = [hour_dist.get(h, 0) for h in hours]
colors_h = ["#e74c3c" if v == max(hour_vals) else "#3498db" for v in hour_vals]
axes[0].bar(hours, hour_vals, color=colors_h, alpha=0.85, edgecolor="white")
for h, v in zip(hours, hour_vals):
    if v > 0:
        axes[0].text(h, v + 0.2, str(v), ha="center", va="bottom", fontsize=8)
axes[0].set_xlabel("Hora del día (UTC+1)", fontsize=11)
axes[0].set_ylabel("Nº horas en Top-100", fontsize=11)
axes[0].set_title("Distribución horaria del Top-100\nhoras de mayor demanda (2023)", fontsize=11, fontweight="bold")
axes[0].set_xticks(range(0, 24, 2))
axes[0].grid(axis="y", alpha=0.3)

# Distribución mensual
month_names = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
month_vals = [month_dist.get(m, 0) for m in range(1, 13)]
colors_m = ["#e74c3c" if v == max(month_vals) else "#e67e22" if v >= sorted(month_vals)[-3] else "#3498db" for v in month_vals]
axes[1].bar(month_names, month_vals, color=colors_m, alpha=0.85, edgecolor="white")
for mn, v in zip(month_names, month_vals):
    if v > 0:
        axes[1].text(month_names.index(mn), v + 0.2, str(v), ha="center", va="bottom", fontsize=9)
axes[1].set_xlabel("Mes", fontsize=11)
axes[1].set_ylabel("Nº horas en Top-100", fontsize=11)
axes[1].set_title("Distribución mensual del Top-100\nhoras de mayor demanda (2023)", fontsize=11, fontweight="bold")
axes[1].grid(axis="y", alpha=0.3)

plt.suptitle("Perfil de las horas críticas de adecuación — España 2023\n(Top-100 horas de mayor demanda real)",
             fontsize=12, fontweight="bold")
plt.tight_layout()
out = FIGURES_DIR / "block_b_6_hourly_profile_top100.png"
plt.savefig(out, dpi=150, bbox_inches="tight")
plt.close()
logger.info(f"\nFigura guardada: {out}")

# ─── INTERPRETACIÓN ──────────────────────────────────────────────────────────
peak_hours = [h for h, v in zip(hours, hour_vals) if v >= 5]
peak_months = [m for m, v in enumerate(month_vals, 1) if v >= 5]
solar_hours = [h for h in peak_hours if 9 <= h <= 17]
logger.info(f"\nHoras pico (≥5 ocurrencias): {peak_hours}")
logger.info(f"De esas, horas con sol (9h-17h): {solar_hours}")
logger.info(f"Meses pico (≥5 ocurrencias): {peak_months}")
if solar_hours:
    pct_solar = len(solar_hours) / len(peak_hours) * 100
    logger.info(f"→ {pct_solar:.0f}% de las horas críticas tienen potencial solar")
    logger.info("→ El ELCC solar de 0.10 es REAL: hay horas críticas con sol disponible")
else:
    logger.info("→ Las horas críticas son nocturnas: el ELCC solar debería ser ~0")
    logger.info("→ Si sale 0.10, revisar el cálculo de demanda neta")
