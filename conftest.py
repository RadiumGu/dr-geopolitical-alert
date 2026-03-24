"""Pytest configuration — add src/ to sys.path so tests can import collectors/shared/engine."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
