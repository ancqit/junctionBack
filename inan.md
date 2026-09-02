# Junction Backend — API Reference

Base URL (production): `https://junctionback.onrender.com`

Most protected endpoints require a Bearer token from login/OTP:

```
Authorization: Bearer <access_token>
```

**Roles:** `admin`, `owner`, `viewer`  
**Plans:** Free Trial (40/15d), Starter (10/₹999/yr), Growth (80/₹2999/yr), Premium (150/₹5999/yr) — attached to each **shop**. Paid plans/packs activate **after payment completes**, then products can be added. Extra packs: 40 products / ₹999. Admins are exempt from plan limits.

**`store_id`** on products, employees, and orders = the **shop ID** from `/shops`.

## Security

- **JWT required** for business data. Send `Authorization: Bearer <access_token>` on every request except the public auth/plan/terms endpoints listed below.
- **`junction.today` guest sessions:** call `POST /session` to get a short-lived JWT (`expires_in` default **100 seconds**). Use that Bearer token for `/locations/*`, catalog **shop/product reads**, and **`POST /orders`** (place an order at any real shop). No user login required.
- **CORS** (`CORS_ORIGINS`) limits which browser sites (e.g. `https://junction.today`) may call the API. It does not block `curl` or Postman — session/user JWT checks do.
- **Shop-scoped writes** (and owner-app reads) require the user to own the shop (or be admin). `junction.today` session tokens are **read-only** for shops/products, except they may **create** orders via `POST /orders`.
- **Image search routes** (`/queries`, `/products/images/suggest`) require JWT, an active plan, `PEXELS_API_KEY`, and are rate-limited (`RATE_LIMIT_AI`, default `30/hour`).
- **Auth routes** are rate-limited (`RATE_LIMIT_AUTH`, default `20/minute`).
- **Guest order creates** (`POST /orders`) are rate-limited (`RATE_LIMIT_GUEST_ORDERS`, default `30/minute`).
- **Public (no JWT):** `/health`, `POST /session`, `/auth/register`, `/auth/login`, `/auth/otp/*`, `/auth/roles`, `GET /plans`, `/terms-and-conditions`, `/auth/digilocker/callback`.
- Set `OPENAPI_ENABLED=false` in production to hide `/docs`.
- **Render health check:** use `GET /health` (returns `{"status":"ok"}`), not `/docs`.

---

## Health

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `GET` | `/health` | Public | Liveness probe. Returns `{"status":"ok"}`. Set this as the Render Health Check Path. |

---

## Session (`/session`) — for `junction.today`

Guest security when there is no user login. Intended for the **junction.today** front end.

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `POST` | `/session` | Public | Create a guest session. Returns `session_id`, `access_token` (JWT), `expires_in` (seconds), `audience` (`junction.today`). Rate-limited (`RATE_LIMIT_AUTH`). |

**Response:**
```json
{
  "session_id": "...",
  "access_token": "<jwt>",
  "token_type": "bearer",
  "expires_in": 100,
  "audience": "junction.today"
}
```

**Front-end flow (`junction.today`):**
1. `POST /session` → store `access_token`
2. Call APIs with `Authorization: Bearer <access_token>`:
   - Locations: `/locations/cities`, `/locations/localities`, `/locations/add-junction`
   - Shop names + phone switch: `/session/shops`, `/session/shops/{id}` (see below)
   - Shops (read): `/shops`, `/shops/{id}`, `/shops/by-name/{name}`, `/shops/by-location?city=&locality=`, `/shops/{id}/products`, `/shops/types`
   - Products (read): `/products`, `/products/{id}`, `/products/by-location?city=&locality=`, `/products/images/{stored_image_id}`
   - Orders (create): `POST /orders` (any real shop; rate-limited; optional `source: "junction.today"`)
3. When the token expires (~100s), call `POST /session` again for a new one

Optional env: `SESSION_EXPIRE_SECONDS=100` (default 100).

Session JWTs are **not** user login tokens — they unlock guest/catalog routes and order placement. Creating or editing shops/products still requires a normal owner login JWT.

### Shop name + phone switch (`GET /session/shops`)

