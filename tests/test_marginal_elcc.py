"""ELCC marginal: signo y monotonía esperados."""
import numpy as np
import pandas as pd
import pytest

from elcc_analysis import compute_elcc, compute_marginal_elcc


def _synthetic_df(days: int = 365) -> pd.DataFrame:
    dates = pd.date_range("2020-01-01", periods=days, freq="D")
    rng = np.random.default_rng(0)
    base = 500_000 + 100_000 * rng.standard_normal(days)
    df = pd.DataFrame({
        "date": dates,
        "wind": np.full(days, 0.3 * 25_000 * 24, dtype=float),
        "solar_pv": np.full(days, 0.2 * 15_000 * 24, dtype=float),
        "solar_thermal": np.zeros(days, dtype=float),
        "hydro": np.full(days, 0.3 * 17_000 * 24, dtype=float),
        "nuclear": np.full(days, 0.92 * 7_000 * 24, dtype=float),
        "ccgt": np.full(days, 0.4 * 26_000 * 24, dtype=float),
        "wind_cap": np.full(days, 25_000.0, dtype=float),
        "solar_pv_cap": np.full(days, 15_000.0, dtype=float),
        "solar_thermal_cap": np.full(days, 2_300.0, dtype=float),
        "hydro_cap": np.full(days, 17_000.0, dtype=float),
        "nuclear_cap": np.full(days, 7_000.0, dtype=float),
        "ccgt_cap": np.full(days, 26_000.0, dtype=float),
    })
    df["generacion_total"] = base + df["wind"] + df["solar_pv"]
    df["regime"] = "pre_crisis"
    return df


@pytest.mark.parametrize("tech", ["wind", "solar_pv", "hydro", "nuclear", "ccgt"])
def test_marginal_elcc_negative_for_1gw(tech):
    """Añadir 1 GW manteniendo generación: ELCC marginal < 0 (dilución)."""
    df = _synthetic_df()
    marginal = compute_marginal_elcc(df, tech, n=100, delta_gw=1.0)
    assert not np.isnan(marginal)
    assert marginal < 0, f"{tech}: marginal {marginal} no es negativo"


def test_marginal_magnitude_consistent_with_finite_difference():
    """marginal ≈ (ELCC(C+ΔC) − ELCC(C)) / ΔC con la misma ΔC."""
    df = _synthetic_df()
    delta_gw = 1.0
    elcc_base = compute_elcc(df, "wind", n=100)
    cap_base_mw = df["wind_cap"].iloc[0]
    elcc_plus = compute_elcc(df, "wind", n=100,
                              cap_override=cap_base_mw + delta_gw * 1000)
    expected = (elcc_plus - elcc_base) / delta_gw
    actual = compute_marginal_elcc(df, "wind", n=100, delta_gw=delta_gw)
    assert actual == pytest.approx(expected, abs=1e-12)


def test_marginal_more_negative_with_smaller_base_capacity():
    """Misma ΔC=1GW: el efecto relativo es mayor cuando cap_base es menor.

    Comparamos `nuclear` (cap_base=7 GW) vs `ccgt` (cap_base=26 GW). +1 GW
    sobre 7 GW produce más dilución que +1 GW sobre 26 GW.
    """
    df = _synthetic_df()
    m_nuclear = compute_marginal_elcc(df, "nuclear", n=100, delta_gw=1.0)
    m_ccgt = compute_marginal_elcc(df, "ccgt", n=100, delta_gw=1.0)
    # Más negativo = magnitud mayor
    assert abs(m_nuclear) > abs(m_ccgt)
