"""Точка входу для WSGI-сервера.

Render: gunicorn wsgi:application (див. render.yaml).
Секрети app.py читає зі змінних середовища або з .env поруч із кодом.
"""

from app import application  # noqa: F401
