"""Deterministic policy engine.

A policy definition is JSON:

    {
      "when": {"any": [ {"field": "risk.score", "op": "gte", "value": 75},
                        {"all": [ {...}, {...} ]} ]},
      "action": "escalate",            # approve | reject | escalate | manual_review | flag
      "reason": "Critical risk score"
    }

Evaluation semantics (documented and tested):
  * Active policies are evaluated in ascending `priority` (lower number = evaluated first).
  * `flag` is non-terminal: it records a flag and evaluation continues.
  * The first matching policy with a terminal action decides. Every policy's result is still
    recorded in the trace so decisions are fully explainable.
  * If no terminal policy matches, the decision falls back to the risk band
    (low→approve, medium→manual_review, high/critical→escalate).
  * System guard: an automated approval is downgraded to manual_review unless every executed
    provider check passed. Policies cannot override this.

No LLM is involved anywhere in this module.
"""

from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Op = Literal["eq", "neq", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "exists", "missing"]
Action = Literal["approve", "reject", "escalate", "manual_review", "flag"]
TERMINAL = {"approve", "reject", "escalate", "manual_review"}
FALLBACK = {"low": "approve", "medium": "manual_review", "high": "escalate", "critical": "escalate"}
MAX_DEPTH = 6


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(pattern=r"^[a-z_]+(\.[a-z_]+){0,4}$")
    op: Op
    value: Any = None

    @model_validator(mode="after")
    def _check_value(self):
        if self.op in ("in", "not_in") and not isinstance(self.value, list):
            raise ValueError(f"'{self.op}' requires a list value")
        if self.op in ("gt", "gte", "lt", "lte") and not isinstance(self.value, (int, float)):
            raise ValueError(f"'{self.op}' requires a numeric value")
        return self


class Group(BaseModel):
    model_config = ConfigDict(extra="forbid")
    all: list["Node"] | None = None
    any: list["Node"] | None = None

    @model_validator(mode="after")
    def _one_of(self):
        if (self.all is None) == (self.any is None):
            raise ValueError("group must have exactly one of 'all' or 'any'")
        if not (self.all or self.any):
            raise ValueError("group must not be empty")
        return self


Node = Annotated[Condition | Group, Field(union_mode="left_to_right")]
Group.model_rebuild()


class PolicyDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    when: Node
    action: Action
    reason: str = Field(min_length=3, max_length=300)

    @model_validator(mode="after")
    def _depth(self):
        def depth(n, d=1):
            if isinstance(n, Condition):
                return d
            return max(depth(c, d + 1) for c in (n.all or n.any))

        if depth(self.when) > MAX_DEPTH:
            raise ValueError(f"conditions nested deeper than {MAX_DEPTH}")
        return self


@dataclass
class PolicySnapshot:
    id: str
    name: str
    version: int
    priority: int
    definition: dict


@dataclass
class PolicyDecision:
    action: str
    decided_by: str  # policy id, "risk_fallback" or "system_guard"
    reason: str
    flags: list[str] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"action": self.action, "decided_by": self.decided_by, "reason": self.reason, "flags": self.flags, "trace": self.trace}


_MISSING = object()


def resolve(ctx: dict, path: str):
    cur: Any = ctx
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def _cmp(actual, op: str, expected) -> bool:
    if op == "exists":
        return actual is not _MISSING and actual is not None
    if op == "missing":
        return actual is _MISSING or actual is None
    if actual is _MISSING or actual is None:
        return False
    try:
        match op:
            case "eq":
                return actual == expected
            case "neq":
                return actual != expected
            case "gt":
                return actual > expected
            case "gte":
                return actual >= expected
            case "lt":
                return actual < expected
            case "lte":
                return actual <= expected
            case "in":
                return actual in expected
            case "not_in":
                return actual not in expected
            case "contains":
                return expected in actual
    except TypeError:
        return False  # type mismatch never matches; deterministic, never raises
    return False


def evaluate_node(node: dict, ctx: dict, explain: list) -> bool:
    if "field" in node:
        actual = resolve(ctx, node["field"])
        ok = _cmp(actual, node["op"], node.get("value"))
        explain.append({
            "field": node["field"], "op": node["op"], "expected": node.get("value"),
            "actual": None if actual is _MISSING else actual, "matched": ok,
        })
        return ok
    if "all" in node and node["all"] is not None:
        return all([evaluate_node(c, ctx, explain) for c in node["all"]])  # list: evaluate all for full trace
    return any([evaluate_node(c, ctx, explain) for c in node["any"]])


def evaluate(policies: list[PolicySnapshot], ctx: dict, provider_outcomes: list[str]) -> PolicyDecision:
    decision: PolicyDecision | None = None
    flags, trace = [], []
    for p in sorted(policies, key=lambda p: (p.priority, p.name)):
        d = p.definition
        conds: list = []
        matched = evaluate_node(d["when"], ctx, conds)
        applied = matched and decision is None and (d["action"] == "flag" or d["action"] in TERMINAL)
        trace.append({
            "policy_id": p.id, "policy_name": p.name, "version": p.version, "priority": p.priority,
            "action": d["action"], "matched": matched, "applied": applied, "reason": d["reason"], "conditions": conds,
        })
        if not applied:
            continue
        if d["action"] == "flag":
            flags.append(d["reason"])
        else:
            decision = PolicyDecision(d["action"], p.id, d["reason"])

    if decision is None:
        cat = ctx.get("risk", {}).get("category", "medium")
        decision = PolicyDecision(FALLBACK.get(cat, "manual_review"), "risk_fallback", f"No terminal policy matched; {cat} risk band default")

    executed = [o for o in provider_outcomes if o != "skipped"]
    if decision.action == "approve" and (not executed or any(o != "pass" for o in executed)):
        decision = PolicyDecision("manual_review", "system_guard", "Automated approval requires every executed provider check to pass")

    decision.flags, decision.trace = flags, trace
    return decision
