"""
hourly_profile.py
-----------------
Verificación complementaria del Block B usando demanda HORARIA de un año
representativo (2023). Sirve para validar si las horas críticas tienen
sol disponible (lo cual justifica un ELCC solar > 0 en España, a
diferencia del norte de Europa).

Requiere descarga puntual de demanda 5-min → hora desde REData. Si la
caché `data/raw/demand_hourly_2023_full.parquet` ya existe se reutiliza.

Genera `figures/block_b_6_hourly_profile_top100.png` y los CSV de
distribución horaria/mensual.
"""

import calendar
import logging
import time
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [HOURLY] %(message)s")
logger = logging.getLogger(__name__)

RAW_DIR = Path("data/raw")
FIGURES_DIR = Path("figures")
RESULTS_DIR = Path("results")

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
BASE_URL = "https://apidatos.ree.es/es/datos"


def _fetch_month(year: int, month: int) -> pd.DataFrame:
    last_day = calendar.monthrange(year, month)[1]
    start = f"{year}-{month:02d}-01T00:00"
    end = f"{year}-{month:02d}-{last_day:02d}T23:59"
    url = f"{BASE_URL}/demanda/demanda-tiempo-real"
    params = {"start_date": start, "end_date": end, "time_trunc": "hour"}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=30)
        if r.status_code != 200:
            return pd.DataFrame()
        rows = []
        for item in r.json().get("included", []):
            for val in item.get("attributes", {}).get("values", []):
                rows.append({"datetime": val["datetime"], "demand": val["value"]})
            if rows:
                break  # primer indicador (Demanda real)
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True).dt.tz_localize(None)
        return df.set_index("datetime").resample("h").mean().reset_index()
    except Exception as e:
        logger.warning(f"  {year}-{month:02d}: {e}")
        return pd.DataFrame()


def load_or_download_demand_2023(cache: Path = RAW_DIR / "demand_hourly_2023_full.parquet") -> pd.DataFrame:
    if cache.exists():
        logger.info(f"Cache de demanda 2023 disponible: {cache}")
        return pd.read_parquet(cache)

    cache.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Descargando demanda horaria 2023 mes a mes…")
    frames = []
    for month in range(1, 13):
        logger.info(f"  Mes {month:02d}/2023")
        df_m = _fetch_month(2023, month)
        if not df_m.empty:
            frames.append(df_m)
        time.sleep(0.5)
    if not frames:
        raise RuntimeError("No se pudo descargar demanda horaria 2023 desde REData.")
    df = pd.concat(frames, ignore_index=True)
    df.to_parquet(cache, index=False)
    return df


def compute_top_100_distributions(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    df = df.copy()
    df["datetime"] = pd.to_datetime(df["datetime"])
    df["hour"] = df["datetime"].dt.hour
    df["month"] = df["datetime"].dt.month
    top100 = df.nlargest(100, "demand")
    return (top100["hour"].value_counts().sort_index(),
            top100["month"].value_counts().sort_index())


def plot_hourly_profile(hour_dist: pd.Series, month_dist: pd.Series,
                         save_dir: Path = FIGURES_DIR):
    save_dir.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    hours = list(range(24))
    hour_vals = [int(hour_dist.get(h, 0)) for h in hours]
    colors_h = ["#e74c3c" if v == max(hour_vals) else "#3498db" for v in hour_vals]
    axes[0].bar(hours, hour_vals, color=colors_h, alpha=0.85, edgecolor="white")
    for h, v in zip(hours, hour_vals):
        if v > 0:
            axes[0].text(h, v + 0.2, str(v), ha="center", va="bottom", fontsize=8)
    axes[0].set_xlabel("Hora del día (UTC+1)", fontsize=11)
    axes[0].set_ylabel("Nº horas en Top-100", fontsize=11)
    axes[0].set_title("Distribución horaria del Top-100\nhoras de mayor demanda (2023)",
                      fontsize=11, fontweight="bold")
    axes[0].set_xticks(range(0, 24, 2))
    axes[0].grid(axis="y", alpha=0.3)

    month_names = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
                   "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
    month_vals = [int(month_dist.get(m, 0)) for m in range(1, 13)]
    third = sorted(month_vals)[-3] if len(month_vals) >= 3 else max(month_vals)
    colors_m = [
        "#e74c3c" if v == max(month_vals)
        else "#e67e22" if v >= third and v > 0
        else "#3498db"
        for v in month_vals
    ]
    axes[1].bar(month_names, month_vals, color=colors_m, alpha=0.85, edgecolor="white")
    for i, v in enumerate(month_vals):
        if v > 0:
            axes[1].text(i, v + 0.2, str(v), ha="center", va="bottom", fontsize=9)
    axes[1].set_xlabel("Mes", fontsize=11)
    axes[1].set_ylabel("Nº horas en Top-100", fontsize=11)
    axes[1].set_title("Distribución mensual del Top-100\nhoras de mayor demanda (2023)",
                      fontsize=11, fontweight="bold")
    axes[1].grid(axis="y", alpha=0.3)

    plt.suptitle("Perfil de las horas críticas de adecuación — España 2023\n"
                 "(Top-100 horas de mayor demanda real)",
                 fontsize=12, fontweight="bold")
    plt.tight_layout()
    out = save_dir / "block_b_6_hourly_profile_top100.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"Figura guardada: {out}")


def run():
    """Pipeline horario completo."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    df = load_or_download_demand_2023()
    hour_dist, month_dist = compute_top_100_distributions(df)

    hour_dist.rename("count").to_csv(RESULTS_DIR / "block_b_top100_hour_distribution.csv")
    month_dist.rename("count").to_csv(RESULTS_DIR / "block_b_top100_month_distribution_2023.csv")

    logger.info(f"\nDistribución HORARIA Top-100 (2023):\n{hour_dist.to_string()}")
    logger.info(f"\nDistribución MENSUAL Top-100 (2023):\n{month_dist.to_string()}")

    peak_hours = [h for h, v in hour_dist.items() if v >= 5]
    solar_hours = [h for h in peak_hours if 9 <= h <= 17]
    logger.info(f"\nHoras pico (≥5 ocurrencias): {peak_hours}")
    logger.info(f"De esas, horas con sol (9h-17h): {solar_hours}")
    if solar_hours and peak_hours:
        pct = len(solar_hours) / len(peak_hours) * 100
        logger.info(f"→ {pct:.0f}% de las horas críticas tienen potencial solar disponible")

    plot_hourly_profile(hour_dist, month_dist)
    return hour_dist, month_dist


if __name__ == "__main__":
    run()