Requires a **session JWT**. Each shop includes `phone_number` and a boolean `show_phone` switch (same idea as `is_open`). The switch **does not** null the number — the front end shows or hides it.

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `GET` | `/session/shops` | Session | List `{ id, name, phone_number, show_phone }`. Optional `shop_id` / `store_id`. Optional `city` + `locality` together. |
| `GET` | `/session/shops/{shop_id}` | Session | One shop with `phone_number` and `show_phone`. |

```json
{ "id": "...", "name": "Ram Kirana", "phone_number": "+919876543210", "show_phone": false }
```

Owner toggles the switch (persisted on the shop, like `is_open`):
- `PUT /shops/phone-status` `{ "name": "Ram Kirana", "show_phone": true }`
- or `PUT /shops/{shop_id}` `{ "show_phone": true }`

Front-end: bind a switch to `show_phone`. Off → hide the number in the UI. On → show `phone_number`. Catalog `GET /shops` also returns both fields.

---

## Authentication (`/auth`)

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `POST` | `/auth/register` | Public | Register with email + password. Returns JWT, user, plan, and role. |
| `POST` | `/auth/login` | Public | Login with email + password (OAuth2 form: `username`, `password`). Returns JWT. |
| `GET` | `/auth/recaptcha-params` | Public | Identity Platform reCAPTCHA site key for web invisible reCAPTCHA. |
| `POST` | `/auth/otp/request` | Public | Send OTP via GCP Identity Platform. **Web (classic default):** `{ display_name, phone_number, recaptcha_token, client_type: "web" }` → backend sends only `recaptchaToken` (no `clientType` / `recaptchaVersion`). Set `GCP_RECAPTCHA_MODE=enterprise` if Enterprise is enabled. **Android APK:** `{ display_name, phone_number, play_integrity_token, client_type: "android" }` (Play Integrity nonce = SHA-256 of E.164 phone, Base64 URL-safe no-wrap). |
| `POST` | `/auth/otp/verify` | Public | Verify OTP and log in (or create account). Body: `phone_number`, `otp`, `session_info`. Phone is the account key. |
| `POST` | `/auth/catalog-otp/request` | Public | junction.today checkout: same GCP SMS as owner OTP + web reCAPTCHA. Body: `{ phone_number, recaptcha_token, display_name?, client_type?: "web" }`. Does **not** create an owner account. |
| `POST` | `/auth/catalog-otp/verify` | Public | Verify catalog SMS OTP. Body: `phone_number`, `otp`, `session_info`. Returns `{ verified, phone_number }` only. |
| `POST` | `/auth/refresh` | Bearer | Issue a fresh token for the current user. |
| `GET` | `/auth/roles` | Public | List available roles (`admin`, `owner`, `viewer`) with labels and descriptions. |
| `GET` | `/auth/me` | Bearer | Get current user, role, and plan summary. |

**Login response includes:** `access_token`, `user`, `plan`, `role`.

`user.plan` is a short plan slug for onboarding: `"free_trial"` while on an active free trial, `"starter"` after the user selects Starter via `POST /plans/select`, or `""` otherwise (Growth, Premium, expired, or not yet selected).

**Role resolution on every login:**
1. Check admin list (`ADMIN_LIST_JSON`, `ADMIN_EMAIL`, `ADMIN_PHONE`, or `admin.json`) — admins are always `admin`
2. Else if trial or grace period has ended (`plan.viewing_applied`) → `viewer`
3. Else → `owner`

There is no deactivated state. Users are either `admin`, `owner`, or `viewer`.

---

## DigiLocker (`/auth/digilocker`)

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `GET` | `/auth/digilocker/connect` | Bearer | Start DigiLocker OAuth. Returns `authorization_url` to redirect the user. Requires verified phone. |
| `GET` | `/auth/digilocker/callback` | Public | OAuth callback. Query: `state`, `code` (or `error`). Marks user as DigiLocker-verified. |

---

## Profile (`/profile`)

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `GET` | `/profile` | Bearer | Get logged-in user's profile (name, bio, avatar, email, phone). |
| `PATCH` | `/profile` | Bearer | Update `display_name`, `bio`, and/or `avatar_url`. |

---

## Notices (`/notices`)

