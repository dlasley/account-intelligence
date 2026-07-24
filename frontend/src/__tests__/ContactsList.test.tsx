import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
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

  describe('cap + "See all"', () => {
    const MANY = Array.from({ length: 9 }, (_, i) => ({
      id: `c-${i}`,
      display_name: `Contact ${i}`,
      email: `contact${i}@example.com`,
      is_internal: false,
    }))

    it('does not show the "See all" control under the cap', () => {
      render(<ContactsList contacts={EXTERNAL} />)
      expect(screen.queryByText(/See all/)).toBeNull()
    })

    it('renders only the first 7 contacts and a "See all N" control above the cap', () => {
      render(<ContactsList contacts={MANY} />)
      expect(screen.getByText('Contact 0')).toBeTruthy()
      expect(screen.getByText('Contact 6')).toBeTruthy()
      expect(screen.queryByText('Contact 7')).toBeNull()
      expect(screen.getByText('See all 9')).toBeTruthy()
    })

    it('reveals the remaining contacts on click', () => {
      render(<ContactsList contacts={MANY} />)
      fireEvent.click(screen.getByText('See all 9'))
      expect(screen.getByText('Contact 8')).toBeTruthy()
      expect(screen.getByText('Show fewer')).toBeTruthy()
    })
  })
})
