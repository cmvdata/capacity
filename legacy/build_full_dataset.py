"""
build_full_dataset.py
---------------------
Construye el dataset completo 2019-2024 a partir de los parquets descargados
y lo guarda como cm_dataset_2019_2024.parquet.
"""
import sys
sys.path.insert(0, "src")

import pandas as pd
import numpy as np
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

PROCESSED_DIR = Path("data/processed")
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

# 1. Generación diaria completa
gen_file = PROCESSED_DIR / "generation_daily_2019_2024.parquet"
df_gen = pd.read_parquet(gen_file)
df_gen["date"] = pd.to_datetime(df_gen["datetime"]).dt.normalize()
df_gen = df_gen.drop(columns=["datetime"])
logger.info(f"Generación: {len(df_gen)} días | cols: {df_gen.columns.tolist()[:5]}...")

# 2. Capacidad instalada mensual → forward-fill a diario
cap_file = PROCESSED_DIR / "installed_capacity_2019_2024.parquet"

# Si no existe el parquet consolidado, concatenar los anuales
if not cap_file.exists():
    cap_frames = []
    for year in range(2019, 2025):
        f = Path(f"data/raw/installed_capacity_{year}_{year}.parquet")
        if f.exists():
            cap_frames.append(pd.read_parquet(f))
    if cap_frames:
        df_cap_all = pd.concat(cap_frames, ignore_index=True)
        df_cap_all.to_parquet(cap_file, index=False)
    else:
        df_cap_all = pd.DataFrame()
else:
    df_cap_all = pd.read_parquet(cap_file)

if not df_cap_all.empty:
    df_cap_all["date"] = pd.to_datetime(df_cap_all["datetime"]).dt.normalize()
    df_cap_all = df_cap_all.drop(columns=["datetime"])
    # Renombrar columnas de capacidad con sufijo _cap
    cap_cols = [c for c in df_cap_all.columns if c != "date"]
    df_cap_all = df_cap_all.rename(columns={c: f"{c}_cap" for c in cap_cols})
    # Forward-fill a diario
    date_range = pd.DataFrame({"date": pd.date_range("2019-01-01", "2024-12-31", freq="D")})
    df_cap_daily = date_range.merge(df_cap_all, on="date", how="left").ffill()
    logger.info(f"Capacidad: {len(df_cap_daily)} días | cols: {df_cap_daily.columns.tolist()[:5]}...")
else:
    df_cap_daily = pd.DataFrame()

# 3. Precios MIBEL (ya disponibles)
mibel_path = Path("data/mibel_outputs/mibel_daily_prices.parquet")
df_mibel = pd.read_parquet(mibel_path)
df_mibel["date"] = pd.to_datetime(df_mibel["date"]).dt.normalize()
logger.info(f"Precios MIBEL: {len(df_mibel)} días")

# 4. Ensamblaje
df = df_gen.copy()
df = df.merge(df_mibel, on="date", how="left")
if not df_cap_daily.empty:
    cap_cols = [c for c in df_cap_daily.columns if c != "date"]
    df = df.merge(df_cap_daily[["date"] + cap_cols], on="date", how="left")

# 5. Régimen de mercado
df["regime"] = "pre_crisis"
df.loc[df["date"] >= "2022-01-01", "regime"] = "excepcion_iberica"
df.loc[df["date"] >= "2024-01-01", "regime"] = "post_excepcion"

# 6. Total renovables
ren_cols = ["wind", "solar_pv", "solar_thermal", "hydro", "other_ren", "waste_ren"]
df["renewables_total"] = df[[c for c in ren_cols if c in df.columns]].sum(axis=1)

out = PROCESSED_DIR / "cm_dataset_2019_2024.parquet"
df.to_parquet(out, index=False)
logger.info(f"Dataset completo guardado: {len(df)} días | {len(df.columns)} columnas → {out}")
logger.info(f"Columnas: {df.columns.tolist()}")
