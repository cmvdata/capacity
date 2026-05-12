"""
elcc_analysis.py
----------------
Block B: cálculo de Effective Load Carrying Capability (ELCC) para
tecnologías renovables en España (2019–2024).

Metodología:
  ELCC(tech, N) = mean(gen_tech[top_N_net_demand]) / (cap_instalada_tech_top_N × interval_hours)

  - freq='daily'  → interval_hours=24, gen en MWh/día, top-N días
  - freq='hourly' → interval_hours=1,  gen en MWh/h (=MW), top-N horas

  El denominador es siempre la capacidad instalada real (REData), NO max(gen).

Outputs:
  data/processed/elcc_results.parquet
  results/block_b_*.csv
  figures/block_b_1..7.png
"""

import logging
from pathlib import Path
from typing import Literal

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [BLOCK_B] %(message)s")
logger = logging.getLogger(__name__)

FIGURES_DIR = Path("figures")
RESULTS_DIR = Path("results")
DATA_DIR = Path("data")

N_CRITICAL = 100
TOP_N_VALUES = [50, 100, 250, 500, 1000]

# Equivalentes en horas (×24): el comportamiento se mantiene si N(daily) y N(hourly) cubren un volumen comparable.
N_CRITICAL_HOURLY = 2400
TOP_N_VALUES_HOURLY = [1200, 2400, 6000, 12000, 24000]

TECH_MAP = {
    "wind":          ("wind",          "wind_cap"),
    "solar_pv":      ("solar_pv",      "solar_pv_cap"),
    "solar_thermal": ("solar_thermal", "solar_thermal_cap"),
    "hydro":         ("hydro",         "hydro_cap"),
    "nuclear":       ("nuclear",       "nuclear_cap"),
    "ccgt":          ("ccgt",          "ccgt_cap"),
}

TECH_LABELS = {
    "wind":          "Eólica",
    "solar_pv":      "Solar PV",
    "solar_thermal": "Solar Térmica",
    "hydro":         "Hidráulica",
    "nuclear":       "Nuclear",
    "ccgt":          "Ciclo Combinado",
}

# Para hourly (dataset ENTSO-E): hidráulica desagregada, gas no es estricto CCGT.
TECH_MAP_HOURLY = {
    "wind":            ("wind",            "wind_cap"),
    "solar":           ("solar",           "solar_cap"),
    "hydro_reservoir": ("hydro_reservoir", "hydro_reservoir_cap"),
    "hydro_ror":       ("hydro_ror",       "hydro_ror_cap"),
    "hydro_pumped":    ("hydro_pumped",    "hydro_pumped_cap"),
    "hydro_total":     ("hydro_total",     "hydro_total_cap"),
    "nuclear":         ("nuclear",         "nuclear_cap"),
    "fossil_gas":      ("fossil_gas",      "fossil_gas_cap"),
}

TECH_LABELS_HOURLY = {
    "wind":            "Eólica",
    "solar":           "Solar",
    "hydro_reservoir": "Hidro Embalse",
    "hydro_ror":       "Hidro Fluyente",
    "hydro_pumped":    "Hidro Bombeo",
    "hydro_total":     "Hidráulica total",
    "nuclear":         "Nuclear",
    "fossil_gas":      "Fossil Gas",
}

REGIMES = {
    "pre_crisis":        ("2019-01-01", "2021-12-31"),
    "excepcion_iberica": ("2022-01-01", "2023-12-31"),
    "post_excepcion":    ("2024-01-01", "2024-12-31"),
}

# Benchmarks europeos publicados (National Grid ESO 2024, EirGrid 2023, PSE 2023)
EU_BENCHMARKS = {
    "UK (National Grid ESO 2024)": {"wind": 0.08, "solar_pv": 0.03, "nuclear": 0.92, "hydro": 0.15},
    "Irlanda (EirGrid 2023)":      {"wind": 0.10, "solar_pv": 0.01, "nuclear": None, "hydro": 0.20},
    "Polonia (PSE 2023)":          {"wind": 0.08, "solar_pv": 0.02, "nuclear": None, "hydro": 0.10},
}


