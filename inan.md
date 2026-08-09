# Junction Backend — API Reference

Base URL (production): `https://junctionback.onrender.com`

Most protected endpoints require a Bearer token from login/OTP:

```
Authorization: Bearer <access_token>
```

**Roles:** `admin`, `owner`, `viewer`  
**Plans:** Free Trial, Starter, Growth, Premium — admins are exempt from all plan limits.

**`store_id`** on products, employees, and orders = the **shop ID** from `/shops`.

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

**Role resolution on every login:**
1. Check admin list (`ADMIN_LIST_JSON`, `ADMIN_EMAIL`, `ADMIN_PHONE`, or `admin.json`)
2. Else check MongoDB role keeper (owner/viewer mappings)
3. Default role: `owner`

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

## Shops (`/shops`)

Shops are the main entry point. Products and employees are linked via `store_id` = shop `id`.

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `GET` | `/shops` | Bearer | List shops. Owners see their own; admins see all. |
| `GET` | `/shops/{shop_id}` | Bearer | Get one shop by ID. |
| `GET` | `/shops/by-name/{shop_name}` | Bearer | Find shop(s) by name (case-insensitive). |
| `POST` | `/shops` | Bearer | Create shop. Body: `{ "name": "..." }`. Phone is taken from logged-in user and **cannot be changed later**. |
| `PUT` | `/shops/{shop_id}` | Bearer | Update **shop name only**. Body: `{ "name": "..." }`. |
| `DELETE` | `/shops/{shop_id}` | Bearer | Delete a shop. |

---

## Products (`/products`)

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `GET` | `/products` | Public | List products. Optional query: `store_id` (shop ID). |
| `POST` | `/products` | Bearer | Create product for a shop. Enforces plan product limits. Body includes `store_id`, `sku`, `name`, `category`, `price`, stock, etc. |
| `PUT` | `/products/{product_id}` | Public | Update product fields (name, price, stock, status, image, etc.). |
| `DELETE` | `/products/{product_id}` | Public | Delete a product. |
| `GET` | `/products/images/{stored_image_id}` | Public | Serve a stored product image (from upload or query flow). |
| `POST` | `/products/{product_id}/image/cdn` | Public | Set product image to an external CDN URL. Body: `{ "cdn": "https://..." }`. |
| `POST` | `/products/{product_id}/image/use` | Public | Download image from CDN URL, store in GridFS, attach to product. Body: `{ "cdn": "https://..." }`. |
| `POST` | `/products/{product_id}/image/upload` | Public | Upload image file (`multipart/form-data`, field: `file`). Stores in GridFS and attaches to product. |

**Image flows:** CDN link only → `/image/cdn` · Search then use → `/queries` + `/image/use` · Direct upload → `/image/upload`

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

Uses Pexels API (`PEXELS_API_KEY`) to search stock images for product photos.

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `GET` | `/queries` | Public | Search images. Query: `query`, `page`, `per_page`. |
| `POST` | `/queries` | Public | Same search via body: `{ "query": "...", "page": 1, "per_page": 20 }`. |

Use a returned image URL with `POST /products/{id}/image/use`.

---

## Plans (`/plans`)

| Method | Endpoint | Auth | Use |
|--------|----------|------|-----|
| `GET` | `/plans` | Public | List available plans (Free Trial, Starter, Growth, Premium) with pricing and limits. |
| `GET` | `/plans/me` | Bearer | Get current user's plan status, days remaining, grace period, etc. Admins get unlimited admin plan. |
| `POST` | `/plans/select` | Bearer | Choose a paid plan. Body: `{ "plan_type": "growth" }`. Persists on phone-based account. |
| `POST` | `/plans/cancel` | Bearer | Cancel paid plan and enter 15-day grace period (`PLAN_GRACE_DAYS`). |

| Plan | Price | Products | Notes |
|------|-------|----------|-------|
| Free Trial | ₹0 | 150 | 15 days full access |
| Starter | ₹0 | 0 | Profile only |
| Growth | ₹399 | 100 | |
| Premium | ₹599 | 150+ | Unlimited |

---

## Admin (`/admin`)

All endpoints require **admin** role.

| Method | Endpoint | Use |
|--------|----------|-----|
| `GET` | `/admin/users` | List all users with role, plan status, account status. |
| `POST` | `/admin/users/{user_id}/activate` | Reactivate a deactivated user and restore their plan. |
| `POST` | `/admin/users/{user_id}/deactivate` | Deactivate a user account. |
| `PATCH` | `/admin/users/{user_id}/role` | Change user role (`owner` / `viewer`). Body: `{ "role": "viewer" }`. Admins are not set here — use admin list. |
| `GET` | `/admin/role-keeper` | Read MongoDB role keeper (owner/viewer phone → role map). |
| `PUT` | `/admin/role-keeper` | Update role keeper mappings. Admins cannot be added here. |
| `GET` | `/admin/admins` | View loaded admin list (from env vars + `admin.json`). |
| `POST` | `/admin/admins/refresh` | Reload admin list from disk/env without redeploying. |

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

### Add product image
1. `GET /queries?query=shirt` → pick image URL
2. `POST /products/{id}/image/use` with `{ "cdn": "<url>" }`
3. Or `POST /products/{id}/image/upload` with file

### Admin setup (production)
1. Set `ADMIN_LIST_JSON` on Render
2. Admin logs in via OTP
3. `GET /admin/users` to manage accounts
4. `PUT /admin/role-keeper` to assign owner/viewer roles
