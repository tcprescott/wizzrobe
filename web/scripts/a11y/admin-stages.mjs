/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-stages', path: '/t/sgl/admin/stages/1' },
  { name: 'admin-stages-add', path: '/t/sgl/admin/stages', act: (p) => p.getByRole('button', { name: 'Add stage' }).click(), wait: 'role=dialog' },
]

export const fixtures = {
  "/api/stages": [
    {
      "id": 1,
      "name": "Main stage",
      "is_active": true,
      "stream_url": "https://twitch.tv/speedgaming",
      "created_at": "{{now-10d}}",
      "updated_at": "{{now-10d}}"
    },
    {
      "id": 2,
      "name": "Side stage",
      "is_active": true,
      "stream_url": null,
      "created_at": "{{now-10d}}",
      "updated_at": "{{now-10d}}"
    },
    {
      "id": 3,
      "name": "Old stage",
      "is_active": false,
      "stream_url": null,
      "created_at": "{{now-10d}}",
      "updated_at": "{{now-10d}}"
    }
  ]
}
