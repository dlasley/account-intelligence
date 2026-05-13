-- Rename accounts.crm_slug → crm_record_id.
-- "slug" implies a URL-friendly string; this is an arbitrary external CRM identifier.
-- ADR naming-opacity audit 2026-05-07.

ALTER TABLE accounts RENAME COLUMN crm_slug TO crm_record_id;
