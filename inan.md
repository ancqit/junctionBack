# Junction Backend — API Reference

Base URL (production): `https://junctionback.onrender.com`

Most protected endpoints require a Bearer token from login/OTP:

```
Authorization: Bearer <access_token>
```

**Roles:** `admin`, `owner`, `viewer`  
**Plans:** Free Trial, Starter, Growth, Premium — admins are exempt from all plan limits.

**`store_id`** on products, employees, and orders = the **shop ID** from `/shops`.

## Security

- **JWT required** for business data. Send `Authorization: Bearer <access_token>` on every request except the public auth/plan/terms endpoints listed below.
- **CORS** (`CORS_ORIGINS`) limits which browser sites (e.g. `https://junction.today`) may call the API. It does not block `curl` or Postman — JWT and shop ownership checks do.
- **Shop-scoped writes/reads** require the user to own the shop (or be admin).
- **AI routes** require JWT, an active plan, and are rate-limited (`RATE_LIMIT_AI`, default `30/hour`).
- **Auth routes** are rate-limited (`RATE_LIMIT_AUTH`, default `20/minute`).
- **Public (no JWT):** `/auth/register`, `/auth/login`, `/auth/otp/*`, `/auth/roles`, `GET /plans`, `/terms-and-conditions`, `/auth/digilocker/callback`.
- Set `OPENAPI_ENABLED=false` in production to hide `/docs`.

---

## Authentication (`/auth`)

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `POST` | `/auth/register` | Public | Register with email + password. Returns JWT, user, plan, and role. |
| `POST` | `/auth/login` | Public | Login with email + password (OAuth2 form: `username`, `password`). Returns JWT. |
| `POST` | `/auth/otp/request` | Public | Send OTP to phone via GCP Identity Platform. Body: `display_name`, `phone_number`, `recaptcha_token`. |
| `POST` | `/auth/otp/verify` | Public | Verify OTP and log in (or create account). Body: `phone_number`, `otp`, `session_info`. Phone is the account key. |
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
| `POST` | `/notices` | Bearer | Post or update today's notice. Body: `{ "store_id": "<shop_id>", "message": "20% off today!" }`. |
| `GET` | `/notices/today?store_id=<shop_id>` | Public | Get today's notice for a shop. |
| `GET` | `/notices` | Bearer | List today's notices for the logged-in owner's shops (admins see all). |

---

## Shops (`/shops`)

Shops are the main entry point. Products and employees are linked via `store_id` = shop `id`.

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `GET` | `/shops` | Bearer | List shops. Owners see their own; admins see all. |
| `GET` | `/shops/{shop_id}` | Bearer | Get one shop by ID. |
| `GET` | `/shops/by-name/{shop_name}` | Bearer | Find shop(s) by name (case-insensitive). |
| `POST` | `/shops` | Bearer | Create shop. Body: `{ "name": "...", "city": "...", "locality": "..." }` (all required). Phone from logged-in user and **cannot be changed later**. |
| `PUT` | `/shops/{shop_id}` | Bearer | Update shop `name`, `city`, and/or `locality`. |
| `DELETE` | `/shops/{shop_id}` | Bearer | Delete a shop. |
| `GET` | `/shops/types` | Public | List all shop/business types as a JSON array. Each item has `value`, `label`, `category` (`retail`, `food`, `beverage`, `services`, etc.), optional `group` (e.g. `technician`, `home_maintenance` under services), and `description`. |

---

## Products (`/products`)

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `GET` | `/products` | Public | List products. Optional query: `store_id` (shop ID). |
| `POST` | `/products` | Bearer | Create product for a shop. Enforces plan product limits. Body includes `store_id`, `sku`, `name`, `category`, `price`, stock, etc. |
| `PUT` | `/products/{product_id}` | Public | Update product fields (name, price, stock, status, image, etc.). |
| `DELETE` | `/products/{product_id}` | Public | Delete a product. |
| `POST` | `/products/images/suggest` | Public | Suggest **10** Gemini-generated CDN images for a product name. Body: `{ "product_name": "wireless earbuds" }`. |
| `GET` | `/products/images/{stored_image_id}` | Public | Serve a stored product image (from upload or query flow). |
| `POST` | `/products/{product_id}/image/cdn` | Public | Set hero image to an external CDN URL. Body: `{ "cdn": "https://..." }`. |
| `POST` | `/products/{product_id}/image/use` | Public | Download one CDN image and add it to the gallery (max 5). Body: `{ "cdn": "https://..." }`. |
| `POST` | `/products/{product_id}/images` | Public | Attach up to **5** chosen CDN images to the product. Body: `{ "cdns": ["https://...", "..."] }`. Replaces the gallery. |
| `POST` | `/products/{product_id}/image/upload` | Public | Upload image file (`multipart/form-data`, field: `file`). Stores in GridFS and adds to gallery (max 5). |

