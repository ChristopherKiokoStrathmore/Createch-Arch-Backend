# Createch Architects - Media/Chrome Backend

Django + DRF service for the Createch Architects site. It stores project
images, project records, and the editable "site chrome" JSON, and exposes:

- Public read API for the Next.js site (`/api/...`)
- Admin write API guarded by an `X-Admin-Key` header (`/api/admin/...`)
- Django admin (`/admin/`) as a fallback editor

It is a standalone Railway service. It does not touch any other backend.

## API surface

Public (no auth):
- `GET  /api/health/`             `{ok: true}` or 503. Checks the database, and `createch_cache` on Postgres
- `GET  /api/projects/`            list published projects
- `GET  /api/projects/<slug>/`    one published project
- `GET  /api/chrome/`             site chrome JSON, without `draft` or `versions`
- `POST /api/enquiries/`          public enquiry (honeypot `company`, rate limited). Stored with `status: "new"`

Admin (send header `X-Admin-Key: <ARCH_ADMIN_SECRET>`):
- `GET/POST         /api/admin/media/`                  list / upload image (long edge capped at 2400px, EXIF removed, WebP or JPEG)
- `POST             /api/admin/media/reorder/`          `{"order": [id, ...]}` or `[{id, sort}, ...]`
- `GET/POST         /api/admin/projects/`               list / create project
- `PATCH/DELETE     /api/admin/projects/<slug>/`        edit / delete
- `POST             /api/admin/projects/reorder/`       `{"order": [slug, ...]}` or `[{slug, order}, ...]`
- `POST             /api/admin/projects/<slug>/gallery/` attach asset
- `GET/PUT          /api/admin/chrome/`                 read / replace chrome JSON. Each publish keeps the previous public document
- `GET              /api/admin/chrome/versions/`        last 10 published versions (`id`, `label`, `created_at`, `data`)
- `POST             /api/admin/chrome/versions/<id>/restore/`  publish that version; the current `draft` stays
- `GET/POST/PUT     /api/admin/pin/`                    /admin PIN: state / verify / replace. Any printable Unicode, 4–128 characters, hashed and compared exactly. POST verify is limited to 10/minute per IP (429, `Retry-After`)
- `GET              /api/admin/enquiries/?status=`      paginated inbox, newest first. `status` is `new`, `replied`, or `archived`
- `GET              /api/admin/enquiries/summary/`      `{"new": 0, "replied": 0, "archived": 0}`
- `GET/PATCH/DELETE /api/admin/enquiries/<id>/`         read one / set `{"status": "replied"}` / delete

## Deploy to Railway (once)

1. Put this folder in a Git repo (see "Push to git" below).
2. Railway -> New Project -> Deploy from GitHub repo -> pick it.
3. Add a Postgres database. It is NOT wired in automatically: on the web
   service add the variable `DATABASE_URL=${{Postgres.DATABASE_URL}}`.
   The app refuses to start without it. SQLite is not used on Railway
   (it used to be a silent fallback, and every deploy wiped it).
4. Add a Volume on the web service, mount path `/data` (durable image
   storage — without it every upload is wiped on redeploy).
5. Variables tab -> add the env vars in the next section.
6. Deploy. The start command runs migrate + collectstatic + gunicorn.
7. Create an admin login for `/admin/`: open the service Shell and run
   `python manage.py createsuperuser`.

## Environment variables

Set these in Railway -> Variables before deploying. The process logs one
warning line at startup, `Missing environment variables: ...`, naming every
required variable that is still empty. It refuses to boot when `DEBUG` is
off and `DJANGO_SECRET_KEY` is missing, shorter than 32 characters, or still
the dev default.

