"""Schemas for a tournament's match form and a player's answers to it."""

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

Kind = Literal['yes_no', 'choice_one', 'choice_many', 'number']
Who = Literal['each_player', 'higher_seed', 'previous_loser']
AskOn = Literal['every_match', 'when_used']
Visibility = Literal['right_away', 'at_roll', 'never']
AnswerValue = Union[str, int, List[str]]


class MatchFormChoice(BaseModel):
    value: Optional[str] = Field(None, description="Kept across saves; left out, the server makes one. Yes/No use `yes`/`no`.")
    label: str = Field(..., max_length=80)
    preset: Optional[int] = Field(None, description="A preset this choice stands for, which a `from_answer` rule rolls.")


class MatchFormQuestionIn(BaseModel):
    key: Optional[str] = Field(None, description="Kept across saves; seed rules read the question by it.")
    kind: Kind
    prompt: str = Field(..., max_length=200)
    help: Optional[str] = Field(None, max_length=500)
    choices: List[MatchFormChoice] = Field(default_factory=list, description="2 to 16; Yes/No has its two fixed.")
    config: Dict[str, int] = Field(default_factory=dict, description="`min` and `max` for a number question.")
    who: Who = Field('each_player', description="Who in the match answers: everyone, the higher native-bracket seed, or the loser of the set's previous game.")
    ask_on: AskOn = Field('every_match', description="`when_used`: only on matches where a seed rule reading it could fit.")
    visibility: Visibility = Field('right_away', description="When the other players see an answer; `never` keeps it private (an opt-in).")


class MatchFormQuestionOut(MatchFormQuestionIn):
    key: str
    choices: List[MatchFormChoice]
    config: Dict[str, int]
    used_by: List[int] = Field(..., description="The numbers of the seed rules that read this question.")


class MatchFormUpdate(BaseModel):
    questions: List[MatchFormQuestionIn] = Field(..., max_length=12)


class MatchFormEditorResponse(BaseModel):
    tournament_id: int
    questions: List[MatchFormQuestionOut]


class OtherAnswer(BaseModel):
    name: str
    value: Any


class PlayerFormQuestion(BaseModel):
    key: str
    kind: Kind
    prompt: str
    help: Optional[str] = None
    choices: List[MatchFormChoice]
    config: Dict[str, int]
    visibility: Visibility
    answer: Optional[AnswerValue] = Field(None, description="Your answer, or null.")
    others: List[OtherAnswer] = Field(..., description="The other players' answers you may see.")


class PlayerFormResponse(BaseModel):
    match_id: int
    locked: bool = Field(..., description="True once the seed is rolled: answers can't change.")
    questions: List[PlayerFormQuestion]


class PlayerAnswer(BaseModel):
    value: Optional[AnswerValue] = Field(None, description="Null clears your answer.")
