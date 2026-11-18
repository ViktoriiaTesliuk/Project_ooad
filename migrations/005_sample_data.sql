-- Mark sample data so it can be told apart from real records and removed in one step.
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS is_sample BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS is_sample BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE monthly_budgets ADD COLUMN IF NOT EXISTS is_sample BOOLEAN NOT NULL DEFAULT false;

-- Sample data added before this migration: only accounts that have all three sample wallets
-- exactly as pocket_ledger/demo.py creates them (name, currency and opening balance).
UPDATE wallets SET is_sample = true
WHERE NOT is_sample
  AND (name, currency, opening_balance) IN (('Everyday card', 'UAH', 8000.00), ('Cash', 'UAH', 1200.00), ('Travel card', 'EUR', 150.00))
  AND user_id IN (
      SELECT user_id FROM wallets
      WHERE (name, currency, opening_balance) IN (('Everyday card', 'UAH', 8000.00), ('Cash', 'UAH', 1200.00), ('Travel card', 'EUR', 150.00))
      GROUP BY user_id HAVING count(DISTINCT name) = 3
  );

-- Only the sample entries in those wallets; anything the user added keeps is_sample = false.
UPDATE transactions SET is_sample = true
WHERE NOT is_sample
  AND wallet_id IN (SELECT id FROM wallets WHERE is_sample)
  AND description IN ('Monthly salary', 'Rent share', 'Weekly groceries', 'Bus fare', 'Lunch', 'Taxi home',
                      'Groceries for the week', 'Phone plan', 'Café on a city trip', 'Train ticket',
                      'Sold old headphones', 'Coffee and pastry', 'Groceries', 'Monthly transit pass');

-- The two sample limits (12 000 and 11 000 UAH) of accounts that still have sample wallets.
UPDATE monthly_budgets SET is_sample = true
WHERE NOT is_sample
  AND currency = 'UAH' AND amount IN (12000.00, 11000.00)
  AND (user_id, month_start) IN (
      SELECT w.user_id, CAST(date_trunc('month', t.occurred_on) AS DATE)
      FROM transactions AS t JOIN wallets AS w ON w.id = t.wallet_id
      WHERE w.is_sample AND t.is_sample
  );
