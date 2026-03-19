"""
phase/solid_engine.py — SOLID: the 3-validator consensus engine.

SOLID runs three independent validators using different LLM models.
Majority vote (2/3) required to approve any result.

Validates two things:
  1. FLUX task results — did the node actually solve the problem?
  2. PLASMA evolution proposals — is the proposed code change safe?

Design principles:
  - Three DIFFERENT models for genuine diversity of opinion
  - Each validator gets the same prompt independently (no anchoring)
  - Validators explain their reasoning, not just yes/no
  - Rejection always includes actionable feedback for the FLUX node
  - SOLID never modifies results — only approves or rejects

Usage:
    from solid_engine import solid
    result = await solid.validate_task_result(task, flux_output)
    result = await solid.validate_evolution(proposal_code, current_code)
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Optional

from config  import cfg
from llm     import call_llm
from state   import state
from task    import Task, ValidationResult

logger = logging.getLogger(__name__)

# ── Prompts ───────────────────────────────────────────────────────────────────

_TASK_VALIDATE_PROMPT = """\
You are SOLID — an independent validator in the PHASE multi-agent AI system.
Your job: evaluate whether a FLUX node's output actually solves the given task.

ORIGINAL TASK:
{goal}

FLUX NODE TYPE: {node_type}

FLUX OUTPUT:
{output}

Evaluate on three criteria:
1. COMPLETENESS — does the output fully address the task?
2. QUALITY — is the output correct, clear, and well-structured?
3. SAFETY — does the output contain anything harmful or problematic?

Respond in this exact format:
VOTE: YES or NO
REASON: (2-3 sentences explaining your decision)
FEEDBACK: (if NO — one specific, actionable improvement the node should make)
"""

_EVOLUTION_VALIDATE_PROMPT = """\
You are SOLID — an independent validator in the PHASE multi-agent AI system.
PLASMA (the boss agent) is proposing to modify its own code.
Your job: evaluate whether this change is safe and beneficial.

PROPOSED CHANGE DESCRIPTION:
{description}

CURRENT CODE (relevant section):
{current_code}

PROPOSED NEW CODE:
{proposed_code}

Evaluate:
1. SAFETY — does this change break anything critical?
2. ALIGNMENT — does it respect the system's core principles (LLM-First, Continuity, Minimalism)?
3. BENEFIT — does it actually improve something meaningful?
4. REVERSIBILITY — if it goes wrong, can it be rolled back?

