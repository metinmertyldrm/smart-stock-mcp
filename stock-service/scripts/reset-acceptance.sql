\set ON_ERROR_STOP on

-- This script is intentionally destructive and must only target the dedicated
-- an _acceptance database. The PowerShell wrapper derives the target from the
-- same DB_URL as Spring and enforces that guard before opening psql.
TRUNCATE TABLE
    marketplace_order_items,
    marketplace_orders,
    marketplace_purchase_draft_items,
    marketplace_purchase_drafts,
    incoming_orders,
    marketplace_offers,
    marketplace_sellers,
    products,
    models,
    brands,
    subcategories,
    categories
RESTART IDENTITY CASCADE;
