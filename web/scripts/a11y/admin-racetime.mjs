/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-racetime', path: '/t/sgl/admin/racetime/1' },
]

export const fixtures = {
  "/api/race-room-profiles": [
    {
      "id": 1,
      "name": "League race",
      "goal": "Beat the game",
      "invitational": false,
      "unlisted": false,
      "auto_start": true,
      "allow_comments": true,
      "allow_midrace_chat": true,
      "allow_non_entrant_chat": false,
      "chat_message_delay": 0,
      "start_delay": 15,
      "time_limit": 24,
      "streaming_required": true,
      "created_at": "{{now-10d}}",
      "updated_at": "{{now-10d}}"
    },
    {
      "id": 2,
      "name": "Casual",
      "goal": null,
      "invitational": false,
      "unlisted": false,
      "auto_start": true,
      "allow_comments": true,
      "allow_midrace_chat": true,
      "allow_non_entrant_chat": false,
      "chat_message_delay": 0,
      "start_delay": 15,
      "time_limit": 24,
      "streaming_required": true,
      "created_at": "{{now-10d}}",
      "updated_at": "{{now-10d}}"
    }
  ]
}
