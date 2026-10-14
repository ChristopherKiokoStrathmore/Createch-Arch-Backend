web: python manage.py migrate --noinput && python ensure_cache.py && python manage.py collectstatic --noinput && gunicorn config.wsgi --bind 0.0.0.0:$PORT --log-file -
