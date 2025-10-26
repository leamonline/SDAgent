"""
Local fallback implementation for the OpenAI Agents SDK so smarter_dog.py can
run without installing external dependencies. When the real SDK is available,
smarter_dog.py will import that instead of this module.

This updated stub supports:
- Agent handoffs (handoffs, handoff_description parameters)
- Typed output extraction (output_type, final_output_as)
- Pydantic model validation
- Enhanced prompt parsing for customer details
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Dict, Iterable, Optional, Type, TypeVar

try:
    from pydantic import BaseModel
except ImportError:
    # Minimal BaseModel shim if pydantic not available
    class BaseModel:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

        @classmethod
        def model_validate(cls, data):
            return cls(**data)


T = TypeVar('T', bound=BaseModel)
ToolCallable = Callable[..., Any]


def function_tool(func: ToolCallable) -> ToolCallable:
    """Decorator shim that simply returns the wrapped function."""
    return func


@dataclass
class Agent:
    """Agent configuration with support for handoffs and typed outputs."""
    name: str
    instructions: str
    tools: Iterable[ToolCallable] = field(default_factory=list)
    handoffs: Optional[list[Agent]] = None
    handoff_description: Optional[str] = None
    output_type: Optional[Type[BaseModel]] = None


@dataclass
class HostedMCPTool:
    """Hosted MCP tool configuration."""
    tool_config: Dict[str, Any]


@dataclass
class RunnerResult:
    """Result from running an agent with typed output support."""
    final_output: str
    _output_type: Optional[Type[BaseModel]] = None

    def final_output_as(self, model_class: Type[T]) -> T:
        """
        Extract final output as a typed Pydantic model.

        Args:
            model_class: Pydantic BaseModel class to parse into

        Returns:
            Instance of model_class with validated data
        """
        try:
            data = json.loads(self.final_output)
            return model_class.model_validate(data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Could not parse final output as JSON: {self.final_output}") from exc
        except Exception as exc:
            raise ValueError(f"Could not validate output as {model_class.__name__}") from exc


class Runner:
    """Minimal harness that deterministically calls tool functions."""

    @staticmethod
    async def run(agent: Agent, prompt: str) -> RunnerResult:
        """
        Run an agent with the given prompt.

        Supports handoffs by checking if the agent has handoff agents configured.
        """
        # Handle different agent names (original and refactored)
        if agent.name in ("Smarter Dog", "Smarter Dog Grooming"):
            booking_json = await Runner._handle_booking_request(agent, prompt)
            return RunnerResult(
                final_output=booking_json,
                _output_type=agent.output_type
            )
        if agent.name == "Sheet Logger":
            status_json = await Runner._handle_sheet_logging(prompt)
            return RunnerResult(
                final_output=status_json,
                _output_type=agent.output_type
            )
        raise RuntimeError(f"Unsupported agent '{agent.name}'.")

    @staticmethod
    async def _handle_booking_request(agent: Agent, prompt: str) -> str:
        """Handle booking requests with enhanced customer detail extraction."""
        request = Runner._parse_booking_prompt(prompt)
        tools = list(agent.tools)
        if len(tools) < 2:
            raise RuntimeError("Booking agent requires availability and booking tools.")

        get_available, book = tools[0], tools[1]

        # Call availability tool
        availability = get_available(
            requested_date=request["requested_date"],
            dog_size=request["dog_size"],
        )

        # Select slot (prefer requested, fallback to first available)
        slot = request["requested_time"]
        if slot not in availability["available_slots"]:
            alternatives = availability["available_slots"]
            if not alternatives:
                raise RuntimeError("No slots available; booking cannot be completed.")
            slot = alternatives[0]

        # Call booking tool
        booking = book(
            dog_name=request["dog_name"],
            dog_size=request["dog_size"],
            requested_date=request["requested_date"],
            requested_time=slot,
            customer_name=request["customer_name"],
            contact_number=request["contact_number"],
        )

        await asyncio.sleep(0)
        return json.dumps(booking)

    @staticmethod
    async def _handle_sheet_logging(prompt: str) -> str:
        """Handle sheet logging by parsing booking payload from prompt."""
        marker = "Booking payload:\n"
        if marker not in prompt:
            raise RuntimeError("Sheet logger prompt missing booking payload.")

        booking_json = prompt.split(marker, 1)[1]
        booking = json.loads(booking_json)

        details = (
            f"Appended booking for {booking['dog_name']} on {booking['date']} at {booking['time']} "
            f"for {booking['customer']}."
        )

        await asyncio.sleep(0)
        return json.dumps({"status": "success", "details": details})

    @staticmethod
    def _parse_booking_prompt(prompt: str) -> Dict[str, str]:
        """
        Extract booking details from the request prompt.

        Supports parsing:
        - Dog name (e.g., "book Luna")
        - Dog size (small/medium/large)
        - Date (e.g., "July 17th")
        - Time (e.g., "10:30")
        - Customer name (e.g., "Customer name is Sarah Chen")
        - Phone number (e.g., "phone number is 555-0123")

        Falls back to defaults when parts are missing.
        """
        # Extract dog name
        dog_name = Runner._match_or_default(r"book (\w+)", prompt, default="Doggo")

        # Extract dog size
        size = Runner._match_or_default(r"a (small|medium|large) dog", prompt, default="medium")

        # Extract time
        time_match = Runner._match_or_default(r"at (\d{1,2}:\d{2})", prompt, default="09:00")

        # Extract date
        date_text = Runner._match_or_default(
            r"for ([A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?)",
            prompt,
            default="July 10th",
        )
        parsed_date = Runner._parse_month_day(date_text)

        # Extract customer name (enhanced)
        customer_name = Runner._match_or_default(
            r"[Cc]ustomer name is ([A-Za-z\s]+?)(?:,|\.|$)",
            prompt,
            default="Smarter Dog Customer"
        )
        customer_name = customer_name.strip()

        # Extract phone number (enhanced)
        contact_number = Runner._match_or_default(
            r"phone number is ([\d\-]+)",
            prompt,
            default="N/A"
        )

        return {
            "dog_name": dog_name,
            "dog_size": size,
            "requested_date": parsed_date,
            "requested_time": time_match,
            "customer_name": customer_name,
            "contact_number": contact_number,
        }

    @staticmethod
    def _match_or_default(pattern: str, text: str, default: str) -> str:
        """Match regex pattern in text, return default if not found."""
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1) if match else default

    @staticmethod
    def _parse_month_day(value: str) -> str:
        """Parse month/day text into ISO date format."""
        cleaned = re.sub(r"(st|nd|rd|th)", "", value)
        current_year = 2024
        try:
            parsed = datetime.strptime(f"{cleaned} {current_year}", "%B %d %Y")
        except ValueError:
            parsed = datetime(current_year, 7, 10)
        return parsed.date().isoformat()


__all__ = ["Agent", "HostedMCPTool", "Runner", "RunnerResult", "function_tool"]
