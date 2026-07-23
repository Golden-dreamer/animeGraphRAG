"""Конфигурация pytest: пути к модулям."""
import os
import sys

# Добавляем все директории с кодом в sys.path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "backend"))
sys.path.insert(0, os.path.join(_ROOT, "parsers"))
sys.path.insert(0, os.path.join(_ROOT, "parsers", "anime"))
sys.path.insert(0, os.path.join(_ROOT, "parsers", "user_anime"))
sys.path.insert(0, os.path.join(_ROOT, "parsers", "user_user"))