Confirm these on Railway (the deploy will not start without the first one):

    DJANGO_SECRET_KEY     required, 32+ random characters, not the dev default
    DATABASE_URL          ${{Postgres.DATABASE_URL}}
    ARCH_ADMIN_SECRET     required for admin writes, 32+ characters, same value as Vercel
    DEBUG                 False
    ALLOWED_HOSTS         optional. If unset, defaults to .up.railway.app,createch.co.ke,www.createch.co.ke
    CSRF_TRUSTED_ORIGINS  https://createch.co.ke,https://www.createch.co.ke
    CORS_ALLOWED_ORIGINS  https://createch.co.ke,https://www.createch.co.ke
    MEDIA_ROOT            /data/media
    SERVE_MEDIA           True
    DB_SSL_REQUIRE        False

`ALLOWED_HOSTS` is not `*` in production. Leaving it unset still serves
`*.up.railway.app`, `createch.co.ke`, and `www.createch.co.ke`. Set it
explicitly if you add another domain. A leading dot matches subdomains.

Optional size caps (defaults are fine):

    DATA_UPLOAD_MAX_MEMORY_SIZE   1048576     JSON parse cap, bytes
    ARCH_MAX_UPLOAD_BYTES         8388608     image upload cap, bytes
    ENQUIRY_MAX_BODY_BYTES        32768
    CHROME_MAX_BODY_BYTES         524288

Email alerts are optional. If any of these are unset, enquiries are still
saved and the process logs a warning instead of failing the request:

    EMAIL_HOST
    EMAIL_PORT
    EMAIL_HOST_USER
    EMAIL_HOST_PASSWORD
    EMAIL_USE_TLS          true or false
    DEFAULT_FROM_EMAIL
    ENQUIRY_NOTIFY_TO      comma-separated recipients

`ARCH_ADMIN_SECRET` must be byte-identical to the value you set on Vercel,
because the Next PIN admin sends it as the `X-Admin-Key` header.

`DATABASE_URL` is Railway Postgres. The admin PIN is a password, not a
numeric code: letters, numbers, symbols, and spaces, any printable Unicode
from 4 to 128 characters. `PUT /api/admin/pin/` stores it as a Django
password hash (`AdminPin.pin_hash`, never the plaintext). Verification
compares that hash exactly — no trimming and no case folding — so a PIN
already stored as `1234` keeps working. A wrong PIN returns `{ok: false}`.
Too short, too long, or non-printable input is 400. Enquiries from
`POST /api/enquiries/` are rows in
the same database. `ALLOW_SQLITE` is for local experiments only — do not
set it on Railway.

Public `POST /api/enquiries/` accepts `name`, `email`, `phone`,
`project_type`, `site`, `stage`, `timeline`, `message`. `projectType` is
accepted as an alias of `project_type`, and `location` as an alias of
`site`. If both the canonical name and the alias are sent, the canonical
name wins. A non-empty `company` field is a honeypot: the API answers 201
and stores nothing. Bodies over `ENQUIRY_MAX_BODY_BYTES` (and chrome writes
over `CHROME_MAX_BODY_BYTES`) return 413.
Anonymous callers are limited to 10 posts an hour per `REMOTE_ADDR` and
5 an hour per email address (429). The Next server may send
`X-Admin-Key` plus `X-Createch-Client-Ip` so each visitor has their own
bucket; a missing or wrong key ignores that header.

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

    curl https://<service>.railway.app/api/health/
    curl https://<service>.railway.app/api/chrome/
    curl https://<service>.railway.app/api/projects/
    # admin check (should return 200 with the key, 403 without):
    curl -H "X-Admin-Key: <ARCH_ADMIN_SECRET>" https://<service>.railway.app/api/admin/media/

## Run locally

`DEBUG=True` or `ALLOW_SQLITE=True` keeps a short `DJANGO_SECRET_KEY` working
on your machine. Neither flag relaxes that check on Railway.
`ARCH_ADMIN_SECRET` must still be at least 32 characters or every admin
route returns 403.

    pip install -r requirements.txt -r requirements-dev.txt
    export DEBUG=True ALLOW_SQLITE=True
    export DJANGO_SECRET_KEY=local-dev-only-change-me-not-for-prod
    export ARCH_ADMIN_SECRET=local-dev-admin-secret-not-for-production
    python manage.py migrate
    python manage.py runserver
    python manage.py test
    ruff check .
    pip-audit -r requirements.txt
