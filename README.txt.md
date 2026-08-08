# Junction Backend

FastAPI CRUD service using MongoDB Atlas, packaged for local Docker development.

## Configure Atlas

1. Create a free Atlas cluster and database user.
2. Add your current development IP under Atlas Network Access.
3. Copy `.env.example` to `.env` and insert the Atlas Python connection string.
4. URL-encode special characters in credentials and never commit `.env`.

## Run locally

In Git Bash, WSL, macOS, or Linux, run `chmod +x run.sh` and then `./run.sh`.

In PowerShell, run `Copy-Item .env.example .env`, edit `.env`, and run `docker compose up --build`.

Open <http://localhost:8000/docs> for interactive API documentation.

## Login and profile API

`POST /auth/register` creates the account and initial profile. `POST /auth/login`
accepts an OAuth2 form with the email in `username` and returns a bearer token.
Use that token for the protected `GET /profile` and `PATCH /profile` routes.

```json
{"email":"person@example.com","password":"strong-password","display_name":"Person"}
```

Customers without email can use the phone flow. Phone numbers must use E.164
format, such as `+919876543210`:

1. `POST /auth/otp/request` with `display_name` and `phone_number`.
2. `POST /auth/otp/verify` with `phone_number` and the six-digit `otp`.
3. Use the returned bearer token with `/profile`.

`OTP_DEBUG=true` returns `debug_otp` for local testing. Set it to `false` in
production and implement the SMS provider inside `send_otp` in `app/login.py`.

## CRUD API

| Operation | Method | Path |
|---|---|---|
| Create | `POST` | `/items` |
| Read | `GET` | `/items/{item_id}` |
| Update | `PUT` | `/items/{item_id}` |
| Delete | `DELETE` | `/items/{item_id}` |

Example create body:

```json
{"store_id":"store-mumbai","name":"Running shoe","description":"Daily trainer","price":99.99}
```

The driver reuses a connection pool configured by `MONGODB_MIN_POOL_SIZE` and `MONGODB_MAX_POOL_SIZE`. Tune this alongside Atlas connection limits before adding API containers. For production, store credentials in the hosting platform, restrict network access, enable monitoring and backups, and select a paid tier using measured traffic.

Atlas is a strong match for multi-store e-commerce because it offers flexible documents, rich queries, indexes, transactions, and managed scaling. Firestore is more compelling when Firebase client SDKs, realtime listeners, or offline-first mobile behavior are central. Moving Atlas from free development to a dedicated cluster does not require changing this application's database API.
