"""
elcc_corrected.py
-----------------
ELCC corregido: denominador = capacidad instalada (MW), no max(generación).
Incluye:
  - Tabla ELCC completo vs sin Excepción Ibérica
  - Sensibilidad por año (IQR)
  - Benchmarks europeos
  - Distribución mensual de las top-100 horas (verificación de artefacto)
"""
import sys
sys.path.insert(0, "src")

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

FIGURES_DIR = Path("figures")
RESULTS_DIR = Path("results")
FIGURES_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ─── BENCHMARKS EUROPEOS (National Grid ESO 2024, EirGrid CRU 2023, PSE 2023) ──
EU_BENCHMARKS = {
    "UK (National Grid ESO 2024)": {"wind": 0.08, "solar_pv": 0.03, "nuclear": 0.92, "hydro": 0.15},
    "Irlanda (EirGrid 2023)":      {"wind": 0.10, "solar_pv": 0.01, "nuclear": None,  "hydro": 0.20},
    "Polonia (PSE 2023)":          {"wind": 0.08, "solar_pv": 0.02, "nuclear": None,  "hydro": 0.10},
}

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

N_CRITICAL = 100  # Top-N horas de mayor demanda neta


def load_dataset():
    f = Path("data/processed/cm_dataset_2019_2024.parquet")
    df = pd.read_parquet(f)
    df["date"] = pd.to_datetime(df["date"])
    logger.info(f"Dataset: {len(df)} días | {len(df.columns)} cols")
    return df


def get_net_demand(df):
    """Demanda neta = generación total - renovables intermitentes."""
    gen_total_col = "generación_total" if "generación_total" in df.columns else "generation_total"
    wind = df.get("wind", pd.Series(0, index=df.index))
    solar = (df.get("solar_pv", pd.Series(0, index=df.index)) +
             df.get("solar_thermal", pd.Series(0, index=df.index)))
    return df[gen_total_col] - wind - solar


def compute_elcc_correct(df, tech, n=N_CRITICAL):
    """
    ELCC correcto:
      CF = mean(gen_tech[top_N_net_demand]) / mean(cap_installed_tech[top_N])
    
    Denominador = capacidad instalada media en esas horas (MW),
    no el máximo histórico de generación.
    """
    gen_col, cap_col = TECH_MAP.get(tech, (tech, None))
    if gen_col not in df.columns:
        return np.nan

    net_demand = get_net_demand(df)
    top_n_idx = net_demand.nlargest(min(n, len(df))).index

    gen_top_n = df.loc[top_n_idx, gen_col]

    # Denominador: capacidad instalada media en esas horas × 24h
    # gen está en MWh/día, cap en MW → factor de planta = gen_MWh_día / (cap_MW × 24)
    if cap_col and cap_col in df.columns:
        cap_top_n = df.loc[top_n_idx, cap_col]
        cap_mean = cap_top_n.mean()
        if cap_mean > 0:
            return float(gen_top_n.mean() / (cap_mean * 24))

    # Fallback: usar max generación anual normalizada por 24h
    gen_max = df[gen_col].max()
    if gen_max > 0:
        return float(gen_top_n.mean() / gen_max)
    return np.nan


def compute_elcc_table_corrected(df):
    """Tabla ELCC correcto para todo el periodo."""
    row = {}
    for tech, label in TECH_LABELS.items():
        row[label] = round(compute_elcc_correct(df, tech), 3)
    return pd.DataFrame([row])


def compute_elcc_excepcion_comparison(df):
    """
    Tabla comparativa:
      - ELCC periodo completo 2019-2024
      - ELCC excluyendo Excepción Ibérica (2022-2023)
      - Diferencia (sesgo regulatorio)
    """
    df_all = df.copy()
    df_no_exc = df[~df["regime"].isin(["excepcion_iberica"])].copy()

    rows = []
    for tech, label in TECH_LABELS.items():
        elcc_all = compute_elcc_correct(df_all, tech)
        elcc_no_exc = compute_elcc_correct(df_no_exc, tech)
        diff = round(elcc_all - elcc_no_exc, 3) if not (np.isnan(elcc_all) or np.isnan(elcc_no_exc)) else np.nan
        rows.append({
            "Tecnología": label,
            "ELCC 2019-2024": round(elcc_all, 3),
            "ELCC sin Exc. Ibérica": round(elcc_no_exc, 3),
            "Sesgo regulatorio": diff,
        })
    return pd.DataFrame(rows)