Daily notice board for shop offers and announcements. One notice per shop per UTC calendar day.

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `POST` | `/notices` | Bearer | Post or update today's notice. Body: `{ "store_id": "<shop_id>", "message": "20% off today!" }` (`shop_id` alias works). |
| `GET` | `/notices/today?store_id=<shop_id>` | Public | Today's notice for a shop as a 0-or-1 list. Also at `/api/notices/today`. `shop_id` query alias works. **No JWT.** Missing/blank `store_id` or no notice → `[]`. |
| `GET` | `/notices` | Public | List today's notices. Optional `store_id` / `shop_id`. Also at `/api/notices`. |

---

## Shops (`/shops`)

Shops are the main entry point. Products and employees are linked via `store_id` = shop `id`.

**Ownership model**
- **Phone / mobile number** = the user account (OTP login). One phone → one user → **many shops**.
- Shop names must be unique **per mobile number** (same phone cannot create two shops with the same name).
- Each shop has its own **plan** (billing/limits live on the shop, not the phone).
- **Products** and **employees** belong to a shop (`store_id`).

**Shop plans** (billing and product limits live on each shop)
| Plan | Products | Price | Duration |
|------|----------|-------|----------|
| Free Trial | 40 | INR 0 | 15 days |
| Starter | 10 | INR 999 | 1 year |
| Growth | 80 | INR 2999 | 1 year |
| Premium | 150 | INR 5999 | 1 year |

**Extra product packs:** after a shop’s plan allowance is used, buy packs of **40 products for INR 999** via `POST /product-bucket/purchase` (then complete payment).

**Paid plan / pack flow (Razorpay — payment required before products unlock):**
1. `POST /shops/{shop_id}/plan/purchase` with `{ "plan_type": "starter" }` → pending payment (`amount_inr`, `id`)
2. `POST /payments/{payment_id}/checkout` → Razorpay order + `key_id`
3. Collect payment in the app via Razorpay Checkout (UPI/card)
4. `POST /payments/{payment_id}/verify` with Razorpay signature fields → plan becomes **active**
5. `POST /products` with that shop’s `store_id` (up to plan limit)
6. When plan products are full: `POST /product-bucket/purchase` → checkout → verify → then add more products

New shops start on **Free Trial** (no payment). Paid plans activate only after payment completion.

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `GET` | `/shops` | Bearer (user **or** session) | List shops. Owner JWT: own shops (admin: all). `junction.today` session: full public catalog. Includes `show_phone` (boolean) and `phone_number`. Optional `shop_id` / `store_id`. |
| `GET` | `/shops/{shop_id}` | Bearer (user **or** session) | Get one shop by ID. Session may read any shop. |
| `GET` | `/shops/{shop_id}/products` | Bearer (user **or** session) | List products for that shop. `junction.today` flow: pick a shop from `/shops/by-location`, then call this. |
| `GET` | `/shops/{shop_id}/plan` | Bearer (user) | Get this shop's plan (limits/billing are per shop). |
| `POST` | `/shops/{shop_id}/plan/purchase` | Bearer (user) | Start a paid plan purchase. Body: `{ "plan_type": "starter" }`. Returns **pending** payment; activate via Razorpay checkout + verify. |
| `POST` | `/shops/{shop_id}/plan/select` | Bearer (user) | Alias of `plan/purchase` (pending payment). Admins activate immediately. |
| `GET` | `/shops/by-name/{shop_name}` | Bearer (user **or** session) | Find shop(s) by name (case-insensitive). |
| `GET` | `/shops/by-location` | Bearer (user **or** session) | List shops for a location. Query: `city`, `locality` (both required). For `junction.today` session: public catalog in that city/locality. |
| `POST` | `/shops` | Bearer (user) | Create another shop for the logged-in phone. Starts on Free Trial (40 products / 15 days). Name must be unique **per mobile number**. Phone is taken from the logged-in user. |
| `PUT` | `/shops/{shop_id}` | Bearer (user) | Update shop `name`, `city`, `locality`, `open_time`, `closed_time`, `is_open`, and/or `show_phone`. |
| `PUT` | `/shops/open-status` | Bearer (user) | Set open/closed for display. Body: `{ "name": "Shop Name", "is_open": true }` or `false`. Finds the caller's shop by name (case-insensitive) and updates `is_open`. |
| `PUT` | `/shops/phone-status` | Bearer (user) | Toggle mobile visibility. Body: `{ "name": "Shop Name", "show_phone": true }` or `false`. Same pattern as `open-status`. |
| `DELETE` | `/shops/{shop_id}` | Bearer (user) | Delete a shop. |
| `GET` | `/shops/types` | Bearer (user **or** session) | List all shop/business types. |

