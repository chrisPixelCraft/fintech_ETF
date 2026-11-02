"""K2.1 exit candidates on the production 22-name K2 (frozen; see PREREGISTRATION.md).

K21Exit (primary): a core name whose 10-session trust net buy is <= 0 is replaced before the next re-screen.
K2Stop (secondary): a core name down >= 10% (action-neutral) since it entered the core is replaced.
Both: core names only, last-3 freeze, replacement must have positive trust ratio, weights unchanged.
"""
from __future__ import annotations

from pathlib import Path
import importlib.util

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('strategies_k3w', HERE.parent / 'k3_weights/strategies.py')
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)
v4 = base.k3.v4
CORE_N, CORE_W, FILL_N, FILL_W = 10, .085, 12, .10 / 12
STOP = -.10


class _Exit(base.K2Buffer):
    def __init__(self, calendar, rules, symbols):
        super().__init__(calendar, rules, symbols)
        self.blocked, self.pending = set(), False

    def triggered(self, view, state, core, ratio):
        raise NotImplementedError

    def replacement_pool(self, view, names, ratio):
        yoy, high = self.rev['yoy'].loc[view.date], self.rev['high3'].loc[view.date]
        ok = v4.portfolio.ranked(ratio[[s for s in names if ratio.get(s, float('nan')) > 0
                                        and high.get(s) == 1 and yoy.get(s, float('nan')) > .20]])
        large = v4.v3.large_order(view)
        return ok + [s for s in large if s not in ok and ratio.get(s, float('nan')) > 0], large

    def rebuilt(self, view, core, exits, ratio, names):
        pool, large = self.replacement_pool(view, names, ratio)
        pool = [s for s in pool if s not in core and s not in self.blocked]
        survivors = [s for s in core if s not in exits]
        new = pool[:len(exits)]
        # Too few replacements: keep the triggered names with the best ratio, so the book never shrinks.
        kept = sorted(exits, key=lambda s: -ratio.get(s, float('-inf')))[:len(exits) - len(new)]
        new_core = survivors + kept + new
        replaced = [s for s in exits if s not in kept]
        fillers = [s for s, w in self.current.items() if w < .05 and s not in new_core]
        for s in pool + large:
            if len(fillers) >= FILL_N:
                break
            if s not in new_core and s not in fillers and s not in self.blocked:
                fillers.append(s)
        assert len(new_core) == CORE_N and len(fillers) == FILL_N and not set(new_core) & set(fillers)
        target = v4.v2._targets({**{s: CORE_W for s in new_core}, **{s: FILL_W for s in fillers}}, self.rules)
        return target, replaced, new

    def decide(self, view, state):
        before = self.current
        result = super().decide(view, state)
        held = dict(state.weights)
        if before is None or state.sessions_remaining <= 3:
            return result
        if self.current is not before:  # accepted re-screen: exit blocks reset
            self.blocked, self.pending = set(), False
            return result
        if result != held:  # breach repair
            return result
        if self.pending and set(held) != set(self.current):
            self.log.append(dict(date=str(state.date.date()), action='exit_retry'))
            return self.current.copy()
        self.pending = False
        names = [s for s in view.symbols if view.valid[s].iloc[-20:].all()]
        ratio = v4.v3._flow_ratio(self.trust, view, names, 10)
        core = [s for s, w in self.current.items() if w >= .05]
        exits = [s for s in self.triggered(view, state, core, ratio) if s in held]
        if not exits:
            return result
        target, replaced, new = self.rebuilt(view, core, exits, ratio, names)
        if not replaced:
            return result
        self.blocked |= set(replaced)
        self.current, self.pending = target, True
        self.log.append(dict(date=str(state.date.date()), action='exit', sold=replaced, bought=new))
        return target.copy()


class K21Exit(_Exit):
    variant = 'K2.1'

    def triggered(self, view, state, core, ratio):
        return [s for s in core if s in ratio.index and ratio[s] <= 0]


class K2Stop(_Exit):
    variant = 'K2.S'

    def __init__(self, calendar, rules, symbols):
        super().__init__(calendar, rules, symbols)
        self.entry = {}

    def triggered(self, view, state, core, ratio):
        self.entry = {s: self.entry.get(s, view.date) for s in core}
        out = []
        for s in core:
            since = view.ret[s].loc[self.entry[s]:].iloc[1:]
            if len(since) and (1 + since.fillna(0.)).prod() - 1 <= STOP:
                out.append(s)
        return out