def compute_elcc_by_year(df):
    """ELCC por año para análisis de sensibilidad."""
    rows = []
    for year in sorted(df["date"].dt.year.unique()):
        sub = df[df["date"].dt.year == year]
        row = {"Año": year}
        for tech, label in TECH_LABELS.items():
            row[label] = round(compute_elcc_correct(sub, tech), 3)
        rows.append(row)
    return pd.DataFrame(rows)


def check_top_n_month_distribution(df, n=N_CRITICAL):
    """Verifica en qué meses caen las top-N horas de demanda neta."""
    net_demand = get_net_demand(df)
    top_n_idx = net_demand.nlargest(n).index
    months = df.loc[top_n_idx, "date"].dt.month
    return months.value_counts().sort_index()


# ─── FIGURAS ─────────────────────────────────────────────────────────────────

def plot_elcc_comparison(df_comp, save_dir):
    """Figura: ELCC completo vs sin Excepción Ibérica."""
    techs = df_comp["Tecnología"].tolist()
    x = np.arange(len(techs))
    w = 0.35

    fig, ax = plt.subplots(figsize=(11, 6))
    b1 = ax.bar(x - w/2, df_comp["ELCC 2019-2024"], w,
                label="2019–2024 (con Exc. Ibérica)", color="#3498db", alpha=0.85)
    b2 = ax.bar(x + w/2, df_comp["ELCC sin Exc. Ibérica"], w,
                label="Sin Excepción Ibérica (2019–2021 + 2024)", color="#e74c3c", alpha=0.85)

    for bar in list(b1) + list(b2):
        h = bar.get_height()
        if not np.isnan(h):
            ax.text(bar.get_x() + bar.get_width()/2, h + 0.005,
                    f"{h:.2f}", ha="center", va="bottom", fontsize=9)

    ax.set_xticks(x)
    ax.set_xticklabels(techs, fontsize=11)
    ax.set_ylabel("ELCC (Top-100 horas demanda neta)", fontsize=11)
    ax.set_ylim(0, 1.15)
    ax.set_title("ELCC por tecnología: impacto de la Excepción Ibérica\n(denominador = capacidad instalada)",
                 fontsize=12, fontweight="bold")
    ax.legend(fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    out = save_dir / "block_b_2_elcc_excepcion_comparison.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    logger.info(f"Figura: {out}")


def plot_elcc_sensitivity_by_year(df_yr, save_dir):
    """Figura: ELCC por año para eólica y solar (sensibilidad meteorológica)."""
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    years = df_yr["Año"].tolist()
    colors = plt.cm.viridis(np.linspace(0, 1, len(years)))

    for ax, tech, label in [(axes[0], "Eólica", "Eólica"), (axes[1], "Solar PV", "Solar PV")]:
        vals = df_yr[tech].tolist()
        bars = ax.bar(years, vals, color=colors, alpha=0.85, edgecolor="white")
        for bar, val in zip(bars, vals):
            if not np.isnan(val):
                ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.005,
                        f"{val:.2f}", ha="center", va="bottom", fontsize=9)
        mean_val = np.nanmean(vals)
        ax.axhline(mean_val, color="black", linestyle="--", linewidth=1.2,
                   label=f"Media: {mean_val:.2f}")
        ax.set_xlabel("Año", fontsize=11)
        ax.set_ylabel("ELCC", fontsize=11)
        ax.set_title(f"ELCC {label} por año\n(variabilidad meteorológica)", fontsize=11, fontweight="bold")
        ax.set_ylim(0, 1.0)
        ax.legend(fontsize=10)
        ax.grid(axis="y", alpha=0.3)

    plt.suptitle("Sensibilidad del ELCC al año meteorológico (2019–2024)",
                 fontsize=13, fontweight="bold", y=1.02)
    plt.tight_layout()
    out = save_dir / "block_b_3_elcc_sensitivity_year.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    logger.info(f"Figura: {out}")


