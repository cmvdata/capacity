"""
alert_analysis.py
-----------------
Block A: Caracterización empírica de episodios de stress sistémico en el
mercado eléctrico español, reutilizando los outputs validados del MIBEL
Congestion Monitor (Vilches 2024).

Inputs:
  data/mibel_outputs/alerts_registry.csv    → 52,554 horas clasificadas
  data/mibel_outputs/residuals.parquet      → residuos del modelo XGBoost
  data/processed/cm_dataset_YYYY_YYYY.parquet → generación y demanda

Outputs:
  data/processed/critical_hours.parquet    → horas críticas con multidimensional tag
  figures/block_a_*.png                    → figuras del Block A
  results/block_a_summary.csv             → tabla resumen para el informe

Metodología:
  Tres definiciones de "hora crítica" (ver Section 4 del README):
  1. Net demand top-N (ENTSO-E approach)
  2. MIBEL Orange/Red alerts (empirical proxy)
  3. Intersection of both
"""

import logging
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [BLOCK_A] %(message)s")
logger = logging.getLogger(__name__)

FIGURES_DIR = Path("figures")
RESULTS_DIR = Path("results")
DATA_DIR = Path("data")

REGIMES = {
    "pre_crisis": ("2019-01-01", "2021-12-31", "#2ecc71"),
    "excepcion_iberica": ("2022-01-01", "2023-12-31", "#e74c3c"),
    "post_excepcion": ("2024-01-01", "2024-12-31", "#3498db"),
}

TOP_N_VALUES = [50, 100, 250, 500, 1000]


# ─── CARGA DE DATOS ──────────────────────────────────────────────────────────

def load_mibel_alerts(alerts_path: Path) -> pd.DataFrame:
    """Carga el registro de alertas del MIBEL Congestion Monitor."""
    if not alerts_path.exists():
        logger.warning(f"Archivo de alertas no encontrado: {alerts_path}")
        logger.warning("Coloca alerts_registry.csv en data/mibel_outputs/")
        return pd.DataFrame()

    df = pd.read_csv(alerts_path, parse_dates=["timestamp"])
    logger.info(f"Alertas MIBEL cargadas: {len(df):,} horas")

    # Normalizar columnas
    if "alert_level" not in df.columns and "level" in df.columns:
        df = df.rename(columns={"level": "alert_level"})

    # Distribución
    dist = df["alert_level"].value_counts()
    for level, count in dist.items():
        logger.info(f"  {level}: {count:,} horas ({count/len(df)*100:.1f}%)")

    return df


def load_mibel_residuals(residuals_path: Path) -> pd.DataFrame:
    """Carga los residuos del modelo XGBoost del MIBEL."""
    if not residuals_path.exists():
        logger.warning(f"Residuos no encontrados: {residuals_path}")
        return pd.DataFrame()

    df = pd.read_parquet(residuals_path)
    logger.info(f"Residuos MIBEL cargados: {len(df):,} horas")
    return df


def load_cm_dataset(processed_dir: Path) -> pd.DataFrame:
    """Carga el dataset diario del proyecto."""
    files = sorted(processed_dir.glob("cm_dataset_*.parquet"))
    if not files:
        logger.warning("Dataset cm no encontrado. Ejecuta build_dataset.py primero.")
        return pd.DataFrame()

    df = pd.read_parquet(files[-1])
    logger.info(f"Dataset CM cargado: {len(df):,} días")
    return df


# ─── DEFINICIÓN DE HORAS CRÍTICAS ────────────────────────────────────────────

def compute_net_demand(df_daily: pd.DataFrame) -> pd.Series:
    """
    Calcula la demanda neta diaria: demanda - (eólica + solar).
    Proxy para la presión sobre capacidad firme.
    """
    if df_daily.empty:
        return pd.Series(dtype=float)

    wind = df_daily.get("wind", pd.Series(0, index=df_daily.index))
    solar = (df_daily.get("solar_pv", pd.Series(0, index=df_daily.index)) +
             df_daily.get("solar_thermal", pd.Series(0, index=df_daily.index)))

    # Si tenemos demanda real, la usamos; si no, usamos generación total como proxy
    if "demand_mw" in df_daily.columns:
        demand = df_daily["demand_mw"]
    elif "generation_total" in df_daily.columns:
        demand = df_daily["generation_total"]
    else:
        logger.warning("Sin columna de demanda, usando generación total como proxy")
        demand = df_daily.get("generation_total", pd.Series(np.nan, index=df_daily.index))

    return demand - wind - solar


