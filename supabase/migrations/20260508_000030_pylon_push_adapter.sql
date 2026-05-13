-- ADR-020 Phase 2.5 — Pylon push adapter
--
-- Extends Phase 1 constraints to allow Pylon as a second push-based ticket vendor.
-- Adds:
--   1. 'pylon_webhook_secret' to external_credentials.kind CHECK constraint
--   2. 'pylon_ticket' to signals.source_type CHECK constraint

-- ─── external_credentials: extend kind CHECK ────────────────────────────────

ALTER TABLE external_credentials DROP CONSTRAINT IF EXISTS external_credentials_kind_check;
ALTER TABLE external_credentials ADD CONSTRAINT external_credentials_kind_check
    CHECK (kind IN ('plain_webhook_secret', 'pylon_webhook_secret', 'granola_api_key'));


-- ─── signals: extend source_type CHECK ──────────────────────────────────────

ALTER TABLE signals DROP CONSTRAINT IF EXISTS signals_source_type_check;
ALTER TABLE signals ADD CONSTRAINT signals_source_type_check
    CHECK (source_type IN (
        'inbound_email', 'json_fixture', 'outbound_email',
        'product_event', 'plain_ticket', 'pylon_ticket', 'granola_note'
    ));
