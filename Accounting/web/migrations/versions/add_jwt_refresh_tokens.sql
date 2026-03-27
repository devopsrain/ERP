-- Migration: add JWT refresh token storage
-- Run this once against your PostgreSQL database.
--
-- Refresh tokens are opaque random values stored as SHA-256 hashes.
-- The token_id column is the lookup key forwarded inside the opaque
-- token string as "<token_id>.<raw_secret>".

CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_id    VARCHAR(64)  PRIMARY KEY,
    user_id     VARCHAR(36)  NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    token_hash  VARCHAR(128) NOT NULL,
    issued_at   TIMESTAMP    NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMP    NOT NULL,
    revoked_at  TIMESTAMP,
    device_hint VARCHAR(200) NOT NULL DEFAULT ''
);

-- Speed up "list active tokens for user" and "expired token cleanup"
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user
    ON refresh_tokens (user_id, expires_at);

-- Optional: automatically delete tokens that expired more than 7 days ago
-- (requires pg_cron or a scheduled job; this block is left as a comment
--  so the migration runs cleanly on vanilla PostgreSQL without pg_cron)
--
-- SELECT cron.schedule('0 3 * * *',
--   $$DELETE FROM refresh_tokens WHERE expires_at < NOW() - INTERVAL '7 days'$$);