Shop responses include `open_time`, `closed_time`, `is_open`, `show_phone`, and `phone_number`. Bind UI switches to `is_open` and `show_phone`; do not null the number when the phone switch is off.

---

## Products (`/products`)

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `GET` | `/products` | Bearer (user **or** session) | List products. Optional `store_id`. Owner JWT is shop-scoped; `junction.today` session sees the public catalog. |
| `GET` | `/products/by-location` | Bearer (user **or** session) | List products for shops in a location. Query: `city`, `locality` (both required). |
| `GET` | `/products/{product_id}` | Bearer (user **or** session) | Get one product by ID. |
| `POST` | `/products` | Bearer (user) | Create product for a shop. Enforces **that shop’s** plan + bucket capacity. Body includes `store_id`, `sku`, `name`, `category`, `price`, stock, etc. |
| `PUT` | `/products/{product_id}` | Bearer (user) | Update product fields (name, price, stock, status, image, etc.). |
| `DELETE` | `/products/{product_id}` | Bearer (user) | Delete a product. |
| `POST` | `/products/images/suggest` | Bearer (user) | Suggest up to **10** Pexels CDN images for a product name. Body: `{ "product_name": "wireless earbuds" }`. Requires active plan. |
| `GET` | `/products/images/{stored_image_id}` | Bearer (user **or** session) | Serve a stored product image (from upload or query flow). |
| `POST` | `/products/{product_id}/image/cdn` | Bearer (user) | Set hero image to an external CDN URL. Body: `{ "cdn": "https://..." }`. |
| `POST` | `/products/{product_id}/image/use` | Bearer (user) | Download one CDN image and add it to the gallery (max 5). Body: `{ "cdn": "https://..." }`. |
| `POST` | `/products/{product_id}/images` | Bearer (user) | Attach up to **5** chosen CDN images to the product. Body: `{ "cdns": ["https://...", "..."] }`. Replaces the gallery. |
| `POST` | `/products/{product_id}/image/upload` | Bearer (user) | Upload image file (`multipart/form-data`, field: `file`). Stores in GridFS and adds to gallery (max 5). |

**Product images:** Each product supports up to **5** images in `images[]`. The first image is also exposed as `image` / `image_cdn` (hero).

**Suggested image flow:**
1. `GET /queries?query=wireless+earbuds&per_page=10` or `POST /products/images/suggest` → get up to **10** Pexels CDN options
2. User picks up to **5** → `POST /products/{product_id}/images` with `{ "cdns": ["...", "..."] }`
3. Or add one at a time via `/image/use` or `/image/upload`

**Other image flows:** CDN link only → `/image/cdn` · Manual search → `/queries` + `/image/use`

---

## Product bucket (`/product-bucket`)

