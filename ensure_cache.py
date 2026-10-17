"""Create the Postgres cache table used for enquiry IP limits. Idempotent."""

import os

CACHE_TABLE = "createch_cache"


def main():
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()

    from django.conf import settings
    from django.core.management import call_command
    from django.db import connection

    engine = settings.DATABASES["default"]["ENGINE"]
    if "postgresql" not in engine:
        return
    if CACHE_TABLE in connection.introspection.table_names():
        return
    call_command("createcachetable", CACHE_TABLE)


if __name__ == "__main__":
    main()
