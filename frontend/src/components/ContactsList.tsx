export type Contact = {
  id: string
  display_name: string | null
  email: string
  is_internal: boolean
}

export default function ContactsList({ contacts }: { contacts: Contact[] }) {
  const external = contacts.filter((c) => !c.is_internal)

  return (
    <section>
      <h2 className="text-lg font-semibold mb-3">Contacts</h2>
      {external.length === 0 ? (
        <p className="text-gray-400 text-sm">No contacts identified.</p>
      ) : (
        <ul className="space-y-2">
          {external.map((c) => (
            <li key={c.id} className="text-sm">
              <div className="font-medium">{c.display_name ?? c.email}</div>
              {c.display_name && <div className="text-gray-500 text-xs">{c.email}</div>}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
