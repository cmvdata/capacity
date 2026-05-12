"""
fix_2019.py
-----------
Descarga 2019 completo, normaliza nombres de columna (ES→EN),
y reconstruye el dataset 2019-2024 limpio.
"""
import sys
sys.path.insert(0, "src")

import pandas as pd
import numpy as np
from pathlib import Path
import requests
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

RAW_DIR = Path("data/raw")
PROCESSED_DIR = Path("data/processed")

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
BASE_URL = "https://apidatos.ree.es/es/datos"

# Mapa de nombres ES → EN (para normalizar columnas de REData en español)
COL_RENAME = {
    "eolica": "wind",
    "solar_fotovoltaica": "solar_pv",
    "solar_termica": "solar_thermal",
    "hidraulica": "hydro",
    "hidroeolica": "hydro_wind",
    "ciclo_combinado": "ccgt",
    "carbon": "coal",
    "nuclear": "nuclear",
    "turbina_de_gas": "gas_turbine",
    "turbina_de_vapor": "steam_turbine",
    "motores_diesel": "diesel",
    "cogeneracion": "cogen",
    "otras_renovables": "other_ren",
    "residuos_renovables": "waste_ren",
    "residuos_no_renovables": "waste_nonren",
    "fuel_+_gas": "fuel_+_gas",
    "generacion_total": "generacion_total",
    "generación_total": "generacion_total",
    # Nombres ya en inglés (2020-2024)
    "wind": "wind",
    "solar_pv": "solar_pv",
    "solar_thermal": "solar_thermal",
    "hydro": "hydro",
    "hydro_wind": "hydro_wind",
    "ccgt": "ccgt",
    "coal": "coal",
    "gas_turbine": "gas_turbine",
    "steam_turbine": "steam_turbine",
    "diesel": "diesel",
    "cogen": "cogen",
    "other_ren": "other_ren",
    "waste_ren": "waste_ren",
    "waste_nonren": "waste_nonren",
}


def normalize_columns(df):
    """Renombra columnas al estándar EN."""
    rename = {c: COL_RENAME.get(c, c) for c in df.columns}
    return df.rename(columns=rename)


def fetch_redata(widget, start, end, time_trunc="day"):
    url = f"{BASE_URL}/{widget}"
    params = {"start_date": start, "end_date": end, "time_trunc": time_trunc}
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    if r.status_code != 200:
        logger.warning(f"  {widget} → {r.status_code}")
        return {}
    return r.json()


def parse_generation(data):
    rows = []
    for item in data.get("included", []):
        tech_name = item.get("attributes", {}).get("title", "").lower()
        tech_name = (tech_name.replace(" ", "_").replace("/", "_")
                     .replace("á","a").replace("é","e").replace("ó","o")
                     .replace("ú","u").replace("ñ","n").replace("(","").replace(")",""))
        for val in item.get("attributes", {}).get("values", []):
            rows.append({"datetime": val["datetime"], "tech": tech_name, "value": val["value"]})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True).dt.tz_localize(None)
    df["date"] = df["datetime"].dt.normalize()
    pivot = df.pivot_table(index="date", columns="tech", values="value", aggfunc="sum")
    pivot.columns.name = None
    return pivot.reset_index()


# ─── 1. Descargar 2019 completo ───────────────────────────────────────────────
cache_2019 = RAW_DIR / "generation_daily_2019_full.parquet"
if cache_2019.exists():
    df_2019 = pd.read_parquet(cache_2019)
    logger.info(f"2019 cargado desde caché: {len(df_2019)} días")
else:
    logger.info("Descargando generación 2019 completo...")
    data_2019 = fetch_redata("generacion/estructura-generacion",
                              "2019-01-01T00:00", "2019-12-31T23:59", "day")
    df_2019 = parse_generation(data_2019)
    if not df_2019.empty:
        df_2019.to_parquet(cache_2019, index=False)
        logger.info(f"  2019 completo: {len(df_2019)} días")

# ─── 2. Normalizar y concatenar todos los años ────────────────────────────────
logger.info("Reconstruyendo dataset 2019-2024 con nombres normalizados...")

