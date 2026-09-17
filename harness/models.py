from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

class CandidatePools(Strict):
    average: list[Text] = Field(min_length=1, max_length=1)
    upper_mid: list[Text] = Field(min_length=1)
    lower_mid: list[Text] = Field(min_length=1)
    high: list[Text] = Field(min_length=1)

class InterviewTurn(Strict):
    text: Text = Field(max_length=1500)

class CandidateTurn(Strict):
    text: Text = Field(max_length=1800)

class Evidence(Strict):
    source_id: Text = Field(max_length=240)
    quote: Text = Field(max_length=10000)

class RankedCandidate(Strict):
    rank: int = Field(ge=1, le=4)
    candidate_id: Text
    rationale: Text = Field(max_length=10000)
    evidence: list[Evidence] = Field(min_length=1, max_length=40)
    uncertainty: Text = Field(max_length=10000)

class Ranking(Strict):
    ranking: list[RankedCandidate] = Field(min_length=4, max_length=4)

class Expression(Strict):
    voice: Text
    self_expression: Text
    memory_rule: Text
    pressure_rule: Text
    unknowns: Text
    evidence_status: Text

class CurrentThought(Strict):
    state: Text
    certainty: Literal["stated", "tentative", "unknown"]

class Persona(Strict):
    id: Text
    name: Text
    as_of: Text
    history: list[Text] = Field(min_length=1)
    motivations: Text
    behavior_patterns: Text
    traits: Text
    self_expression: Expression
    current_state: dict[str, CurrentThought]
