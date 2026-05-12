"""
download_full.py
----------------
Descarga el dataset completo 2019-2024 de REData año por año
y construye el dataset ensamblado final.
"""
import sys
sys.path.insert(0, "src")

import pandas as pd
from pathlib import Path
from redata_downloader import download_generation_daily, download_installed_capacity
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

RAW_DIR = Path("data/raw")
PROCESSED_DIR = Path("data/processed")
RAW_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

years = range(2019, 2025)
gen_frames = []
cap_frames = []

for year in years:
    start = f"{year}-01-01T00:00"
    end = f"{year}-12-31T23:59"
    logger.info(f"Descargando {year}...")

    df_gen = download_generation_daily(start, end, RAW_DIR)
    if not df_gen.empty:
        gen_frames.append(df_gen)
        logger.info(f"  Generación {year}: {len(df_gen)} días")

    df_cap = download_installed_capacity(start, end, RAW_DIR)
    if not df_cap.empty:
        cap_frames.append(df_cap)
        logger.info(f"  Capacidad {year}: {len(df_cap)} registros")

# Concatenar
if gen_frames:
    df_gen_all = pd.concat(gen_frames, ignore_index=True)
    df_gen_all.to_parquet(PROCESSED_DIR / "generation_daily_2019_2024.parquet", index=False)
    logger.info(f"Generación total: {len(df_gen_all)} días")

if cap_frames:
    df_cap_all = pd.concat(cap_frames, ignore_index=True)
    df_cap_all.to_parquet(PROCESSED_DIR / "installed_capacity_2019_2024.parquet", index=False)
    logger.info(f"Capacidad total: {len(df_cap_all)} registros")

logger.info("Descarga completa.")
