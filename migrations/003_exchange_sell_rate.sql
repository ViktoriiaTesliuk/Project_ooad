ALTER TABLE exchange_rates ADD COLUMN IF NOT EXISTS sell_rate NUMERIC(24, 12)
    CHECK (sell_rate > 0);
