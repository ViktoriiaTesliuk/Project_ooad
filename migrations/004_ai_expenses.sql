INSERT INTO categories (name, kind)
VALUES ('Shopping', 'expense'), ('Entertainment', 'expense'), ('Health', 'expense'),
       ('Utilities', 'expense'), ('Cafes & Restaurants', 'expense'), ('Education', 'expense'),
       ('Travel', 'expense')
ON CONFLICT (name, kind) DO NOTHING;

-- Expenses recognised from a sentence or a receipt, waiting for the user's review.
CREATE TABLE IF NOT EXISTS ai_drafts (
    token TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source TEXT NOT NULL CHECK (source IN ('text', 'receipt')),
    receipt_hash TEXT,
    payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '30 minutes'),
    confirmed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ai_drafts_user_idx ON ai_drafts (user_id, receipt_hash);

-- A saved receipt, by file hash and by merchant/date/total, so it isn't recorded twice.
-- Deleting the expense removes its fingerprints, which allows the receipt again.
CREATE TABLE IF NOT EXISTS receipt_fingerprints (
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('file', 'content')),
    fingerprint TEXT NOT NULL,
    transaction_id BIGINT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, kind, fingerprint)
);

-- AI requests per user per Kyiv calendar day, to share the Gemini free tier fairly.
CREATE TABLE IF NOT EXISTS ai_usage (
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day DATE NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0 CHECK (requests >= 0),
    PRIMARY KEY (user_id, day)
);
