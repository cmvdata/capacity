# paper/

Versión académica del análisis (`docs/Resumen_CM_Spain.md` reescrito como
artículo formal).

## Archivos

- `cm_spain_paper.tex` — fuente LaTeX (article 11pt, single column)
- `refs.bib` — bibliografía BibTeX
- `cm_spain_paper.pdf` — generado por compilación (no versionado)

## Cifras

Todas las cifras numéricas del paper proceden de los CSVs en
`../results/`. Si re-ejecutas el pipeline (`python main.py --freq hourly`)
y los CSVs cambian, **actualiza también las tablas LaTeX**.

Mapeo tabla LaTeX → CSV:

| Tabla en paper | CSV de origen |
|---|---|
| Table 1 (`tab:elcc-comparison`) | `block_b_elcc_excepcion_comparison.csv` + `..._hourly.csv` (col "ELCC sin Exc. Ibérica") |
| Table 2 (`tab:excepcion`) | mismos CSVs (col "Sesgo regulatorio") |
| Table 3 (`tab:marginal`) | `block_b_marginal_elcc_hourly.csv` |
| Table 4 (`tab:sensitivity`) | `block_b_hydro_cap_sensitivity.csv` |
| Table 5 (`tab:benchmarks`) | hardcoded (UK/IE/PL desde `EU_BENCHMARKS` en `src/elcc_analysis.py`) |

## Figuras referenciadas

Las figuras se referencian con ruta relativa `../figures/`:

- `block_b_1_elcc_heatmap_hourly.png` (Figura 1)
- `block_b_6_hourly_profile_top100.png` (Figura 2)
- `block_a_2_overlap_table.png` (Figura 3)

## Compilación

LaTeX no está instalado en esta máquina por defecto. Para generar el PDF:

### Opción 1 — TeX Live / MiKTeX local

```bash
cd paper/
pdflatex cm_spain_paper.tex
bibtex   cm_spain_paper
pdflatex cm_spain_paper.tex
pdflatex cm_spain_paper.tex
```

O con `latexmk`:

```bash
cd paper/
latexmk -pdf cm_spain_paper.tex
```

### Opción 2 — Overleaf

Subir `cm_spain_paper.tex`, `refs.bib` y la carpeta `../figures/` a un
proyecto nuevo en Overleaf. Cambiar las rutas `../figures/...` por
`figures/...` si la estructura de carpetas cambia.

### Opción 3 — Docker

```bash
docker run --rm -v "$(pwd):/data" -w /data ghcr.io/xu-cheng/texlive-full:latest \
  latexmk -pdf cm_spain_paper.tex
```

## Esperado

- 12--15 páginas, una columna
- 5 tablas con `booktabs`
- 3 figuras
- ~12 referencias BibTeX