# ─── CARGA ───────────────────────────────────────────────────────────────────

def load_dataset(processed_dir: Path = DATA_DIR / "processed",
                  freq: Literal["daily", "hourly"] = "daily") -> pd.DataFrame:
    """
    Carga el dataset consolidado.

    freq='daily'  → cm_dataset_2019_2024_clean.parquet (REData)
    freq='hourly' → entsoe_hourly_dataset.parquet (construido por entsoe_loader)
    """
    if freq == "hourly":
        path = processed_dir / "entsoe_hourly_dataset.parquet"
        if not path.exists():
            raise FileNotFoundError(
                f"No se encontró {path}. Ejecuta primero "
                "`python -m entsoe_loader` o `python main.py --step build_hourly`.")
        df = pd.read_parquet(path)
        # Canonicalizar a 'date' (timestamp horario) para compartir lógica con daily
        df["date"] = pd.to_datetime(df["datetime"])
        if "regime" not in df.columns:
            df["regime"] = "pre_crisis"
            df.loc[df["date"] >= "2022-01-01", "regime"] = "excepcion_iberica"
            df.loc[df["date"] >= "2024-01-01", "regime"] = "post_excepcion"
        logger.info(f"Dataset HOURLY cargado: {len(df):,} horas | {len(df.columns)} columnas")
        return df

    clean = processed_dir / "cm_dataset_2019_2024_clean.parquet"
    if clean.exists():
        df = pd.read_parquet(clean)
    else:
        candidates = sorted(processed_dir.glob("cm_dataset_*.parquet"))
        if not candidates:
            raise FileNotFoundError(
                f"No se encontró ningún cm_dataset_*.parquet en {processed_dir}. "
                "Ejecuta primero `python main.py --step build`.")
        df = pd.read_parquet(candidates[-1])

    df["date"] = pd.to_datetime(df["date"])

    if "generación_total" in df.columns and "generacion_total" not in df.columns:
        df = df.rename(columns={"generación_total": "generacion_total"})

    if "regime" not in df.columns:
        df["regime"] = "pre_crisis"
        df.loc[df["date"] >= "2022-01-01", "regime"] = "excepcion_iberica"
        df.loc[df["date"] >= "2024-01-01", "regime"] = "post_excepcion"

    logger.info(f"Dataset DAILY cargado: {len(df):,} días | {len(df.columns)} columnas")
    return df


# ─── DEMANDA NETA Y TOP-N ────────────────────────────────────────────────────

def compute_net_demand(df: pd.DataFrame) -> pd.Series:
    """Demanda neta diaria = generación total − (eólica + solar PV + solar térmica)."""
    if "net_demand" in df.columns:
        return df["net_demand"]

    gen_col = "generacion_total" if "generacion_total" in df.columns else "generación_total"
    if gen_col not in df.columns:
        raise ValueError("Falta columna de generación total (generacion_total).")

    wind = df.get("wind", pd.Series(0, index=df.index))
    solar_pv = df.get("solar_pv", pd.Series(0, index=df.index))
    solar_th = df.get("solar_thermal", pd.Series(0, index=df.index))
    return df[gen_col] - wind.fillna(0) - solar_pv.fillna(0) - solar_th.fillna(0)


def get_top_n_idx(df: pd.DataFrame, n: int = N_CRITICAL) -> pd.Index:
    return compute_net_demand(df).nlargest(min(n, len(df))).index


# ─── ELCC ────────────────────────────────────────────────────────────────────

def compute_elcc(df: pd.DataFrame, tech: str, n: int = N_CRITICAL,
                 cap_override: float | None = None,
                 interval_hours: float = 24.0,
                 tech_map: dict | None = None) -> float:
    """
    ELCC con denominador = capacidad instalada (MW) × interval_hours.

    Para freq='daily':  interval_hours=24, gen es MWh/día.
    Para freq='hourly': interval_hours=1,  gen es MWh/h (=MW promedio).
    """
    tmap = tech_map or TECH_MAP
    gen_col, cap_col = tmap.get(tech, (tech, f"{tech}_cap"))
    if gen_col not in df.columns:
        return np.nan

    top_idx = get_top_n_idx(df, n)
    if len(top_idx) == 0:
        return np.nan

    gen_top = df.loc[top_idx, gen_col].mean()

    if cap_override is not None:
        cap_mw = cap_override
    elif cap_col in df.columns:
        cap_mw = df.loc[top_idx, cap_col].mean()
    else:
        return np.nan

    if not (cap_mw and cap_mw > 0):
        return np.nan
    return float(gen_top / (cap_mw * interval_hours))


