/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-triforce', path: '/t/sgl/admin/triforce-texts/1' },
]

export const fixtures = {
  "/api/triforce-texts": [
    {
      "id": 1,
      "tournament_id": 1,
      "text": "GG FROM\nTHE CROWD\nAT SGL",
      "author": "Ari",
      "user_id": 11,
      "approved": null,
      "created_at": "{{now-2h}}"
    },
    {
      "id": 2,
      "tournament_id": 1,
      "text": "GOOD LUCK\nRUNNERS\n",
      "author": "Bex",
      "user_id": 12,
      "approved": true,
      "approved_at": "{{now-1h}}",
      "created_at": "{{now-3h}}"
    }
  ]
}
