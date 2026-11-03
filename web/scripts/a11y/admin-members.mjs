/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-members', path: '/t/sgl/admin/members' },
  { name: 'admin-member', path: '/t/sgl/admin/members/2' },
  { name: 'admin-member-message', path: '/t/sgl/admin/members/2', act: (p) => p.getByRole('button', { name: 'Message' }).click(), wait: 'role=dialog' },
  { name: 'admin-member-menu', path: '/t/sgl/admin/members/2', act: (p) => p.getByRole('button', { name: 'More for Kirby' }).click(), wait: 'role=menu' },
  { name: 'admin-members-ada', path: '/t/sgl/admin/members/ada' },
  { name: 'admin-members-ada-request', path: '/t/sgl/admin/members/ada/6' },
]

export const fixtures = {
  "/api/admin/members": {
    "members": [
      {
        "id": 1,
        "username": "thomas",
        "display_name": "Thomas",
        "preferred_name": "Thomas",
        "pronouns": null,
        "avatar_url": null,
        "is_active": true,
        "challonge": null,
        "roles": [
          {
            "role": "staff",
            "label": "Staff",
            "event_name": null
          },
          {
            "role": "proctor",
            "label": "Proctor",
            "event_name": null
          }
        ],
        "tournaments_admin": 2,
        "tournaments_crew_coordinator": 0,
        "joined_at": "2025-09-01T15:00:00Z",
        "joined_via": "Discord",
        "joined_recently": false,
        "ada": null
      },
      {
        "id": 2,
        "username": "kirby",
        "display_name": "Kirby",
        "preferred_name": "Kirby",
        "patron_badge": "Hero patron",
        "pronouns": "they/them",
        "avatar_url": null,
        "is_active": true,
        "challonge": "kirbyrando",
        "roles": [
          {
            "role": "volunteer",
            "label": "Volunteer",
            "event_name": null
          }
        ],
        "tournaments_admin": 0,
        "tournaments_crew_coordinator": 0,
        "joined_at": "{{now-2d}}",
        "joined_via": "Join request",
        "joined_recently": true,
        "ada": {
          "request_id": 5,
          "status": "new",
          "label": "New",
          "changed_since_arranged": false
        }
      },
      {
        "id": 3,
        "username": "samus_main",
        "display_name": null,
        "preferred_name": "samus_main",
        "pronouns": null,
        "avatar_url": null,
        "is_active": true,
        "challonge": null,
        "roles": [],
        "tournaments_admin": 0,
        "tournaments_crew_coordinator": 0,
        "joined_at": "2026-08-12T18:00:00Z",
        "joined_via": "Discord",
        "joined_recently": false,
        "ada": null
      },
      {
        "id": 4,
        "username": "juno",
        "display_name": "Juno",
        "preferred_name": "Juno",
        "pronouns": "she/her",
        "avatar_url": null,
        "is_active": false,
        "challonge": null,
        "roles": [
          {
            "role": "check_in_desk",
            "label": "Check-in desk",
            "event_name": "SGL 2026"
          }
        ],
        "tournaments_admin": 0,
        "tournaments_crew_coordinator": 1,
        "joined_at": "2026-01-04T18:00:00Z",
        "joined_via": "Added by staff",
        "joined_recently": false,
        "ada": {
          "request_id": 6,
          "status": "arranged",
          "label": "Arranged",
          "changed_since_arranged": true
        }
      }
    ],
    "join_requests": 1,
    "ada_action_needed": 2,
    "switches": {
      "accommodations": true,
      "emergency_contacts": true,
      "challonge": true
    },
    "zone": "America/New_York",
    "role_options": [
      {
        "role": "staff",
        "label": "Staff",
        "per_event": false
      },
      {
        "role": "proctor",
        "label": "Proctor",
        "per_event": true
      },
      {
        "role": "stream_manager",
        "label": "Broadcast Tech",
        "per_event": true
      },
      {
        "role": "volunteer",
        "label": "Volunteer",
        "per_event": true
      },
      {
        "role": "volunteer_coordinator",
        "label": "Volunteer coordinator",
        "per_event": true
      },
      {
        "role": "check_in_desk",
        "label": "Check-in desk",
        "per_event": true
      },
      {
        "role": "equipment_manager",
        "label": "Equipment manager",
        "per_event": false
      }
    ]
  },
  "/api/join-requests": [
    {
      "id": 9,
      "user_id": 7,
      "user_name": "link_speed",
      "status": "pending",
      "message": "Running Z1R this year!",
      "created_at": "{{now-3h}}",
      "decided_at": null
    }
  ],
  "/api/users/2/details": {
    "id": 2,
    "username": "kirby",
    "display_name": "Kirby",
    "pronouns": "they/them",
    "is_active": true,
    "timezone": "America/Chicago",
    "discord_id": 1234,
    "joined_at": "{{now-2d}}",
    "joined_via": "join_request",
    "roles": [
      "volunteer"
    ],
    "tournament_admin_of": [],
    "crew_coordinator_of": [],
    "entered": [
      "ALTTPR Main",
      "Super Metroid"
    ],
    "equipment_out": [
      "#14 Retron 5"
    ],
    "check_in": "Checked in",
    "channels": {
      "has_discord": true,
      "discord_dms": true,
      "push_devices": 1,
      "email": "unverified",
      "telegram": null,
      "last_delivered_at": "{{now-5h}}"
    },
    "linked_accounts": [
      {
        "service": "Challonge",
        "name": "kirbyrando"
      }
    ],
    "patron": {
      "is_patron": true,
      "tier_ids": ["t-hero"],
      "badge_label": "Hero patron",
      "badge_hidden": false,
      "last_synced": "{{now-12m}}"
    },
    "preset": "needs_me",
    "notifications": [
      {
        "key": "match.reminder",
        "recipient": "player",
        "title": "Match starts soon",
        "section": "Your matches",
        "community": "ping",
        "theirs": "default",
        "effective": "ping",
        "reason": null
      },
      {
        "key": "crew.approved",
        "recipient": "crew",
        "title": "Crew signup approved",
        "section": "Crew",
        "community": "ping",
        "theirs": "silent",
        "effective": "silent",
        "reason": "Their preset: only what needs them"
      },
      {
        "key": "shift.reminder",
        "recipient": "volunteer",
        "title": "Shift starts soon",
        "section": "Volunteering",
        "community": "ping",
        "theirs": "off",
        "effective": "off",
        "reason": "They turned it off"
      }
    ],
    "recent_messages": [
      {
        "id": 31,
        "body": "Your match vs samus_main starts in 30 minutes at Stage 2.",
        "title": "Match starts soon",
        "created_at": "{{now-5h}}",
        "discord_id": 1234,
        "silent": false,
        "status": "sent",
        "error": null
      },
      {
        "id": 30,
        "body": "Thanks for signing up to volunteer!",
        "title": null,
        "created_at": "{{now-2d}}",
        "discord_id": 1234,
        "silent": true,
        "status": "sent",
        "error": null
      }
    ],
    "upcoming_matches": [
      {
        "match_id": 40,
        "title": null,
        "tournament": "ALTTPR Main",
        "opponents": [
          "samus_main"
        ],
        "scheduled_at": "{{now+20h}}"
      }
    ],
    "upcoming_shifts": [
      {
        "position": "Check-in desk",
        "starts_at": "{{now+1d}}",
        "ends_at": "{{now+28h}}",
        "called_off": false,
        "checked_in": false
      }
    ]
  },
  "/api/admin/members/ada-requests": [
    {
      "id": 5,
      "user_id": 2,
      "preferred_name": "Kirby",
      "username": "kirby",
      "status": "new",
      "label": "New",
      "details": "I use a wheelchair and need an accessible station near the exit.",
      "staff_notes": null,
      "changed_since_arranged": false,
      "arranged_details": null,
      "created_at": "2026-10-01T15:00:00Z",
      "updated_at": "{{now-1d}}"
    },
    {
      "id": 6,
      "user_id": 4,
      "preferred_name": "Juno",
      "username": "juno",
      "status": "arranged",
      "label": "Arranged",
      "details": "Seat near the front for hearing, and captions on the stage feed please.",
      "staff_notes": "Row A, seat 3. Captions on Stage 1.",
      "changed_since_arranged": true,
      "arranged_details": "Seat near the front for hearing.",
      "created_at": "2026-10-01T15:00:00Z",
      "updated_at": "{{now-1d}}"
    }
  ],
  "/api/admin/members/ada-requests/6": {
    "id": 6,
    "user_id": 4,
    "preferred_name": "Juno",
    "username": "juno",
    "status": "arranged",
    "label": "Arranged",
    "details": "Seat near the front for hearing, and captions on the stage feed please.",
    "staff_notes": "Row A, seat 3. Captions on Stage 1.",
    "changed_since_arranged": true,
    "arranged_details": "Seat near the front for hearing.",
    "created_at": "2026-10-01T15:00:00Z",
    "updated_at": "{{now-1d}}"
  }
}
