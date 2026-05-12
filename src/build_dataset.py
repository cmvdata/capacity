"""
build_dataset.py
----------------
Descarga y ensambla el dataset completo del proyecto cm-spain-renewables.

Fuentes:
  - REData (REE): generación diaria por tecnología, demanda horaria
  - OMIE: precios day-ahead España y Portugal (reutilizado del MIBEL)
  - JAO SWE CCR: NTC ES↔FR
  - Yahoo Finance: TTF gas, CO₂ EUA

Outputs:
  data/processed/cm_dataset_YYYY_YYYY.parquet  → dataset diario
  data/processed/demand_hourly_YYYY_YYYY.parquet → demanda horaria

Uso:
  python src/build_dataset.py --start 2019-01-01 --end 2024-12-31
"""

import argparse
import logging
import sys
from pathlib import Path
import pandas as pd
import numpy as np

# Añadir src/ al path
sys.path.insert(0, str(Path(__file__).parent))
from redata_downloader import (
    download_generation_daily,
    download_demand_5min,
    download_installed_capacity,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [BUILD] %(message)s")
logger = logging.getLogger(__name__)

RAW_DIR = Path("data/raw")
PROCESSED_DIR = Path("data/processed")


# ─── OMIE ────────────────────────────────────────────────────────────────────

def download_omie_prices(start: str, end: str) -> pd.DataFrame:
    """
    Descarga precios day-ahead de España y Portugal desde OMIE.
    Devuelve DataFrame diario con precio medio ES y PT.
    """
    import requests
    from datetime import datetime, timedelta

    logger.info(f"Descargando precios OMIE {start} → {end}")
    cache = RAW_DIR / f"omie_prices_{start[:4]}_{end[:4]}.parquet"
    if cache.exists():
        logger.info(f"  Cargando desde caché: {cache}")
        return pd.read_parquet(cache)

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.omie.es/",
    }
    URL = "https://www.omie.es/es/file-download?parents=marginalpdbc&filename={fn}"

    records = []
    s = datetime.fromisoformat(start)
    e = datetime.fromisoformat(end)
    current = s
    errors = 0

    while current <= e:
        fn = f"marginalpdbc_{current.strftime('%Y%m%d')}.1"
        try:
            r = requests.get(URL.format(fn=fn), headers=HEADERS, timeout=20)
            if r.status_code == 200:
                content = r.content.decode("latin-1")
                lines = [l for l in content.splitlines()
                         if l.strip() and not l.startswith("*")]
                for line in lines:
                    parts = [p.strip() for p in line.split(";") if p.strip()]
                    if len(parts) >= 5:
                        try:
                            year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
                            hour = int(parts[3])
                            price_es = float(parts[4].replace(",", "."))
                            price_pt = float(parts[5].replace(",", ".")) if len(parts) > 5 else price_es
                            records.append({
                                "date": pd.Timestamp(year, month, day),
                                "hour": hour,
                                "price_es": price_es,
                                "price_pt": price_pt,
                            })
                        except (ValueError, IndexError):
                            continue
        except Exception:
            errors += 1

        current += timedelta(days=1)
        if errors > 10:
            logger.warning("Demasiados errores OMIE, puede haber bloqueo de IP")
            break

    if not records:
        logger.warning("No se obtuvieron precios OMIE")
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df_daily = df.groupby("date").agg(
        price_es_mean=("price_es", "mean"),
        price_pt_mean=("price_pt", "mean"),
        price_es_max=("price_es", "max"),
        price_es_min=("price_es", "min"),
        spread_es_pt_mean=("price_es", lambda x: x.mean() - df.loc[x.index, "price_pt"].mean()),
    ).reset_index()

    df_daily.to_parquet(cache, index=False)
    logger.info(f"OMIE guardado: {len(df_daily)} días")
    return df_daily


# ─── JAO ─────────────────────────────────────────────────────────────────────

