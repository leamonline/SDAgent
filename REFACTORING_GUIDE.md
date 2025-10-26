# OpenAI AgentSDK Refactoring Guide

## Overview

This guide documents the refactoring of `smarter_dog.py` to align with OpenAI AgentSDK best practices.

---

## Key Changes

### 1. **Pydantic Schemas for Type Safety** ✨

**Before:**
```python
@function_tool
def get_available_slots(requested_date: str, dog_size: Literal["small", "medium", "large"]) -> dict:
    # Implicit parameter validation only through type hints
    ...
```

**After:**
```python
class SlotAvailabilityRequest(BaseModel):
    """Request parameters for checking slot availability."""
    requested_date: str = Field(..., description="Date in ISO format (YYYY-MM-DD)")
    dog_size: Literal["small", "medium", "large"] = Field(..., description="Size of the dog")

class SlotAvailabilityResponse(BaseModel):
    """Response containing available time slots."""
    requested_date: str
    operating_date: str
    available_slots: list[str]
    notes: list[str] = Field(default_factory=list)
```

**Benefits:**
- Runtime validation with Pydantic
- Auto-generated JSON schemas for the LLM
- Self-documenting code with Field descriptions
- Type-safe responses

---

### 2. **Comprehensive Tool Docstrings** 📝

**Before:**
```python
@function_tool
def get_available_slots(requested_date: str, dog_size: Literal[...]) -> dict:
    # No docstring - LLM has no description of what this tool does
    operating_day, notes, is_open = _resolve_operating_day(requested_date)
    ...
```

**After:**
```python
@function_tool
def get_available_slots(requested_date: str, dog_size: Literal[...]) -> dict:
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
```

**Benefits:**
- LLM understands **when** and **how** to use the tool
- Better tool selection by the agent
- Improved developer documentation

---

### 3. **Agent Handoffs for Multi-Agent Orchestration** 🔄

**Before:**
```python
async def main() -> None:
    grooming_agent = Agent(name="Smarter Dog", ...)

    # Manual orchestration in main()
    result = await Runner.run(grooming_agent, request)
    booking = json.loads(result.final_output)

    # Manually call second agent
    await log_booking_to_sheet(booking)
```

**After:**
```python
def create_grooming_agent(sheet_logger: Agent) -> Agent:
    return Agent(
        name="Smarter Dog Grooming",
        instructions=(
            "Workflow:\n"
            "1. Check availability\n"
            "2. Book appointment\n"
            "3. Hand off to Sheet Logger agent to persist the booking\n"
        ),
        tools=[get_available_slots, book_grooming_appointment],
        handoffs=[sheet_logger],  # SDK handles delegation
        output_type=BookingResponse,
    )
```

**Benefits:**
- SDK manages agent transitions automatically
- Cleaner separation of concerns
- Each agent has clear `handoff_description`
- More scalable for complex workflows

---

### 4. **Typed Output Extraction** 🎯

**Before:**
```python
result = await Runner.run(grooming_agent, request)
try:
    booking = json.loads(result.final_output)  # Manual JSON parsing
except json.JSONDecodeError as exc:
    raise RuntimeError("Booking agent must return JSON") from exc
```

**After:**
```python
grooming_agent = Agent(
    name="Smarter Dog Grooming",
    output_type=BookingResponse,  # Pydantic model
    ...
)

result = await Runner.run(grooming_agent, request)
booking = result.final_output_as(BookingResponse)  # Type-safe extraction
```

**Benefits:**
- No manual JSON parsing
- Type-safe access to fields
- Automatic validation
- Better IDE autocomplete

---

### 5. **Structured Error Handling** ⚠️

**Before:**
```python
@function_tool
def book_grooming_appointment(...) -> dict:
    if not is_open:
        raise ValueError(f"Salon closed on {operating_day.isoformat()}")
    if requested_time not in SLOT_TIMES:
        raise ValueError("Requested time is outside operating hours.")
```

**After:**
```python
class BookingResponse(BaseModel):
    status: Literal["Booked", "Failed"]
    notes: list[str] = Field(default_factory=list)

@function_tool
def book_grooming_appointment(...) -> dict:
    """Book a grooming appointment for a dog at a specific date and time.

    ...

    Raises:
        ValueError: If the salon is closed, time is invalid, or slot is full
    """
    # Errors are documented and predictable
    if not is_open:
        raise ValueError(f"Salon closed on {operating_day.isoformat()}")
```