**Product images:** Each product supports up to **5** images in `images[]`. The first image is also exposed as `image` / `image_cdn` (hero).

**Suggested image flow:**
1. `GET /queries?query=wireless+earbuds&per_page=10` or `POST /products/images/suggest` → get **10** generated CDN options
2. User picks up to **5** → `POST /products/{product_id}/images` with `{ "cdns": ["...", "..."] }`
3. Or add one at a time via `/image/use` or `/image/upload`

**Other image flows:** CDN link only → `/image/cdn` · Manual search → `/queries` + `/image/use`

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

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `GET` | `/orders` | Public | List orders. Optional query: `store_id`, `customer_name`, `status`. |
| `GET` | `/orders/by-name/{customer_name}` | Public | Get orders for a customer by name. Optional query: `store_id`. |
| `GET` | `/orders/{order_id}` | Public | Get one order by ID (includes billing details and line items). |
| `POST` | `/orders` | Public | Create order with billing. Body: `store_id`, `customer_name`, line items, `billing` (subtotal, tax, total, payment method, address). Auto-generates `order_number`. |
| `DELETE` | `/orders/{order_id}` | Public | Delete an order. |

**Order statuses:** `pending`, `confirmed`, `completed`, `cancelled`  
**Payment statuses:** `pending`, `paid`, `failed`, `refunded`

---

## Image search / Queries (`/queries`)

Pexels-style API powered by **Gemini image generation**. Pass a product keyword and receive generated product photos with CDN URLs served by this backend.

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `GET` | `/queries` | Public | Generate images from a keyword. Query: `query`, `page`, `per_page` (max 10). |
| `POST` | `/queries` | Public | Same via body: `{ "query": "wireless earbuds", "page": 1, "per_page": 10 }`. |
| `POST` | `/queries/suggest-images` | Public | Alias that returns **10** generated options. Body: `{ "product_name": "wireless earbuds" }`. Prefer `POST /products/images/suggest`. |

**Response shape (like Pexels):**
```json
{
  "query": "wireless earbuds",
  "page": 1,
  "per_page": 10,
  "total_results": 10,
  "images": [
    {
      "id": "...",
      "cdn_url": "https://junctionback.onrender.com/products/images/...",
      "thumbnail_url": "https://junctionback.onrender.com/products/images/...",
      "alt": "wireless earbuds - front view ...",
      "width": 1024,
      "height": 1024,
      "source": "gemini"
    }
  ]
}
```

Requires `GEMINI_API_KEY`. Optional: `GEMINI_MODEL` (default `gemini-3-pro-image`) — same model for images and descriptions.

---

## Product descriptions (`/descriptions`)

Uses Google Gemini (`GEMINI_API_KEY`) to expand a short product summary into a detailed description.

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `POST` | `/descriptions/generate` | Public | Body: `{ "text": "wireless earbuds" }`. Returns `{ "description": "..." }`. |

Optional env: `GEMINI_MODEL` (default `gemini-3-pro-image`) — shared with product image generation.

---

## Locations (`/locations`)

Dropdown data for shop city and locality fields.

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `GET` | `/locations/cities` | Public | List all available cities. |
| `GET` | `/locations/localities?city=Mumbai` | Public | List localities for a selected city. |
| `POST` | `/locations/add-junction` | Bearer | Add a city and locality to the lists. Body: `{ "city": "...", "locality": "..." }`. |

Seeded with default Indian cities and localities on first request. New defaults (e.g. Ranchi) are merged in automatically on the next list call.

**Adding cities/localities** (two ways):

1. **`POST /locations/add-junction`** — user submits `city` and `locality` directly.
2. **`POST /shops` / `PUT /shops/{shop_id}`** — city and locality are added automatically when a shop is created or updated.

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

| Plan | Price | Products | Notes |
|------|-------|----------|-------|
| Free Trial | ₹0 | 150 | 15 days full access |
| Starter | ₹0 | 10 | Profile and up to 10 products |
| Growth | ₹399 | 100 | |
| Premium | ₹599 | 150+ | Unlimited |

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
1. `POST /auth/otp/request` → `POST /auth/otp/verify` (get token + plan + role)
2. `POST /shops` with shop name
3. `POST /products` with `store_id` = shop ID
4. `POST /employees` with same `store_id`
5. `POST /orders` when a sale is made

### Add product images
1. `GET /queries?query=shirt&per_page=10` or `POST /products/images/suggest` → pick up to 5 CDN URLs
2. `POST /products/{id}/images` with `{ "cdns": ["...", "..."] }`
3. Or `POST /products/{id}/image/upload` with file (max 5 total per product)

### Admin setup (production)
1. Set `ADMIN_LIST_JSON` on Render
2. Admin logs in via OTP
3. `GET /admin/users` to manage accounts
4. `PUT /admin/role-keeper` to assign owner/viewer roles
