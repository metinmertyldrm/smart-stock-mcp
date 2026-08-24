-- Seed Categories
INSERT INTO categories (id, name) VALUES (1, 'Elektronik') ON CONFLICT (id) DO NOTHING;
INSERT INTO categories (id, name) VALUES (2, 'Ofis Malzemeleri') ON CONFLICT (id) DO NOTHING;

-- Seed Subcategories
INSERT INTO subcategories (id, category_id, name) VALUES (1, 1, U&'Ak\0131ll\0131 Telefonlar') ON CONFLICT (id) DO NOTHING;
INSERT INTO subcategories (id, category_id, name) VALUES (2, 1, 'Bilgisayarlar') ON CONFLICT (id) DO NOTHING;
INSERT INTO subcategories (id, category_id, name) VALUES (3, 2, U&'Ka\011F\0131t \00DCr\00FCnleri') ON CONFLICT (id) DO NOTHING;

-- Seed Brands
INSERT INTO brands (id, name) VALUES (1, 'Apple') ON CONFLICT (id) DO NOTHING;
INSERT INTO brands (id, name) VALUES (2, 'Samsung') ON CONFLICT (id) DO NOTHING;
INSERT INTO brands (id, name) VALUES (3, 'HP') ON CONFLICT (id) DO NOTHING;
INSERT INTO brands (id, name)
VALUES (4, 'Dell') ON CONFLICT (id) DO NOTHING;


-- Seed Models
INSERT INTO models (id, brand_id, name) VALUES (1, 1, 'iPhone 15 Pro') ON CONFLICT (id) DO NOTHING;
INSERT INTO models (id, brand_id, name) VALUES (2, 2, 'Galaxy S24') ON CONFLICT (id) DO NOTHING;
INSERT INTO models (id, brand_id, name) VALUES (3, 3, 'LaserJet Pro') ON CONFLICT (id) DO NOTHING;
INSERT INTO models (id, brand_id, name)
VALUES (4, 4, 'Latitude 5440')
ON CONFLICT (id) DO NOTHING;

INSERT INTO models (id, brand_id, name)
VALUES (5, 3, 'Wireless Keyboard')
ON CONFLICT (id) DO NOTHING;


-- Seed Products
INSERT INTO products (id, sku, name, description, subcategory_id, model_id, stock_quantity, minimum_stock, target_stock, warehouse_info) 
VALUES (1, 'APP-IPH15P', 'iPhone 15 Pro 128GB', U&'Apple amiral gemisi ak\0131ll\0131 telefon, siyah titanyum.', 1, 1, 2, 5, 10, 'A-Blok Raf 4')
ON CONFLICT (id) DO NOTHING;

INSERT INTO products (id, sku, name, description, subcategory_id, model_id, stock_quantity, minimum_stock, target_stock, warehouse_info) 
VALUES (2, 'SAM-GS24', 'Galaxy S24 256GB', U&'Samsung amiral gemisi ak\0131ll\0131 telefon, gri.', 1, 2, 0, 3, 8, 'A-Blok Raf 5')
ON CONFLICT (id) DO NOTHING;

INSERT INTO products (id, sku, name, description, subcategory_id, model_id, stock_quantity, minimum_stock, target_stock, warehouse_info) 
VALUES (3, 'HP-LJP-M15', U&'LaserJet M15w Yaz\0131c\0131', U&'HP Lazer yaz\0131c\0131, siyah-beyaz.', 3, 3, 12, 4, 10, 'B-Blok Raf 12')
ON CONFLICT (id) DO NOTHING;

INSERT INTO products (
    id,
    sku,
    name,
    description,
    subcategory_id,
    model_id,
    stock_quantity,
    minimum_stock,
    target_stock,
    warehouse_info
)
VALUES (
    4,
    'DELL-LAT-5440',
    'Dell Latitude 5440',
    U&'Kurumsal diz\00FCst\00FC bilgisayar.',
    2,
    4,
    1,
    3,
    5,
    'A-Blok Raf 8'
)
ON CONFLICT (id) DO NOTHING;


