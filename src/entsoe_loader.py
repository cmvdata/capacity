"""
entsoe_loader.py
----------------
Carga generación horaria por tecnología desde los CSVs ENTSO-E
(`gen_es_YYYYMM.csv`, dataset 16.1.B&C, BZN|ES) y construye un dataset
horario apto para `elcc_analysis.py` con `freq='hourly'`.

Unidad oficial ENTSO-E (16.1.b/c): "Average of all available instantaneous
net power output values in each Market Time Unit" — es decir, MW promedio
del intervalo. Para obtener MWh de energía: MW × duración_intervalo_h.

Resoluciones:
  - 2019-01 → 2022-05: PT60M (1 hora)
  - 2022-06 → 2024-12: PT15M (15 minutos) → resample a PT60M con `.mean()`
"""
from __future__ import annotations

import logging
import re as _re
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [ENTSOE] %(message)s")
logger = logging.getLogger(__name__)

RAW_DIR = Path("data/raw")
PROCESSED_DIR = Path("data/processed")

# ─── Mapping ─────────────────────────────────────────────────────────────────

# Nombre ENTSO-E → nombre analítico (snake_case, en inglés)
PSR_TYPE_MAP: dict[str, str] = {
    "Biomass":                            "biomass",
    "Energy storage":                     "storage",
    "Fossil Brown coal/Lignite":          "coal_brown",
    "Fossil Coal-derived gas":            "coal_gas",
    "Fossil Gas":                         "fossil_gas",
    "Fossil Hard coal":                   "coal_hard",
    "Fossil Oil":                         "oil",
    "Fossil Oil shale":                   "oil_shale",
    "Fossil Peat":                        "peat",
    "Geothermal":                         "geothermal",
    "Hydro Pumped Storage":               "hydro_pumped",
    "Hydro Run-of-river and pondage":     "hydro_ror",
    "Hydro Water Reservoir":              "hydro_reservoir",
    "Marine":                             "marine",
    "Nuclear":                            "nuclear",
    "Other":                              "other",
    "Other renewable":                    "other_ren",
    "Solar":                              "solar",
    "Waste":                              "waste",
    "Wind Offshore":                      "wind_offshore",
    "Wind Onshore":                       "wind_onshore",
}

# Agregaciones derivadas: clave analítica → lista de claves PSR a sumar
DERIVED_AGGREGATIONS: dict[str, list[str]] = {
    "wind":         ["wind_onshore", "wind_offshore"],
    "hydro_total":  ["hydro_reservoir", "hydro_ror", "hydro_pumped"],
}

# Mapping de capacidad REData → tech analítico ENTSO-E.
# El dataset diario REData tiene columnas wind_cap, solar_pv_cap, etc.
# Aquí indicamos cómo construir cap_<tech> para el hourly dataset.
REDATA_CAP_MAP: dict[str, list[str] | str] = {
    "nuclear":         "nuclear_cap",
    "solar":           ["solar_pv_cap", "solar_thermal_cap"],
    "wind":            "wind_cap",
    "hydro_total":     "hydro_cap",
    "fossil_gas":      ["ccgt_cap", "gas_turbine_cap"],
    "biomass":         "other_ren_cap",   # aproximación (REData no separa biomass)
    "coal_hard":       "coal_cap",
    "storage":         None,
}

# Aproximación de capacidad de sub-tecs hidráulicas. REData publica un único
# `hydro_cap` agregado; ENTSO-E desagrega la generación en 3 sub-tecs pero no
# publica capacidad por sub-tec en 16.1.B&C. Estos ratios son aproximados a
# partir de estadísticas REE públicas (mix peninsular ~17 GW total).
HYDRO_CAP_RATIOS: dict[str, float] = {
    "hydro_reservoir": 0.35,  # ~6 GW de embalse convencional
    "hydro_ror":       0.45,  # ~7.5 GW fluyente y pondage
    "hydro_pumped":    0.20,  # ~3.5 GW bombeo (turbinación)
}


# ─── Carga de un CSV individual ─────────────────────────────────────────────

_FNAME_RE = _re.compile(r"gen_es_(\d{4})(\d{2})\.csv$")


def _parse_filename(path: Path) -> tuple[int, int]:
    m = _FNAME_RE.search(path.name)
    if not m:
        raise ValueError(f"Nombre de archivo inesperado: {path.name}")
    return int(m.group(1)), int(m.group(2))


