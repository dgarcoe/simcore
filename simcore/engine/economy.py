"""Markets (`environment.locations[].market`): agents buy and sell items for a currency with the
"buy" and "sell" actions. Prices move with supply and demand: each unit bought makes the item
dearer at that market and each unit sold cheaper, and prices drift back to their base every tick.
A market may keep a limited stock, open only at some hours, and events change prices
(`prices: {place: {item: factor}}`) or open and close markets (`markets: {place: {open: false}}`).
Mixed into SimulationEngine.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from simcore.agents.actions import Action, ActionType, fmt_amount
from simcore.engine.events import EventType, SimEvent

if TYPE_CHECKING:
    from simcore.agents.agent import Agent
    from simcore.config.schema import MarketConfig

MAX_UNITS = 5  # bought or sold in one go


class EconomyMixin:
    def _init_economy(self) -> None:
        self._markets: dict[str, MarketConfig] = {
            loc.name: loc.market for loc in self.config.environment.locations if loc.market
        }
        self._factor: dict[str, dict[str, float]] = {p: {} for p in self._markets}
        self._market_stock: dict[str, dict[str, int]] = {
            p: dict(m.stock) for p, m in self._markets.items()
        }
        self._market_open: dict[str, bool] = {}  # set by events; else the market's hours
        self._earned_selling: dict[str, float] = defaultdict(float)

    @property
    def currency(self) -> str:
        return self.config.economy.currency

    def _currency_of(self, place: str) -> str:
        m = self._markets.get(place)
        return (m.currency if m and m.currency else "") or self.currency

    def _hour(self) -> int:
        return (self.clock.current_tick * self.clock.tick_duration_minutes // 60) % 24

    def _is_open(self, place: str) -> bool:
        if place not in self._markets:
            return False
        if place in self._market_open:
            return self._market_open[place]
        hours = self._markets[place].hours
        if not hours:
            return True
        start, end = hours
        h = self._hour()
        return start <= h < end if start <= end else (h >= start or h < end)

    def _price(self, place: str, item: str, side: str) -> int:
        """What an agent pays ("buy") or gets ("sell") for one unit now."""
        m = self._markets[place]
        base = (m.sells if side == "buy" else m.buys).get(item)
        if base is None:
            return 0
        return max(1, round(base * self._factor[place].get(item, 1.0)))

    def _trend(self, place: str, item: str) -> str:
        f = self._factor[place].get(item, 1.0)
        return " (pricey now)" if f > 1.15 else " (cheap now)" if f < 0.87 else ""

    async def _market_action(self, agent: Agent, action: Action, tick: int) -> Action:
        place = agent.location
        market = self._markets.get(place)
        buying = action.type == ActionType.BUY
        if market is None:
            here = ", ".join(self._markets) or "none in this story"
            why = f"there is no market here (markets: {here})"
            return await self._refuse(agent, action, tick, why)
        if not self._is_open(place):
            return await self._refuse(agent, action, tick, f"the market at {place} is closed now")
        listing = market.sells if buying else market.buys
        item = self._resolve_item(action.item or "", {i: 1 for i in listing},
                                  (action.content or "", action.target or ""))
        if item not in listing:
            what = ", ".join(listing) or "nothing"
            verb = "sells" if buying else "buys"
            why = f"{place} doesn't {verb} {action.item or 'that'} ({verb}: {what})"
            return await self._refuse(agent, action, tick, why)
        money = self._currency_of(place)
        wanted = max(1, min(MAX_UNITS, int(action.amount or 1)))
        stock = self._market_stock[place]
        e = market.elasticity
        done, total = 0, 0
        inv = agent.state.resources
        for _ in range(wanted):
            price = self._price(place, item, "buy" if buying else "sell")
            if buying:
                if inv.get(money, 0) + 1e-9 < price or (item in stock and stock[item] <= 0):
                    break
                inv[money] = inv.get(money, 0) - price
                inv[item] = inv.get(item, 0) + 1
                if item in stock:
                    stock[item] -= 1
                self._factor[place][item] = self._factor[place].get(item, 1.0) * (1 + e)
            else:
                if inv.get(item, 0) + 1e-9 < 1:
                    break
                inv[item] -= 1
                inv[money] = inv.get(money, 0) + price
                if item in stock:
                    stock[item] += 1
                self._factor[place][item] = self._factor[place].get(item, 1.0) * (1 - e)
                self._earned_selling[agent.name] += price
            done += 1
            total += price
        for k in (money, item):
            if inv.get(k, 0) <= 1e-9:
                inv.pop(k, None)
        if not done:
            why = (f"not enough {money} ({fmt_amount(self._price(place, item, 'buy'))} each)"
                   if buying and not (item in stock and stock[item] <= 0) else
                   f"{item} is sold out here" if buying else f"I have no {item}")
            return await self._refuse(agent, action, tick, why)
        kind = "buy" if buying else "sell"
        market_id = f"market:{place}"
        await self._transfer(tick, kind, item, done, *((market_id, agent.name) if buying
                                                        else (agent.name, market_id)), place)
        await self._transfer(tick, kind, money, total, *((agent.name, market_id) if buying
                                                         else (market_id, agent.name)), place)
        await self.event_bus.emit(SimEvent(
            type=EventType.MARKET, tick=tick,
            data={"agent": agent.name, "place": place, "kind": kind, "item": item,
                  "amount": done, "price": total, "currency": money},
            source=agent.name,
        ))
        agent.observe(
            f"I {'bought' if buying else 'sold'} {done} {item} for {total} {money} at {place}.",
            tick, importance=0.5,
        )
        action.item = item
        action.amount = done
        action.parameters = {**(action.parameters or {}), "price": total, "currency": money}
        return action

    async def _economy_tick(self, tick: int) -> None:
        for place, market in self._markets.items():
            r = market.recovery
            for item, f in list(self._factor[place].items()):
                f += (1.0 - f) * r
                self._factor[place][item] = 1.0 if abs(f - 1.0) < 0.01 else f

    def _apply_market_effects(self, prices: dict[str, dict[str, float]],
                              markets: dict[str, bool]) -> None:
        for place, items in prices.items():
            if place in self._factor:
                for item, factor in items.items():
                    self._factor[place][item] = self._factor[place].get(item, 1.0) * factor
        for place, is_open in markets.items():
            if place in self._markets:
                self._market_open[place] = is_open

    def _economy_prompt(self, agent: Agent) -> str:
        if not self._markets:
            return ""
        lines = []
        place = agent.location
        if place in self._markets:
            money = self._currency_of(place)
            have = fmt_amount(agent.state.resources.get(money, 0))
            if not self._is_open(place):
                lines.append(f"The market here is closed now (you have {have} {money}).")
            else:
                stock = self._market_stock[place]
                sells = "; ".join(
                    f"{i} {self._price(place, i, 'buy')}{self._trend(place, i)}"
                    + (" (sold out)" if i in stock and stock[i] <= 0 else
                       f" ({stock[i]} left)" if i in stock else "")
                    for i in self._markets[place].sells)
                buys = "; ".join(f"{i} {self._price(place, i, 'sell')}{self._trend(place, i)}"
                                 for i in self._markets[place].buys)
                lines.append(
                    f"Market here (prices in {money}; you have {have}): "
                    + (f"sells {sells}. " if sells else "")
                    + (f"Buys from you: {buys}. " if buys else "")
                    + 'To trade, use "action": "buy" or "sell" with "item" and "amount" '
                    f"(up to {MAX_UNITS}). Prices go up as people buy and down as they sell."
                )
        others = [p for p in self._markets if p != place]
        if others:
            lines.append("Other markets: " + "; ".join(
                f"{p}{'' if self._is_open(p) else ' (closed now)'}: sells "
                f"{', '.join(self._markets[p].sells) or 'nothing'}, buys "
                f"{', '.join(self._markets[p].buys) or 'nothing'}" for p in others[:6]) + ".")
        return "\n".join(lines)

    def markets_state(self) -> dict:
        out = {}
        for place, market in self._markets.items():
            stock = self._market_stock[place]
            out[place] = {
                "open": self._is_open(place),
                "currency": self._currency_of(place),
                "sells": {i: {"price": self._price(place, i, "buy"),
                              **({"stock": stock[i]} if i in stock else {})}
                          for i in market.sells},
                "buys": {i: self._price(place, i, "sell") for i in market.buys},
            }
        return out

    def _wealth(self, agent: Agent) -> float:
        return agent.state.resources.get(self.currency, 0)
