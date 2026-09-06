# Companion frontends for branded QR posters

Backend API is in this repo (`GET /qr/taglines`, `POST /qr/generate`). Apply the same branch name `cursor/qr-code-generator-4f24` on the two frontends.

## jtoday (`ancqit/jtoday`)

- Add a **QR** control at the **bottom of Shops/Services** (`marketplace-panel`).
- Pressing it opens a modal listing taglines / sayings / thoughts from `GET /qr/taglines`.
- Create + download calls `POST /qr/generate` with the current Junction (`city` + `locality` from the welcome profile). City-wide scope omits locality.
- New files: `junction-web/src/app/core/qr.api.ts`, `junction-web/src/app/components/qr-create-modal/*`.
- Treat `/qr/*` as public in `session.interceptor.ts` (same as notices).

## junctionFrontweb (`ancqit/junctionFrontweb`)

- Add nav + route **Promotion** (`/back-office/promotion`).
- Shop and Junction come from Overview (`CurrentShopService`).
- User selects up to three sayings, sees a promotion document preview, then creates/downloads the branded QR (shop name + `store_id` included).
- New files: `apps/back-office/src/app/core/qr.api.ts`, `apps/back-office/src/app/features/promotion/*`.
- Mark `/qr/*` as `none` auth in back-office and shell `api-auth.ts`.

Patches were prepared locally but this agent cannot push to those repos (403). Copy from a machine with write access, or re-apply the files listed above.
