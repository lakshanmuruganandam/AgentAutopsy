"""Contract validation and checkpointing for AgentAutopsy.

Addresses the 'fail early' problem where agents hallucinate malformed 
outputs at step 8 but don't crash until step 45. Validates outputs against 
Pydantic contracts and checkpoints valid states to SQLite to allow resuming.
"""

from __future__ import annotations

import json
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from agentautopsy.db import get_db, insert_event

T = TypeVar("T", bound=BaseModel)

class ContractCheckpointer:
    """Enforce contracts on agent steps and checkpoint valid state."""

    def __init__(self, run_id: str, db: Any | None = None) -> None:
        self.run_id = run_id
        self.db = db or get_db()

    def enforce(self, step_name: str, output: dict[str, Any], contract: type[T]) -> T:
        """Validate an output against a Pydantic contract.
        
        If valid, checkpoints the state to the SQLite DB.
        If invalid, intercepts the error and crashes immediately.
        """
        try:
            validated = contract.model_validate(output)
        except ValidationError as e:
            # Detonate immediately on contract failure
            error_details = {"errors": e.errors(), "raw_output": output}
            insert_event(
                self.db, 
                self.run_id, 
                "contract_failure", 
                {
                    "step": step_name, 
                    "contract": contract.__name__, 
                    "details": error_details
                }
            )
            print(f"\n[AgentAutopsy] 🛑 CONTRACT DETONATION at step '{step_name}'")
            print(f"Expected schema: {contract.model_json_schema()}")
            print(f"Validation Error: {e}")
            raise RuntimeError(f"Contract failure at {step_name}") from e

        # Checkpoint successful state
        insert_event(
            self.db,
            self.run_id,
            "checkpoint",
            {
                "step": step_name,
                "contract": contract.__name__,
                "state": validated.model_dump(mode="json")
            }
        )
        return validated

    def get_last_checkpoint(self, step_name: str) -> dict[str, Any] | None:
        """Resume state from the last successful checkpoint of a given step."""
        if not self.db["events"].exists():
            return None
            
        rows = list(self.db["events"].rows_where(
            where="run_id = ? AND type = 'checkpoint'",
            where_args=[self.run_id],
            order_by="timestamp desc"
        ))
        
        for row in rows:
            try:
                payload = json.loads(row.get("payload", "{}"))
                if payload.get("step") == step_name:
                    return payload.get("state")
            except Exception:
                continue
        return None