def compute_elcc_table(df: pd.DataFrame,
                        tech_labels: dict | None = None,
                        tech_map: dict | None = None,
                        top_n_values: list | None = None,
                        interval_hours: float = 24.0) -> pd.DataFrame:
    """ELCC para todas las tecnologías y todos los umbrales N (heatmap)."""
    labels = tech_labels or TECH_LABELS
    tmap = tech_map or TECH_MAP
    n_values = top_n_values or TOP_N_VALUES
    rows = []
    for n in n_values:
        row = {"N": n}
        for tech, label in labels.items():
            row[label] = round(compute_elcc(df, tech, n, interval_hours=interval_hours,
                                              tech_map=tmap), 3)
        rows.append(row)
    return pd.DataFrame(rows)


def compute_elcc_by_regime(df: pd.DataFrame, n: int = N_CRITICAL,
                            tech_labels: dict | None = None,
                            tech_map: dict | None = None,
                            interval_hours: float = 24.0) -> pd.DataFrame:
    labels = tech_labels or TECH_LABELS
    tmap = tech_map or TECH_MAP
    rows = []
    for regime, (start, end) in REGIMES.items():
        mask = (df["date"] >= start) & (df["date"] <= end)
        sub = df[mask]
        if len(sub) < n:
            continue
        row = {"regime": regime, "n_records": len(sub)}
        for tech, label in labels.items():
            row[label] = round(compute_elcc(sub, tech, n, interval_hours=interval_hours,
                                              tech_map=tmap), 3)
        rows.append(row)
    return pd.DataFrame(rows)


def compute_elcc_excepcion_comparison(df: pd.DataFrame, n: int = N_CRITICAL,
                                       tech_labels: dict | None = None,
                                       tech_map: dict | None = None,
                                       interval_hours: float = 24.0) -> pd.DataFrame:
    """ELCC completo vs excluyendo Excepción Ibérica (sesgo regulatorio)."""
    labels = tech_labels or TECH_LABELS
    tmap = tech_map or TECH_MAP
    df_no_exc = df[df["regime"] != "excepcion_iberica"]
    rows = []
    for tech, label in labels.items():
        elcc_all = compute_elcc(df, tech, n, interval_hours=interval_hours, tech_map=tmap)
        elcc_no_exc = compute_elcc(df_no_exc, tech, n, interval_hours=interval_hours, tech_map=tmap)
        diff = (round(elcc_all - elcc_no_exc, 3)
                if not (np.isnan(elcc_all) or np.isnan(elcc_no_exc)) else np.nan)
        rows.append({
            "Tecnología": label,
            "ELCC 2019-2024": round(elcc_all, 3),
            "ELCC sin Exc. Ibérica": round(elcc_no_exc, 3),
            "Sesgo regulatorio": diff,
        })
    return pd.DataFrame(rows)


def compute_elcc_by_year(df: pd.DataFrame, n: int = N_CRITICAL,
                          tech_labels: dict | None = None,
                          tech_map: dict | None = None,
                          interval_hours: float = 24.0) -> pd.DataFrame:
    """ELCC por año (sensibilidad meteorológica)."""
    labels = tech_labels or TECH_LABELS
    tmap = tech_map or TECH_MAP
    rows = []
    for year in sorted(df["date"].dt.year.unique()):
        sub = df[df["date"].dt.year == year]
        if len(sub) < n:
            continue
        row = {"Año": int(year)}
        for tech, label in labels.items():
            row[label] = round(compute_elcc(sub, tech, n, interval_hours=interval_hours,
                                              tech_map=tmap), 3)
        rows.append(row)
    return pd.DataFrame(rows)