def get_critical_hours_net_demand(df_daily: pd.DataFrame, n: int) -> pd.Index:
    """Top-N días por demanda neta (definición ENTSO-E)."""
    net_demand = compute_net_demand(df_daily)
    if net_demand.empty:
        return pd.Index([])
    return net_demand.nlargest(n).index


def get_critical_hours_mibel(df_alerts: pd.DataFrame,
                              levels: list = None) -> pd.DatetimeIndex:
    """Horas con alertas MIBEL de nivel Orange o Red."""
    if df_alerts.empty:
        return pd.DatetimeIndex([])
    if levels is None:
        # Acepta tanto mayúsculas como minúsculas
        levels = ["Naranja", "Roja", "Orange", "Red", "naranja", "rojo", "orange", "red"]
    mask = df_alerts["alert_level"].isin(levels)
    return pd.DatetimeIndex(df_alerts.loc[mask, "timestamp"])


# ─── TABLA DE SOLAPAMIENTO (Sección 6.1 del README) ─────────────────────────

def compute_overlap_table(df_daily: pd.DataFrame,
                           df_alerts: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula el solapamiento entre las tres definiciones de horas críticas.
    Devuelve la tabla de la Sección 6.1 del diseño.
    """
    mibel_hours = get_critical_hours_mibel(df_alerts)
    n_mibel = len(mibel_hours)

    # Convertir alertas a fechas para comparar con datos diarios
    mibel_dates = set(pd.DatetimeIndex(mibel_hours).normalize())

    rows = []
    for n in TOP_N_VALUES:
        top_n_idx = get_critical_hours_net_demand(df_daily, n)
        if len(top_n_idx) == 0:
            continue
        top_n_dates = set(df_daily.loc[top_n_idx, "date"] if "date" in df_daily.columns
                          else df_daily.index[top_n_idx])

        overlap = len(mibel_dates & top_n_dates)
        union = len(mibel_dates | top_n_dates)
        jaccard = overlap / union if union > 0 else 0.0

        rows.append({
            "N (top net demand)": n,
            "Hours O/R alerts": n_mibel,
            "Hours top-N": n,
            "Overlap": overlap,
            "Jaccard": round(jaccard, 3),
            "Interpretation": (
                "MIBEL ≈ adequacy stress" if jaccard > 0.5
                else "MIBEL ≠ adequacy (price/congestion)"
            ),
        })

    return pd.DataFrame(rows)


# ─── CARACTERIZACIÓN TEMPORAL ────────────────────────────────────────────────

def characterize_stress_temporal(df_alerts: pd.DataFrame) -> dict:
    """
    Caracteriza la distribución temporal de episodios de stress.
    Retorna dict con DataFrames de distribución por hora, día, mes y régimen.
    """
    if df_alerts.empty:
        return {}

    df = df_alerts.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["hour_of_day"] = df["timestamp"].dt.hour
    df["day_of_week"] = df["timestamp"].dt.dayofweek
    df["month"] = df["timestamp"].dt.month
    df["year"] = df["timestamp"].dt.year

    # Régimen
    df["regime"] = "pre_crisis"
    df.loc[df["timestamp"] >= "2022-01-01", "regime"] = "excepcion_iberica"
    df.loc[df["timestamp"] >= "2024-01-01", "regime"] = "post_excepcion"

    # Filtrar Orange + Red (acepta mayúsculas y minúsculas)
    stress = df[df["alert_level"].isin(["Naranja", "Roja", "Orange", "Red",
                                          "naranja", "rojo", "orange", "red"])]

    return {
        "by_hour": stress.groupby("hour_of_day").size().rename("count"),
        "by_dow": stress.groupby("day_of_week").size().rename("count"),
        "by_month": stress.groupby("month").size().rename("count"),
        "by_regime": stress.groupby("regime").size().rename("count"),
        "by_year": stress.groupby("year").size().rename("count"),
        "total_stress": len(stress),
        "total_hours": len(df),
        "stress_rate": len(stress) / len(df),
    }


# ─── FIGURAS ─────────────────────────────────────────────────────────────────

def plot_alert_distribution(df_alerts: pd.DataFrame, save_dir: Path):
    """Figura Block A-1: Distribución temporal de alertas por régimen."""
    if df_alerts.empty:
        return

    save_dir.mkdir(parents=True, exist_ok=True)
    df = df_alerts.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["date"] = df["timestamp"].dt.date
    df["regime"] = "pre_crisis"
    df.loc[df["timestamp"] >= "2022-01-01", "regime"] = "excepcion_iberica"
    df.loc[df["timestamp"] >= "2024-01-01", "regime"] = "post_excepcion"

    level_colors = {
        "Verde": "#2ecc71", "Green": "#2ecc71", "verde": "#2ecc71",
        "Ámbar": "#f1c40f", "Amber": "#f1c40f", "ambar": "#f1c40f",
        "Naranja": "#e67e22", "Orange": "#e67e22", "naranja": "#e67e22",
        "Roja": "#e74c3c", "Red": "#e74c3c", "rojo": "#e74c3c",
    }

    fig, axes = plt.subplots(3, 1, figsize=(14, 12), sharex=False)

    # Panel 1: Serie temporal de alertas
    monthly = (df.groupby([pd.Grouper(key="timestamp", freq="ME"), "alert_level"])
               .size().unstack(fill_value=0))
    monthly.plot(kind="bar", stacked=True, ax=axes[0],
                 color=[level_colors.get(c, "#95a5a6") for c in monthly.columns],
                 legend=True, width=0.8)
    axes[0].set_title("Distribución mensual de alertas MIBEL (2019–2024)", fontsize=13, fontweight="bold")
    axes[0].set_ylabel("Horas")
    axes[0].tick_params(axis="x", rotation=45, labelsize=7)

    # Panel 2: Por hora del día
    stress = df[df["alert_level"].isin(["Naranja", "Roja", "Orange", "Red"])]
    by_hour = stress.groupby(stress["timestamp"].dt.hour).size()
    axes[1].bar(by_hour.index, by_hour.values, color="#e74c3c", alpha=0.8)
    axes[1].set_title("Alertas O/R por hora del día", fontsize=12)
    axes[1].set_xlabel("Hora del día")
    axes[1].set_ylabel("Horas O/R")
    axes[1].set_xticks(range(0, 24))

    # Panel 3: Por mes del año
    by_month = stress.groupby(stress["timestamp"].dt.month).size()
    month_names = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
                   "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
    axes[2].bar(by_month.index, by_month.values, color="#e67e22", alpha=0.8)
    axes[2].set_title("Alertas O/R por mes del año", fontsize=12)
    axes[2].set_xlabel("Mes")
    axes[2].set_ylabel("Horas O/R")
    axes[2].set_xticks(range(1, 13))
    axes[2].set_xticklabels(month_names)

    plt.tight_layout()
    out = save_dir / "block_a_1_alert_distribution.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    logger.info(f"Figura guardada: {out}")


def plot_overlap_table(overlap_df: pd.DataFrame, save_dir: Path):
    """Figura Block A-2: Tabla de solapamiento (Sección 6.1)."""
    if overlap_df.empty:
        return

    save_dir.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(12, 3))
    ax.axis("off")

    display_cols = ["N (top net demand)", "Hours O/R alerts", "Hours top-N", "Overlap", "Jaccard"]
    table_data = overlap_df[display_cols].values
    col_labels = display_cols

    t = ax.table(cellText=table_data, colLabels=col_labels,
                 loc="center", cellLoc="center")
    t.auto_set_font_size(False)
    t.set_fontsize(11)
    t.scale(1, 1.8)

    for j in range(len(col_labels)):
        t[0, j].set_facecolor("#2c3e50")
        t[0, j].set_text_props(color="white", fontweight="bold")

    for i in range(1, len(table_data) + 1):
        jaccard = float(table_data[i - 1][4])
        color = "#d5f5e3" if jaccard > 0.5 else "#fdebd0"
        for j in range(len(col_labels)):
            t[i, j].set_facecolor(color)

    plt.title("Tabla 6.1: Solapamiento entre definiciones de horas críticas",
              fontsize=12, fontweight="bold", pad=20)
    out = save_dir / "block_a_2_overlap_table.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    logger.info(f"Figura guardada: {out}")


def plot_stress_by_regime(df_alerts: pd.DataFrame, save_dir: Path):
    """Figura Block A-3: Tasa de stress por régimen de mercado."""
    if df_alerts.empty:
        return

    save_dir.mkdir(parents=True, exist_ok=True)
    df = df_alerts.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["regime"] = "pre_crisis"
    df.loc[df["timestamp"] >= "2022-01-01", "regime"] = "excepcion_iberica"
    df.loc[df["timestamp"] >= "2024-01-01", "regime"] = "post_excepcion"

    stress_levels = ["Naranja", "Roja", "Orange", "Red", "naranja", "rojo", "orange", "red"]
    regime_order = ["pre_crisis", "excepcion_iberica", "post_excepcion"]
    regime_labels = ["Pre-crisis\n(2019–2021)", "Excepción Ibérica\n(2022–2023)", "Post-excepción\n(2024)"]
    regime_colors = ["#2ecc71", "#e74c3c", "#3498db"]

    rates = []
    for regime in regime_order:
        sub = df[df["regime"] == regime]
        stress = sub[sub["alert_level"].isin(stress_levels)]
        rates.append(len(stress) / len(sub) * 100 if len(sub) > 0 else 0)

    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.bar(regime_labels, rates, color=regime_colors, alpha=0.85, width=0.5)
    for bar, rate in zip(bars, rates):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.3,
                f"{rate:.1f}%", ha="center", va="bottom", fontsize=12, fontweight="bold")

    ax.set_ylabel("Tasa de alertas O/R (%)", fontsize=11)
    ax.set_title("Tasa de stress sistémico por régimen de mercado\n(Alertas Naranja + Roja como % del total)",
                 fontsize=12, fontweight="bold")
    ax.set_ylim(0, max(rates) * 1.3)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()

    out = save_dir / "block_a_3_stress_by_regime.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    logger.info(f"Figura guardada: {out}")


# ─── PIPELINE PRINCIPAL ──────────────────────────────────────────────────────

def run_block_a():
    """Ejecuta el Block A completo."""
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 60)
    logger.info("BLOCK A: Caracterización empírica de stress sistémico")
    logger.info("=" * 60)

    # Cargar datos
    alerts_path = DATA_DIR / "mibel_outputs" / "alerts_registry.csv"
    residuals_path = DATA_DIR / "mibel_outputs" / "residuals_v2.parquet"

    df_alerts = load_mibel_alerts(alerts_path)
    df_residuals = load_mibel_residuals(residuals_path)
    df_cm = load_cm_dataset(DATA_DIR / "processed")

    if df_alerts.empty:
        logger.error("Sin datos de alertas MIBEL. Copia los outputs del MIBEL Congestion Monitor.")
        logger.error("Ver instrucciones en README.md → Sección 'Reproduce'")
        return

    # Caracterización temporal
    logger.info("\n--- Caracterización temporal ---")
    temporal = characterize_stress_temporal(df_alerts)
    logger.info(f"Total horas stress (O/R): {temporal.get('total_stress', 0):,}")
    logger.info(f"Tasa de stress: {temporal.get('stress_rate', 0)*100:.1f}%")
    if "by_regime" in temporal:
        logger.info(f"Por régimen:\n{temporal['by_regime']}")

    # Tabla de solapamiento
    if not df_cm.empty:
        logger.info("\n--- Tabla de solapamiento (Sección 6.1) ---")
        overlap_df = compute_overlap_table(df_cm, df_alerts)
        logger.info(f"\n{overlap_df.to_string(index=False)}")
        overlap_df.to_csv(RESULTS_DIR / "block_a_overlap_table.csv", index=False)
        plot_overlap_table(overlap_df, FIGURES_DIR)

    # Figuras
    logger.info("\n--- Generando figuras ---")
    plot_alert_distribution(df_alerts, FIGURES_DIR)
    plot_stress_by_regime(df_alerts, FIGURES_DIR)

    # Resumen
    summary = {
        "total_hours": len(df_alerts),
        "stress_hours_or": temporal.get("total_stress", 0),
        "stress_rate_pct": round(temporal.get("stress_rate", 0) * 100, 2),
    }
    pd.DataFrame([summary]).to_csv(RESULTS_DIR / "block_a_summary.csv", index=False)
    logger.info(f"\nResumen guardado: {RESULTS_DIR / 'block_a_summary.csv'}")
    logger.info("Block A completado.")


if __name__ == "__main__":
    run_block_a()
