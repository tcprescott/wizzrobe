/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-surveys', path: '/t/sgl/admin/surveys' },
  { name: 'admin-survey-build', path: '/t/sgl/admin/surveys/8/build/71' },
  { name: 'admin-survey-question', path: '/t/sgl/admin/surveys/8/build/71', act: (p) => p.getByRole('button', { name: 'Add question' }).click(), wait: 'role=dialog' },
  { name: 'admin-survey-send', path: '/t/sgl/admin/surveys/8/send' },
  { name: 'admin-survey-sent', path: '/t/sgl/admin/surveys/7/send' },
  { name: 'admin-survey-results', path: '/t/sgl/admin/surveys/7/results' },
  { name: 'admin-survey-comments', path: '/t/sgl/admin/surveys/7/comments/all' },
  { name: 'admin-survey-preview', path: '/t/sgl/admin/surveys/8/preview/71%7C' },
]

export const fixtures = {
  "/api/surveys": [
    {
      "id": 7,
      "title": "SGL26 post-event survey",
      "status": "open",
      "allow_anonymous": true,
      "sent_at": "2026-11-01T18:00:00Z",
      "closes_at": "2026-11-08T23:59:00Z",
      "recipients": 1140,
      "answered": 312,
      "reminders_sent": 0,
      "reminders_left": 2,
      "next_reminder_at": "2026-11-02T18:00:00Z"
    },
    {
      "id": 8,
      "title": "SGL26 volunteer check-in",
      "status": "draft",
      "allow_anonymous": true,
      "sent_at": null,
      "closes_at": null,
      "recipients": 0,
      "answered": 0,
      "reminders_sent": 0,
      "reminders_left": 0,
      "next_reminder_at": null
    },
    {
      "id": 6,
      "title": "SGL25 post-event survey",
      "status": "closed",
      "allow_anonymous": true,
      "sent_at": "2025-11-01T18:00:00Z",
      "closes_at": "2025-11-08T23:59:00Z",
      "recipients": 1020,
      "answered": 498,
      "reminders_sent": 0,
      "reminders_left": 0,
      "next_reminder_at": null
    }
  ],
  "/api/surveys/7": {
    "id": 7,
    "title": "SGL26 post-event survey",
    "status": "open",
    "allow_anonymous": true,
    "sent_at": "2026-11-01T18:00:00Z",
    "closes_at": "2026-11-08T23:59:00Z",
    "recipients": 1140,
    "answered": 312,
    "reminders_sent": 0,
    "reminders_left": 2,
    "next_reminder_at": "2026-11-02T18:00:00Z",
    "intro": "Thanks for coming. This takes a few minutes.",
    "after_send": "Results go up on the blog in December.",
    "sections": [
      {
        "id": 71,
        "title": "About the event",
        "description": "",
        "audience": "everyone",
        "position": 0,
        "conditions": [],
        "questions": [
          {
            "id": 701,
            "prompt": "How was SGL26 overall?",
            "kind": "rating",
            "options": null,
            "required": true,
            "other_option": false,
            "none_option": null,
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "position": 0,
            "conditions": [],
            "original_prompt": null,
            "reworded_at": null,
            "reworded_after": null,
            "copied_from_id": null
          },
          {
            "id": 702,
            "prompt": "How likely are you to recommend SGL to a friend?",
            "kind": "nps",
            "options": null,
            "required": false,
            "other_option": false,
            "none_option": null,
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "position": 1,
            "conditions": [],
            "original_prompt": null,
            "reworded_at": null,
            "reworded_after": null,
            "copied_from_id": null
          },
          {
            "id": 703,
            "prompt": "Which days did you come?",
            "kind": "choice_many",
            "options": [
              "Thursday",
              "Friday",
              "Saturday",
              "Sunday"
            ],
            "required": false,
            "other_option": false,
            "none_option": "None of these",
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "position": 2,
            "conditions": [],
            "original_prompt": null,
            "reworded_at": null,
            "reworded_after": null,
            "copied_from_id": null
          },
          {
            "id": 704,
            "prompt": "Did the schedule work for you?",
            "kind": "yes_no",
            "options": null,
            "required": false,
            "other_option": false,
            "none_option": null,
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "position": 3,
            "conditions": [],
            "original_prompt": null,
            "reworded_at": null,
            "reworded_after": null,
            "copied_from_id": null
          },
          {
            "id": 705,
            "prompt": "What should we change?",
            "kind": "comment",
            "options": null,
            "required": false,
            "other_option": false,
            "none_option": null,
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "position": 4,
            "conditions": [
              {
                "key": "answer",
                "question_id": 704,
                "values": [
                  "No"
                ]
              }
            ],
            "original_prompt": null,
            "reworded_at": null,
            "reworded_after": null,
            "copied_from_id": null
          }
        ]
      },
      {
        "id": 72,
        "title": "Your tournament",
        "description": "One of these per tournament you played.",
        "audience": "each_tournament",
        "position": 1,
        "conditions": [],
        "questions": [
          {
            "id": 711,
            "prompt": "How was the tournament?",
            "kind": "rating",
            "options": null,
            "required": false,
            "other_option": false,
            "none_option": null,
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "position": 0,
            "conditions": [],
            "original_prompt": null,
            "reworded_at": null,
            "reworded_after": null,
            "copied_from_id": null
          },
          {
            "id": 712,
            "prompt": "Did your matches start on time?",
            "kind": "yes_no",
            "options": null,
            "required": false,
            "other_option": false,
            "none_option": null,
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "position": 1,
            "conditions": [],
            "original_prompt": null,
            "reworded_at": null,
            "reworded_after": null,
            "copied_from_id": null
          },
          {
            "id": 713,
            "prompt": "Anything about this tournament?",
            "kind": "comment",
            "options": null,
            "required": false,
            "other_option": false,
            "none_option": null,
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "position": 2,
            "conditions": [],
            "original_prompt": null,
            "reworded_at": null,
            "reworded_after": null,
            "copied_from_id": null
          }
        ]
      },
      {
        "id": 73,
        "title": "Your volunteer shifts",
        "description": "",
        "audience": "each_position",
        "position": 2,
        "conditions": [],
        "questions": [
          {
            "id": 721,
            "prompt": "The briefing told me what I needed",
            "kind": "agree_scale",
            "options": [
              "I knew where to go",
              "I knew who to ask"
            ],
            "required": false,
            "other_option": false,
            "none_option": null,
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "position": 0,
            "conditions": [],
            "original_prompt": null,
            "reworded_at": null,
            "reworded_after": null,
            "copied_from_id": null
          }
        ]
      }
    ]
  },
  "/api/surveys/8": {
    "id": 8,
    "title": "SGL26 volunteer check-in",
    "status": "draft",
    "allow_anonymous": true,
    "sent_at": null,
    "closes_at": null,
    "recipients": 0,
    "answered": 0,
    "reminders_sent": 0,
    "reminders_left": 0,
    "next_reminder_at": null,
    "intro": "",
    "after_send": "",
    "sections": [
      {
        "id": 71,
        "title": "About the event",
        "description": "",
        "audience": "everyone",
        "position": 0,
        "conditions": [],
        "questions": [
          {
            "id": 701,
            "prompt": "How was SGL26 overall?",
            "kind": "rating",
            "options": null,
            "required": true,
            "other_option": false,
            "none_option": null,
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "position": 0,
            "conditions": [],
            "original_prompt": null,
            "reworded_at": null,
            "reworded_after": null,
            "copied_from_id": null
          },
          {
            "id": 702,
            "prompt": "How likely are you to recommend SGL to a friend?",
            "kind": "nps",
            "options": null,
            "required": false,
            "other_option": false,
            "none_option": null,
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "position": 1,
            "conditions": [],
            "original_prompt": null,
            "reworded_at": null,
            "reworded_after": null,
            "copied_from_id": null
          },
          {
            "id": 703,
            "prompt": "Which days did you come?",
            "kind": "choice_many",
            "options": [
              "Thursday",
              "Friday",
              "Saturday",
              "Sunday"
            ],
            "required": false,
            "other_option": false,
            "none_option": "None of these",
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "position": 2,
            "conditions": [],
            "original_prompt": null,
            "reworded_at": null,
            "reworded_after": null,
            "copied_from_id": null
          },
          {
            "id": 704,
            "prompt": "Did the schedule work for you?",
            "kind": "yes_no",
            "options": null,
            "required": false,
            "other_option": false,
            "none_option": null,
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "position": 3,
            "conditions": [],
            "original_prompt": null,
            "reworded_at": null,
            "reworded_after": null,
            "copied_from_id": null
          },
          {
            "id": 705,
            "prompt": "What should we change?",
            "kind": "comment",
            "options": null,
            "required": false,
            "other_option": false,
            "none_option": null,
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "position": 4,
            "conditions": [
              {
                "key": "answer",
                "question_id": 704,
                "values": [
                  "No"
                ]
              }
            ],
            "original_prompt": null,
            "reworded_at": null,
            "reworded_after": null,
            "copied_from_id": null
          }
        ]
      },
      {
        "id": 72,
        "title": "Your tournament",
        "description": "One of these per tournament you played.",
        "audience": "each_tournament",
        "position": 1,
        "conditions": [],
        "questions": [
          {
            "id": 711,
            "prompt": "How was the tournament?",
            "kind": "rating",
            "options": null,
            "required": false,
            "other_option": false,
            "none_option": null,
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "position": 0,
            "conditions": [],
            "original_prompt": null,
            "reworded_at": null,
            "reworded_after": null,
            "copied_from_id": null
          },
          {
            "id": 712,
            "prompt": "Did your matches start on time?",
            "kind": "yes_no",
            "options": null,
            "required": false,
            "other_option": false,
            "none_option": null,
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "position": 1,
            "conditions": [],
            "original_prompt": null,
            "reworded_at": null,
            "reworded_after": null,
            "copied_from_id": null
          },
          {
            "id": 713,
            "prompt": "Anything about this tournament?",
            "kind": "comment",
            "options": null,
            "required": false,
            "other_option": false,
            "none_option": null,
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "position": 2,
            "conditions": [],
            "original_prompt": null,
            "reworded_at": null,
            "reworded_after": null,
            "copied_from_id": null
          }
        ]
      },
      {
        "id": 73,
        "title": "Your volunteer shifts",
        "description": "",
        "audience": "each_position",
        "position": 2,
        "conditions": [],
        "questions": [
          {
            "id": 721,
            "prompt": "The briefing told me what I needed",
            "kind": "agree_scale",
            "options": [
              "I knew where to go",
              "I knew who to ask"
            ],
            "required": false,
            "other_option": false,
            "none_option": null,
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "position": 0,
            "conditions": [],
            "original_prompt": null,
            "reworded_at": null,
            "reworded_after": null,
            "copied_from_id": null
          }
        ]
      }
    ]
  },
  "/api/surveys/8/builder": {
    "typical_seconds": 240,
    "longest_seconds": 480,
    "longest_questions": 21,
    "longest_who": "a player who also volunteered and did commentary",
    "target_seconds": 300,
    "budget_seconds": 600,
    "counts": {
      "everyone": 1140
    },
    "answered": 0,
    "checks": [
      {
        "check": "follow_up",
        "link": "What should we change?",
        "text": "is optional and only shows after a No."
      }
    ],
    "checks_passed": [
      "Empty parts",
      "Unreachable questions"
    ],
    "ai_provider": "Anthropic",
    "suggestions": [
      {
        "id": 5,
        "section_id": 71,
        "model": "claude",
        "drafts": [
          {
            "index": 0,
            "prompt": "How easy was it to find your stage?",
            "kind": "rating",
            "options": null
          },
          {
            "index": 1,
            "prompt": "Which meal options did you use?",
            "kind": "choice_many",
            "options": [
              "Venue",
              "Nearby",
              "Brought my own"
            ]
          }
        ]
      }
    ],
    "zone": "America/New_York"
  },
  "/api/surveys/7/builder": {
    "typical_seconds": 240,
    "longest_seconds": 480,
    "longest_questions": 21,
    "longest_who": "a player who also volunteered and did commentary",
    "target_seconds": 300,
    "budget_seconds": 600,
    "counts": {
      "everyone": 1140
    },
    "answered": 312,
    "checks": [
      {
        "check": "follow_up",
        "link": "What should we change?",
        "text": "is optional and only shows after a No."
      }
    ],
    "checks_passed": [
      "Empty parts",
      "Unreachable questions"
    ],
    "ai_provider": "Anthropic",
    "suggestions": [],
    "zone": "America/New_York"
  },
  "/api/surveys/8/preview-paths": [
    {
      "persona": "everything",
      "label": "Everyone",
      "parts": 1,
      "questions": 5,
      "seconds": 60,
      "hidden": [
        "Your tournament (only players)",
        "Your volunteer shifts (only volunteers)"
      ]
    },
    {
      "persona": "player",
      "label": "A player in two tournaments",
      "parts": 3,
      "questions": 11,
      "seconds": 150,
      "hidden": [
        "Your volunteer shifts (only volunteers)"
      ]
    },
    {
      "persona": "volunteer",
      "label": "A volunteer who also played",
      "parts": 4,
      "questions": 13,
      "seconds": 200,
      "hidden": []
    }
  ],
  "/api/surveys/7/preview-paths": [
    {
      "persona": "everything",
      "label": "Everyone",
      "parts": 1,
      "questions": 5,
      "seconds": 60,
      "hidden": [
        "Your tournament (only players)",
        "Your volunteer shifts (only volunteers)"
      ]
    },
    {
      "persona": "player",
      "label": "A player in two tournaments",
      "parts": 3,
      "questions": 11,
      "seconds": 150,
      "hidden": [
        "Your volunteer shifts (only volunteers)"
      ]
    },
    {
      "persona": "volunteer",
      "label": "A volunteer who also played",
      "parts": 4,
      "questions": 13,
      "seconds": 200,
      "hidden": []
    }
  ],
  "/api/surveys/8/preview": {
    "id": 8,
    "title": "SGL26 post-event survey",
    "intro": "Thanks for coming. This takes a few minutes.",
    "closes_at": "2026-11-08T23:59:00Z",
    "allow_anonymous": true,
    "after_send": "Results go up on the blog in December.",
    "open": false,
    "submitted": false,
    "anonymous": null,
    "seconds": 240,
    "parts": [
      {
        "key": "71|",
        "title": "About the event",
        "description": "",
        "reason": "Everyone",
        "icon": "rate_review",
        "scope": "",
        "shows_if": [],
        "questions": [
          {
            "id": 701,
            "prompt": "How was SGL26 overall?",
            "kind": "rating",
            "options": null,
            "required": true,
            "other_option": false,
            "none_option": null,
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "shows_if": []
          },
          {
            "id": 702,
            "prompt": "How likely are you to recommend SGL to a friend?",
            "kind": "nps",
            "options": null,
            "required": false,
            "other_option": false,
            "none_option": null,
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "shows_if": []
          },
          {
            "id": 703,
            "prompt": "Which days did you come?",
            "kind": "choice_many",
            "options": [
              "Thursday",
              "Friday",
              "Saturday",
              "Sunday"
            ],
            "required": false,
            "other_option": true,
            "none_option": "None of these",
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "shows_if": []
          },
          {
            "id": 704,
            "prompt": "Did the schedule work for you?",
            "kind": "yes_no",
            "options": null,
            "required": false,
            "other_option": false,
            "none_option": null,
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "shows_if": []
          },
          {
            "id": 705,
            "prompt": "What should we change?",
            "kind": "comment",
            "options": null,
            "required": false,
            "other_option": false,
            "none_option": null,
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "shows_if": [
              {
                "question_id": 704,
                "values": [
                  "No"
                ]
              }
            ]
          }
        ],
        "answers": {},
        "answered": 0,
        "showing": 5,
        "shown": true,
        "seconds": 60
      },
      {
        "key": "72|t:1",
        "title": "ALTTPR Main",
        "description": "",
        "reason": "You played ALTTPR Main",
        "icon": "rate_review",
        "scope": "t:1",
        "shows_if": [],
        "questions": [
          {
            "id": 711,
            "prompt": "How was the tournament?",
            "kind": "rating",
            "options": null,
            "required": false,
            "other_option": false,
            "none_option": null,
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "shows_if": []
          },
          {
            "id": 712,
            "prompt": "Did your matches start on time?",
            "kind": "yes_no",
            "options": null,
            "required": false,
            "other_option": false,
            "none_option": null,
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "shows_if": []
          },
          {
            "id": 713,
            "prompt": "Anything about this tournament?",
            "kind": "comment",
            "options": null,
            "required": false,
            "other_option": false,
            "none_option": null,
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "shows_if": []
          }
        ],
        "answers": {},
        "answered": 0,
        "showing": 3,
        "shown": true,
        "seconds": 60
      },
      {
        "key": "73|p:2",
        "title": "Check-in desk",
        "description": "",
        "reason": "You worked Check-in desk",
        "icon": "rate_review",
        "scope": "p:2",
        "shows_if": [],
        "questions": [
          {
            "id": 721,
            "prompt": "The briefing told me what I needed",
            "kind": "agree_scale",
            "options": [
              "I knew where to go",
              "I knew who to ask"
            ],
            "required": false,
            "other_option": false,
            "none_option": null,
            "min_picks": null,
            "max_picks": null,
            "long_answer": false,
            "shows_if": []
          }
        ],
        "answers": {},
        "answered": 0,
        "showing": 1,
        "shown": true,
        "seconds": 60
      }
    ],
    "missing": []
  },
  "/api/surveys/7/results": {
    "survey_id": 7,
    "recipients": 1140,
    "opened": 520,
    "responses": 312,
    "anonymous": 41,
    "group": null,
    "questions": [
      {
        "question_id": 701,
        "section_id": 71,
        "section_title": "About the event",
        "prompt": "How was SGL26 overall?",
        "kind": "rating",
        "overall": {
          "count": 310,
          "average": 4.4,
          "score": null,
          "counts": {
            "1": 4,
            "2": 8,
            "3": 31,
            "4": 102,
            "5": 165
          },
          "hidden": false,
          "top_share": 86,
          "statements": {}
        },
        "groups": {},
        "folded": [],
        "condition": "",
        "comments": []
      },
      {
        "question_id": 702,
        "section_id": 71,
        "section_title": "About the event",
        "prompt": "How likely are you to recommend SGL to a friend?",
        "kind": "nps",
        "overall": {
          "count": 288,
          "average": 8.7,
          "score": 52,
          "counts": {},
          "hidden": false,
          "top_share": null,
          "statements": {}
        },
        "groups": {},
        "folded": [],
        "condition": "",
        "comments": []
      },
      {
        "question_id": 704,
        "section_id": 71,
        "section_title": "About the event",
        "prompt": "Did the schedule work for you?",
        "kind": "yes_no",
        "overall": {
          "count": 300,
          "average": null,
          "score": null,
          "counts": {
            "Yes": 251,
            "No": 49
          },
          "hidden": false,
          "top_share": null,
          "statements": {}
        },
        "groups": {},
        "folded": [],
        "condition": "",
        "comments": []
      },
      {
        "question_id": 711,
        "section_id": 72,
        "section_title": "Your tournament",
        "prompt": "How was the tournament?",
        "kind": "rating",
        "overall": {
          "count": 240,
          "average": 4.2,
          "score": null,
          "counts": {},
          "hidden": false,
          "top_share": 79,
          "statements": {}
        },
        "groups": {
          "ALTTPR Main": {
            "count": 120,
            "average": 4.3,
            "score": null,
            "counts": {},
            "hidden": false,
            "top_share": 81,
            "statements": {}
          },
          "Other": {
            "count": 9,
            "average": null,
            "score": null,
            "counts": {},
            "hidden": true,
            "top_share": null,
            "statements": {}
          }
        },
        "folded": [
          "Zelda 1"
        ],
        "condition": "",
        "comments": []
      },
      {
        "question_id": 721,
        "section_id": 73,
        "section_title": "Your volunteer shifts",
        "prompt": "The briefing told me what I needed",
        "kind": "agree_scale",
        "overall": {
          "count": 60,
          "average": null,
          "score": null,
          "counts": {},
          "hidden": false,
          "top_share": null,
          "statements": {
            "I knew where to go": [
              1,
              3,
              6,
              20,
              30
            ],
            "I knew who to ask": [
              2,
              6,
              10,
              22,
              20
            ]
          }
        },
        "groups": {},
        "folded": [],
        "condition": "Volunteers",
        "comments": []
      },
      {
        "question_id": 713,
        "section_id": 72,
        "section_title": "Your tournament",
        "prompt": "Anything about this tournament?",
        "kind": "comment",
        "overall": {
          "count": 44,
          "average": null,
          "score": null,
          "counts": {},
          "hidden": false,
          "top_share": null,
          "statements": {}
        },
        "groups": {},
        "folded": [],
        "condition": "",
        "comments": []
      }
    ],
    "dropoff": {
      "started": 330,
      "declined": 12,
      "median_seconds": 250,
      "estimate_seconds": 240,
      "parts": [
        {
          "title": "About the event",
          "opened": 330,
          "answered": 325,
          "stopped": 4
        },
        {
          "title": "Your tournament · ALTTPR Main",
          "opened": 150,
          "answered": 140,
          "stopped": 6
        }
      ]
    },
    "groups": [
      {
        "key": "t:1",
        "label": "ALTTPR Main",
        "sent": 300,
        "responded": 120
      }
    ],
    "audiences": [
      {
        "key": "",
        "label": "Players",
        "sent": 800,
        "responded": 230
      },
      {
        "key": "",
        "label": "Volunteers",
        "sent": 200,
        "responded": 70
      }
    ]
  },
  "/api/surveys/7/comments": {
    "survey_id": 7,
    "unread": 2,
    "tags": [
      {
        "id": 1,
        "name": "Venue",
        "count": 3
      },
      {
        "id": 2,
        "name": "Schedule",
        "count": 1
      }
    ],
    "comments": [
      {
        "answer_id": 9001,
        "question_id": 713,
        "prompt": "Anything about this tournament?",
        "section_title": "Your tournament",
        "scope_label": "ALTTPR Main",
        "who": "Rae",
        "group": "",
        "text": "Loved the stage setup. The Saturday queue ran long though.",
        "write_in": false,
        "read": false,
        "read_at": null,
        "read_by": "",
        "tags": [
          2
        ]
      },
      {
        "answer_id": 9002,
        "question_id": 705,
        "prompt": "What should we change?",
        "section_title": "About the event",
        "scope_label": "",
        "who": "Anonymous",
        "group": "Players",
        "text": "More water stations near the back hall.",
        "write_in": false,
        "read": false,
        "read_at": null,
        "read_by": "",
        "tags": [
          1
        ]
      },
      {
        "answer_id": 9003,
        "question_id": 703,
        "prompt": "Which days did you come?",
        "section_title": "About the event",
        "scope_label": "",
        "who": "Kit",
        "group": "",
        "text": "Wednesday setup",
        "write_in": true,
        "read": true,
        "read_at": "2026-11-02T10:00:00Z",
        "read_by": "Thomas",
        "tags": []
      }
    ]
  }
}