INSERT INTO products (
    id,
    sku,
    name,
    description,
    subcategory_id,
    model_id,
    stock_quantity,
    minimum_stock,
    target_stock,
    warehouse_info
)
VALUES (
    5,
    'OFF-A4-500',
    U&'A4 Fotokopi Ka\011F\0131d\0131 500 Yaprak',
    U&'Standart ofis tipi A4 fotokopi ka\011F\0131d\0131.',
    3,
    NULL,
    0,
    10,
    30,
    'B-Blok Raf 2'
)
ON CONFLICT (id) DO NOTHING;


INSERT INTO products (
    id,
    sku,
    name,
    description,
    subcategory_id,
    model_id,
    stock_quantity,
    minimum_stock,
    target_stock,
    warehouse_info
)
VALUES (
    6,
    'HP-WKEY-01',
    'Kablosuz Klavye',
    U&'Ofis kullan\0131m\0131 i\00E7in kablosuz klavye.',
    2,
    5,
    4,
    5,
    10,
    'A-Blok Raf 10'
)
ON CONFLICT (id) DO NOTHING;


INSERT INTO products (
    id,
    sku,
    name,
    description,
    subcategory_id,
    model_id,
    stock_quantity,
    minimum_stock,
    target_stock,
    warehouse_info
)
VALUES (
    7,
    'GEN-HDMI-2M',
    'HDMI Kablosu 2 Metre',
    U&'Standart y\00FCksek h\0131zl\0131 HDMI kablosu.',
    2,
    NULL,
    20,
    5,
    15,
    'A-Blok Raf 15'
)
ON CONFLICT (id) DO NOTHING;


-- Seed Incoming Orders
INSERT INTO incoming_orders (id, product_id, quantity, status, expected_delivery_date, created_at)
VALUES (1, 1, 3, 'PENDING', CURRENT_TIMESTAMP + INTERVAL '3 days', CURRENT_TIMESTAMP)
ON CONFLICT (id) DO NOTHING;

