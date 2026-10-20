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
    # Private talk
    WHISPER = "whisper"
    INVITE = "invite"
    FOLLOW = "follow"
    FIND = "find"  # look for someone who isn't here
    # Society
    ATTACK = "attack"
    HEAL = "heal"
    FOUND_GROUP = "found_group"
    JOIN = "join"
    LEAVE = "leave"
    EXPEL = "expel"
    CALL_VOTE = "call_vote"
    VOTE = "vote"


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
    share: str = ""  # speak / interact: a belief or secret told to the target (or a lie)
    plan_step_done: bool = False
    place: str = ""  # invite: where to meet
    goal: str = ""  # found_group: what the group is for
    options: list[str] = field(default_factory=list)  # call_vote: the choices
    emotion: str = ""  # how the agent feels (optional)
    intent: str = ""  # rpg: persuade / deceive / intimidate (speech)  # the current step of the agent's plan is done

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
            case ActionType.WHISPER:
                return f'whispers to {self.target}: "{self.content}"'
            case ActionType.INVITE:
                words = f': "{self.content}"' if self.content else ""
                return f"invites {self.target} to {self.place or 'somewhere quiet'}{words}"
            case ActionType.FOLLOW:
                return f"follows {self.target}"
            case ActionType.FIND:
                return f"looks for {self.target}"
            case ActionType.ATTACK:
                return f"attacks {self.target}"
            case ActionType.HEAL:
                return f"heals {self.target}" if self.target else "heals themself"
            case ActionType.FOUND_GROUP:
                return f'founds the group "{self.content}"'
            case ActionType.JOIN:
                return f"joins {self.target}"
            case ActionType.LEAVE:
                return f"leaves {self.target}" if self.target else "leaves their group"
            case ActionType.EXPEL:
                return f"expels {self.target}"
            case ActionType.CALL_VOTE:
                return f'calls a vote: "{self.content}"'
            case ActionType.VOTE:
                return f'votes "{self.content}"'
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
