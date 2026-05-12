"""
marginal_elcc.py
----------------
Calcula el ELCC marginal por tecnología siguiendo la aproximación de
Mills & Wiser (2012): diferencia finita del ELCC promedio al añadir
un incremento de capacidad ΔC sobre la base instalada.

ELCC_marginal(tech) ≈ d(ELCC_total) / d(C_tech)

Implementación: se calcula el ELCC con la capacidad instalada real
y con capacidad instalada +10% para cada tecnología, manteniendo
el resto constante. La diferencia es el ELCC marginal.
"""
import sys
sys.path.insert(0, "src")

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

FIGURES_DIR = Path("figures")
RESULTS_DIR = Path("results")

TECH_MAP = {
    "wind":          ("wind",          "wind_cap"),
    "solar_pv":      ("solar_pv",      "solar_pv_cap"),
    "solar_thermal": ("solar_thermal",  "solar_thermal_cap"),
    "hydro":         ("hydro",         "hydro_cap"),
    "nuclear":       ("nuclear",       "nuclear_cap"),
    "ccgt":          ("ccgt",          "ccgt_cap"),
}

TECH_LABELS = {
    "wind": "Eólica", "solar_pv": "Solar PV", "solar_thermal": "Solar Térmica",
    "hydro": "Hidráulica", "nuclear": "Nuclear", "ccgt": "Ciclo Combinado",
}

N_CRITICAL = 100
DELTA = 0.10  # +10% de capacidad instalada


def load_dataset():
    f = Path("data/processed/cm_dataset_2019_2024_clean.parquet")
    if not f.exists():
        f = Path("data/processed/cm_dataset_2019_2024.parquet")
    df = pd.read_parquet(f)
    df["date"] = pd.to_datetime(df["date"])
    # Excluir 2019 si solo tiene datos parciales
    year_counts = df.groupby(df["date"].dt.year).size()
    incomplete = [y for y, c in year_counts.items() if c < 300]
    if incomplete:
        logger.info(f"Excluyendo años incompletos: {incomplete}")
        df = df[~df["date"].dt.year.isin(incomplete)]
    logger.info(f"Dataset: {len(df)} días ({df['date'].dt.year.min()}–{df['date'].dt.year.max()})")
    return df


def get_net_demand(df):
    gen_total_col = next((c for c in df.columns if "generaci" in c.lower() or c == "net_demand"), None)
    if gen_total_col == "net_demand":
        return df["net_demand"]
    if gen_total_col:
        wind = df.get("wind", pd.Series(0, index=df.index))
        solar = (df.get("solar_pv", pd.Series(0, index=df.index)) +
                 df.get("solar_thermal", pd.Series(0, index=df.index)))
        return df[gen_total_col] - wind - solar
    # Fallback: usar la suma de todas las tecnologías
    tech_cols = [c for c in ["hydro", "nuclear", "coal", "ccgt", "wind", "solar_pv",
                              "solar_thermal", "other_ren", "cogen", "waste_ren"] if c in df.columns]
    return df[tech_cols].sum(axis=1)


def compute_elcc(df, tech, n=N_CRITICAL, cap_override=None):
    """
    ELCC = mean(gen_tech[top_N]) / (cap_tech_mean[top_N] * 24)
    cap_override: si se pasa, usa ese valor como capacidad instalada (para marginal)
    """
    gen_col, cap_col = TECH_MAP.get(tech, (tech, None))
    if gen_col not in df.columns:
        return np.nan

    net_demand = get_net_demand(df)
    top_n_idx = net_demand.nlargest(min(n, len(df))).index
    gen_top_n = df.loc[top_n_idx, gen_col]

    if cap_override is not None:
        cap_mean = cap_override
    elif cap_col and cap_col in df.columns:
        cap_mean = df.loc[top_n_idx, cap_col].mean()
    else:
        return np.nan

    if cap_mean > 0:
        return float(gen_top_n.mean() / (cap_mean * 24))
    return np.nan