**Benefits:**
- Documented error conditions
- Structured status fields in responses
- Agent can handle errors gracefully

---

### 6. **Agent Factory Functions** 🏭

**Before:**
```python
async def main():
    grooming_agent = Agent(
        name="Smarter Dog",
        instructions="...",
        tools=[...],
    )

    sheet_agent = Agent(
        name="Sheet Logger",
        instructions="...",
        tools=[...],
    )
```

**After:**
```python
def create_sheet_logger_agent() -> Agent:
    """Create an agent responsible for logging bookings to Google Sheets."""
    return Agent(
        name="Sheet Logger",
        handoff_description="Logs confirmed bookings to the Google Sheets spreadsheet",
        instructions=(...),
        tools=[_google_drive_connector()],
        output_type=SheetLogResponse,
    )

def create_grooming_agent(sheet_logger: Agent) -> Agent:
    """Create the main grooming booking agent with handoff to sheet logger."""
    return Agent(
        name="Smarter Dog Grooming",
        instructions=(...),
        tools=[get_available_slots, book_grooming_appointment],
        handoffs=[sheet_logger],
        output_type=BookingResponse,
    )
```

**Benefits:**
- Testable agent creation
- Clear dependencies between agents
- Reusable agent configurations
- Better documentation

---

## Migration Checklist

When refactoring to OpenAI AgentSDK best practices:

- [ ] Define Pydantic `BaseModel` classes for:
  - [ ] Tool input parameters
  - [ ] Tool output responses
  - [ ] Agent output types

- [ ] Add comprehensive docstrings to all `@function_tool` functions:
  - [ ] Summary of what the tool does
  - [ ] Args section describing each parameter
  - [ ] Returns section describing the output structure
  - [ ] Raises section for error conditions

- [ ] Configure agents with:
  - [ ] `output_type` parameter for type-safe extraction
  - [ ] `handoff_description` for agents that receive handoffs
  - [ ] `handoffs` list for multi-agent workflows

- [ ] Replace manual orchestration with agent handoffs

- [ ] Use `result.final_output_as(ModelClass)` instead of `json.loads()`

- [ ] Extract agent creation into factory functions

---

## OpenAI AgentSDK Patterns Reference

### Pattern 1: Handoffs (Agent Delegation)
```python
specialist_agent = Agent(
    name="Specialist",
    handoff_description="What this agent specializes in",
    ...
)

main_agent = Agent(
    name="Main",
    handoffs=[specialist_agent],
    instructions="When you need X, hand off to Specialist",
    ...
)
```

### Pattern 2: Agents as Tools (Manager Pattern)
```python
specialist = Agent(...)

manager = Agent(
    tools=[
        specialist.as_tool(
            tool_name="consult_specialist",
            tool_description="When to use the specialist",
        )
    ],
    ...
)
```

### Pattern 3: Output Type Safety
```python
class MyOutput(BaseModel):
    result: str
    confidence: float

agent = Agent(
    output_type=MyOutput,
    ...
)

result = await Runner.run(agent, "...")
typed_output = result.final_output_as(MyOutput)
print(typed_output.confidence)  # Type-safe access
```

---

## Testing the Refactored Code

```bash
# Set required environment variables
export GOOGLE_DRIVE_CONNECTOR_ID="your-connector-id"
export GOOGLE_DRIVE_AUTHORIZATION='{"token": "..."}'

# Run the refactored version
python smarter_dog_refactored.py
```

Expected output:
```
Starting booking request...

Booking confirmed for Luna:
  Date: 2024-07-17
  Time: 10:30
  Customer: Sarah Chen
  Status: Booked
```

---

## Further Reading

- [OpenAI Agents JS Documentation](https://github.com/openai/openai-agents-js)
- [OpenAI Agents Python Documentation](https://github.com/openai/openai-agents-python)
- [Pydantic Documentation](https://docs.pydantic.dev/)
- [Agent Handoffs Guide](https://github.com/openai/openai-agents-python/blob/main/docs/handoffs.md)
- [Function Tools Guide](https://github.com/openai/openai-agents-python/blob/main/docs/tools.md)