def parse_csv(path: Path, *, resample_to_60min: bool = True) -> pd.DataFrame:
    """
    Carga un CSV mensual ENTSO-E y devuelve un DataFrame long con columnas:
      datetime (UTC, naive) | tech (analytic key) | mw

    Detecta automáticamente la resolución del archivo. Si `resample_to_60min`
    y la resolución nativa es PT15M, agrega los 4 cuartos a hora con
    `mean(MW)` (semánticamente correcto para potencia promedio).
    """
    df = pd.read_csv(path)
    if "Generation (MW)" not in df.columns:
        raise ValueError(f"{path.name}: falta columna 'Generation (MW)'")

    df["mw"] = pd.to_numeric(df["Generation (MW)"], errors="coerce")
    times = df["MTU (UTC)"].str.split(" - ", expand=True)
    df["start"] = pd.to_datetime(times[0], format="%d/%m/%Y %H:%M:%S")
    df["end"]   = pd.to_datetime(times[1], format="%d/%m/%Y %H:%M:%S")
    df["dur_h"] = (df["end"] - df["start"]).dt.total_seconds() / 3600.0

    # Detectar resolución (tomamos la mediana — robusto si hay un error puntual)
    median_dur = df["dur_h"].median()
    if abs(median_dur - 1.0) < 1e-6:
        native_res = "PT60M"
    elif abs(median_dur - 0.25) < 1e-6:
        native_res = "PT15M"
    else:
        raise ValueError(f"{path.name}: resolución inesperada (mediana {median_dur} h)")

    df["tech"] = df["Production Type"].map(PSR_TYPE_MAP)
    unmapped = df[df["tech"].isna()]["Production Type"].unique()
    if len(unmapped):
        raise ValueError(f"{path.name}: production types sin mapear: {list(unmapped)}")

    out = df[["start", "tech", "mw"]].rename(columns={"start": "datetime"})

    if resample_to_60min and native_res == "PT15M":
        out = (out.set_index("datetime")
                  .groupby("tech")["mw"]
                  .resample("h")
                  .mean()
                  .reset_index())
    return out


# ─── Carga multi-archivo ─────────────────────────────────────────────────────

def _select_files(years: Iterable[int], raw_dir: Path = RAW_DIR) -> list[Path]:
    files: list[Path] = []
    for f in sorted(raw_dir.glob("gen_es_*.csv")):
        try:
            yr, _ = _parse_filename(f)
        except ValueError:
            continue
        if yr in set(years):
            files.append(f)
    return files


