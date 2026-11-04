"""Schemas for a tournament's seed rules (Admin → Tournaments → Seed rules)."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class SeedRuleWhen(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    round: Optional[List[str]] = Field(None, description="Round keys, any of: `w1`, `gf`, `phase:…`, `round:2`.")
    game: Optional[List[Union[int, Literal['last']]]] = Field(
        None, description="Game numbers in a set, any of; `last` is the decider.",
    )
    title: Optional[List[str]] = Field(None, description="The match title contains any of these (any case).")
    on_stage: Optional[bool] = Field(None, description="True: only on a stage; false: only off one.")
    from_: Optional[datetime] = Field(None, alias='from', description="Scheduled at or after (UTC).")
    to: Optional[datetime] = Field(None, description="Scheduled before (UTC).")


class SeedRuleWeighted(BaseModel):
    preset: int = Field(..., description="A preset id.")
    weight: int = Field(..., ge=0, le=10_000)


class SeedRuleThen(BaseModel):
    preset: Optional[int] = Field(None, description="Roll this preset id.")
    weighted: Optional[List[SeedRuleWeighted]] = Field(None, description="Or draw one of these by weight.")


class SeedRule(BaseModel):
    id: Optional[str] = Field(None, description="Kept across saves; left out, the server makes one.")
    name: str = Field('', max_length=80)
    when: SeedRuleWhen = Field(default_factory=lambda: SeedRuleWhen.model_validate({}), description="Every condition must hold; none = every match.")
    then: SeedRuleThen
    tweaks: Dict[str, Any] = Field(
        default_factory=dict, description="Settings set on top of the preset, by dotted path (`item.swords`).",
    )


class SeedRulesUpdate(BaseModel):
    rules: List[SeedRule] = Field(..., max_length=50, description="The whole list, first match wins.")


class PresetRef(BaseModel):
    id: int
    name: str
    randomizer: str


class RoundOption(BaseModel):
    key: str
    label: str


class PreviewMatch(BaseModel):
    id: int
    label: str
    scheduled_at: Optional[datetime] = None


class SeedRulesEditorResponse(BaseModel):
    tournament_id: int
    randomizer: Optional[str] = Field(None, description="The tournament's randomizer; rules may only name its presets.")
    rules: List[SeedRule]
    default_preset: Optional[PresetRef] = Field(None, description="What rolls when no rule fits.")
    presets: List[PresetRef] = Field(..., description="Presets a rule may name.")
    rounds: List[RoundOption] = Field(..., description="Round keys this bracket offers, in play order.")
    matches: List[PreviewMatch] = Field(..., description="Unseeded matches to try in the preview, soonest first.")
    max_rules: int


class PreviewFacts(BaseModel):
    round: Optional[str] = None
    game: Optional[int] = Field(None, ge=1, le=9)
    best_of: Optional[int] = Field(None, ge=1, le=9)
    title: Optional[str] = Field(None, max_length=200)
    on_stage: bool = False
    scheduled_at: Optional[datetime] = None


class SeedRulesPreviewRequest(BaseModel):
    rules: Optional[List[SeedRule]] = Field(None, description="An unsaved draft to try; left out, the saved rules.")
    match_id: Optional[int] = Field(None, description="A real match; its facts win over `facts`.")
    facts: Optional[PreviewFacts] = Field(None, description="A made-up match.")


class PreviewOutcome(BaseModel):
    id: int
    name: str
    randomizer: str
    weight: int
    percent: float


class SeedRulesPreviewResponse(BaseModel):
    source: Literal['rule', 'default'] = Field(..., description="`default` when no rule fits: the tournament's preset.")
    rule_id: Optional[str] = None
    rule_number: Optional[int] = None
    rule_name: Optional[str] = None
    outcomes: List[PreviewOutcome] = Field(..., description="What it would roll; several with their odds when weighted.")
    tweaks: Dict[str, Any]
    settings: Optional[Any] = Field(None, description="The settings it would send; null for a weighted draw.")
    facts: PreviewFacts = Field(..., description="The facts the rules were tried against.")
    match_label: Optional[str] = None


class SeedRulesText(BaseModel):
    text: str = Field(..., max_length=100_000, description="The rules as YAML, presets by name.")


class SeedRulesParsed(BaseModel):
    rules: List[SeedRule] = Field(..., description="The text's rules, checked but not saved.")
