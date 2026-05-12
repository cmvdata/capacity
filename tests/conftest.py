"""Permite importar módulos de `src/` en los tests sin instalar el paquete."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
