-- ADR-013: Safe contact upsert — preserves non-NULL account_id and display_name on conflict.
-- is_internal is INSERT-only: set when the contact is first created, never overwritten
-- on subsequent signals (a manual is_internal=true flag must not be silently downgraded
-- when a later signal arrives whose payload computed is_internal=false).
-- Called by both ingest paths (normalizer.py, product_event.py) via .rpc('upsert_contact_safe', ...).

CREATE OR REPLACE FUNCTION upsert_contact_safe(
    p_workspace_id   uuid,
    p_email          text,
    p_display_name   text,
    p_is_internal    boolean,
    p_account_id     uuid
)
RETURNS contacts
LANGUAGE sql
AS $$
    INSERT INTO contacts (workspace_id, email, display_name, is_internal, account_id)
    VALUES (p_workspace_id, p_email, p_display_name, p_is_internal, p_account_id)
    ON CONFLICT (workspace_id, email) DO UPDATE
        SET account_id   = COALESCE(EXCLUDED.account_id,   contacts.account_id),
            display_name = COALESCE(EXCLUDED.display_name, contacts.display_name),
            updated_at   = now()
    RETURNING *;
$$;

-- Executable by authenticated and service_role callers (worker uses service_role;
-- future client-side paths use authenticated). Not SECURITY DEFINER — RLS on
-- contacts table continues to apply.
GRANT EXECUTE ON FUNCTION upsert_contact_safe(uuid, text, text, boolean, uuid)
    TO authenticated, service_role;