# ─── ELCC MARGINAL (Mills-Wiser 2012) ────────────────────────────────────────

def compute_marginal_elcc(df: pd.DataFrame, tech: str, n: int = N_CRITICAL,
                           delta_gw: float = 1.0,
                           interval_hours: float = 24.0,
                           tech_map: dict | None = None) -> float:
    """
    ELCC marginal por GW adicional (saturación).

    Asunción: la generación en las horas top-N no cambia al añadir capacidad
    (límite superior de la dilución). Para renovables esta es una cota inferior
    del marginal real; para despachables es razonable porque ya operan al máximo
    útil en horas pico.

    Convención de signo: marginal NEGATIVO indica dilución (más capacidad sin
    generación adicional ⇒ ELCC cae). Espera valores ≤ 0 para todas las tecs.
    """
    tmap = tech_map or TECH_MAP
    gen_col, cap_col = tmap.get(tech, (tech, f"{tech}_cap"))
    if gen_col not in df.columns or cap_col not in df.columns:
        return np.nan

    top_idx = get_top_n_idx(df, n)
    cap_base_mw = df.loc[top_idx, cap_col].mean()
    if not (cap_base_mw and cap_base_mw > 0):
        return np.nan

    elcc_base = compute_elcc(df, tech, n, interval_hours=interval_hours, tech_map=tmap)
    elcc_plus = compute_elcc(df, tech, n, cap_override=cap_base_mw + delta_gw * 1000,
                              interval_hours=interval_hours, tech_map=tmap)

    if np.isnan(elcc_base) or np.isnan(elcc_plus):
        return np.nan
    return float((elcc_plus - elcc_base) / delta_gw)


def compute_marginal_elcc_table(df: pd.DataFrame, n: int = N_CRITICAL,
                                 delta_gw: float = 1.0,
                                 exclude_excepcion: bool = True,
                                 tech_labels: dict | None = None,
                                 tech_map: dict | None = None,
                                 interval_hours: float = 24.0) -> pd.DataFrame:
    """Tabla ELCC marginal por tecnología.

    Por defecto excluye Excepción Ibérica."""
    labels = tech_labels or TECH_LABELS
    tmap = tech_map or TECH_MAP
    df_use = df[df["regime"] != "excepcion_iberica"] if exclude_excepcion else df

    rows = []
    for tech, label in labels.items():
        gen_col, cap_col = tmap.get(tech, (tech, f"{tech}_cap"))
        if gen_col not in df_use.columns or cap_col not in df_use.columns:
            continue

        top_idx = get_top_n_idx(df_use, n)
        cap_base_mw = df_use.loc[top_idx, cap_col].mean()
        elcc_base = compute_elcc(df_use, tech, n, interval_hours=interval_hours, tech_map=tmap)
        elcc_plus = compute_elcc(df_use, tech, n,
                                  cap_override=cap_base_mw + delta_gw * 1000,
                                  interval_hours=interval_hours, tech_map=tmap)
        marginal = ((elcc_plus - elcc_base) / delta_gw
                    if not (np.isnan(elcc_base) or np.isnan(elcc_plus)) else np.nan)

        rows.append({
            "Tecnología": label,
            "ELCC base": round(elcc_base, 3),
            f"ELCC +{delta_gw} GW": round(elcc_plus, 3),
            "ELCC marginal (por GW adicional)": round(marginal, 4),
            "Interpretación": ("Dilución por saturación"
                                if marginal is not None and marginal < 0
                                else "Sin dilución medible"),
        })
        logger.info(f"  {label}: base={elcc_base:.3f} | +{delta_gw}GW={elcc_plus:.3f}"
                    f" | marginal={marginal:+.4f}/GW")
    return pd.DataFrame(rows)


# ─── DISTRIBUCIÓN MENSUAL DE TOP-N ───────────────────────────────────────────

def compute_top_n_month_distribution(df: pd.DataFrame, n: int = N_CRITICAL) -> pd.Series:
    top_idx = get_top_n_idx(df, n)
    return df.loc[top_idx, "date"].dt.month.value_counts().sort_index()


