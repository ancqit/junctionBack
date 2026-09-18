"""Emit TypeScript fallback catalog from product_categories.py."""
from pathlib import Path

from app.product_categories import PRODUCT_CATEGORIES

out = Path(__file__).resolve().parents[2] / "junction-frontweb" / "apps" / "back-office" / "src" / "app" / "core" / "product-categories.fallback.ts"
lines = [
    "/** Auto-synced fallback for GET /products/categories — product-centric taxonomy. */",
    "import { ProductCategoryInfo } from './products.api';",
    "",
    "export const PRODUCT_CATEGORY_FALLBACK: ProductCategoryInfo[] = [",
]
for c in PRODUCT_CATEGORIES:
    lines.append(
        "  {"
        f" value: {c.value!r},"
        f" label: {c.label!r},"
        f" description: {c.description!r},"
        f" group: {c.group!r},"
        f" group_label: {c.group_label!r}"
        " },"
    )
lines.append("];")
lines.append("")
out.write_text("\n".join(lines), encoding="utf-8")
print(f"wrote {out} ({len(PRODUCT_CATEGORIES)} rows)")