def load_entsoe_hourly(years: Iterable[int],
                       techs: Iterable[str] | None = None,
                       resample_to_60min: bool = True,
                       raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    """
    Devuelve un DataFrame wide con índice datetime (UTC naive, hourly) y
    una columna por tecnología (clave analítica de PSR_TYPE_MAP).
    """
    files = _select_files(years, raw_dir)
    if not files:
        raise FileNotFoundError(f"No hay CSVs ENTSO-E en {raw_dir} para {list(years)}")

    frames = [parse_csv(f, resample_to_60min=resample_to_60min) for f in files]
    long = pd.concat(frames, ignore_index=True)
    wide = long.pivot_table(index="datetime", columns="tech",
                             values="mw", aggfunc="mean").sort_index()
    wide.columns.name = None

    # Asegurar columnas estables: si una PSR no aparece (todos NaN en el periodo,
    # típico de Marine/Storage/Hydro Pumped en años tempranos), añadirla con NaN.
    for analytic_key in PSR_TYPE_MAP.values():
        if analytic_key not in wide.columns:
            wide[analytic_key] = np.nan

    if techs is not None:
        wide = wide[[c for c in techs if c in wide.columns]]
    return wide.reset_index()


def load_entsoe_daily(years: Iterable[int],
                      techs: Iterable[str] | None = None,
                      raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    """
    Suma a MWh diarios (UTC). Cada hora aporta MW × 1h = MWh.
    """
    h = load_entsoe_hourly(years, techs=techs, resample_to_60min=True, raw_dir=raw_dir)
    h = h.set_index("datetime")
    daily = h.resample("D").sum(min_count=1)
    daily.index.name = "date"
    return daily.reset_index()


# ─── Construcción del dataset horario para elcc_analysis ────────────────────

def _add_capacity_columns(df_h: pd.DataFrame, redata_path: Path) -> pd.DataFrame:
    """Añade columnas <tech>_cap por forward-fill desde el dataset diario REData.
    REData publica capacidad mensual; aquí la traemos a horaria con ffill.

    Para sub-tecs hidráulicas (Reservoir / RoR / Pumped) no hay desglose en
    REData; se asignan ratios constantes (`HYDRO_CAP_RATIOS`) sobre `hydro_cap`.
    """
    re = pd.read_parquet(redata_path)
    re["date"] = pd.to_datetime(re["date"])
    re = re.set_index("date").sort_index()

    df_h = df_h.copy()
    df_h["_d"] = df_h["datetime"].dt.normalize()
    re_h = re.reindex(pd.date_range(df_h["_d"].min(), df_h["_d"].max(), freq="D"))
    re_h = re_h.ffill().bfill()
    re_h.index.name = "_d"

    for tech, src in REDATA_CAP_MAP.items():
        if src is None:
            continue
        if isinstance(src, str):
            if src in re_h.columns:
                cap_series = re_h[src]
            else:
                continue
        else:
            cols = [c for c in src if c in re_h.columns]
            if not cols:
                continue
            cap_series = re_h[cols].sum(axis=1, min_count=1)
        df_h = df_h.merge(cap_series.rename(f"{tech}_cap").reset_index(),
                           on="_d", how="left")

    if "hydro_total_cap" in df_h.columns:
        for sub_tech, ratio in HYDRO_CAP_RATIOS.items():
            df_h[f"{sub_tech}_cap"] = df_h["hydro_total_cap"] * ratio

    return df_h.drop(columns=["_d"])


def build_hourly_dataset(start: str, end: str,
                          raw_dir: Path = RAW_DIR,
                          processed_dir: Path = PROCESSED_DIR,
                          redata_path: Path | None = None) -> pd.DataFrame:
    """
    Construye `data/processed/entsoe_hourly_dataset.parquet`:
      - índice horario UTC (naive)
      - generación: 21 PSR claves analíticas + agregados (`wind`, `hydro_total`)
      - capacidad: <tech>_cap (de REData, ffill mensual; sub-tecs hidráulicas ratio)
      - net_demand = sum(generación) − wind − solar
      - regime: pre_crisis / excepcion_iberica / post_excepcion
    """
    processed_dir.mkdir(parents=True, exist_ok=True)
    redata_path = redata_path or processed_dir / "cm_dataset_2019_2024_clean.parquet"

    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    years = sorted({y for y in range(s.year, e.year + 1)})
    logger.info(f"Construyendo dataset horario {start} → {end} (años {years})")

    df = load_entsoe_hourly(years, raw_dir=raw_dir)
    df = df[(df["datetime"] >= s) & (df["datetime"] <= e + pd.Timedelta(hours=23))]

    # Agregados derivados
    for derived, components in DERIVED_AGGREGATIONS.items():
        cols = [c for c in components if c in df.columns]
        if cols:
            df[derived] = df[cols].sum(axis=1, min_count=1)

    # Generación total (todas las PSR mapeadas, antes de duplicar agregados)
    psr_cols = [c for c in df.columns
                if c in PSR_TYPE_MAP.values() and c != "datetime"]
    df["gen_total"] = df[psr_cols].sum(axis=1, min_count=1)

    # Net demand: gen_total − VRE
    vre_cols = [c for c in ["wind", "solar"] if c in df.columns]
    df["net_demand"] = df["gen_total"] - df[vre_cols].sum(axis=1, min_count=1)

    # Capacidades desde REData (ffill mensual)
    if redata_path.exists():
        df = _add_capacity_columns(df, redata_path)
    else:
        logger.warning(f"REData no encontrado en {redata_path} — sin columnas _cap")

    # Régimen
    df["regime"] = "pre_crisis"
    df.loc[df["datetime"] >= "2022-01-01", "regime"] = "excepcion_iberica"
    df.loc[df["datetime"] >= "2024-01-01", "regime"] = "post_excepcion"

    out = processed_dir / "entsoe_hourly_dataset.parquet"
    df.to_parquet(out, index=False)
    logger.info(f"Dataset horario guardado: {len(df):,} horas | {len(df.columns)} cols → {out}")
    return df


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="ENTSO-E hourly dataset builder")
    p.add_argument("--start", default="2019-01-01")
    p.add_argument("--end",   default="2024-12-31")
    args = p.parse_args()
    build_hourly_dataset(args.start, args.end)
