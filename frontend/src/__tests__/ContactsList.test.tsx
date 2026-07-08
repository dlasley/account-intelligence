import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import ContactsList from '@/components/ContactsList'

const EXTERNAL = [
  { id: 'c-1', display_name: 'Priya Sharma', email: 'priya@formationbio.com', is_internal: false },
  { id: 'c-2', display_name: null, email: 'bob@formationbio.com', is_internal: false },
]

const INTERNAL = [
  { id: 'c-3', display_name: 'CSM Dave', email: 'dave@quantaslabs.com', is_internal: true },
]

describe('ContactsList', () => {
  it('renders "No contacts identified" when list is empty', () => {
    render(<ContactsList contacts={[]} />)
    expect(screen.getByText('No contacts identified.')).toBeTruthy()
  })

  it('renders "No contacts identified" when all contacts are internal', () => {
    render(<ContactsList contacts={INTERNAL} />)
    expect(screen.getByText('No contacts identified.')).toBeTruthy()
  })

  it('renders external contact display names', () => {
    render(<ContactsList contacts={EXTERNAL} />)
    expect(screen.getByText('Priya Sharma')).toBeTruthy()
  })

  it('falls back to email when display_name is null', () => {
    render(<ContactsList contacts={EXTERNAL} />)
    expect(screen.getByText('bob@formationbio.com')).toBeTruthy()
  })

  it('does not render internal contacts', () => {
    render(<ContactsList contacts={[...EXTERNAL, ...INTERNAL]} />)
    expect(screen.queryByText('CSM Dave')).toBeNull()
    expect(screen.queryByText('dave@quantaslabs.com')).toBeNull()
  })
})