def compute_marginal_elcc(df):
    """
    ELCC marginal por tecnología usando diferencia finita:
    ELCC_marginal = (ELCC_base - ELCC_base_minus_delta) / delta
    
    Interpretación: cuánto ELCC aporta el último MW instalado de esa tecnología.
    """
    rows = []
    for tech, label in TECH_LABELS.items():
        gen_col, cap_col = TECH_MAP.get(tech, (tech, None))
        if gen_col not in df.columns or cap_col not in df.columns:
            continue

        # ELCC base (con capacidad real)
        elcc_base = compute_elcc(df, tech)

        # Capacidad base media en top-N
        net_demand = get_net_demand(df)
        top_n_idx = net_demand.nlargest(N_CRITICAL).index
        cap_base = df.loc[top_n_idx, cap_col].mean()

        # ELCC con +10% de capacidad (mismo despacho, más capacidad instalada)
        # El numerador (generación real) no cambia porque la demanda no cambia
        # Solo cambia el denominador
        cap_plus = cap_base * (1 + DELTA)
        elcc_plus = compute_elcc(df, tech, cap_override=cap_plus)

        # ELCC marginal = (ELCC_base - ELCC_plus) / DELTA
        # (negativo porque más capacidad = menor ELCC por MW)
        marginal = (elcc_base - elcc_plus) / DELTA if not (np.isnan(elcc_base) or np.isnan(elcc_plus)) else np.nan

        rows.append({
            "Tecnología": label,
            "ELCC promedio": round(elcc_base, 3),
            "ELCC +10% cap": round(elcc_plus, 3),
            "ELCC marginal (por MW adicional)": round(marginal, 4),
            "Interpretación": "Degradación por saturación" if marginal > 0 else "Sin degradación"
        })
        logger.info(f"  {label}: base={elcc_base:.3f} | +10%={elcc_plus:.3f} | marginal={marginal:.4f}")

    return pd.DataFrame(rows)


def plot_marginal_elcc(df_marginal, save_dir):
    """Figura: ELCC promedio vs marginal por tecnología."""
    techs = df_marginal["Tecnología"].tolist()
    x = np.arange(len(techs))
    w = 0.35

    fig, ax = plt.subplots(figsize=(11, 6))
    b1 = ax.bar(x - w/2, df_marginal["ELCC promedio"], w,
                label="ELCC promedio (2020–2024)", color="#2c3e50", alpha=0.85)
    b2 = ax.bar(x + w/2, df_marginal["ELCC +10% cap"], w,
                label="ELCC con +10% capacidad instalada", color="#e74c3c", alpha=0.85)

    for bar in list(b1) + list(b2):
        h = bar.get_height()
        if not np.isnan(h) and h > 0.01:
            ax.text(bar.get_x() + bar.get_width()/2, h + 0.005,
                    f"{h:.3f}", ha="center", va="bottom", fontsize=8)

    ax.set_xticks(x)
    ax.set_xticklabels(techs, fontsize=11)
    ax.set_ylabel("ELCC (factor de planta en horas críticas)", fontsize=11)
    ax.set_ylim(0, 1.1)
    ax.set_title("ELCC promedio vs ELCC marginal por tecnología\n(Mills-Wiser 2012: diferencia finita +10% capacidad)",
                 fontsize=12, fontweight="bold")
    ax.legend(fontsize=10)
    ax.grid(axis="y", alpha=0.3)

    # Añadir anotación de degradación
    for i, row in df_marginal.iterrows():
        deg = row["ELCC marginal (por MW adicional)"]
        if not np.isnan(deg) and deg > 0.0001:
            ax.annotate(f"↓{deg:.3f}/MW",
                        xy=(i, max(row["ELCC promedio"], row["ELCC +10% cap"]) + 0.04),
                        ha="center", fontsize=8, color="#c0392b",
                        fontweight="bold")

    plt.tight_layout()
    out = save_dir / "block_b_7_marginal_elcc.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    logger.info(f"Figura: {out}")


def run():
    df = load_dataset()

    # Excluir Excepción Ibérica para el análisis limpio
    df_clean = df[~df["regime"].isin(["excepcion_iberica"])].copy()
    logger.info(f"Dataset sin Excepción Ibérica: {len(df_clean)} días")

    logger.info("\n--- ELCC marginal (Mills-Wiser 2012) ---")
    df_marginal = compute_marginal_elcc(df_clean)
    logger.info(f"\n{df_marginal.to_string(index=False)}")
    df_marginal.to_csv(RESULTS_DIR / "block_b_marginal_elcc_corrected.csv", index=False)

    plot_marginal_elcc(df_marginal, FIGURES_DIR)
    logger.info("\nMarginal ELCC completado.")
    return df_marginal


if __name__ == "__main__":
    run()