def download_jao_ntc(start: str, end: str) -> pd.DataFrame:
    """
    Descarga NTC ES↔FR desde JAO SWE CCR API.
    Disponible desde septiembre 2022.
    """
    import requests
    from datetime import datetime, timedelta

    logger.info(f"Descargando NTC JAO {start} → {end}")
    cache = RAW_DIR / f"jao_ntc_{start[:4]}_{end[:4]}.parquet"
    if cache.exists():
        logger.info(f"  Cargando desde caché: {cache}")
        return pd.read_parquet(cache)

    JAO_URL = "https://publicationtool.jao.eu/swe/api/data/finalNtc"
    HEADERS_JAO = {"Accept": "application/json"}

    records = []
    s = max(datetime.fromisoformat(start), datetime(2022, 9, 1))
    e = datetime.fromisoformat(end)

    # Chunks de 30 días
    current = s
    while current <= e:
        chunk_end = min(current + timedelta(days=30), e)
        params = {
            "dateTimeFrom": current.strftime("%Y-%m-%dT00:00:00.000Z"),
            "dateTimeTo": chunk_end.strftime("%Y-%m-%dT23:59:59.000Z"),
        }
        try:
            r = requests.get(JAO_URL, params=params, headers=HEADERS_JAO, timeout=30)
            if r.status_code == 200:
                items = r.json().get("data", [])
                for item in items:
                    dt_field = item.get("dateTimeUtc") or item.get("datetime") or item.get("date")
                    ntc_es_fr = (item.get("finalNtc_ES_FR") or item.get("ntc_ES_FR")
                                 or item.get("ntcEsFr"))
                    ntc_fr_es = (item.get("finalNtc_FR_ES") or item.get("ntc_FR_ES")
                                 or item.get("ntcFrEs"))
                    if dt_field and ntc_es_fr is not None:
                        records.append({
                            "datetime": pd.to_datetime(dt_field, utc=True).tz_localize(None),
                            "ntc_es_fr": float(ntc_es_fr),
                            "ntc_fr_es": float(ntc_fr_es) if ntc_fr_es is not None else np.nan,
                        })
            else:
                logger.warning(f"JAO HTTP {r.status_code} para {current.date()}")
        except Exception as ex:
            logger.warning(f"JAO error: {ex}")

        current = chunk_end + timedelta(hours=1)

    if not records:
        logger.warning("No se obtuvieron datos JAO (disponible desde sep 2022)")
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df = df.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)
    df_daily = df.groupby(df["datetime"].dt.date).agg(
        ntc_es_fr_mean=("ntc_es_fr", "mean"),
        ntc_fr_es_mean=("ntc_fr_es", "mean"),
    ).reset_index().rename(columns={"datetime": "date"})
    df_daily["date"] = pd.to_datetime(df_daily["date"])

    df_daily.to_parquet(cache, index=False)
    logger.info(f"JAO NTC guardado: {len(df_daily)} días")
    return df_daily


# ─── TTF + CO₂ ───────────────────────────────────────────────────────────────

def download_ttf_co2(start: str, end: str) -> pd.DataFrame:
    """
    Descarga TTF gas y CO₂ EUA desde Yahoo Finance (yfinance).
    """
    import yfinance as yf

    logger.info(f"Descargando TTF/CO₂ {start} → {end}")
    cache = RAW_DIR / f"ttf_co2_{start[:4]}_{end[:4]}.parquet"
    if cache.exists():
        logger.info(f"  Cargando desde caché: {cache}")
        return pd.read_parquet(cache)

    CCGT_EFFICIENCY = 0.58
    GAS_EMISSION_FACTOR = 0.202  # tCO₂/MWh_gas

    ttf_tickers = ["TTF=F", "NG=F"]
    co2_tickers = ["CO2.DE", "EUETS.PA", "CARB.L"]

    ttf_data = None
    for ticker in ttf_tickers:
        try:
            raw = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
            if not raw.empty:
                # yfinance puede devolver MultiIndex — aplanar
                if isinstance(raw.columns, pd.MultiIndex):
                    raw.columns = raw.columns.get_level_values(0)
                if "Close" in raw.columns:
                    ttf_data = raw[["Close"]].rename(columns={"Close": "ttf_eur_mwh"})
                    logger.info(f"TTF: {ticker} → {len(ttf_data)} días")
                    break
        except Exception as ex:
            logger.warning(f"TTF {ticker} falló: {ex}")

    co2_data = None
    for ticker in co2_tickers:
        try:
            raw = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
            if not raw.empty:
                if isinstance(raw.columns, pd.MultiIndex):
                    raw.columns = raw.columns.get_level_values(0)
                if "Close" in raw.columns:
                    co2_data = raw[["Close"]].rename(columns={"Close": "co2_eur_t"})
                    logger.info(f"CO₂: {ticker} → {len(co2_data)} días")
                    break
        except Exception as ex:
            logger.warning(f"CO₂ {ticker} falló: {ex}")

    # Fallback con valores históricos aproximados
    dates = pd.date_range(start, end, freq="D")
    if ttf_data is None or ttf_data.empty:
        logger.warning("TTF no disponible, usando valores históricos aproximados")
        ttf_approx = {2019: 14, 2020: 10, 2021: 30, 2022: 120, 2023: 45, 2024: 35}
        ttf_data = pd.DataFrame(
            {"ttf_eur_mwh": [ttf_approx.get(d.year, 35) for d in dates]},
            index=dates
        )
    if co2_data is None or co2_data.empty:
        logger.warning("CO₂ no disponible, usando valores históricos aproximados")
        co2_approx = {2019: 25, 2020: 24, 2021: 50, 2022: 80, 2023: 85, 2024: 65}
        co2_data = pd.DataFrame(
            {"co2_eur_t": [co2_approx.get(d.year, 60) for d in dates]},
            index=dates
        )

    df = ttf_data[["ttf_eur_mwh"]].join(co2_data[["co2_eur_t"]], how="outer")
    df = df.resample("D").interpolate(method="linear").ffill().bfill()
    df["gas_cost_mwh"] = df["ttf_eur_mwh"] / CCGT_EFFICIENCY
    df["co2_cost_mwh"] = (df["co2_eur_t"] * GAS_EMISSION_FACTOR) / CCGT_EFFICIENCY
    df["spark_spread_proxy"] = df["gas_cost_mwh"] + df["co2_cost_mwh"]
    # Aplanar el índice (puede ser DatetimeIndex con nombre 'Date' o sin nombre)
    df = df.reset_index()
    # Renombrar la primera columna a 'date' independientemente de su nombre original
    df.columns = ["date"] + list(df.columns[1:])
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df[(df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))]

    df.to_parquet(cache, index=False)
    logger.info(f"TTF/CO₂ guardado: {len(df)} días")
    return df