# ─── FIGURAS ─────────────────────────────────────────────────────────────────

def _save(fig, save_dir: Path, name: str):
    save_dir.mkdir(parents=True, exist_ok=True)
    out = save_dir / name
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"Figura guardada: {out}")


def plot_elcc_heatmap(elcc_table: pd.DataFrame, save_dir: Path = FIGURES_DIR,
                       name: str = "block_b_1_elcc_heatmap.png",
                       n_values: list | None = None):
    """block_b_1: heatmap ELCC por N y tecnología."""
    if elcc_table.empty:
        return
    n_vals = n_values or TOP_N_VALUES
    tech_cols = [c for c in elcc_table.columns if c != "N"]
    matrix = elcc_table[tech_cols].values.astype(float)

    fig, ax = plt.subplots(figsize=(11, 5))
    im = ax.imshow(matrix, cmap="RdYlGn", aspect="auto", vmin=0, vmax=1)
    ax.set_xticks(range(len(tech_cols)))
    ax.set_xticklabels(tech_cols, rotation=30, ha="right", fontsize=11)
    ax.set_yticks(range(len(n_vals)))
    ax.set_yticklabels([f"Top-{n}" for n in n_vals], fontsize=11)
    for i in range(len(n_vals)):
        for j in range(len(tech_cols)):
            v = matrix[i, j]
            if not np.isnan(v):
                ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                        fontsize=10, fontweight="bold",
                        color="white" if v < 0.3 or v > 0.7 else "black")
    plt.colorbar(im, ax=ax, label="ELCC (factor de planta en pico)")
    ax.set_title("ELCC por tecnología y umbral de horas críticas\n"
                 "(España 2019–2024)",
                 fontsize=13, fontweight="bold")
    plt.tight_layout()
    _save(fig, save_dir, name)


def plot_elcc_excepcion_comparison(df_comp: pd.DataFrame, save_dir: Path = FIGURES_DIR,
                                     name: str = "block_b_2_elcc_excepcion_comparison.png"):
    """block_b_2: ELCC completo vs sin Excepción Ibérica."""
    techs = df_comp["Tecnología"].tolist()
    x = np.arange(len(techs))
    w = 0.35

    fig, ax = plt.subplots(figsize=(11, 6))
    b1 = ax.bar(x - w / 2, df_comp["ELCC 2019-2024"], w,
                label="2019–2024 (con Exc. Ibérica)", color="#3498db", alpha=0.85)
    b2 = ax.bar(x + w / 2, df_comp["ELCC sin Exc. Ibérica"], w,
                label="Sin Excepción Ibérica (2019–2021 + 2024)", color="#e74c3c", alpha=0.85)
    for bar in list(b1) + list(b2):
        h = bar.get_height()
        if not np.isnan(h):
            ax.text(bar.get_x() + bar.get_width() / 2, h + 0.005,
                    f"{h:.2f}", ha="center", va="bottom", fontsize=9)
    ax.set_xticks(x)
    ax.set_xticklabels(techs, fontsize=11)
    ax.set_ylabel("ELCC (Top-100 horas demanda neta)", fontsize=11)
    ax.set_ylim(0, 1.15)
    ax.set_title("ELCC por tecnología: impacto de la Excepción Ibérica\n"
                 "(denominador = capacidad instalada)",
                 fontsize=12, fontweight="bold")
    ax.legend(fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    _save(fig, save_dir, name)


def plot_elcc_sensitivity_by_year(df_yr: pd.DataFrame, save_dir: Path = FIGURES_DIR,
                                    name: str = "block_b_3_elcc_sensitivity_year.png",
                                    tech_labels: dict | None = None):
    """block_b_3: ELCC por año (sensibilidad meteorológica). Eólica y solar."""
    labels = tech_labels or TECH_LABELS
    wind_label = labels.get("wind", "Eólica")
    solar_label = next((labels[k] for k in ("solar_pv", "solar")
                         if k in labels), "Solar")
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    years = df_yr["Año"].tolist()
    colors = plt.cm.viridis(np.linspace(0, 1, len(years)))
    for ax, label in [(axes[0], wind_label), (axes[1], solar_label)]:
        if label not in df_yr.columns:
            ax.set_axis_off()
            continue
        vals = df_yr[label].tolist()
        bars = ax.bar(years, vals, color=colors, alpha=0.85, edgecolor="white")
        for bar, v in zip(bars, vals):
            if not np.isnan(v):
                ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.005,
                        f"{v:.2f}", ha="center", va="bottom", fontsize=9)
        mean = np.nanmean(vals)
        ax.axhline(mean, color="black", linestyle="--", linewidth=1.2, label=f"Media: {mean:.2f}")
        ax.set_xlabel("Año", fontsize=11)
        ax.set_ylabel("ELCC", fontsize=11)
        ax.set_title(f"ELCC {label} por año\n(variabilidad meteorológica)",
                     fontsize=11, fontweight="bold")
        ax.set_ylim(0, 1.0)
        ax.legend(fontsize=10)
        ax.grid(axis="y", alpha=0.3)
    plt.suptitle("Sensibilidad del ELCC al año meteorológico (2019–2024)",
                 fontsize=13, fontweight="bold", y=1.02)
    plt.tight_layout()
    _save(fig, save_dir, name)