Extra product capacity for a shop after its plan allowance is used. Sold in packs of **40 products for INR 999**. Capacity is added **only after payment completes**.

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `GET` | `/product-bucket` | Bearer (user) | Capacity for a shop. Query: `store_id` / `shop_id`, and/or `product_id` (looks up that product's shop). |
| `POST` | `/product-bucket/purchase` | Bearer (user) | Start pack purchase. Body: `{ "store_id": "<shop_id>", "packs": 1 }` or `{ "product_id": "<product_id>", "packs": 1 }`. Returns pending payment. |
| `POST` | `/product-bucket/slots` | Bearer (user) | Alias of `/purchase` (pending payment). Admins apply packs immediately. |

Then call `POST /payments/{payment_id}/checkout` and `POST /payments/{payment_id}/verify` to add the slots. Total capacity = shop plan `max_products` + `extra_slots`.

---

## Shop payments (`/payments`)

Plan and product-pack purchases for a shop. Paid plans/packs unlock product capacity only when status becomes `paid` and fulfillment runs.

**Razorpay collection (Starter / Growth / Premium + product packs):**
1. `POST /shops/{shop_id}/plan/purchase` or `POST /product-bucket/purchase` → pending `payment_id`
2. `POST /payments/{payment_id}/checkout` → Razorpay `order_id` + public `key_id`
3. Open Razorpay Checkout in the app (UPI/card)
4. `POST /payments/{payment_id}/verify` with `{ razorpay_order_id, razorpay_payment_id, razorpay_signature }` → plan/packs activate
5. Backup: configure webhook `POST /payments/webhook/razorpay` (event `payment.captured`)

Env: `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`. When Razorpay is configured, owners cannot use `/complete` (admins still can for support).

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `GET` | `/payments?store_id=` | Bearer (user) | List payment attempts for a shop. |
| `GET` | `/payments/{payment_id}` | Bearer (user) | Get one payment. |
| `POST` | `/payments/{payment_id}/checkout` | Bearer (user) | Create/reuse Razorpay order for a pending payment. |
| `POST` | `/payments/{payment_id}/verify` | Bearer (user) | Verify Checkout signature, mark paid, fulfill. |
| `POST` | `/payments/webhook/razorpay` | Razorpay signature | Webhook fulfillment backup (`X-Razorpay-Signature`). |
| `POST` | `/payments/{payment_id}/complete` | Bearer (user) | Mark paid and activate (admin-only when Razorpay is configured). Optional body: `{ "payment_method": "upi", "payment_reference": "..." }`. |
| `POST` | `/payments/{payment_id}/fail` | Bearer (user) | Mark a pending payment as failed. |

**Complete / verify response** includes `payment`, active `plan`, optional `product_bucket`, and a `message`. After a successful plan payment you can `POST /products` for that shop.

---

## Employees (`/employees`)

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `GET` | `/employees` | Public | List employees. Optional query: `store_id`, `status`, `department`. |
| `POST` | `/employees` | Public | Add employee to a shop. Body: `store_id`, `employee_code`, name, phone, role, department, hire date, etc. |
| `PUT` | `/employees/{employee_id}` | Public | Update employee details. |
| `DELETE` | `/employees/{employee_id}` | Public | Remove an employee record. |

---

## Orders (`/orders`)

Shared shop orders: **junction.today** guests place orders; **owner app** lists and updates status.

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `GET` | `/orders` | Bearer (owner/admin) | List orders for shops you own. Optional query: `store_id`, `customer_name`, `status`. |
| `GET` | `/orders/by-name/{customer_name}` | Bearer (owner/admin) | Get orders for a customer by name. Optional query: `store_id`. |
| `GET` | `/orders/{order_id}` | Bearer (owner/admin) | Get one order by ID (includes billing details and line items). |
| `POST` | `/orders` | Bearer (owner/admin **or** session) | Create order with billing. Body: `store_id`, `customer_name`, optional `customer_phone`, line items, `billing` (subtotal, tax, total, payment method, address), optional `source`. Auto-generates `order_number`. Session JWT: any real shop (validated); no ownership check; rate-limited. Owner JWT: must own the shop. When `product_id` is set on a line item, the product must belong to `store_id`. |
| `PATCH` | `/orders/{order_id}` | Bearer (owner/admin) | Update status. Body: `{ "status": "confirmed" \| "completed" \| "cancelled" \| "pending" }`. |
| `DELETE` | `/orders/{order_id}` | Bearer (owner/admin) | Delete an order. |

**`source` (optional):** e.g. `"junction.today"`. Session creates default `source` to `junction.today` when omitted.

**Order statuses:** `pending`, `confirmed`, `completed`, `cancelled`  
**Payment statuses:** `pending`, `paid`, `failed`, `refunded`

**junction.today checkout flow:** `POST /session` → browse shops/products → `POST /orders` with session JWT, `store_id`, customer name/phone, COD/`cash` billing, `source: "junction.today"`.

---

## Image search / Queries (`/queries`)

Search **Pexels** for stock product photos. Pass a keyword and receive CDN image URLs (requires `PEXELS_API_KEY`).

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `GET` | `/queries` | Bearer | Search images. Query: `query`, `page`, `per_page` (max 80). Active plan required. |
| `POST` | `/queries` | Bearer | Same via body: `{ "query": "wireless earbuds", "page": 1, "per_page": 10 }`. |
| `POST` | `/queries/suggest-images` | Bearer | Alias that returns up to **10** Pexels options. Body: `{ "product_name": "wireless earbuds" }`. Prefer `POST /products/images/suggest`. |

**Response shape:**
```json
{
  "query": "wireless earbuds",
  "page": 1,
  "per_page": 10,
  "total_results": 8000,
  "images": [
    {
      "id": "123",
      "cdn_url": "https://images.pexels.com/...",
      "thumbnail_url": "https://images.pexels.com/...",
      "alt": "...",
      "width": 1920,
      "height": 1280,
      "source": "pexels",
      "photographer": "...",
      "photographer_url": "https://www.pexels.com/..."
    }
  ]
}
```

Requires `PEXELS_API_KEY` on the server.

---

## Locations (`/locations`)

Dropdown data for shop city and locality fields.

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `GET` | `/locations/cities` | Session JWT | List all available cities. Requires Bearer token from `POST /session` (for `junction.today`). |
| `GET` | `/locations/localities?city=Mumbai` | Session JWT | List localities for a selected city. |
| `POST` | `/locations/add-junction` | Session JWT + geocode | Add a city and locality. Body: `{ "city": "...", "locality": "..." }`. New places must geocode in India or `400`. Rate-limited. Response may include `latitude` / `longitude`. |

Seeded with default Indian cities and localities on first request. New defaults (e.g. Ranchi) are merged in automatically on the next list call.

**Adding cities/localities** (two ways):

1. **`POST /locations/add-junction`** — requires session JWT; geocoding must succeed for new localities.
2. **`POST /shops` / `PUT /shops/{shop_id}`** — city and locality are added automatically when a shop is created or updated (same geocode gate for **new** localities).

Existing seeded/known localities skip re-geocoding.

---

## Plans (`/plans`)

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `GET` | `/plans` | Public | List all plans (Free Trial, Starter, Growth, Premium). Returns `{ "plans": [...] }`. |
| `GET` | `/plans/me` | Bearer | Get current user's plan status, days remaining, grace period, etc. Admins get unlimited admin plan. |
| `POST` | `/plans/select` | Bearer | **Owners only.** Choose a paid plan. Body: `{ "plan_type": "growth" }`. Viewers must join the waitlist instead. |
| `POST` | `/plans/cancel` | Bearer | Cancel paid plan and enter 15-day grace period (`PLAN_GRACE_DAYS`). |
| `GET` | `/plans/apply/preview` | Bearer | **Viewers only.** Preview waitlist / plan application. Query: `plan_type`. |
| `POST` | `/plans/apply` | Bearer | **Viewers only.** Join the waitlist. Body: `{ "plan_type": "premium", "shop_id": "..." }`. |
| `GET` | `/plans/applications/me` | Bearer | Get your pending waitlist application, if any. |

**Waitlist aliases** (same behavior as plan apply endpoints):

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `GET` | `/waitlist/preview` | Bearer | Preview waitlist entry / plan switch message. |
| `POST` | `/waitlist` | Bearer | Join the waitlist (apply for a plan). |
| `GET` | `/waitlist/me` | Bearer | Get your pending waitlist entry. |

**Lifecycle:**
1. **Free trial** (15 days, `owner`) → trial ends → role becomes `viewer` immediately
2. **Starter / Growth / Premium** expires or is cancelled → **grace period** (15 days, still `owner`) → grace ends → role becomes `viewer`
3. **Owners** restore access via `POST /plans/select`
4. **Viewers** join the waitlist (`POST /waitlist` or `POST /plans/apply`); admin activates via `POST /admin/users/{id}/activate` (requires pending waitlist entry)
5. Admin can delete `viewer` accounts only — **shop owners can never be deleted**

**Canonical shop plan catalog** (same numbers as `GET /plans` / shop plan select):

| Plan | Price | Products | Duration |
|------|-------|----------|----------|
| Free Trial | ₹0 | 40 | 15 days |
| Starter | ₹999 | 10 | 1 year |
| Growth | ₹2999 | 80 | 1 year |
| Premium | ₹5999 | 150 | 1 year |
| Product pack (bucket) | ₹999 | +40 | add-on per shop |

Prefer **`GET/POST /shops/{shop_id}/plan*`** for limits. Legacy **`/plans/me`** and **`POST /plans/select`** still operate on the user document for waitlist/onboarding.

---

## Terms and conditions (`/terms-and-conditions`)

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `GET` | `/terms-and-conditions` | Public | Returns title, version, content, and `updated_at`. |

Content is configurable via Render env vars without code changes:

- `TERMS_AND_CONDITIONS_TITLE`
- `TERMS_AND_CONDITIONS_VERSION`
- `TERMS_AND_CONDITIONS_CONTENT`
- Or `TERMS_AND_CONDITIONS_JSON` for full JSON: `{"title":"...","version":"...","content":"..."}`

---

## Admin (`/admin`)

All endpoints require **admin** role.

| Method | Endpoint | Use |
|--------|----------|-----|
| `GET` | `/admin/users` | List all users with role and plan status. |
| `POST` | `/admin/users/{user_id}/activate` | Approve a viewer's pending waitlist application — upgrades them to `owner` with their requested plan. |
| `PATCH` | `/admin/users/{user_id}/role` | Change user role (`owner` / `viewer`). Body: `{ "role": "viewer" }`. Admins are not set here — use admin list. |
| `GET` | `/admin/role-keeper` | Read MongoDB role keeper (owner/viewer phone → role map). |
| `PUT` | `/admin/role-keeper` | Update role keeper mappings. Admins cannot be added here. |
| `GET` | `/admin/admins` | View loaded admin list (from env vars + `admin.json`). |
| `POST` | `/admin/admins/refresh` | Reload admin list from disk/env without redeploying. |
| `GET` | `/admin/viewers` | List all users with `viewer` role. |
| `DELETE` | `/admin/users` | Bulk-delete **viewers only**. Body: `{ "user_ids": ["id1", "id2"] }`. Owners and admins are protected and returned in `protected_owner_ids` / `protected_admin_ids`. |
| `DELETE` | `/admin/viewers` | Alias for `DELETE /admin/users`. |
| `GET` | `/admin/plan-applications` | List all plan applications (shop name, identity, location, requested plan, switch status). |
| `GET` | `/admin/waitlist` | Alias for `/admin/plan-applications`. |

**Admin access in production (Render):**
```
ADMIN_LIST_JSON={"+918340300635":"admin","admin@example.com":"admin"}
```
Or single values: `ADMIN_PHONE`, `ADMIN_EMAIL`. Optional local file: `admin.json` (not in repo).

---

## Legacy items (`/items`)

Early demo CRUD — not tied to shops.

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `POST` | `/items` | Public | Create a generic item (`store_id`, `name`, `description`, `price`). |
| `GET` | `/items/{item_id}` | Public | Get item by ID. |
| `PUT` | `/items/{item_id}` | Public | Update item fields. |
| `DELETE` | `/items/{item_id}` | Public | Delete item. |

---

## Typical flows

### New shop owner (OTP login)
1. `POST /auth/otp/request` → `POST /auth/otp/verify` (get token + plan + role) — phone is the account
2. `POST /shops` as many times as needed (multiple shops per phone; unique name per phone)
3. Paid upgrade: `POST /shops/{shop_id}/plan/purchase` → Razorpay checkout → `POST /payments/{payment_id}/verify`
4. `POST /products` with `store_id` = chosen shop ID (limits from that shop’s active plan + paid packs)
5. When plan products are full: `POST /product-bucket/purchase` → checkout → verify
6. `POST /employees` with same `store_id`
7. `POST /orders` when a sale is made

### Add product images
1. `GET /queries?query=shirt&per_page=10` or `POST /products/images/suggest` → pick up to 5 CDN URLs
2. `POST /products/{id}/images` with `{ "cdns": ["...", "..."] }`
3. Or `POST /products/{id}/image/upload` with file (max 5 total per product)

### Admin setup (production)
1. Set `ADMIN_LIST_JSON` on Render
2. Admin logs in via OTP
3. `GET /admin/users` to manage accounts
4. `PUT /admin/role-keeper` to assign owner/viewer roles
