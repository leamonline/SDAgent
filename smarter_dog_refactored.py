from __future__ import annotations

"""
Smarter Dog sample workflow - Refactored to OpenAI AgentSDK Best Practices.

This version demonstrates:
- Pydantic schemas for type-safe tool parameters and outputs
- Comprehensive docstrings for tool descriptions
- Agent handoffs for multi-agent orchestration
- Typed output extraction with output_type
- Structured error handling

Set GOOGLE_DRIVE_CONNECTOR_ID and GOOGLE_DRIVE_AUTHORIZATION before running the script.
Optionally override the sheet name with SMARTER_DOG_SHEET_NAME.
"""

import asyncio
import calendar
import json
import os
from datetime import date, datetime, timedelta
from functools import lru_cache
from threading import RLock
from typing import Literal

from pydantic import BaseModel, Field

try:
    from agents import Agent, HostedMCPTool, Runner, function_tool  # type: ignore
except ModuleNotFoundError:
    from agents_stub import Agent, HostedMCPTool, Runner, function_tool

# Constants
SLOT_TIMES = (
    "08:30",
    "09:00",
    "09:30",
    "10:00",
    "10:30",
    "11:00",
    "11:30",
    "12:00",
    "12:30",
    "13:00",
)
OPEN_WEEKDAYS = {0, 1, 2}
CAPACITY_UNITS = 2
DOG_SIZE_UNITS: dict[Literal["small", "medium", "large"], int] = {
    "small": 1,
    "medium": 1,
    "large": 2,
}
SHEET_NAME = os.environ.get("SMARTER_DOG_SHEET_NAME", "Smarter Dog Bookings")

CURRENT_BOOKINGS: dict[str, dict[str, int]] = {
    "2024-07-10": {"09:00": 2},
    "2024-07-17": {"10:30": 1},
}
BOOKINGS_LOCK = RLock()


# ============================================================================
# Pydantic Models for Type-Safe Tool Parameters and Outputs
# ============================================================================


class SlotAvailabilityRequest(BaseModel):
    """Request parameters for checking slot availability."""

    requested_date: str = Field(
        ...,
        description="Date in ISO format (YYYY-MM-DD) for which to check availability",
    )
    dog_size: Literal["small", "medium", "large"] = Field(
        ..., description="Size of the dog: small, medium, or large"
    )


class SlotAvailabilityResponse(BaseModel):
    """Response containing available time slots for grooming."""

    requested_date: str = Field(..., description="Original requested date")
    operating_date: str = Field(..., description="Actual operating date after adjustments")
    available_slots: list[str] = Field(..., description="List of available time slots")
    notes: list[str] = Field(default_factory=list, description="Additional notes or warnings")


class BookingRequest(BaseModel):
    """Request parameters for booking a grooming appointment."""

    dog_name: str = Field(..., description="Name of the dog")
    dog_size: Literal["small", "medium", "large"] = Field(
        ..., description="Size of the dog: small, medium, or large"
    )
    requested_date: str = Field(..., description="Requested date in ISO format (YYYY-MM-DD)")
    requested_time: str = Field(..., description="Requested time slot (e.g., '09:00')")
    customer_name: str = Field(..., description="Name of the customer")
    contact_number: str = Field(..., description="Customer contact phone number")


class BookingResponse(BaseModel):
    """Confirmed booking details."""

    dog_name: str
    dog_size: Literal["small", "medium", "large"]
    date: str
    time: str
    customer: str
    phone: str
    status: Literal["Booked", "Failed"]
    notes: list[str] = Field(default_factory=list)


class SheetLogResponse(BaseModel):
    """Response from sheet logging operation."""

    status: Literal["success", "error"]
    details: str


# ============================================================================
# Business Logic Helpers
# ============================================================================


def _parse_date(value: str) -> date:
    """Parse ISO date string to date object."""
    return datetime.fromisoformat(value).date()


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """Find the last occurrence of a weekday in a given month."""
    last_day = calendar.monthrange(year, month)[1]
    candidate = date(year, month, last_day)
    while candidate.weekday() != weekday:
        candidate -= timedelta(days=1)
    return candidate


@lru_cache(maxsize=None)
def _bank_holidays_for_year(year: int) -> set[date]:
    """Calculate UK bank holidays for a given year."""
    late_may = _last_weekday_of_month(year, 5, 0)  # Spring bank holiday (last Monday in May)
    late_august = _last_weekday_of_month(year, 8, 0)  # Summer bank holiday (last Monday in Aug)
    holidays = {late_may, late_august}

    christmas = date(year, 12, 25)
    holidays.add(christmas)
    # Substitute Christmas bank holidays when the 25th falls on a weekend.
    if christmas.weekday() == 5:  # Saturday
        holidays.add(christmas + timedelta(days=2))
    elif christmas.weekday() == 6:  # Sunday
        holidays.add(christmas + timedelta(days=1))
    return holidays


def _is_bank_holiday(day: date) -> bool:
    """Check if a date is a UK bank holiday."""
    return day in _bank_holidays_for_year(day.year)


