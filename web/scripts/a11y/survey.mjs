/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'survey', path: '/t/sgl/survey/7' },
  { name: 'survey-part', path: '/t/sgl/survey/7/71%7C' },
]

export const fixtures = {
  "/api/me/surveys/7": {
    "id": 7,
    "title": "SGL26 post-event survey",
    "intro": "Thanks for coming. This takes a few minutes.",
    "closes_at": "2026-11-08T23:59:00Z",
    "allow_anonymous": true,
    "after_send": "Results go up on the blog in December.",
    "open": true,
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
        "answers": {
          "701": 4,
          "704": "Yes"
        },
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
  }
}
