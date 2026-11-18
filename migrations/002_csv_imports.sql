CREATE TABLE IF NOT EXISTS csv_imports (
    token TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    wallet_id BIGINT NOT NULL REFERENCES wallets(id) ON DELETE CASCADE,
    currency VARCHAR(3) NOT NULL,
    payload JSONB,
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '30 minutes'),
    confirmed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS csv_imports_user_expiry_idx ON csv_imports (user_id, expires_at);