-- Seed Marketplace Sellers
INSERT INTO marketplace_sellers (id, name, rating, base_delivery_days) VALUES (1, 'TechStore', 4.8, 2) ON CONFLICT (id) DO NOTHING;
INSERT INTO marketplace_sellers (id, name, rating, base_delivery_days) VALUES (2, 'ElectroShop', 4.2, 3) ON CONFLICT (id) DO NOTHING;
INSERT INTO marketplace_sellers (id, name, rating, base_delivery_days) VALUES (3, 'OfficeDepot', 4.6, 3) ON CONFLICT (id) DO NOTHING;
INSERT INTO marketplace_sellers (id, name, rating, base_delivery_days) VALUES (4, 'FastDelivery', 3.9, 1) ON CONFLICT (id) DO NOTHING;
INSERT INTO marketplace_sellers (
    id,
    name,
    rating,
    base_delivery_days
)
VALUES (
    5,
    'BudgetMarket',
    3.7,
    5
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO marketplace_sellers (
    id,
    name,
    rating,
    base_delivery_days
)
VALUES (
    6,
    'PremiumSupply',
    4.9,
    2
)
ON CONFLICT (id) DO NOTHING;


-- Seed Marketplace Offers
INSERT INTO marketplace_offers (id, product_id, seller_id, price, stock_quantity, shipping_fee, delivery_time_days) VALUES (1, 1, 1, 52000.00, 10, 150.00, 1) ON CONFLICT (id) DO NOTHING;
INSERT INTO marketplace_offers (id, product_id, seller_id, price, stock_quantity, shipping_fee, delivery_time_days) VALUES (2, 1, 2, 50500.00, 5, 200.00, 3) ON CONFLICT (id) DO NOTHING;
INSERT INTO marketplace_offers (id, product_id, seller_id, price, stock_quantity, shipping_fee, delivery_time_days) VALUES (3, 1, 4, 54000.00, 15, 0.00, 1) ON CONFLICT (id) DO NOTHING;

INSERT INTO marketplace_offers (id, product_id, seller_id, price, stock_quantity, shipping_fee, delivery_time_days) VALUES (4, 2, 1, 38000.00, 8, 100.00, 2) ON CONFLICT (id) DO NOTHING;
INSERT INTO marketplace_offers (id, product_id, seller_id, price, stock_quantity, shipping_fee, delivery_time_days) VALUES (5, 2, 2, 37200.00, 3, 150.00, 3) ON CONFLICT (id) DO NOTHING;
INSERT INTO marketplace_offers (id, product_id, seller_id, price, stock_quantity, shipping_fee, delivery_time_days) VALUES (6, 2, 4, 39500.00, 10, 0.00, 1) ON CONFLICT (id) DO NOTHING;

INSERT INTO marketplace_offers (id, product_id, seller_id, price, stock_quantity, shipping_fee, delivery_time_days) VALUES (7, 3, 3, 4500.00, 20, 50.00, 2) ON CONFLICT (id) DO NOTHING;
INSERT INTO marketplace_offers (id, product_id, seller_id, price, stock_quantity, shipping_fee, delivery_time_days) VALUES (8, 3, 1, 4800.00, 10, 100.00, 1) ON CONFLICT (id) DO NOTHING;
INSERT INTO marketplace_offers (id, product_id, seller_id, price, stock_quantity, shipping_fee, delivery_time_days) VALUES (9, 3, 2, 4300.00, 2, 120.00, 4) ON CONFLICT (id) DO NOTHING;

INSERT INTO marketplace_offers (
    id,
    product_id,
    seller_id,
    price,
    stock_quantity,
    shipping_fee,
    delivery_time_days
)
VALUES
(10, 4, 2, 28500.00, 10, 0.00, 4),
(11, 4, 1, 29800.00, 6, 150.00, 2),
(12, 4, 6, 30500.00, 10, 0.00, 1)
ON CONFLICT (id) DO NOTHING;


-- A4 paper offers

INSERT INTO marketplace_offers (
    id,
    product_id,
    seller_id,
    price,
    stock_quantity,
    shipping_fee,
    delivery_time_days
)
VALUES
(13, 5, 5, 120.00, 100, 0.00, 5),
(14, 5, 3, 135.00, 50, 30.00, 2),
(15, 5, 6, 145.00, 100, 0.00, 1)
ON CONFLICT (id) DO NOTHING;


-- Wireless keyboard offers

INSERT INTO marketplace_offers (
    id,
    product_id,
    seller_id,
    price,
    stock_quantity,
    shipping_fee,
    delivery_time_days
)
VALUES
(16, 6, 2, 750.00, 20, 50.00, 4),
(17, 6, 1, 820.00, 20, 0.00, 2),
(18, 6, 6, 860.00, 20, 0.00, 1)
ON CONFLICT (id) DO NOTHING;


-- HDMI cable offers: product is not critical, these should not be selected

INSERT INTO marketplace_offers (
    id,
    product_id,
    seller_id,
    price,
    stock_quantity,
    shipping_fee,
    delivery_time_days
)
VALUES
(19, 7, 5, 90.00, 100, 0.00, 5),
(20, 7, 1, 115.00, 100, 0.00, 2)
ON CONFLICT (id) DO NOTHING;

-- Keep identity sequences ahead of both seed records and records created by users.
SELECT setval('categories_id_seq', COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM categories;
SELECT setval('subcategories_id_seq', COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM subcategories;
SELECT setval('brands_id_seq', COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM brands;
SELECT setval('models_id_seq', COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM models;
SELECT setval('products_id_seq', COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM products;
SELECT setval('incoming_orders_id_seq', COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM incoming_orders;
SELECT setval('marketplace_sellers_id_seq', COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM marketplace_sellers;
SELECT setval('marketplace_offers_id_seq', COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM marketplace_offers;