Respond in this exact format:
VOTE: YES or NO
REASON: (2-3 sentences)
FEEDBACK: (one specific concern or suggestion)
"""


# ── Validator ─────────────────────────────────────────────────────────────────

@dataclass
class ValidatorVote:
    validator_id: str
    model:        str
    vote:         bool
    reason:       str
    feedback:     str


async def _cast_vote(
    validator_id: str,
    model:        str,
    prompt:       str,
) -> ValidatorVote:
    """One validator casts one vote."""
    raw = await call_llm(
        model      = model,
        messages   = [{"role": "user", "content": prompt}],
        max_tokens = 200,
        temperature= 0.3,   # lower temp for consistency
        tag        = f"solid_{validator_id}",
    )
    raw = (raw or "").strip()

    # Parse structured response
    vote     = False
    reason   = ""
    feedback = ""

    for line in raw.split("\n"):
        line = line.strip()
        if line.upper().startswith("VOTE:"):
            vote = "YES" in line.upper()
        elif line.startswith("REASON:"):
            reason = line[7:].strip()
        elif line.startswith("FEEDBACK:"):
            feedback = line[9:].strip()

    if not reason:
        reason = raw[:150]   # fallback if format not followed

    return ValidatorVote(
        validator_id = validator_id,
        model        = model,
        vote         = vote,
        reason       = reason,
        feedback     = feedback,
    )


# ── Main SOLID class ──────────────────────────────────────────────────────────

class Solid:

    def __init__(self):
        self.models = {
            "v1": cfg.solid.validator_1,
            "v2": cfg.solid.validator_2,
            "v3": cfg.solid.validator_3,
        }

    async def validate_task_result(
        self,
        task:   Task,
        output: str,
    ) -> ValidationResult:
        """
        Validate a FLUX node's task output.
        Returns ValidationResult with approved/rejected verdict.
        """
        if not output or not output.strip():
            await state.record_vote(approved=False)
            return ValidationResult(
                task_id  = task.task_id,
                approved = False,
                votes    = [],
                consensus= "rejected",
                feedback = "FLUX node returned empty output. Task must be retried.",
            )

        prompt = _TASK_VALIDATE_PROMPT.format(
            goal      = task.goal[:800],
            node_type = task.node_type,
            output    = output[:1500],
        )

        return await self._run_vote(task.task_id, prompt)

    async def validate_evolution(
        self,
        description:   str,
        current_code:  str,
        proposed_code: str,
    ) -> ValidationResult:
        """
        Validate a PLASMA evolution proposal.
        Higher bar than task validation — ALL 3 must approve.
        """
        prompt = _EVOLUTION_VALIDATE_PROMPT.format(
            description   = description[:400],
            current_code  = current_code[:1000],
            proposed_code = proposed_code[:1000],
        )

        result = await self._run_vote("evolution", prompt, require_unanimous=True)
        logger.info(
            "solid: evolution vote — %s (%s)",
            "APPROVED" if result.approved else "REJECTED",
            result.consensus,
        )
        return result

    async def _run_vote(
        self,
        context_id:        str,
        prompt:            str,
        require_unanimous: bool = False,
    ) -> ValidationResult:
        """
        Run all 3 validators concurrently and tally votes.
        """
        # All 3 vote independently and concurrently
        vote_tasks = [
            _cast_vote(vid, model, prompt)
            for vid, model in self.models.items()
        ]
        votes: list[ValidatorVote] = await asyncio.gather(*vote_tasks)

        yes_count = sum(1 for v in votes if v.vote)
        no_count  = len(votes) - yes_count

        if require_unanimous:
            approved = yes_count == len(votes)
        else:
            approved = yes_count >= 2   # majority

        if yes_count == len(votes):
            consensus = "unanimous"
        elif yes_count > no_count:
            consensus = "majority"
        elif yes_count == no_count:
            consensus = "split"
        else:
            consensus = "rejected"

        # Aggregate feedback from no-voters
        feedback_parts = [
            f"[{v.validator_id}]: {v.feedback}"
            for v in votes if not v.vote and v.feedback
        ]
        feedback = " | ".join(feedback_parts) if feedback_parts else "No specific feedback."

        await state.record_vote(approved=approved)
        await state.log_event(
            source     = "SOLID",
            event_type = "vote_result",
            message    = f"{'APPROVED' if approved else 'REJECTED'} ({consensus}) for {context_id}",
            metadata   = {
                "yes": yes_count, "no": no_count,
                "context_id": context_id,
                "unanimous_required": require_unanimous,
            },
        )

        logger.info(
            "solid: %s/%d yes — %s [%s]",
            yes_count, len(votes),
            "APPROVED" if approved else "REJECTED",
            context_id,
        )

        return ValidationResult(
            task_id  = context_id,
            approved = approved,
            votes    = [
                {
                    "validator": v.validator_id,
                    "model":     v.model,
                    "vote":      v.vote,
                    "reason":    v.reason,
                    "feedback":  v.feedback,
                }
                for v in votes
            ],
            consensus = consensus,
            feedback  = feedback,
        )

    def status_summary(self) -> str:
        lines = ["SOLID validators:"]
        for vid, model in self.models.items():
            lines.append(f"  {vid}: {model}")
        return "\n".join(lines)


# ── Singleton ─────────────────────────────────────────────────────────────────
solid = Solid()
