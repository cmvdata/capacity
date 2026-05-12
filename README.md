# Capacity Market Design and Renewable Investment in Spain

**Proyecto:** Evaluación empírica del valor de adecuación de las tecnologías renovables en el mercado eléctrico español (2019–2024), con aplicación al diseño de mecanismos de capacidad.

**Autor:** Carlo Vilches
**Repositorio relacionado:** [MIBEL Congestion Monitor](https://github.com/cmvdata/mibel-congestion-monitor) (Vilches 2024)

---

## Descripción

Este proyecto cuantifica la **Effective Load Carrying Capability (ELCC)** de las tecnologías renovables en España bajo tres regímenes de mercado distintos, y analiza si los episodios de stress sistémico identificados por el MIBEL Congestion Monitor corresponden a situaciones de insuficiencia de adecuación o a dinámicas de precio/congestión.

### Estructura del análisis

| Bloque | Descripción | Módulo |
|--------|-------------|--------|
| **Block A** | Caracterización empírica de episodios de stress usando outputs validados del MIBEL Congestion Monitor | `src/alert_analysis.py` |
| **Block B (daily)** | ELCC sobre REData diaria. Top-100 días, denominador cap × 24h | `src/elcc_analysis.py` (`freq='daily'`) |
| **Block B (hourly)** | ELCC sobre ENTSO-E horaria (BZN\|ES). Top-2400 horas, denominador cap × 1h. Hidráulica desagregada en embalse/fluyente/bombeo | `src/elcc_analysis.py` (`freq='hourly'`) + `src/entsoe_loader.py` |
| **Block B (perfil horario)** | Validación complementaria con demanda horaria 2023 (figura `block_b_6`) | `src/hourly_profile.py` |

---

## Metodología

### ELCC

El ELCC se define como el factor de planta de cada tecnología durante las Top-N horas (días, en este pipeline diario) de mayor demanda neta:

```
ELCC(tech, N) = mean(gen_tech[top_N_net_demand]) / (cap_instalada_tech × 24)
```

El **denominador es la capacidad instalada (MW) × 24h** obtenida de REData, no `max(gen_tech)`. Esta corrección es importante para tecnologías despachables: la versión con `max(gen)` infla el ELCC de hidráulica en ~+0.22 y el de CCGT en ~+0.26 (nuclear se ve afectada de forma despreciable porque opera al ~92% de su capacidad nominal).

### Excepción Ibérica

Se compara el ELCC en el periodo completo 2019–2024 contra el ELCC excluyendo la Excepción Ibérica (2022–2023). El **sesgo regulatorio** es la diferencia: positivo significa que la intervención inflaba artificialmente el ELCC de la tecnología.

### ELCC marginal (Mills-Wiser 2012)

```
marginal_ELCC(tech) = (ELCC(C + ΔC) − ELCC(C)) / ΔC
```

Con `ΔC = 1 GW`, asumiendo dispatch saturado en horas pico (la generación en top-N no aumenta). Por construcción, todos los marginales son **negativos** (dilución por saturación).

---

## Limitaciones de los datos

Estas limitaciones están reconocidas explícitamente en el pipeline; no se ocultan tras parámetros heurísticos.

1. **Granularidad diaria, no horaria.** REData publica generación por tecnología sólo en resolución diaria sin token. Para tecnologías despachables (hidráulica de embalse, bombeo) cuyo dispatch se concentra en horas pico, el factor de planta diario subestima el factor de planta horario. La hidráulica agregada (fluyente + embalse + bombeo) tiene un caveat metodológico documentado en `docs/Resumen_CM_Spain.md`.
2. **Capacidad instalada mensual forward-fill.** REData publica capacidad instalada mensual; se interpola forward-fill a diario. Pequeño desfase en meses de puesta en servicio masiva de solar PV (2021–2024).
3. **Token ENTSO-E pendiente.** ENTSO-E Transparency Platform (`transparency.entsoe.eu`) publica generación horaria por tecnología para España. El acceso por API requiere un security token gratuito que se obtiene enviando email a `transparency@entsoe.eu` (1–3 días laborables). A fecha 2026-05-04 el token está pendiente; cuando llegue, se sustituirá REData diaria por ENTSO-E horaria sin tocar la lógica de Block B.
4. **Token ESIOS no concedido.** ESIOS daría granularidad por planta y permitiría desagregar hidráulica de embalse vs fluyente. Solicitud pendiente; refinamiento futuro.

---

## Reproduce

### Setup

```bash
git clone https://github.com/cmvdata/cm-spain-renewables.git
cd cm-spain-renewables

pip install -r requirements.txt

# Copia los outputs del MIBEL Congestion Monitor
cp /ruta/a/mibel-congestion-monitor/results/alerts_registry.csv data/mibel_outputs/
```

### Pipeline completo (end-to-end)

```bash
# Daily — REData, sin token (rápido, base histórica)
python main.py --start 2019-01-01 --end 2024-12-31 --freq daily

# Hourly — ENTSO-E (requiere CSVs en data/raw/gen_es_*.csv, ver "Datos ENTSO-E")
python main.py --freq hourly --step build_hourly
python main.py --freq hourly --step block_b
```

Genera **todas las figuras** del entregable:
- `figures/block_a_1..3.png` — Block A (alertas MIBEL)
- `figures/block_b_1..7.png` — Block B daily (REData diaria)
- `figures/block_b_*_hourly.png` — Block B hourly (ENTSO-E con hidráulica desagregada)
- `results/block_*.csv` — tablas

### Pipeline paso a paso

```bash
python main.py --step download         # descarga REData/OMIE/JAO/yfinance
python main.py --step build            # cm_dataset_2019_2024.parquet (REData diaria)
python main.py --step build_hourly --freq hourly  # entsoe_hourly_dataset.parquet
python main.py --step block_a          # Block A (requiere alerts_registry.csv)
python main.py --step block_b          # Block B daily
python main.py --step block_b --freq hourly       # Block B hourly
python main.py --step hourly           # Perfil horario REData 2023 (figura b_6)
```

### Datos ENTSO-E

`freq=hourly` requiere los CSVs mensuales `gen_es_YYYYMM.csv` (dataset 16.1.B&C, BZN|ES) en `data/raw/`. Descarga manual desde [ENTSO-E Transparency](https://transparency.entsoe.eu/) (registro gratuito, sin token; o con token vía email a `transparency@entsoe.eu`).

Si ya has descargado todo:

```bash
python main.py --skip-download              # usa cachés en data/raw/
python main.py --skip-hourly                # omite la descarga 2023 adicional
```

### Tests

```bash
pytest tests/ -v
```

20 tests cubren:
- ELCC sintético (factor 1.0, 0.0, 0.5; tecnologías inexistentes; selección top-N)
- ELCC marginal (signo negativo para ΔC=1GW; consistencia con diferencia finita; magnitud relativa por capacidad base)
- Esquema del dataset (columnas requeridas, tipos numéricos, rango temporal, generación no negativa, regímenes canónicos)

---

## Estructura del repositorio

```
cm-spain-renewables/
├── src/
│   ├── redata_downloader.py    # Descarga REData sin token
│   ├── build_dataset.py        # Ensamblaje del dataset diario
│   ├── alert_analysis.py       # Block A: caracterización de stress (Jaccard MIBEL)
│   ├── elcc_analysis.py        # Block B: ELCC con cap instalada, marginal, sensibilidad
│   └── hourly_profile.py       # Block B: perfil horario 2023 (figura b_6)
├── tests/
│   ├── test_elcc.py
│   ├── test_marginal_elcc.py
│   └── test_dataset_schema.py
├── data/
│   ├── raw/                    # Datos crudos descargados (no versionados)
│   ├── processed/              # Dataset ensamblado (no versionado)
│   └── mibel_outputs/          # Outputs del MIBEL Congestion Monitor
│       └── alerts_registry.csv ← copiar manualmente
├── figures/                    # Figuras generadas (no versionadas)
├── results/                    # Tablas de resultados (no versionadas)
├── legacy/                     # Scripts ad-hoc deprecados (consolidados en src/)
├── scripts/one_off/            # Scripts one-off documentados (e.g. fix_2019.py)
├── main.py                     # Orquestador del pipeline
├── Dockerfile
└── requirements.txt
```

---

## Fuentes de datos

| Fuente | Datos | Token |
|--------|-------|-------|
| [REData (REE)](https://apidatos.ree.es) | Generación diaria por tecnología, capacidad instalada mensual | No |
| [OMIE](https://www.omie.es) | Precios day-ahead España y Portugal | No |
| [JAO SWE CCR](https://publicationtool.jao.eu/swe) | NTC ES↔FR (desde sep 2022) | No |
| [Yahoo Finance](https://finance.yahoo.com) | TTF gas, CO₂ EUA | No |
| MIBEL Congestion Monitor | Registro de alertas 2019–2024 (52,554 horas) | — |
| **ENTSO-E Transparency** *(pendiente)* | Generación horaria por tecnología | **Sí**, gratuito (email `transparency@entsoe.eu`) |
| **ESIOS** *(pendiente)* | Generación por planta | Sí, no concedido |

---

## Resultados esperados

| Hipótesis | Indicador | Resultado esperado |
|-----------|-----------|-------------------|
| ELCC eólico decrece con penetración | Correlación ELCC–penetración | Negativa |
| ELCC solar < ELCC eólico en horas pico | Comparación top-100 | Solar < Eólico (en sistemas con pico nocturno) |
| Excepción Ibérica distorsiona ELCC del CCGT | Diferencia entre regímenes | Significativa (+) |
| Stress MIBEL ≠ insuficiencia adecuación | Jaccard < 0.3 | Confirmar |

---

## Cómo actualizar los datos

```bash
python main.py --start 2025-01-01 --end 2025-12-31
```

No se requieren tokens para el pipeline diario. Si `yfinance` falla:
```bash
pip install --upgrade yfinance
```