def plot_benchmarks_comparison(df_comp, save_dir):
    """Figura: ELCC España vs benchmarks europeos."""
    # España (sin Excepción Ibérica, más limpio)
    spain = {row["Tecnología"]: row["ELCC sin Exc. Ibérica"]
             for _, row in df_comp.iterrows()}

    # Mapeo nombre → clave interna
    label_to_key = {v: k for k, v in TECH_LABELS.items()}

    techs_to_plot = ["Eólica", "Solar PV", "Hidráulica", "Nuclear"]
    x = np.arange(len(techs_to_plot))
    n_series = 1 + len(EU_BENCHMARKS)
    w = 0.18

    fig, ax = plt.subplots(figsize=(13, 6))
    palette = ["#2c3e50", "#e74c3c", "#3498db", "#2ecc71"]

    # España
    spain_vals = [spain.get(t, np.nan) for t in techs_to_plot]
    offset = (0 - n_series/2 + 0.5) * w
    bars = ax.bar(x + offset, spain_vals, w, label="España (sin Exc. Ibérica)",
                  color=palette[0], alpha=0.9)
    for bar, val in zip(bars, spain_vals):
        if not np.isnan(val):
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.005,
                    f"{val:.2f}", ha="center", va="bottom", fontsize=8, fontweight="bold")

    # Benchmarks
    for i, (country, benchmarks) in enumerate(EU_BENCHMARKS.items()):
        vals = []
        for t in techs_to_plot:
            key = label_to_key.get(t)
            v = benchmarks.get(key, np.nan)
            vals.append(v if v is not None else np.nan)
        offset = (i + 1 - n_series/2 + 0.5) * w
        bars = ax.bar(x + offset, vals, w, label=country,
                      color=palette[i+1], alpha=0.85)
        for bar, val in zip(bars, vals):
            if not np.isnan(val):
                ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.005,
                        f"{val:.2f}", ha="center", va="bottom", fontsize=8)

    ax.set_xticks(x)
    ax.set_xticklabels(techs_to_plot, fontsize=12)
    ax.set_ylabel("ELCC (Top-100 horas demanda neta)", fontsize=11)
    ax.set_ylim(0, 1.15)
    ax.set_title("ELCC España vs benchmarks europeos\n(España: denominador = capacidad instalada REData)",
                 fontsize=12, fontweight="bold")
    ax.legend(fontsize=9, loc="upper right")
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    out = save_dir / "block_b_4_benchmarks_comparison.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    logger.info(f"Figura: {out}")


def plot_top_n_month_distribution(month_dist, save_dir):
    """Figura: distribución mensual de las top-100 horas de demanda neta."""
    month_names = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
                   "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
    vals = [month_dist.get(m, 0) for m in range(1, 13)]

    fig, ax = plt.subplots(figsize=(10, 5))
    colors = ["#e74c3c" if v == max(vals) else "#3498db" for v in vals]
    bars = ax.bar(month_names, vals, color=colors, alpha=0.85, edgecolor="white")
    for bar, val in zip(bars, vals):
        if val > 0:
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.3,
                    str(val), ha="center", va="bottom", fontsize=10)

    ax.set_ylabel("Número de días en Top-100", fontsize=11)
    ax.set_title("Distribución mensual de las Top-100 horas de mayor demanda neta\n(2019–2024)",
                 fontsize=12, fontweight="bold")
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    out = save_dir / "block_b_5_top100_month_distribution.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    logger.info(f"Figura: {out}")


# ─── MAIN ────────────────────────────────────────────────────────────────────

def run():
    df = load_dataset()

    # 1. Verificar distribución mensual de top-100
    logger.info("\n--- Distribución mensual Top-100 ---")
    month_dist = check_top_n_month_distribution(df)
    logger.info(f"\n{month_dist.to_string()}")

    # 2. Tabla ELCC corregido
    logger.info("\n--- ELCC corregido (denominador = capacidad instalada) ---")
    elcc_table = compute_elcc_table_corrected(df)
    logger.info(f"\n{elcc_table.to_string(index=False)}")

    # 3. Tabla con/sin Excepción Ibérica
    logger.info("\n--- Comparación con/sin Excepción Ibérica ---")
    df_comp = compute_elcc_excepcion_comparison(df)
    logger.info(f"\n{df_comp.to_string(index=False)}")
    df_comp.to_csv(RESULTS_DIR / "block_b_elcc_excepcion_comparison.csv", index=False)

    # 4. Sensibilidad por año
    logger.info("\n--- ELCC por año ---")
    df_yr = compute_elcc_by_year(df)
    logger.info(f"\n{df_yr.to_string(index=False)}")
    df_yr.to_csv(RESULTS_DIR / "block_b_elcc_by_year.csv", index=False)

    # IQR
    for tech in TECH_LABELS.values():
        if tech in df_yr.columns:
            vals = df_yr[tech].dropna()
            logger.info(f"  {tech}: media={vals.mean():.3f} | IQR=[{vals.quantile(0.25):.3f}, {vals.quantile(0.75):.3f}]")

    # 5. Figuras
    logger.info("\n--- Generando figuras ---")
    plot_top_n_month_distribution(month_dist, FIGURES_DIR)
    plot_elcc_comparison(df_comp, FIGURES_DIR)
    plot_elcc_sensitivity_by_year(df_yr, FIGURES_DIR)
    plot_benchmarks_comparison(df_comp, FIGURES_DIR)

    logger.info("\nAnálisis ELCC corregido completado.")
    return df_comp, df_yr


if __name__ == "__main__":
    run()
