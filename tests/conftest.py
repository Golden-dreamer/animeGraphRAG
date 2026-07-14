"""Конфигурация pytest: пути к модулям backend/ и parsers/."""
import os
import sys

# Добавляем backend/ и parsers/ в sys.path, чтобы импортировать модули
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "backend"))
sys.path.insert(0, os.path.join(_ROOT, "parsers"))