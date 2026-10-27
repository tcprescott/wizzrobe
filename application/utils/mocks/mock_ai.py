"""Canned answers for :mod:`application.utils.clients.ai_client` in development."""

from application.utils.clients.ai_client import AiAnswer

_DRAFTS = [
    {'prompt': 'How smooth was check-in for your matches?', 'kind': 'rating', 'options': []},
    {'prompt': 'Did your matches start close to the scheduled time?', 'kind': 'yes_no', 'options': []},
    {'prompt': 'Which part of the day would you change first?', 'kind': 'choice_one',
     'options': ['Check-in', 'Station setup', 'Start times', 'Results']},
    {'prompt': 'How much do you agree?', 'kind': 'agree_scale',
     'options': ['The schedule ran on time', 'I knew where my station was']},
]


def mock_answer(prompt: str) -> AiAnswer:
    return AiAnswer({'questions': [dict(d) for d in _DRAFTS]}, input_tokens=len(prompt) // 4, output_tokens=180)