def _is_christmas_shutdown(day: date) -> bool:
    """Check if a date falls within the Christmas shutdown period."""
    if day.month == 12 and day.day in {24, 25, 26}:
        return True
    dec26 = date(day.year, 12, 26)
    if day <= dec26:
        return False
    first_monday = dec26 + timedelta(days=1)
    while first_monday.weekday() != 0:
        first_monday += timedelta(days=1)
    shutdown_window = {first_monday + timedelta(days=i) for i in range(3)}
    return day in shutdown_window


def _shift_bank_holiday(day: date) -> tuple[date, list[str], bool]:
    """Shift bank holiday bookings to Thursday if they fall on operating days."""
    if day.weekday() in OPEN_WEEKDAYS and _is_bank_holiday(day):
        new_day = day + timedelta(days=3 - day.weekday())
        return new_day, [
            f"{day.isoformat()} is a bank holiday, booking moved to {new_day.isoformat()}."
        ], True
    return day, [], False


def _ensure_operating_day(day: date, force_open: bool = False) -> tuple[date, list[str]]:
    """Validate that a day is an operating day for the salon."""
    if force_open and day.weekday() == 3 and not _is_christmas_shutdown(day):
        return day, []
    if day.weekday() not in OPEN_WEEKDAYS:
        return day, [f"{day.isoformat()} falls on {day.strftime('%A')}, salon closed."]
    if _is_christmas_shutdown(day):
        return day, [f"{day.isoformat()} is during the Christmas shutdown."]
    return day, []


def _resolve_operating_day(requested_date: str) -> tuple[date, list[str], bool]:
    """Resolve the actual operating day from a requested date, handling holidays and closures."""
    requested = _parse_date(requested_date)
    day, notes, force_open = _shift_bank_holiday(requested)
    operating_day, closure_notes = _ensure_operating_day(day, force_open)
    combined_notes = [*notes, *closure_notes]
    is_open = not closure_notes and (force_open or operating_day.weekday() in OPEN_WEEKDAYS)
    return operating_day, combined_notes, is_open


def _slot_has_capacity(day: date, slot: str, units_needed: int) -> bool:
    """Check if a time slot has sufficient capacity for the booking."""
    day_key = day.isoformat()
    with BOOKINGS_LOCK:
        used = CURRENT_BOOKINGS.get(day_key, {}).get(slot, 0)
    return used + units_needed <= CAPACITY_UNITS


# ============================================================================
# Function Tools with Pydantic Schemas and Docstrings
# ============================================================================


@function_tool
def get_available_slots(
    requested_date: str, dog_size: Literal["small", "medium", "large"]
) -> dict:
    """Get available grooming time slots for a specific date and dog size.

    This tool checks which time slots are available on the requested date,
    taking into account salon operating hours, capacity constraints, existing
    bookings, bank holidays, and Christmas shutdown periods.

    Args:
        requested_date: The desired booking date in ISO format (YYYY-MM-DD)
        dog_size: The size of the dog - determines capacity units needed
            (small/medium = 1 unit, large = 2 units)

    Returns:
        Dictionary containing:
        - requested_date: Original date requested
        - operating_date: Actual date after holiday adjustments
        - available_slots: List of available time slots (HH:MM format)
        - notes: Any warnings or informational messages
    """
    operating_day, notes, is_open = _resolve_operating_day(requested_date)
    if not is_open:
        reasons = notes or [f"{operating_day.isoformat()} is outside operating days."]
        return {
            "requested_date": requested_date,
            "operating_date": operating_day.isoformat(),
            "available_slots": [],
            "notes": reasons,
        }

    units_needed = DOG_SIZE_UNITS[dog_size]
    available = [
        slot for slot in SLOT_TIMES if _slot_has_capacity(operating_day, slot, units_needed)
    ]
    return {
        "requested_date": requested_date,
        "operating_date": operating_day.isoformat(),
        "available_slots": available,
        "notes": notes,
    }


@function_tool
def book_grooming_appointment(
    dog_name: str,
    dog_size: Literal["small", "medium", "large"],
    requested_date: str,
    requested_time: str,
    customer_name: str,
    contact_number: str,
) -> dict:
    """Book a grooming appointment for a dog at a specific date and time.

    This tool attempts to book an appointment at the requested slot. It validates
    that the salon is open, the time is within operating hours, and there is
    sufficient capacity available.

    Args:
        dog_name: Name of the dog being groomed
        dog_size: Size of the dog (small, medium, or large)
        requested_date: Desired appointment date in ISO format (YYYY-MM-DD)
        requested_time: Desired time slot (e.g., '09:00', '10:30')
        customer_name: Full name of the customer
        contact_number: Customer's phone number for contact

    Returns:
        Dictionary containing confirmed booking details including:
        - dog_name, dog_size, date, time
        - customer, phone
        - status: 'Booked' if successful
        - notes: Any relevant messages or warnings

    Raises:
        ValueError: If the salon is closed, time is invalid, or slot is full
    """
    operating_day, notes, is_open = _resolve_operating_day(requested_date)
    if not is_open:
        raise ValueError(f"Salon closed on {operating_day.isoformat()}")
    if requested_time not in SLOT_TIMES:
        raise ValueError("Requested time is outside operating hours.")

    units_needed = DOG_SIZE_UNITS[dog_size]
    day_key = operating_day.isoformat()
    with BOOKINGS_LOCK:
        ledger = CURRENT_BOOKINGS.setdefault(day_key, {})
        used = ledger.get(requested_time, 0)
        if used + units_needed > CAPACITY_UNITS:
            raise ValueError("Requested slot is full; pick another time.")
        ledger[requested_time] = used + units_needed
    return {
        "dog_name": dog_name,
        "dog_size": dog_size,
        "date": operating_day.isoformat(),
        "time": requested_time,
        "customer": customer_name,
        "phone": contact_number,
        "status": "Booked",
        "notes": notes,
    }


