"""ELCC sobre series sintéticas con propiedades conocidas."""
import numpy as np
import pandas as pd
import pytest

from elcc_analysis import compute_elcc, compute_net_demand, get_top_n_idx


def _synthetic_df(wind_factor: float, solar_factor: float, days: int = 365) -> pd.DataFrame:
    """
    Construye un dataset sintético con generación y capacidad conocidas.

    factor=1.0  → la tecnología genera cap * 24 todos los días (CF = 1.0)
    factor=0.0  → no genera nada
    factor=0.5  → genera la mitad

    La generación total está dominada por una tecnología "base" cuyo perfil
    no es plano, de modo que las top-N horas de demanda neta sean determinadas
    por esa señal y no degeneren a un orden arbitrario.
    """
    dates = pd.date_range("2020-01-01", periods=days, freq="D")
    rng = np.random.default_rng(42)
    base = 500_000 + 100_000 * rng.standard_normal(days)  # MWh/día oscilante

    wind_cap = 25_000  # MW
    solar_cap = 15_000

    df = pd.DataFrame({
        "date": dates,
        "wind": np.full(days, wind_factor * wind_cap * 24, dtype=float),
        "solar_pv": np.full(days, solar_factor * solar_cap * 24, dtype=float),
        "solar_thermal": np.zeros(days, dtype=float),
        "hydro": np.full(days, 0.3 * 17_000 * 24, dtype=float),
        "nuclear": np.full(days, 0.92 * 7_000 * 24, dtype=float),
        "ccgt": np.full(days, 0.4 * 26_000 * 24, dtype=float),
        "wind_cap": np.full(days, wind_cap, dtype=float),
        "solar_pv_cap": np.full(days, solar_cap, dtype=float),
        "solar_thermal_cap": np.full(days, 2_300.0, dtype=float),
        "hydro_cap": np.full(days, 17_000.0, dtype=float),
        "nuclear_cap": np.full(days, 7_000.0, dtype=float),
        "ccgt_cap": np.full(days, 26_000.0, dtype=float),
    })
    df["generacion_total"] = base + df["wind"] + df["solar_pv"]
    df["regime"] = "pre_crisis"
    return df


def test_elcc_full_capacity_is_one():
    """Tecnología que genera cap*24 todos los días: ELCC ≈ 1.0."""
    df = _synthetic_df(wind_factor=1.0, solar_factor=0.0)
    elcc = compute_elcc(df, "wind", n=100)
    assert elcc == pytest.approx(1.0, abs=1e-9)


def test_elcc_zero_generation_is_zero():
    """Tecnología que no genera: ELCC = 0."""
    df = _synthetic_df(wind_factor=0.0, solar_factor=0.0)
    elcc = compute_elcc(df, "wind", n=100)
    assert elcc == pytest.approx(0.0, abs=1e-9)


def test_elcc_half_capacity_is_half():
    """Tecnología que genera 0.5 * cap * 24: ELCC = 0.5."""
    df = _synthetic_df(wind_factor=0.5, solar_factor=0.5)
    assert compute_elcc(df, "wind", n=100) == pytest.approx(0.5, abs=1e-9)
    assert compute_elcc(df, "solar_pv", n=100) == pytest.approx(0.5, abs=1e-9)


def test_elcc_returns_nan_for_missing_tech():
    df = _synthetic_df(0.5, 0.5)
    assert np.isnan(compute_elcc(df, "tech_inexistente"))


def test_top_n_idx_selects_largest_net_demand():
    """Los top-N índices corresponden a los días con mayor demanda neta."""
    df = _synthetic_df(0.3, 0.2)
    nd = compute_net_demand(df)
    top = get_top_n_idx(df, n=10)
    threshold = nd.loc[top].min()
    # Cualquier día NO en top tiene demanda neta ≤ threshold
    others = nd.drop(top)
    assert (others <= threshold).all()