def plot_benchmarks_comparison(df_comp: pd.DataFrame, save_dir: Path = FIGURES_DIR,
                                 name: str = "block_b_4_benchmarks_comparison.png"):
    """block_b_4: España vs benchmarks europeos publicados."""
    spain = {row["Tecnología"]: row["ELCC sin Exc. Ibérica"]
             for _, row in df_comp.iterrows()}
    label_to_key = {v: k for k, v in TECH_LABELS.items()}

    techs_to_plot = ["Eólica", "Solar PV", "Hidráulica", "Nuclear"]
    x = np.arange(len(techs_to_plot))
    n_series = 1 + len(EU_BENCHMARKS)
    w = 0.18
    palette = ["#2c3e50", "#e74c3c", "#3498db", "#2ecc71"]

    fig, ax = plt.subplots(figsize=(13, 6))
    spain_vals = [spain.get(t, np.nan) for t in techs_to_plot]
    offset = (0 - n_series / 2 + 0.5) * w
    bars = ax.bar(x + offset, spain_vals, w, label="España (sin Exc. Ibérica)",
                  color=palette[0], alpha=0.9)
    for bar, v in zip(bars, spain_vals):
        if not np.isnan(v):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.005,
                    f"{v:.2f}", ha="center", va="bottom", fontsize=8, fontweight="bold")
    for i, (country, marks) in enumerate(EU_BENCHMARKS.items()):
        vals = []
        for t in techs_to_plot:
            v = marks.get(label_to_key.get(t), np.nan)
            vals.append(v if v is not None else np.nan)
        offset = (i + 1 - n_series / 2 + 0.5) * w
        bars = ax.bar(x + offset, vals, w, label=country,
                      color=palette[i + 1], alpha=0.85)
        for bar, v in zip(bars, vals):
            if not np.isnan(v):
                ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.005,
                        f"{v:.2f}", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(techs_to_plot, fontsize=12)
    ax.set_ylabel("ELCC (Top-100 horas demanda neta)", fontsize=11)
    ax.set_ylim(0, 1.15)
    ax.set_title("ELCC España vs benchmarks europeos\n"
                 "(España: denominador = capacidad instalada REData)",
                 fontsize=12, fontweight="bold")
    ax.legend(fontsize=9, loc="upper right")
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    _save(fig, save_dir, name)


def plot_top_n_month_distribution(month_dist: pd.Series, save_dir: Path = FIGURES_DIR,
                                    name: str = "block_b_5_top100_month_distribution.png",
                                    n: int = 100):
    """block_b_5: distribución mensual del Top-N."""
    month_names = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
                   "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
    vals = [int(month_dist.get(m, 0)) for m in range(1, 13)]
    colors = ["#e74c3c" if v == max(vals) else "#3498db" for v in vals]

    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.bar(month_names, vals, color=colors, alpha=0.85, edgecolor="white")
    for bar, v in zip(bars, vals):
        if v > 0:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.3,
                    str(v), ha="center", va="bottom", fontsize=10)
    ax.set_ylabel(f"Records en Top-{n}", fontsize=11)
    ax.set_title(f"Distribución mensual del Top-{n} de mayor demanda neta\n(2019–2024)",
                 fontsize=12, fontweight="bold")
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    _save(fig, save_dir, name)


