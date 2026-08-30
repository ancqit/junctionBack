# Agent brief: PR for `feature/shared-shop-orders`

Use this file to open the **backend** PR that unlocks shared orders between junction.today and Junction Front Web.

## Repo facts

| Field | Value |
| --- | --- |
| GitHub | `ancqit/junctionBack` |
| Branch | `feature/shared-shop-orders` |
| Base | `main` |
| Remote | `origin` |

## What this branch does

- Allow **junction.today session JWT** to `POST /orders` for a real shop (`store_id`) without owner access.
- Owners keep `GET` / `DELETE`; add **`PATCH /orders/{id}`** for status (`pending` → `confirmed` → `completed` / `cancelled`).
- Optional `source` (defaults to `junction.today` for session creates).
- Guest create rate limit (`RATE_LIMIT_GUEST_ORDERS`, default `30/minute`).
- Docs in `inan.md` updated.

## Create the PR (copy-paste)

```powershell
git push -u origin HEAD

gh pr create --base main --head feature/shared-shop-orders --title "Shared shop orders: session create + owner PATCH status" --body "$( @'
## Summary
- junction.today session JWT can create COD orders for any shop (`store_id`).
- Owners can PATCH order status; list remains shop-scoped.
- Tag `source` for Today vs owner-created orders; rate-limit guest creates.

## Companion frontends (merge after this deploys)
- jtoday `feature/shared-shop-orders`
- junctionFrontweb `feature/shared-shop-orders`

## Test plan
- [ ] Session JWT POST /orders with valid store_id → 201 + order_number
- [ ] Owner JWT GET /orders?store_id= sees it
- [ ] Owner PATCH status works; session cannot PATCH/list other shops
- [ ] Invalid store_id / product from another shop rejected
'@ )"
```

Merge and deploy to Render **before** merging the frontend PRs.