# ─── ENSAMBLAJE ──────────────────────────────────────────────────────────────

def build_dataset(start: str, end: str) -> pd.DataFrame:
    """
    Ensambla el dataset diario completo con todas las fuentes.
    """
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    y0, y1 = start[:4], end[:4]
    out_file = PROCESSED_DIR / f"cm_dataset_{y0}_{y1}.parquet"

    logger.info(f"Construyendo dataset {start} → {end}")

    # 1. Generación diaria
    df_gen = download_generation_daily(f"{start}T00:00", f"{end}T23:59", RAW_DIR)
    if df_gen.empty:
        raise RuntimeError("No se pudo descargar generación de REData")
    df_gen["date"] = pd.to_datetime(df_gen["datetime"]).dt.normalize()
    df_gen = df_gen.drop(columns=["datetime"])

    # 2. Capacidad instalada (mensual → forward-fill a diario)
    df_cap = download_installed_capacity(f"{start}T00:00", f"{end}T23:59", RAW_DIR)
    if not df_cap.empty:
        df_cap["date"] = pd.to_datetime(df_cap["datetime"]).dt.normalize()
        df_cap = df_cap.drop(columns=["datetime"])
        df_cap = df_cap.add_suffix("_cap").rename(columns={"date_cap": "date"})
        date_range = pd.DataFrame({"date": pd.date_range(start, end, freq="D")})
        df_cap = date_range.merge(df_cap, on="date", how="left").ffill()

    # 3. Precios OMIE + NTC + TTF/CO₂ desde outputs MIBEL (ya disponibles)
    mibel_prices_path = Path("data/mibel_outputs/mibel_daily_prices.parquet")
    df_mibel = pd.DataFrame()
    if mibel_prices_path.exists():
        df_mibel = pd.read_parquet(mibel_prices_path)
        df_mibel["date"] = pd.to_datetime(df_mibel["date"]).dt.normalize()
        df_mibel = df_mibel[
            (df_mibel["date"] >= pd.Timestamp(start)) &
            (df_mibel["date"] <= pd.Timestamp(end))
        ]
        logger.info(f"Precios MIBEL cargados: {len(df_mibel)} días")
    else:
        # Fallback: descargar OMIE, JAO y TTF por separado
        logger.info("mibel_daily_prices.parquet no encontrado, descargando fuentes individuales")
        df_omie = download_omie_prices(start, end)
        df_jao = download_jao_ntc(start, end)
        df_ttf = download_ttf_co2(start, end)

    # Ensamblaje
    df = df_gen.copy()
    if not df_mibel.empty:
        df = df.merge(df_mibel, on="date", how="left")
    else:
        df_omie = locals().get("df_omie", pd.DataFrame())
        df_jao = locals().get("df_jao", pd.DataFrame())
        df_ttf = locals().get("df_ttf", pd.DataFrame())
        if not df_omie.empty:
            df = df.merge(df_omie, on="date", how="left")
        if not df_jao.empty:
            df = df.merge(df_jao, on="date", how="left")
        if not df_ttf.empty:
            df = df.merge(df_ttf, on="date", how="left")
    if not df_cap.empty:
        cap_cols = [c for c in df_cap.columns if c != "date"]
        df = df.merge(df_cap[["date"] + cap_cols], on="date", how="left")

    # Features derivadas
    if "wind" in df.columns and "solar_pv" in df.columns:
        df["renewables_total"] = df[["wind", "solar_pv", "solar_thermal",
                                     "hydro", "other_ren"]].sum(axis=1, min_count=1)
    if "generación_total" in df.columns:
        df = df.rename(columns={"generación_total": "generation_total"})

    # Régimen de mercado (igual que MIBEL)
    df["regime"] = "pre_crisis"
    df.loc[df["date"] >= "2022-01-01", "regime"] = "excepcion_iberica"
    df.loc[df["date"] >= "2024-01-01", "regime"] = "post_excepcion"

    df = df.sort_values("date").reset_index(drop=True)
    df.to_parquet(out_file, index=False)
    logger.info(f"Dataset guardado: {len(df):,} días | {len(df.columns)} columnas → {out_file}")
    logger.info(f"Columnas: {list(df.columns)}")
    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build cm-spain-renewables dataset")
    parser.add_argument("--start", default="2019-01-01")
    parser.add_argument("--end", default="2024-12-31")
    args = parser.parse_args()

    df = build_dataset(args.start, args.end)
    print(f"\nDataset listo: {len(df):,} días | {len(df.columns)} columnas")
    print(df.head(3).to_string())