gen_frames = []
for year in range(2019, 2025):
    if year == 2019:
        f = RAW_DIR / "generation_daily_2019_full.parquet"
        if not f.exists():
            f = RAW_DIR / "generation_daily_2019_2019.parquet"
    else:
        f = RAW_DIR / f"generation_daily_{year}_{year}.parquet"
    if f.exists():
        df_yr = pd.read_parquet(f)
        if "datetime" in df_yr.columns:
            df_yr["date"] = pd.to_datetime(df_yr["datetime"]).dt.normalize()
            df_yr = df_yr.drop(columns=["datetime"])
        df_yr["date"] = pd.to_datetime(df_yr["date"])
        df_yr = normalize_columns(df_yr)
        gen_frames.append(df_yr)
        logger.info(f"  {year}: {len(df_yr)} días | cols: {[c for c in df_yr.columns if c != 'date'][:5]}")

df_gen = pd.concat(gen_frames, ignore_index=True)
df_gen["date"] = pd.to_datetime(df_gen["date"])
# Eliminar días fuera del rango
df_gen = df_gen[(df_gen["date"].dt.year >= 2019) & (df_gen["date"].dt.year <= 2024)]
# Eliminar duplicados por fecha
df_gen = df_gen.sort_values("date").drop_duplicates(subset=["date"])
logger.info(f"Generación total: {len(df_gen)} días")
logger.info(f"Columnas generación: {df_gen.columns.tolist()}")

# Capacidad instalada
cap_frames = []
for year in range(2019, 2025):
    f = RAW_DIR / f"installed_capacity_{year}_{year}.parquet"
    if f.exists():
        df_c = pd.read_parquet(f)
        if "datetime" in df_c.columns:
            df_c["date"] = pd.to_datetime(df_c["datetime"]).dt.normalize()
            df_c = df_c.drop(columns=["datetime"])
        df_c = normalize_columns(df_c)
        cap_frames.append(df_c)

df_cap = pd.concat(cap_frames, ignore_index=True)
df_cap["date"] = pd.to_datetime(df_cap["date"])
cap_cols = [c for c in df_cap.columns if c != "date"]
df_cap = df_cap.rename(columns={c: f"{c}_cap" for c in cap_cols})
date_range = pd.DataFrame({"date": pd.date_range("2019-01-01", "2024-12-31", freq="D")})
df_cap_daily = date_range.merge(df_cap, on="date", how="left").ffill()

# Precios MIBEL
df_mibel = pd.read_parquet("data/mibel_outputs/mibel_daily_prices.parquet")
df_mibel["date"] = pd.to_datetime(df_mibel["date"]).dt.normalize()

# Ensamblaje
df = df_gen.merge(df_mibel, on="date", how="left")
cap_cols_all = [c for c in df_cap_daily.columns if c != "date"]
df = df.merge(df_cap_daily[["date"] + cap_cols_all], on="date", how="left")

# Régimen
df["regime"] = "pre_crisis"
df.loc[df["date"] >= "2022-01-01", "regime"] = "excepcion_iberica"
df.loc[df["date"] >= "2024-01-01", "regime"] = "post_excepcion"

# Generación total y demanda neta
gen_total_col = "generacion_total"
if gen_total_col in df.columns:
    df["net_demand"] = (df[gen_total_col]
                        - df.get("wind", pd.Series(0, index=df.index))
                        - df.get("solar_pv", pd.Series(0, index=df.index))
                        - df.get("solar_thermal", pd.Series(0, index=df.index)))

# Renovables totales
ren_cols = ["wind", "solar_pv", "solar_thermal", "hydro", "other_ren", "waste_ren"]
df["renewables_total"] = df[[c for c in ren_cols if c in df.columns]].sum(axis=1)

out = PROCESSED_DIR / "cm_dataset_2019_2024_clean.parquet"
df.to_parquet(out, index=False)
logger.info(f"\nDataset limpio guardado: {len(df)} días | {len(df.columns)} cols → {out}")
logger.info(f"Días por año:\n{df.groupby(df['date'].dt.year).size().to_string()}")
logger.info(f"\nVerificación wind no-NaN: {df['wind'].notna().sum()} días")
logger.info(f"Verificación solar_pv no-NaN: {df['solar_pv'].notna().sum()} días")
logger.info(f"Verificación net_demand no-NaN: {df['net_demand'].notna().sum() if 'net_demand' in df.columns else 'N/A'}")
