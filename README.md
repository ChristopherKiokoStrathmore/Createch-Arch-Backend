# Createch Architects - Media/Chrome Backend

Django + DRF service for the Createch Architects site. It stores project
images, project records, and the editable "site chrome" JSON, and exposes:

- Public read API for the Next.js site (`/api/...`)
- Admin write API guarded by an `X-Admin-Key` header (`/api/admin/...`)
- Django admin (`/admin/`) as a fallback editor

It is a standalone Railway service. It does not touch any other backend.

## API surface

Public (no auth):
- `GET  /api/projects/`            list published projects
- `GET  /api/projects/<slug>/`    one published project
- `GET  /api/chrome/`             site chrome JSON

Admin (send header `X-Admin-Key: <ARCH_ADMIN_SECRET>`):
- `GET/POST         /api/admin/media/`                  list / upload image
- `POST             /api/admin/media/reorder/`          [{id, sort}, ...]
- `GET/POST         /api/admin/projects/`               list / create project
- `PATCH/DELETE     /api/admin/projects/<slug>/`        edit / delete
- `POST             /api/admin/projects/reorder/`       [{slug, order}, ...]
- `POST             /api/admin/projects/<slug>/gallery/` attach asset
- `GET/PUT          /api/admin/chrome/`                 read / replace chrome JSON

## Deploy to Railway (once)

1. Put this folder in a Git repo (see "Push to git" below).
2. Railway -> New Project -> Deploy from GitHub repo -> pick it.
3. Add plugin: Postgres. Railway injects `DATABASE_URL` automatically.
4. Add a Volume, mount path `/data` (durable image storage).
5. Variables tab -> add the env vars in the next section.
6. Deploy. The start command runs migrate + collectstatic + gunicorn.
7. Create an admin login for `/admin/`: open the service Shell and run
   `python manage.py createsuperuser`.

## Environment variables

Set these in Railway -> Variables. You must type the two secret values
yourself (I can't type secrets for you).

    DJANGO_SECRET_KEY     <paste a long random string>
    ARCH_ADMIN_SECRET     <paste a long random string>
    DEBUG                 False
    ALLOWED_HOSTS         .railway.app,createch.co.ke,www.createch.co.ke
    CSRF_TRUSTED_ORIGINS  https://createch.co.ke,https://www.createch.co.ke
    CORS_ALLOWED_ORIGINS  https://createch.co.ke,https://www.createch.co.ke
    MEDIA_ROOT            /data/media
    SERVE_MEDIA           True
    DB_SSL_REQUIRE        False

`ARCH_ADMIN_SECRET` must be byte-identical to the value you set on Vercel,
because the Next PIN admin sends it as the `X-Admin-Key` header.

Generate a secret (any one):

    node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
    python -c "import secrets; print(secrets.token_hex(32))"

## Optional: Cloudflare R2 / S3 instead of the volume

Uncomment django-storages + boto3 in requirements.txt, then set
`USE_S3=True` and the `AWS_*` vars documented in `.env.example`.

## Push to git

    cd createch-arch-backend
    git init
    git add .
    git commit -m "Createch media/chrome backend"
    git branch -M main
    git remote add origin <your-new-repo-url>
    git push -u origin main

## Verify after deploy

    curl https://<service>.railway.app/api/chrome/
    curl https://<service>.railway.app/api/projects/
    # admin check (should return 200 with the key, 403 without):
    curl -H "X-Admin-Key: <ARCH_ADMIN_SECRET>" https://<service>.railway.app/api/admin/media/

## Run locally

    pip install -r requirements.txt
    export DJANGO_SECRET_KEY=dev ARCH_ADMIN_SECRET=dev-secret
    python manage.py migrate
    python manage.py runserver
    python manage.py test        # 5 smoke tests
