# legacy/

Scripts deprecados. Su funcionalidad está consolidada en `src/`:

| Script legacy | Reemplazado por |
|---|---|
| `download_full.py` | `python main.py --step download` (usa `src/redata_downloader.py`) |
| `build_full_dataset.py` | `python main.py --step build` (usa `src/build_dataset.py`) |
| `elcc_corrected.py` | `src/elcc_analysis.py::compute_elcc()` (denominador = capacidad instalada) |
| `marginal_elcc.py` | `src/elcc_analysis.py::compute_marginal_elcc()` |
| `hourly_profile.py` | `src/hourly_profile.py` (módulo) |

Conservados como referencia histórica. No se ejecutan desde el pipeline.
