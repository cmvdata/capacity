"""Tests para el loader ENTSO-E (16.1.B&C, BZN|ES)."""
import pandas as pd
import pytest
from pathlib import Path

from entsoe_loader import (
    PSR_TYPE_MAP,
    parse_csv,
    load_entsoe_hourly,
    load_entsoe_daily,
)

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"


def _has(year: int, month: int) -> bool:
    return (RAW / f"gen_es_{year:04d}{month:02d}.csv").exists()


@pytest.fixture(scope="module")
def csv_pt60m() -> Path:
    p = RAW / "gen_es_201901.csv"
    if not p.exists():
        pytest.skip("CSV PT60M (2019-01) no presente en data/raw/")
    return p


@pytest.fixture(scope="module")
def csv_pt15m() -> Path:
    p = RAW / "gen_es_202301.csv"
    if not p.exists():
        pytest.skip("CSV PT15M (2023-01) no presente en data/raw/")
    return p


def test_parse_csv_pt60m(csv_pt60m):
    """Archivo PT60M: 24h × 31d × 21 techs = 15.624 filas tras parse."""
    df = parse_csv(csv_pt60m, resample_to_60min=True)
    assert {"datetime", "tech", "mw"}.issubset(df.columns)
    # 21 production types, 24×31 = 744 horas en enero
    assert df["tech"].nunique() == 21
    counts = df.groupby("tech").size()
    assert (counts == 744).all(), f"Filas por tech inesperadas: {counts.to_dict()}"


def test_parse_csv_pt15m_resampled(csv_pt15m):
    """PT15M con resample=True: queda 1 fila por hora por tech."""
    df = parse_csv(csv_pt15m, resample_to_60min=True)
    counts = df.groupby("tech").size()
    # Enero 2023: 31×24 = 744 horas por tech
    assert (counts == 744).all(), counts.to_dict()


def test_parse_csv_pt15m_native(csv_pt15m):
    """PT15M sin resample: 4 filas por hora por tech."""
    df = parse_csv(csv_pt15m, resample_to_60min=False)
    counts = df.groupby("tech").size()
    # 31 × 96 = 2976
    assert (counts == 2976).all()


def test_psr_mapping_complete():
    """Todos los Production Types observados en el CSV están en el mapa."""
    f = RAW / "gen_es_201906.csv"
    if not f.exists():
        pytest.skip("CSV de muestra no presente")
    raw = pd.read_csv(f, usecols=["Production Type"])
    observed = set(raw["Production Type"].unique())
    missing = observed - set(PSR_TYPE_MAP.keys())
    assert not missing, f"PSR codes sin mapear: {missing}"


def test_load_entsoe_hourly_concat():
    """Carga 2019 entero: 21 PSR estables, índice horario único, cobertura ≥99%.

    Nota: en 2019 hay un gap conocido el 24-dic 02-06h UTC (5 horas) — gap real
    de la fuente, no del loader. Aceptamos hasta 0.5% de horas faltantes.
    """
    if not (_has(2019, 1) and _has(2019, 2)):
        pytest.skip("CSVs 2019-01 y 2019-02 no presentes")
    df = load_entsoe_hourly([2019])
    psr_cols = [c for c in df.columns if c in PSR_TYPE_MAP.values()]
    assert len(psr_cols) == 21
    assert df["datetime"].is_unique
    expected_hours = 365 * 24  # 8760 (2019 no es bisiesto)
    coverage = len(df) / expected_hours
    assert coverage >= 0.995, f"Cobertura {coverage:.4%}, faltan {expected_hours - len(df)} horas"


def test_resampling_preserves_total_mwh(csv_pt15m):
    """PT15M: sum(MW × 0.25h) == sum(mean_per_hour × 1h) para cada tech."""
    raw = parse_csv(csv_pt15m, resample_to_60min=False)
    raw["mwh"] = raw["mw"] * 0.25
    raw_total_per_tech = raw.groupby("tech")["mwh"].sum()

    res = parse_csv(csv_pt15m, resample_to_60min=True)
    res["mwh"] = res["mw"] * 1.0
    res_total_per_tech = res.groupby("tech")["mwh"].sum()

    # Tolerancia 0.5% por errores de redondeo en valores muy pequeños
    common = raw_total_per_tech.index.intersection(res_total_per_tech.index)
    for t in common:
        a = raw_total_per_tech[t]
        b = res_total_per_tech[t]
        if abs(a) < 1.0:
            continue  # techs con generación cero
        assert abs(a - b) / abs(a) < 0.005, f"{t}: raw={a:.1f}  resampled={b:.1f}"


def test_daily_aggregation_matches_redata():
    """Sanity check: nuclear 2019 (TWh) ENTSO-E ≈ REData (Δ < 1%)."""
    re_path = Path(__file__).resolve().parents[1] / "data" / "processed" / "cm_dataset_2019_2024_clean.parquet"
    if not re_path.exists():
        pytest.skip("REData clean no disponible")
    if not _has(2019, 1):
        pytest.skip("CSVs 2019 no presentes")

    re = pd.read_parquet(re_path)
    re["date"] = pd.to_datetime(re["date"])
    re_2019 = re[re["date"].dt.year == 2019]["nuclear"].sum() / 1e6  # TWh

    daily = load_entsoe_daily([2019])
    ee_2019 = daily[daily["date"].dt.year == 2019]["nuclear"].sum() / 1e6

    diff_pct = abs(ee_2019 - re_2019) / re_2019 * 100
    assert diff_pct < 1.0, f"Nuclear 2019: ENTSO-E={ee_2019:.2f} REData={re_2019:.2f} diff={diff_pct:.2f}%"
