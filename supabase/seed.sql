-- Runs automatically after `supabase db reset` (and on the first `supabase start`).
--
-- Seeds the organization + workspace for the `seed-stage-saas` fixture scenario
-- (fixtures/synthetic/seed-stage-saas/) with the exact deterministic IDs the worker
-- computes from their slugs (uuid5(NAMESPACE_DNS, "lattice-build")). Running
-- `process-fixtures --scenario seed-stage-saas` afterward upserts the same slug and
-- ID, so it updates these rows in place rather than creating duplicates.
--
-- Accounts, contacts, and signals are populated by `process-fixtures`, not here.
--
-- This does NOT create a `public.users` row: that table's `id` column is a foreign
-- key to `auth.users(id)`, and no auth identity exists until one is created by hand
-- (Studio, or `supabase auth admin` on the CLI). See the README's local-setup
-- section for the exact insert once that identity exists — it references the fixed
-- workspace ID seeded below so there is nothing to look up first.

insert into organizations (id, slug, name)
values ('e20008c3-2f9f-5717-a076-eb101fd99bd8', 'lattice-build', 'Lattice Build')
on conflict (slug) do nothing;

insert into workspaces (id, organization_id, slug, name, internal_domains)
values (
    'e20008c3-2f9f-5717-a076-eb101fd99bd8',
    'e20008c3-2f9f-5717-a076-eb101fd99bd8',
    'lattice-build',
    'Lattice Build',
    '{}'
)
on conflict (slug) do nothing;
