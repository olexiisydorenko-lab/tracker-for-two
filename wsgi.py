"""Точка входу для PythonAnywhere (та будь-якого WSGI-сервера).

На PythonAnywhere файл /var/www/<нік>_pythonanywhere_com_wsgi.py має містити лише:

    import sys
    sys.path.insert(0, "/home/<нік>/tracker-for-two")
    from wsgi import application

Секрети app.py читає з .env поруч із кодом.
"""

from app import application  # noqa: F401
