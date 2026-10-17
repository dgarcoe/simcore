"""Action types that agents can perform."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ActionType(Enum):
    MOVE = "move"
    SPEAK = "speak"
    TRADE = "trade"
    INTERACT = "interact"
    WAIT = "wait"
    OBSERVE = "observe"
    WORK = "work"
    REST = "rest"
    POST = "post"
    CUSTOM = "custom"
    # Items
    GIVE = "give"
    TAKE = "take"
    DROP = "drop"
    USE = "use"
    ACCEPT = "accept"
    DECLINE = "decline"


ITEM_ACTIONS = frozenset({
    ActionType.GIVE, ActionType.TAKE, ActionType.DROP, ActionType.USE,
    ActionType.TRADE, ActionType.ACCEPT, ActionType.DECLINE,
})


def fmt_amount(amount: float) -> str:
    return str(int(amount)) if float(amount).is_integer() else f"{amount:g}"


@dataclass
class Action:
    """An action decided by an agent."""

    type: ActionType
    target: str = ""  # target agent name, location, or item
    content: str = ""  # speech content, trade details, etc.
    parameters: dict[str, object] = field(default_factory=dict)
    reasoning: str = ""  # why the agent chose this action
    item: str = ""  # give / take / drop / use / trade: what
    amount: float = 1
    ask_item: str = ""  # trade: what is asked in return
    ask_amount: float = 1

    def describe(self) -> str:
        """Human-readable description of the action."""
        match self.type:
            case ActionType.MOVE:
                return f"moves to {self.target}"
            case ActionType.SPEAK:
                return f'says to {self.target}: "{self.content}"'
            case ActionType.TRADE:
                if self.item:
                    offer = f"{fmt_amount(self.amount)} {self.item}"
                    ask = f" for {fmt_amount(self.ask_amount)} {self.ask_item}" if self.ask_item else ""
                    return f"offers {self.target} {offer}{ask}"
                return f"trades with {self.target}: {self.content}"
            case ActionType.GIVE:
                return f"gives {self.target} {fmt_amount(self.amount)} {self.item}"
            case ActionType.TAKE:
                return f"takes {fmt_amount(self.amount)} {self.item}"
            case ActionType.DROP:
                return f"leaves {fmt_amount(self.amount)} {self.item} here"
            case ActionType.USE:
                return f"uses {self.item}" + (f" on {self.target}" if self.target else "")
            case ActionType.ACCEPT:
                return f"accepts the offer from {self.target}"
            case ActionType.DECLINE:
                return f"declines the offer from {self.target}"
            case ActionType.INTERACT:
                return f"interacts with {self.target}: {self.content}"
            case ActionType.WORK:
                return f"works: {self.content}"
            case ActionType.REST:
                return "rests"
            case ActionType.OBSERVE:
                return f"observes {self.target or 'surroundings'}"
            case ActionType.WAIT:
                return "waits"
            case ActionType.POST:
                return f"posts online: {self.content}"
            case ActionType.CUSTOM:
                return f"{self.content}"
