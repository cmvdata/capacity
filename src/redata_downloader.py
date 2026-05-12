"""
redata_downloader.py
--------------------
Descarga datos de generación, demanda y capacidad instalada desde la API
pública de REData (Red Eléctrica de España).

Fuente: https://apidatos.ree.es
NO requiere token. Solo GET requests públicos.

Resolución disponible (verificada):
  - generacion/estructura-generacion  → diaria (time_trunc=day)
  - demanda/demanda-tiempo-real       → 5 minutos (resampleado a hora)
  - generacion/potencia-instalada     → mensual

Nota: la generación horaria por tecnología requiere token de ESIOS
(api.esios.ree.es, gratuito). Este script usa la resolución diaria
disponible sin token, suficiente para ELCC naive y Block A.
"""

import requests
import pandas as pd
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [REData] %(message)s")
logger = logging.getLogger(__name__)

BASE_URL = "https://apidatos.ree.es/es/datos"
HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}

TECH_MAP = {
    "Eólica": "wind",
    "Solar fotovoltaica": "solar_pv",
    "Solar térmica": "solar_thermal",
    "Hidráulica": "hydro",
    "Nuclear": "nuclear",
    "Ciclo combinado": "ccgt",
    "Carbón": "coal",
    "Cogeneración": "cogen",
    "Residuos no renovables": "waste_nonren",
    "Residuos renovables": "waste_ren",
    "Turbinación bombeo": "pumped_hydro",
    "Motores diésel": "diesel",
    "Turbina de gas": "gas_turbine",
    "Turbina de vapor": "steam_turbine",
    "Hidroeólica": "hydro_wind",
    "Otras renovables": "other_ren",
    "Otras no renovables": "other_nonren",
}


def _get(widget: str, start: str, end: str, time_trunc: str = "day") -> dict | None:
    url = f"{BASE_URL}/{widget}"
    params = {
        "start_date": start,
        "end_date": end,
        "time_trunc": time_trunc,
    }
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=30)
        if r.status_code == 200:
            return r.json()
        logger.warning(f"HTTP {r.status_code} para {widget} [{start} → {end}]")
        return None
    except Exception as e:
        logger.error(f"Error en {widget}: {e}")
        return None


def _parse_values(data: dict) -> pd.DataFrame:
    """Parsea included[] → DataFrame wide con una columna por tecnología."""
    records = {}
    for indicator in data.get("included", []):
        tech_name = indicator["attributes"]["title"]
        col = TECH_MAP.get(tech_name, tech_name.lower().replace(" ", "_"))
        for v in indicator["attributes"].get("values", []):
            dt = v["datetime"]
            if dt not in records:
                records[dt] = {"datetime": dt}
            records[dt][col] = v["value"]

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(list(records.values()))
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True).dt.tz_localize(None)
    df = df.sort_values("datetime").reset_index(drop=True)
    return df


def _annual_chunks(start: str, end: str):
    """Genera pares (start, end) en bloques anuales."""
    s = datetime.fromisoformat(start)
    e = datetime.fromisoformat(end)
    current = s
    while current <= e:
        year_end = min(datetime(current.year, 12, 31, 23, 59), e)
        yield current.strftime("%Y-%m-%dT%H:%M"), year_end.strftime("%Y-%m-%dT%H:%M")
        current = datetime(current.year + 1, 1, 1)


def download_generation_daily(start: str, end: str, save_dir: Path) -> pd.DataFrame:
    """
    Descarga generación diaria por tecnología (MWh).
    Resolución: 1 valor por día por tecnología.
    Años disponibles sin token: 2014–2024.
    """
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    y0, y1 = start[:4], end[:4]
    cache = save_dir / f"generation_daily_{y0}_{y1}.parquet"

    if cache.exists():
        logger.info(f"Cargando generación diaria desde caché: {cache}")
        return pd.read_parquet(cache)

    logger.info(f"Descargando generación diaria {start} → {end}")
    frames = []
    for cs, ce in _annual_chunks(start, end):
        logger.info(f"  {cs[:4]}: {cs} → {ce}")
        data = _get("generacion/estructura-generacion", cs, ce, time_trunc="day")
        if data:
            df_chunk = _parse_values(data)
            if not df_chunk.empty:
                frames.append(df_chunk)
        time.sleep(0.5)

    if not frames:
        logger.error("No se obtuvieron datos de generación")
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df = df.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)
    df.to_parquet(cache, index=False)
    logger.info(f"Generación diaria guardada: {len(df):,} días → {cache}")
    return df


