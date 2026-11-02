"""Load and validate interaction state files (format version 1)."""
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Optional

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SIZE_PATTERN = re.compile(r"^\d+x\d+$")
ACTIONS = ("click", "hover", "focus", "press", "waitFor", "wait")
SELECTOR_ACTIONS = ("click", "hover", "focus", "waitFor")
MAX_WAIT_MS = 10000


class StateFileError(ValueError):
    """The state file is unreadable or breaks the format rules."""


@dataclass(frozen=True)
class Step:
    action: str
    selector: Optional[str] = None
    index: int = 0
    key: Optional[str] = None
    ms: Optional[int] = None


@dataclass(frozen=True)
class State:
    id: str
    when: Optional[str]
    steps: tuple[Step, ...]
    sizes: Optional[tuple[str, ...]]


def _parse_step(raw, label: str) -> Step:
    if not isinstance(raw, dict):
        raise StateFileError(f"{label}: step must be an object")
    action = raw.get("action")
    if action not in ACTIONS:
        raise StateFileError(f"{label}: unknown action: {action}")
    selector = raw.get("selector")
    if action in SELECTOR_ACTIONS and not (isinstance(selector, str) and selector):
        raise StateFileError(f"{label}: {action} needs selector")
    key = raw.get("key")
    if action == "press" and not (isinstance(key, str) and key):
        raise StateFileError(f"{label}: press needs key")
    ms = raw.get("ms")
    if action == "wait":
        # bool is an int subclass in Python, so reject it explicitly
        if isinstance(ms, bool) or not isinstance(ms, int) or not 1 <= ms <= MAX_WAIT_MS:
            raise StateFileError(f"{label}: wait needs ms between 1 and {MAX_WAIT_MS}")
    index = raw.get("index", 0)
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise StateFileError(f"{label}: index must be a non-negative integer")
    return Step(
        action=action,
        selector=selector if action in SELECTOR_ACTIONS else None,
        index=index,
        key=key if action == "press" else None,
        ms=ms if action == "wait" else None,
    )


def _parse_state(raw, position: int, seen: set) -> State:
    if not isinstance(raw, dict):
        raise StateFileError(f"state {position}: must be an object")
    state_id = raw.get("id")
    if not isinstance(state_id, str) or not ID_PATTERN.match(state_id):
        raise StateFileError(f"state {position}: invalid id: {state_id}")
    if state_id in seen:
        raise StateFileError(f"state {position}: duplicate state id: {state_id}")
    seen.add(state_id)
    label = f"state {position} ({state_id})"

    when = raw.get("when")
    if when is not None and not (isinstance(when, str) and when):
        raise StateFileError(f"{label}: when must be a non-empty string")

    raw_steps = raw.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise StateFileError(f"{label}: steps must be a non-empty list")
    steps = tuple(_parse_step(step, f"{label} step {i}") for i, step in enumerate(raw_steps))

    sizes = raw.get("sizes")
    if sizes is not None:
        if not isinstance(sizes, list):
            raise StateFileError(f"{label}: sizes must be a list")
        for size in sizes:
            if not isinstance(size, str) or not SIZE_PATTERN.match(size):
                raise StateFileError(f"{label}: invalid size: {size}")
        sizes = tuple(sizes)
    return State(id=state_id, when=when, steps=steps, sizes=sizes)


def load_states(path: str) -> list[State]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise StateFileError(f"cannot read state file {path}: {error}") from error
    if not isinstance(data, dict) or data.get("version") != 1:
        raise StateFileError("version must be 1")
    raw_states = data.get("states")
    if not isinstance(raw_states, list):
        raise StateFileError("states must be a list")
    seen: set = set()
    return [_parse_state(raw, i, seen) for i, raw in enumerate(raw_states)]


def states_to_json(states: list[State]) -> list[dict]:
    """Return the file shape; the Node helper reads it with the same parser rules."""
    result = []
    for state in states:
        steps = []
        for step in state.steps:
            item = {k: v for k, v in vars(step).items() if v is not None}
            steps.append(item)
        result.append({
            "id": state.id,
            "when": state.when,
            "steps": steps,
            "sizes": list(state.sizes) if state.sizes is not None else None,
        })
    return result


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        digest.update(handle.read())
    return digest.hexdigest()
