CREATE TABLE IF NOT EXISTS users (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    google_sub TEXT NOT NULL UNIQUE,
    email TEXT,
    display_name TEXT
);

CREATE TABLE IF NOT EXISTS wallets (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (btrim(name) <> ''),
    currency VARCHAR(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    opening_balance NUMERIC(18, 2) NOT NULL DEFAULT 0,
    UNIQUE (id, currency)
);

CREATE TABLE IF NOT EXISTS categories (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
    UNIQUE (name, kind),
    UNIQUE (id, kind)
);

CREATE TABLE IF NOT EXISTS transactions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    wallet_id BIGINT NOT NULL,
    category_id BIGINT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
    amount NUMERIC(18, 2) NOT NULL CHECK (amount > 0),
    currency VARCHAR(3) NOT NULL,
    occurred_on DATE NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (wallet_id, currency) REFERENCES wallets(id, currency) ON DELETE CASCADE,
    FOREIGN KEY (category_id, kind) REFERENCES categories(id, kind)
);

CREATE TABLE IF NOT EXISTS monthly_budgets (
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    month_start DATE NOT NULL CHECK (EXTRACT(DAY FROM month_start) = 1),
    amount NUMERIC(18, 2) NOT NULL CHECK (amount > 0),
    currency VARCHAR(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    PRIMARY KEY (user_id, month_start)
);

CREATE TABLE IF NOT EXISTS exchange_rates (
    base_currency VARCHAR(3) NOT NULL CHECK (base_currency ~ '^[A-Z]{3}$'),
    quote_currency VARCHAR(3) NOT NULL CHECK (quote_currency ~ '^[A-Z]{3}$'),
    rate NUMERIC(24, 12) NOT NULL CHECK (rate > 0),
    source TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (base_currency, quote_currency),
    CHECK (base_currency <> quote_currency)
);

CREATE INDEX IF NOT EXISTS wallets_user_idx ON wallets (user_id);
CREATE INDEX IF NOT EXISTS transactions_wallet_date_idx ON transactions (wallet_id, occurred_on);

INSERT INTO categories (name, kind)
VALUES ('Food', 'expense'), ('Transport', 'expense'), ('Housing', 'expense'),
       ('Other', 'expense'), ('Salary', 'income'), ('Other', 'income')
ON CONFLICT (name, kind) DO NOTHING;