def download_demand_5min(start: str, end: str, save_dir: Path,
                         resample_to_hour: bool = True) -> pd.DataFrame:
    """
    Descarga demanda real en resolución de 5 minutos (MW).
    Si resample_to_hour=True, devuelve media horaria.
    Columnas: datetime, demand_mw
    """
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    y0, y1 = start[:4], end[:4]
    suffix = "hourly" if resample_to_hour else "5min"
    cache = save_dir / f"demand_{suffix}_{y0}_{y1}.parquet"

    if cache.exists():
        logger.info(f"Cargando demanda desde caché: {cache}")
        return pd.read_parquet(cache)

    logger.info(f"Descargando demanda 5min {start} → {end}")
    frames = []

    # La demanda en tiempo real solo acepta ventanas cortas — chunks de 7 días
    s = datetime.fromisoformat(start)
    e = datetime.fromisoformat(end)
    current = s
    while current < e:
        chunk_end = min(current + timedelta(days=7), e)
        cs = current.strftime("%Y-%m-%dT%H:%M")
        ce = chunk_end.strftime("%Y-%m-%dT%H:%M")
        data = _get("demanda/demanda-tiempo-real", cs, ce, time_trunc="hour")
        if data:
            df_chunk = _parse_values(data)
            if not df_chunk.empty:
                # El widget devuelve Prevista, Programada, Real → quedamos con Real
                real_col = next((c for c in df_chunk.columns
                                 if c not in ("datetime",) and "real" in c.lower()), None)
                if real_col:
                    df_chunk = df_chunk[["datetime", real_col]].rename(
                        columns={real_col: "demand_mw"})
                frames.append(df_chunk)
        current = chunk_end + timedelta(minutes=5)
        time.sleep(0.3)

    if not frames:
        logger.error("No se obtuvieron datos de demanda")
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df = df.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)

    if resample_to_hour:
        df = df.set_index("datetime").resample("h").mean().reset_index()

    df.to_parquet(cache, index=False)
    logger.info(f"Demanda guardada: {len(df):,} registros → {cache}")
    return df


def download_installed_capacity(start: str, end: str, save_dir: Path) -> pd.DataFrame:
    """
    Descarga evolución mensual de capacidad instalada por tecnología (MW).
    """
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    y0, y1 = start[:4], end[:4]
    cache = save_dir / f"installed_capacity_{y0}_{y1}.parquet"

    if cache.exists():
        logger.info(f"Cargando capacidad instalada desde caché: {cache}")
        return pd.read_parquet(cache)

    logger.info(f"Descargando capacidad instalada {start} → {end}")
    data = _get("generacion/potencia-instalada", start, end, time_trunc="month")

    if not data:
        logger.error("No se obtuvieron datos de capacidad instalada")
        return pd.DataFrame()

    df = _parse_values(data)
    if not df.empty:
        df.to_parquet(cache, index=False)
        logger.info(f"Capacidad instalada guardada: {len(df)} meses → {cache}")
    return df


if __name__ == "__main__":
    import sys
    raw_dir = Path("data/raw")

    logger.info("=== Test REData downloader (sin token) ===")

    # Test generación diaria enero 2019
    df_gen = download_generation_daily("2019-01-01T00:00", "2019-01-31T23:59", raw_dir)
    if not df_gen.empty:
        cols = [c for c in df_gen.columns if c != "datetime"]
        logger.info(f"Generación diaria: {df_gen.shape} | Tecnologías: {cols}")
        logger.info(f"\n{df_gen[['datetime','wind','solar_pv','nuclear','ccgt']].head(5).to_string()}")

    # Test demanda horaria enero 2019 (3 días para ser rápido)
    df_dem = download_demand_5min("2019-01-01T00:00", "2019-01-03T23:59", raw_dir)
    if not df_dem.empty:
        logger.info(f"\nDemanda horaria: {df_dem.shape}")
        logger.info(f"\n{df_dem.head(5).to_string()}")
