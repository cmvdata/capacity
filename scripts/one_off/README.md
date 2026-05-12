# scripts/one_off/

Scripts ejecutados una sola vez sobre los datos crudos. No forman parte del
pipeline reproducible (`main.py`) pero se conservan documentados.

## `fix_2019.py`

REData devolvió 2019 con nombres de columna en español (acentos incluidos)
y los demás años en inglés. Este script:

1. Re-descargó 2019 completo en un único bloque (no anual)
2. Normalizó nombres de columna (ES → EN; quitó acentos)
3. Reconstruyó `cm_dataset_2019_2024_clean.parquet` con esquema homogéneo

Una vez generado el dataset _clean, el pipeline lee directamente ese parquet
y este script no necesita re-ejecutarse. Se conserva para reproducibilidad
histórica.
