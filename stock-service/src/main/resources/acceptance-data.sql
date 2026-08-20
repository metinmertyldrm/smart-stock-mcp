-- Acceptance fixture: the regular demo order arrives three days in the future.
-- Make it receivable so the two-turn pending_orders_receive scenario is deterministic.
UPDATE incoming_orders
SET status = 'PENDING',
    expected_delivery_date = CURRENT_TIMESTAMP - INTERVAL '1 minute'
WHERE id = 1;
