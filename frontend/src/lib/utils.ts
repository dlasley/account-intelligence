const SCORE_BANDS = [
  { minScore: 80, label: 'high',   color: 'bg-green-100 text-green-800' },
  { minScore: 60, label: 'good',   color: 'bg-emerald-100 text-emerald-800' },
  { minScore: 40, label: 'medium', color: 'bg-yellow-100 text-yellow-800' },
  { minScore: 20, label: 'fair',   color: 'bg-orange-100 text-orange-800' },
  { minScore: 0,  label: 'low',    color: 'bg-red-100 text-red-800' },
]

export function scoreBadge(score: number | null): { label: string; color: string } {
  if (score === null) return { label: '—', color: 'bg-gray-100 text-gray-500' }
  const band = SCORE_BANDS.find(b => score >= b.minScore) ?? SCORE_BANDS.at(-1)!
  return { label: band.label, color: band.color }
}

export function relativeTime(isoString: string | null): string {
  if (!isoString) return '—'
  const diff = Date.now() - new Date(isoString).getTime()
  const seconds = Math.floor(diff / 1000)
  if (seconds < 60) return 'just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days} day${days !== 1 ? 's' : ''} ago`
  const months = Math.floor(days / 30)
  if (months < 12) return `${months} month${months !== 1 ? 's' : ''} ago`
  return `${Math.floor(months / 12)} year${Math.floor(months / 12) !== 1 ? 's' : ''} ago`
}
