"""
main.py
-------
Orquestador del pipeline completo de cm-spain-renewables.

Uso:
  python main.py                        # pipeline completo 2019–2024
  python main.py --start 2022-01-01     # desde 2022
  python main.py --step download        # solo descarga de datos
  python main.py --step build           # solo ensamblaje del dataset
  python main.py --step block_a         # solo Block A (alertas MIBEL)
  python main.py --step block_b         # solo Block B (ELCC, figuras 1-5,7)
  python main.py --step hourly          # solo perfil horario (figura 6)
  python main.py --skip-download        # usa datos ya descargados

Outputs principales:
  results/                # tablas CSV
  figures/block_a_1..3.png, block_b_1..7.png
  data/processed/cm_dataset_2019_2024.parquet
  data/processed/elcc_results.parquet
"""

import argparse
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MAIN] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("pipeline.log", mode="a"),
    ],
)
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent / "src"))


def step_download(start: str, end: str):
    from redata_downloader import (
        download_generation_daily,
        download_demand_5min,
        download_installed_capacity,
    )
    raw_dir = Path("data/raw")
    logger.info(f"Descargando datos {start} → {end}")

    df_gen = download_generation_daily(f"{start}T00:00", f"{end}T23:59", raw_dir)
    logger.info(f"Generación diaria: {len(df_gen):,} días")

    df_cap = download_installed_capacity(f"{start}T00:00", f"{end}T23:59", raw_dir)
    logger.info(f"Capacidad instalada: {len(df_cap)} meses")

    # Demanda: descargamos solo el primer y último año para no saturar la API
    for year in [start[:4], end[:4]]:
        df_dem = download_demand_5min(
            f"{year}-01-01T00:00", f"{year}-12-31T23:59", raw_dir
        )
        logger.info(f"Demanda {year}: {len(df_dem):,} registros")

    logger.info("Descarga completada.")


def step_build(start: str, end: str):
    from build_dataset import build_dataset
    df = build_dataset(start, end)
    logger.info(f"Dataset ensamblado: {len(df):,} días | {len(df.columns)} columnas")
    return df


def step_block_a():
    from alert_analysis import run_block_a
    run_block_a()


def step_block_b(freq: str = "daily"):
    from elcc_analysis import run_block_b
    run_block_b(freq=freq)


def step_build_hourly(start: str, end: str):
    """Construye `data/processed/entsoe_hourly_dataset.parquet` desde los CSVs ENTSO-E."""
    from entsoe_loader import build_hourly_dataset
    build_hourly_dataset(start, end)


def step_hourly():
    """Perfil horario (figura block_b_6). Requiere descarga de demanda 2023."""
    from hourly_profile import run as run_hourly
    run_hourly()


def main():
    parser = argparse.ArgumentParser(
        description="cm-spain-renewables pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--start", default="2019-01-01", help="Fecha inicio (YYYY-MM-DD)")
    parser.add_argument("--end", default="2024-12-31", help="Fecha fin (YYYY-MM-DD)")
    parser.add_argument(
        "--step",
        choices=["download", "build", "build_hourly", "block_a", "block_b", "hourly", "all"],
        default="all",
        help="Paso a ejecutar",
    )
    parser.add_argument(
        "--freq",
        choices=["daily", "hourly"],
        default="daily",
        help="Frecuencia de Block B: daily (REData) | hourly (ENTSO-E). "
             "hourly requiere `--step build_hourly` previamente o `--step all`.",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Omite la descarga y usa datos ya existentes",
    )
    parser.add_argument(
        "--skip-hourly",
        action="store_true",
        help="Omite el perfil horario (descarga adicional 2023)",
    )
    args = parser.parse_args()

    t0 = time.time()
    logger.info("=" * 60)
    logger.info("cm-spain-renewables | Capacity Market & Renewable Investment")
    logger.info(f"Periodo: {args.start} → {args.end}")
    logger.info(f"Paso: {args.step}")
    logger.info("=" * 60)

    try:
        if args.step in ("download", "all") and not args.skip_download:
            step_download(args.start, args.end)

        if args.step in ("build", "all"):
            step_build(args.start, args.end)

        if args.step in ("build_hourly", "all") and args.freq == "hourly":
            step_build_hourly(args.start, args.end)

        if args.step in ("block_a", "all"):
            step_block_a()

        if args.step in ("block_b", "all"):
            step_block_b(freq=args.freq)

        if args.step in ("hourly", "all") and not args.skip_hourly:
            step_hourly()

    except Exception as e:
        logger.error(f"Pipeline fallido en paso '{args.step}': {e}", exc_info=True)
        sys.exit(1)

    elapsed = time.time() - t0
    logger.info(f"\nPipeline completado en {elapsed:.1f}s")
    logger.info("Resultados en: results/ | Figuras en: figures/")


if __name__ == "__main__":
    main()
