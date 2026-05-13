export type AccountListRow = {
  id: string
  workspace_id: string
  slug: string
  name: string
  primary_domain: string | null
  additional_domains: string[]
  vertical: string | null
  crm_record_id: string | null
  status: 'active' | 'candidate'
  last_narrative_generated_at: string | null
  created_at: string
  updated_at: string
  overall_health_score: number | null
  narrative_excerpt: string | null
  last_signal_at: string | null
  audit_passed: boolean | null
  audit_criteria_passed: number | null
  audit_criteria_total: number | null
  audit_audited_at: string | null
}
