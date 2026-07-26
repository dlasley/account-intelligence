-- ADR-012 Amendment: update key_prefix column comment for pk_live_/sk_live_ scheme.
-- No structural changes: column is unbounded text, UNIQUE constraint operates on value,
-- no CHECK constraint references the prefix string.
COMMENT ON COLUMN api_keys.key_prefix IS
    'First 24 characters of the full key. pk_live_<16 random hex> or sk_live_<16 random hex> = 64 bits entropy in stored prefix.';