# ============================================================================
# Agent Definitions with Handoffs
# ============================================================================


def _google_drive_connector() -> HostedMCPTool:
    """Create a Google Drive connector tool for sheet logging."""
    connector_id = os.environ.get("GOOGLE_DRIVE_CONNECTOR_ID", "local-mock-connector")
    authorization = os.environ.get("GOOGLE_DRIVE_AUTHORIZATION", "{}")
    return HostedMCPTool(
        tool_config={
            "type": "mcp",
            "server_label": "google_drive",
            "connector_id": connector_id,
            "authorization": authorization,
            "require_approval": "never",
        }
    )


def create_sheet_logger_agent() -> Agent:
    """Create an agent responsible for logging bookings to Google Sheets.

    This agent uses the Google Drive connector to find the booking spreadsheet
    and append confirmed appointment details.
    """
    return Agent(
        name="Sheet Logger",
        handoff_description="Logs confirmed bookings to the Google Sheets spreadsheet",
        instructions=(
            "You log confirmed Smarter Dog grooming appointments to a Google Sheet. "
            f"Use the Google Drive connector to find the spreadsheet titled '{SHEET_NAME}'. "
            "If the sheet exists, append a row with the booking fields in order: "
            "Date, Time, Dog Name, Size, Customer, Phone, Status, Notes. "
            "Always respond with JSON in the format: "
            '{"status": "success" | "error", "details": "description of what happened"}.'
        ),
        tools=[_google_drive_connector()],
        output_type=SheetLogResponse,
    )


def create_grooming_agent(sheet_logger: Agent) -> Agent:
    """Create the main grooming booking agent with handoff to sheet logger.

    This agent handles customer booking requests, checks availability, makes
    bookings, and hands off to the sheet logger for persistence.

    Args:
        sheet_logger: The agent responsible for logging to Google Sheets
    """
    return Agent(
        name="Smarter Dog Grooming",
        instructions=(
            "You are the booking assistant for Smarter Dog Grooming Salon. "
            "Operating hours: Monday–Wednesday, 08:30–15:00, with 30-minute slots from 08:30–13:00. "
            "Each slot supports two small/medium dogs or one large dog. "
            "Bank holidays automatically shift appointments to Thursday. "
            "The salon is closed from Christmas Eve through Boxing Day and the following Monday–Wednesday. "
            "\n\n"
            "Workflow:\n"
            "1. Use get_available_slots to check availability before booking\n"
            "2. Use book_grooming_appointment to confirm the booking\n"
            "3. Once booking is confirmed, hand off to the Sheet Logger agent to persist the booking\n"
            "\n"
            "Always check availability first. If the requested slot is unavailable, "
            "suggest the nearest alternative from the available slots."
        ),
        tools=[get_available_slots, book_grooming_appointment],
        handoffs=[sheet_logger],
        output_type=BookingResponse,
    )


# ============================================================================
# Main Workflow
# ============================================================================


async def main() -> None:
    """Run the Smarter Dog booking workflow with agent handoffs."""
    # Create agents with handoff relationship
    sheet_logger = create_sheet_logger_agent()
    grooming_agent = create_grooming_agent(sheet_logger)

    # Example booking request
    request = (
        "I'd like to book Luna, a medium dog, for July 17th at 10:30. "
        "Customer name is Sarah Chen, phone number is 555-0123. "
        "If that slot is unavailable, pick the closest alternative."
    )

    print("Starting booking request...")
    result = await Runner.run(grooming_agent, request)

    # With output_type, we can use type-safe extraction
    try:
        booking = result.final_output_as(BookingResponse)
        print(f"\nBooking confirmed for {booking.dog_name}:")
        print(f"  Date: {booking.date}")
        print(f"  Time: {booking.time}")
        print(f"  Customer: {booking.customer}")
        print(f"  Status: {booking.status}")
        if booking.notes:
            print(f"  Notes: {', '.join(booking.notes)}")
    except Exception as exc:
        # Fallback to JSON parsing if output_type not supported by stub
        print(f"\nType-safe extraction not available in stub, using JSON fallback")
        try:
            booking_dict = json.loads(result.final_output)
            print(f"\nBooking confirmed: {json.dumps(booking_dict, indent=2)}")
        except json.JSONDecodeError:
            print(f"Error: {exc}")
            print(f"Raw output: {result.final_output}")


if __name__ == "__main__":
    asyncio.run(main())
