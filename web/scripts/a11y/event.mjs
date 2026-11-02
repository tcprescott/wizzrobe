/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'event', path: '/t/sgl/event' },
  { name: 'event-schedule', path: '/t/sgl/event/schedule' },
  { name: 'event-row-menu', path: '/t/sgl/event/schedule', act: (p) => p.getByRole('button', { name: 'More for Kai vs Morgan Wu' }).click(), wait: 'role=menu' },
  { name: 'event-crew-choice', path: '/t/sgl/event/schedule', act: (p) => p.getByRole('button', { name: 'Sign up…' }).click(), wait: 'role=dialog' },
  { name: 'event-watch', path: '/t/sgl/event/watch' },
  { name: 'event-bracket', path: '/t/sgl/event/tournament/1' },
]

export const fixtures = {
  "/api/event/hub": {
    "zone": "America/New_York",
    "stages": [
      {
        "id": 1,
        "name": "Stage 1",
        "stream_url": "https://twitch.tv/SpeedGaming",
        "channel": "speedgaming",
        "channel_name": "SpeedGaming",
        "on_now": {
          "match_id": 1,
          "scheduled_at": "{{now-12m}}",
          "started_at": null,
          "tournament_id": 1,
          "tournament": "ALTTPR Open",
          "sides": [
            {
              "name": "Sam Ruiz",
              "undecided": false
            },
            {
              "name": "Alex Chen",
              "undecided": false
            }
          ],
          "stage": ""
        },
        "up_next": [
          {
            "match_id": 4,
            "scheduled_at": "{{now+108m}}",
            "started_at": null,
            "tournament_id": 1,
            "tournament": "ALTTPR Open",
            "sides": [
              {
                "name": "Winner of 1.1",
                "undecided": true
              },
              {
                "name": "Winner of 1.2",
                "undecided": true
              }
            ],
            "stage": ""
          }
        ]
      },
      {
        "id": 2,
        "name": "Stage 2",
        "stream_url": "https://twitch.tv/SpeedGaming2",
        "channel": "speedgaming2",
        "channel_name": "SpeedGaming2",
        "on_now": null,
        "up_next": [
          {
            "match_id": 3,
            "scheduled_at": "{{now+48m}}",
            "started_at": null,
            "tournament_id": 1,
            "tournament": "Super Metroid",
            "sides": [
              {
                "name": "Kai",
                "undecided": false
              },
              {
                "name": "Morgan Wu",
                "undecided": false
              }
            ],
            "stage": ""
          }
        ]
      }
    ],
    "up_next": [
      {
        "match_id": 2,
        "scheduled_at": "{{now+18m}}",
        "started_at": null,
        "finished_at": null,
        "status": "scheduled",
        "tournament_id": 2,
        "tournament": "SMZ3",
        "round": "Round 2",
        "sides": [
          {
            "name": "Priya N.",
            "undecided": false
          },
          {
            "name": "Jordan Lee",
            "undecided": false
          }
        ],
        "stage": "",
        "stage_id": null,
        "stations": [
          "4"
        ],
        "streamed": false,
        "crew": [
          {
            "role": "commentator",
            "label": "Comm",
            "needed": 2,
            "filled": 1,
            "names": [
              "Kai"
            ],
            "yours": null,
            "can_sign_up": true
          }
        ],
        "playing": false,
        "watching": false
      },
      {
        "match_id": 3,
        "scheduled_at": "{{now+48m}}",
        "started_at": null,
        "finished_at": null,
        "status": "scheduled",
        "tournament_id": 2,
        "tournament": "Super Metroid",
        "round": "Semifinal",
        "sides": [
          {
            "name": "Kai",
            "undecided": false
          },
          {
            "name": "Morgan Wu",
            "undecided": false
          }
        ],
        "stage": "Stage 2",
        "stage_id": 1,
        "stations": [],
        "streamed": true,
        "crew": [
          {
            "role": "commentator",
            "label": "Comm",
            "needed": 2,
            "filled": 0,
            "names": [],
            "yours": null,
            "can_sign_up": true
          },
          {
            "role": "tracker",
            "label": "Tracker",
            "needed": 1,
            "filled": 0,
            "names": [],
            "yours": null,
            "can_sign_up": true
          }
        ],
        "playing": false,
        "watching": true
      },
      {
        "match_id": 4,
        "scheduled_at": "{{now+108m}}",
        "started_at": null,
        "finished_at": null,
        "status": "scheduled",
        "tournament_id": 1,
        "tournament": "ALTTPR Open",
        "round": "Top 8",
        "sides": [
          {
            "name": "Winner of 1.1",
            "undecided": true
          },
          {
            "name": "Winner of 1.2",
            "undecided": true
          }
        ],
        "stage": "Stage 1",
        "stage_id": 1,
        "stations": [],
        "streamed": true,
        "crew": [
          {
            "role": "commentator",
            "label": "Comm",
            "needed": 2,
            "filled": 1,
            "names": [
              "Riley"
            ],
            "yours": "pending",
            "can_sign_up": false
          }
        ],
        "playing": false,
        "watching": false
      }
    ],
    "tournaments": [
      {
        "id": 1,
        "name": "ALTTPR Open",
        "format": "Best of 3",
        "status": "Bracket underway",
        "tone": "live",
        "has_bracket": true,
        "bracket_url": null,
        "rules_url": "https://example.com/rules",
        "prize_total": null
      },
      {
        "id": 2,
        "name": "SMZ3",
        "format": "",
        "status": "Groups underway",
        "tone": "live",
        "has_bracket": false,
        "bracket_url": "https://challonge.com/smz3",
        "rules_url": null,
        "prize_total": null
      },
      {
        "id": 3,
        "name": "Final Fantasy",
        "format": "",
        "status": "Complete",
        "tone": "ok",
        "has_bracket": true,
        "bracket_url": null,
        "rules_url": null,
        "prize_total": null
      }
    ]
  },
  "/api/event/schedule": {
    "zone": "America/New_York",
    "rows": [
      {
        "match_id": 1,
        "scheduled_at": "{{now-12m}}",
        "started_at": null,
        "finished_at": null,
        "status": "live",
        "tournament_id": 1,
        "tournament": "ALTTPR Open",
        "round": "Top 8 · Game 2 of 3",
        "sides": [
          {
            "name": "Sam Ruiz",
            "undecided": false
          },
          {
            "name": "Alex Chen",
            "undecided": false
          }
        ],
        "stage": "Stage 1",
        "stage_id": 1,
        "stations": [],
        "streamed": true,
        "crew": [
          {
            "role": "commentator",
            "label": "Comm",
            "needed": 2,
            "filled": 2,
            "names": [
              "Kai",
              "Riley"
            ],
            "yours": null,
            "can_sign_up": false
          },
          {
            "role": "tracker",
            "label": "Tracker",
            "needed": 1,
            "filled": 1,
            "names": [
              "Dana"
            ],
            "yours": null,
            "can_sign_up": false
          }
        ],
        "playing": true,
        "watching": false
      },
      {
        "match_id": 2,
        "scheduled_at": "{{now+18m}}",
        "started_at": null,
        "finished_at": null,
        "status": "scheduled",
        "tournament_id": 2,
        "tournament": "SMZ3",
        "round": "Round 2",
        "sides": [
          {
            "name": "Priya N.",
            "undecided": false
          },
          {
            "name": "Jordan Lee",
            "undecided": false
          }
        ],
        "stage": "",
        "stage_id": null,
        "stations": [
          "4"
        ],
        "streamed": false,
        "crew": [
          {
            "role": "commentator",
            "label": "Comm",
            "needed": 2,
            "filled": 1,
            "names": [
              "Kai"
            ],
            "yours": null,
            "can_sign_up": true
          }
        ],
        "playing": false,
        "watching": false
      },
      {
        "match_id": 3,
        "scheduled_at": "{{now+48m}}",
        "started_at": null,
        "finished_at": null,
        "status": "scheduled",
        "tournament_id": 2,
        "tournament": "Super Metroid",
        "round": "Semifinal",
        "sides": [
          {
            "name": "Kai",
            "undecided": false
          },
          {
            "name": "Morgan Wu",
            "undecided": false
          }
        ],
        "stage": "Stage 2",
        "stage_id": 1,
        "stations": [],
        "streamed": true,
        "crew": [
          {
            "role": "commentator",
            "label": "Comm",
            "needed": 2,
            "filled": 0,
            "names": [],
            "yours": null,
            "can_sign_up": true
          },
          {
            "role": "tracker",
            "label": "Tracker",
            "needed": 1,
            "filled": 0,
            "names": [],
            "yours": null,
            "can_sign_up": true
          }
        ],
        "playing": false,
        "watching": true
      },
      {
        "match_id": 4,
        "scheduled_at": "{{now+108m}}",
        "started_at": null,
        "finished_at": null,
        "status": "scheduled",
        "tournament_id": 1,
        "tournament": "ALTTPR Open",
        "round": "Top 8",
        "sides": [
          {
            "name": "Winner of 1.1",
            "undecided": true
          },
          {
            "name": "Winner of 1.2",
            "undecided": true
          }
        ],
        "stage": "Stage 1",
        "stage_id": 1,
        "stations": [],
        "streamed": true,
        "crew": [
          {
            "role": "commentator",
            "label": "Comm",
            "needed": 2,
            "filled": 1,
            "names": [
              "Riley"
            ],
            "yours": "pending",
            "can_sign_up": false
          }
        ],
        "playing": false,
        "watching": false
      }
    ]
  },
  "/api/event/watch": {
    "zone": "America/New_York",
    "stages": [
      {
        "id": 1,
        "name": "Stage 1",
        "stream_url": "https://twitch.tv/SpeedGaming",
        "channel": "speedgaming",
        "channel_name": "SpeedGaming",
        "on_now": {
          "match_id": 1,
          "scheduled_at": "{{now-12m}}",
          "started_at": null,
          "tournament_id": 1,
          "tournament": "ALTTPR Open",
          "sides": [
            {
              "name": "Sam Ruiz",
              "undecided": false
            },
            {
              "name": "Alex Chen",
              "undecided": false
            }
          ],
          "stage": ""
        },
        "up_next": [
          {
            "match_id": 4,
            "scheduled_at": "{{now+108m}}",
            "started_at": null,
            "tournament_id": 1,
            "tournament": "ALTTPR Open",
            "sides": [
              {
                "name": "Winner of 1.1",
                "undecided": true
              },
              {
                "name": "Winner of 1.2",
                "undecided": true
              }
            ],
            "stage": ""
          }
        ]
      },
      {
        "id": 2,
        "name": "Stage 2",
        "stream_url": "https://twitch.tv/SpeedGaming2",
        "channel": "speedgaming2",
        "channel_name": "SpeedGaming2",
        "on_now": null,
        "up_next": [
          {
            "match_id": 3,
            "scheduled_at": "{{now+48m}}",
            "started_at": null,
            "tournament_id": 1,
            "tournament": "Super Metroid",
            "sides": [
              {
                "name": "Kai",
                "undecided": false
              },
              {
                "name": "Morgan Wu",
                "undecided": false
              }
            ],
            "stage": ""
          }
        ]
      }
    ],
    "coming_up": [
      {
        "match_id": 5,
        "scheduled_at": "{{now+300m}}",
        "started_at": null,
        "tournament_id": 1,
        "tournament": "SMZ3",
        "sides": [
          {
            "name": "Priya N.",
            "undecided": false
          },
          {
            "name": "Jordan Lee",
            "undecided": false
          }
        ],
        "stage": "Stage 2"
      },
      {
        "match_id": 6,
        "scheduled_at": "{{now+1500m}}",
        "started_at": null,
        "tournament_id": 1,
        "tournament": "Zelda 1",
        "sides": [
          {
            "name": "TBD",
            "undecided": false
          }
        ],
        "stage": "Stage 1"
      }
    ],
    "tournaments": [
      {
        "id": 1,
        "name": "ALTTPR Open",
        "format": "Best of 3",
        "status": "Bracket underway",
        "tone": "live",
        "has_bracket": true,
        "bracket_url": null,
        "rules_url": "https://example.com/rules",
        "prize_total": null
      },
      {
        "id": 2,
        "name": "SMZ3",
        "format": "",
        "status": "Groups underway",
        "tone": "live",
        "has_bracket": false,
        "bracket_url": "https://challonge.com/smz3",
        "rules_url": null,
        "prize_total": null
      },
      {
        "id": 3,
        "name": "Final Fantasy",
        "format": "",
        "status": "Complete",
        "tone": "ok",
        "has_bracket": true,
        "bracket_url": null,
        "rules_url": null,
        "prize_total": null
      }
    ]
  },
  "/api/tournaments/1/bracket": {
    "tournament_id": 1,
    "tournament": "ALTTPR Open",
    "source": "native",
    "url": "/tournament/1/brackets",
    "last_synced_at": null,
    "filtered": false,
    "your_entrant_ids": [
      1
    ],
    "entrants": [
      {
        "id": 1,
        "name": "Sam Ruiz",
        "status": null,
        "user_id": null
      },
      {
        "id": 2,
        "name": "Alex Chen",
        "status": null,
        "user_id": null
      },
      {
        "id": 3,
        "name": "Kai",
        "status": null,
        "user_id": null
      },
      {
        "id": 4,
        "name": "Riley",
        "status": null,
        "user_id": null
      },
      {
        "id": 5,
        "name": "Priya N.",
        "status": null,
        "user_id": null
      },
      {
        "id": 6,
        "name": "Jordan Lee",
        "status": null,
        "user_id": null
      },
      {
        "id": 7,
        "name": "Morgan Wu",
        "status": null,
        "user_id": null
      },
      {
        "id": 8,
        "name": "Dana Ortiz",
        "status": null,
        "user_id": null
      }
    ],
    "phases": [
      {
        "id": 10,
        "name": "Swiss stage",
        "order": 1,
        "format": "swiss",
        "state": "complete",
        "bookable": null,
        "source_id": null,
        "pools": [
          {
            "id": null,
            "name": "Swiss",
            "format": "swiss",
            "complete": true,
            "sets_complete": 0,
            "sets_total": 0,
            "sets": [],
            "standings": [
              {
                "entrant_id": 1,
                "name": "Sam Ruiz",
                "placement": 1,
                "wins": 3,
                "losses": 0,
                "draws": 0,
                "points": 9
              },
              {
                "entrant_id": 3,
                "name": "Kai",
                "placement": 2,
                "wins": 2,
                "losses": 1,
                "draws": 0,
                "points": 6
              }
            ]
          }
        ]
      },
      {
        "id": 11,
        "name": "Top 8",
        "order": 2,
        "format": "single_elim",
        "state": "active",
        "bookable": null,
        "source_id": null,
        "pools": [
          {
            "id": null,
            "name": "Top 8",
            "format": "single_elim",
            "complete": false,
            "sets_complete": 1,
            "sets_total": 7,
            "sets": [
              {
                "id": 1,
                "identifier": "1.1",
                "round": 1,
                "round_label": "Quarterfinals",
                "state": "open",
                "slots": [
                  {
                    "entrant_id": 1,
                    "name": "Sam Ruiz",
                    "placeholder": null,
                    "score": 1,
                    "from_set_id": null,
                    "from_outcome": null,
                    "user_id": null
                  },
                  {
                    "entrant_id": 2,
                    "name": "Alex Chen",
                    "placeholder": null,
                    "score": 0,
                    "from_set_id": null,
                    "from_outcome": null,
                    "user_id": null
                  }
                ],
                "winner_entrant_id": null,
                "bookings": [
                  {
                    "match_id": 1,
                    "game": "Game 2 of 3",
                    "scheduled_at": "{{now-12m}}",
                    "stage": "Stage 1",
                    "status": "live"
                  }
                ],
                "best_of": 3,
                "forfeit": false,
                "winner_to": null,
                "loser_to": null,
                "source_id": null,
                "completed_at": null
              },
              {
                "id": 2,
                "identifier": "1.2",
                "round": 1,
                "round_label": "Quarterfinals",
                "state": "complete",
                "slots": [
                  {
                    "entrant_id": 3,
                    "name": "Kai",
                    "placeholder": null,
                    "score": 2,
                    "from_set_id": null,
                    "from_outcome": null,
                    "user_id": null
                  },
                  {
                    "entrant_id": 4,
                    "name": "Riley",
                    "placeholder": null,
                    "score": 0,
                    "from_set_id": null,
                    "from_outcome": null,
                    "user_id": null
                  }
                ],
                "winner_entrant_id": 3,
                "bookings": [],
                "best_of": 3,
                "forfeit": false,
                "winner_to": null,
                "loser_to": null,
                "source_id": null,
                "completed_at": null
              },
              {
                "id": 3,
                "identifier": "1.3",
                "round": 1,
                "round_label": "Quarterfinals",
                "state": "open",
                "slots": [
                  {
                    "entrant_id": 5,
                    "name": "Priya N.",
                    "placeholder": null,
                    "score": null,
                    "from_set_id": null,
                    "from_outcome": null,
                    "user_id": null
                  },
                  {
                    "entrant_id": 6,
                    "name": "Jordan Lee",
                    "placeholder": null,
                    "score": null,
                    "from_set_id": null,
                    "from_outcome": null,
                    "user_id": null
                  }
                ],
                "winner_entrant_id": null,
                "bookings": [
                  {
                    "match_id": 1,
                    "game": "Game 2 of 3",
                    "scheduled_at": "{{now+90m}}",
                    "stage": "Stage 2",
                    "status": "scheduled"
                  }
                ],
                "best_of": 3,
                "forfeit": false,
                "winner_to": null,
                "loser_to": null,
                "source_id": null,
                "completed_at": null
              },
              {
                "id": 4,
                "identifier": "1.4",
                "round": 1,
                "round_label": "Quarterfinals",
                "state": "open",
                "slots": [
                  {
                    "entrant_id": 7,
                    "name": "Morgan Wu",
                    "placeholder": null,
                    "score": null,
                    "from_set_id": null,
                    "from_outcome": null,
                    "user_id": null
                  },
                  {
                    "entrant_id": 8,
                    "name": "Dana Ortiz",
                    "placeholder": null,
                    "score": null,
                    "from_set_id": null,
                    "from_outcome": null,
                    "user_id": null
                  }
                ],
                "winner_entrant_id": null,
                "bookings": [],
                "best_of": 3,
                "forfeit": false,
                "winner_to": null,
                "loser_to": null,
                "source_id": null,
                "completed_at": null
              },
              {
                "id": 5,
                "identifier": "2.5",
                "round": 2,
                "round_label": "Semifinals",
                "state": "pending",
                "slots": [
                  {
                    "entrant_id": null,
                    "name": null,
                    "placeholder": "Winner of 1.1",
                    "score": null,
                    "from_set_id": null,
                    "from_outcome": null,
                    "user_id": null
                  },
                  {
                    "entrant_id": 3,
                    "name": "Kai",
                    "placeholder": null,
                    "score": null,
                    "from_set_id": null,
                    "from_outcome": null,
                    "user_id": null
                  }
                ],
                "winner_entrant_id": null,
                "bookings": [],
                "best_of": 3,
                "forfeit": false,
                "winner_to": null,
                "loser_to": null,
                "source_id": null,
                "completed_at": null
              },
              {
                "id": 6,
                "identifier": "2.6",
                "round": 2,
                "round_label": "Semifinals",
                "state": "pending",
                "slots": [
                  {
                    "entrant_id": null,
                    "name": null,
                    "placeholder": "Winner of 1.3",
                    "score": null,
                    "from_set_id": null,
                    "from_outcome": null,
                    "user_id": null
                  },
                  {
                    "entrant_id": null,
                    "name": null,
                    "placeholder": "Winner of 1.4",
                    "score": null,
                    "from_set_id": null,
                    "from_outcome": null,
                    "user_id": null
                  }
                ],
                "winner_entrant_id": null,
                "bookings": [],
                "best_of": 3,
                "forfeit": false,
                "winner_to": null,
                "loser_to": null,
                "source_id": null,
                "completed_at": null
              },
              {
                "id": 7,
                "identifier": "3.7",
                "round": 3,
                "round_label": "Final",
                "state": "pending",
                "slots": [
                  {
                    "entrant_id": null,
                    "name": null,
                    "placeholder": "Winner of 2.5",
                    "score": null,
                    "from_set_id": null,
                    "from_outcome": null,
                    "user_id": null
                  },
                  {
                    "entrant_id": null,
                    "name": null,
                    "placeholder": "Winner of 2.6",
                    "score": null,
                    "from_set_id": null,
                    "from_outcome": null,
                    "user_id": null
                  }
                ],
                "winner_entrant_id": null,
                "bookings": [],
                "best_of": 3,
                "forfeit": false,
                "winner_to": null,
                "loser_to": null,
                "source_id": null,
                "completed_at": null
              }
            ],
            "standings": []
          }
        ]
      }
    ]
  }
}