def plot_marginal_elcc(df_marginal: pd.DataFrame, save_dir: Path = FIGURES_DIR,
                       delta_gw: float = 1.0,
                       name: str = "block_b_7_marginal_elcc.png"):
    """block_b_7: ELCC base vs +ΔGW."""
    techs = df_marginal["Tecnología"].tolist()
    x = np.arange(len(techs))
    w = 0.35

    fig, ax = plt.subplots(figsize=(11, 6))
    b1 = ax.bar(x - w / 2, df_marginal["ELCC base"], w,
                label="ELCC base", color="#2c3e50", alpha=0.85)
    b2 = ax.bar(x + w / 2, df_marginal[f"ELCC +{delta_gw} GW"], w,
                label=f"ELCC con +{delta_gw} GW capacidad", color="#e74c3c", alpha=0.85)
    for bar in list(b1) + list(b2):
        h = bar.get_height()
        if not np.isnan(h) and h > 0.01:
            ax.text(bar.get_x() + bar.get_width() / 2, h + 0.005,
                    f"{h:.3f}", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(techs, fontsize=11)
    ax.set_ylabel("ELCC (factor de planta en horas críticas)", fontsize=11)
    ax.set_ylim(0, 1.1)
    ax.set_title(f"ELCC base vs ELCC con +{delta_gw} GW por tecnología\n"
                 "(Mills-Wiser 2012: dilución por saturación)",
                 fontsize=12, fontweight="bold")
    ax.legend(fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    for i, row in df_marginal.iterrows():
        m = row["ELCC marginal (por GW adicional)"]
        if not np.isnan(m) and abs(m) > 0.0001:
            ax.annotate(f"{m:+.3f}/GW",
                        xy=(i, max(row["ELCC base"], row[f"ELCC +{delta_gw} GW"]) + 0.04),
                        ha="center", fontsize=8, color="#c0392b", fontweight="bold")
    plt.tight_layout()
    _save(fig, save_dir, name)


# ─── PIPELINE PRINCIPAL ──────────────────────────────────────────────────────

def run_block_b(freq: Literal["daily", "hourly"] = "daily"):
    """Block B completo: ELCC heatmap, excepción, sensibilidad, benchmarks,
    distribución mensual, marginal. Guarda CSVs y figuras.

    freq='daily'  → REData diaria, top-N en días, denominador cap×24
    freq='hourly' → ENTSO-E horaria, top-N en horas, denominador cap×1
    """
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    if freq == "hourly":
        tech_map = TECH_MAP_HOURLY
        tech_labels = TECH_LABELS_HOURLY
        n_critical = N_CRITICAL_HOURLY
        top_n_values = TOP_N_VALUES_HOURLY
        interval_hours = 1.0
        suffix = "_hourly"
    else:
        tech_map = TECH_MAP
        tech_labels = TECH_LABELS
        n_critical = N_CRITICAL
        top_n_values = TOP_N_VALUES
        interval_hours = 24.0
        suffix = ""

    logger.info("=" * 60)
    logger.info(f"BLOCK B (freq={freq}): ELCC con denominador = cap × {interval_hours}h")
    logger.info("=" * 60)

    df = load_dataset(freq=freq)

    # 1. Heatmap
    logger.info("\n--- Tabla ELCC (heatmap) ---")
    elcc_table = compute_elcc_table(df, tech_labels=tech_labels, tech_map=tech_map,
                                      top_n_values=top_n_values,
                                      interval_hours=interval_hours)
    logger.info(f"\n{elcc_table.to_string(index=False)}")
    elcc_table.to_csv(RESULTS_DIR / f"block_b_elcc_table{suffix}.csv", index=False)

    # 2. Excepción Ibérica
    logger.info("\n--- ELCC con/sin Excepción Ibérica ---")
    df_exc = compute_elcc_excepcion_comparison(df, n=n_critical,
                                                 tech_labels=tech_labels, tech_map=tech_map,
                                                 interval_hours=interval_hours)
    logger.info(f"\n{df_exc.to_string(index=False)}")
    df_exc.to_csv(RESULTS_DIR / f"block_b_elcc_excepcion_comparison{suffix}.csv", index=False)

    # 3. ELCC por año
    logger.info("\n--- ELCC por año ---")
    df_yr = compute_elcc_by_year(df, n=n_critical,
                                   tech_labels=tech_labels, tech_map=tech_map,
                                   interval_hours=interval_hours)
    logger.info(f"\n{df_yr.to_string(index=False)}")
    df_yr.to_csv(RESULTS_DIR / f"block_b_elcc_by_year{suffix}.csv", index=False)

    # 4. ELCC por régimen
    df_reg = compute_elcc_by_regime(df, n=n_critical,
                                      tech_labels=tech_labels, tech_map=tech_map,
                                      interval_hours=interval_hours)
    df_reg.to_csv(RESULTS_DIR / f"block_b_elcc_by_regime{suffix}.csv", index=False)

    # 5. Distribución mensual Top-N
    logger.info(f"\n--- Distribución mensual Top-{n_critical} ---")
    month_dist = compute_top_n_month_distribution(df, n=n_critical)
    logger.info(f"\n{month_dist.to_string()}")
    month_dist.rename("count").to_csv(
        RESULTS_DIR / f"block_b_top{n_critical}_month_distribution{suffix}.csv")

    # 6. Marginal
    logger.info(f"\n--- ELCC marginal (Mills-Wiser, delta_C = 1 GW) ---")
    df_mar = compute_marginal_elcc_table(df, n=n_critical, delta_gw=1.0,
                                           tech_labels=tech_labels, tech_map=tech_map,
                                           interval_hours=interval_hours)
    logger.info(f"\n{df_mar.to_string(index=False)}")
    df_mar.to_csv(RESULTS_DIR / f"block_b_marginal_elcc{suffix}.csv", index=False)

    pd.concat([
        elcc_table.assign(type="naive"),
        df_exc.assign(type="excepcion_comparison"),
        df_yr.assign(type="by_year"),
        df_reg.assign(type="by_regime"),
        df_mar.assign(type="marginal"),
    ], ignore_index=True).to_parquet(
        DATA_DIR / "processed" / f"elcc_results{suffix}.parquet", index=False)

    # Figuras (heatmap, excepción, sensibilidad por año, benchmarks, top-N mensual, marginal)
    logger.info("\n--- Generando figuras Block B ---")
    plot_elcc_heatmap(elcc_table, name=f"block_b_1_elcc_heatmap{suffix}.png",
                       n_values=top_n_values)
    plot_elcc_excepcion_comparison(df_exc, name=f"block_b_2_elcc_excepcion_comparison{suffix}.png")
    plot_elcc_sensitivity_by_year(df_yr, name=f"block_b_3_elcc_sensitivity_year{suffix}.png",
                                    tech_labels=tech_labels)
    if freq == "daily":
        # Benchmarks comparison sólo está calibrada con TECH_LABELS daily
        plot_benchmarks_comparison(df_exc, name="block_b_4_benchmarks_comparison.png")
    plot_top_n_month_distribution(month_dist,
                                    name=f"block_b_5_top{n_critical}_month_distribution{suffix}.png",
                                    n=n_critical)
    plot_marginal_elcc(df_mar, name=f"block_b_7_marginal_elcc{suffix}.png")

    logger.info(f"\nBlock B (freq={freq}) completado.")
    return {
        "elcc_table": elcc_table,
        "excepcion_comparison": df_exc,
        "by_year": df_yr,
        "by_regime": df_reg,
        "marginal": df_mar,
        "month_distribution": month_dist,
    }


if __name__ == "__main__":
    run_block_b()
