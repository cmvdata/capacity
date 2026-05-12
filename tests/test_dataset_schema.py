"""Esquema mínimo del dataset ensamblado."""
from pathlib import Path

import pandas as pd
import pytest

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "processed"

# Columnas mínimas requeridas por elcc_analysis.py
REQUIRED_GEN = ["wind", "solar_pv", "solar_thermal", "hydro", "nuclear", "ccgt"]
REQUIRED_CAP = [f"{t}_cap" for t in REQUIRED_GEN]
REQUIRED_OTHER = ["date", "regime"]


def _find_dataset() -> Path:
    clean = DATA_DIR / "cm_dataset_2019_2024_clean.parquet"
    if clean.exists():
        return clean
    candidates = sorted(DATA_DIR.glob("cm_dataset_*.parquet"))
    if not candidates:
        pytest.skip("No hay cm_dataset_*.parquet (corre `python main.py --step build`).")
    return candidates[-1]


@pytest.fixture(scope="module")
def df():
    return pd.read_parquet(_find_dataset())


def test_required_columns_present(df):
    missing = [c for c in REQUIRED_GEN + REQUIRED_CAP + REQUIRED_OTHER
               if c not in df.columns]
    assert not missing, f"Columnas faltantes: {missing}"


def test_date_column_is_datetime(df):
    assert pd.api.types.is_datetime64_any_dtype(df["date"]) or \
           pd.api.types.is_datetime64_dtype(pd.to_datetime(df["date"]))


def test_generation_is_numeric(df):
    for c in REQUIRED_GEN:
        assert pd.api.types.is_numeric_dtype(df[c]), f"{c} no es numérica"


def test_capacity_is_numeric(df):
    for c in REQUIRED_CAP:
        assert pd.api.types.is_numeric_dtype(df[c]), f"{c} no es numérica"


def test_date_range_covers_2019_2024(df):
    d = pd.to_datetime(df["date"])
    assert d.min() <= pd.Timestamp("2019-01-31"), f"Inicio: {d.min()}"
    assert d.max() >= pd.Timestamp("2024-11-01"), f"Fin: {d.max()}"


def test_no_negative_generation(df):
    """La generación diaria nunca debería ser negativa (excluye generación neta)."""
    for c in REQUIRED_GEN:
        non_null = df[c].dropna()
        assert (non_null >= 0).all(), f"{c} tiene valores negativos"


def test_capacity_is_positive_when_present(df):
    for c in REQUIRED_CAP:
        non_null = df[c].dropna()
        assert (non_null > 0).all(), f"{c} tiene capacidad ≤ 0"


def test_regime_values_are_canonical(df):
    expected = {"pre_crisis", "excepcion_iberica", "post_excepcion"}
    actual = set(df["regime"].dropna().unique())
    assert actual.issubset(expected), f"Régimen inesperado: {actual - expected}"
