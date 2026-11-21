"""Truncated Wald SPRT between Bern(tau0) (dependent) and Bern(tau1) (reusable), design §3.

n_max = 24 meets alpha and beta at tau0 / tau1 (phase2b design §5); Phase 2 runs used 8 (configs/phase2.yaml).
"""

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SPRTParams:
    tau0: float = 0.05
    tau1: float = 0.30
    alpha: float = 0.05  # P(decide reuse | dependent)
    beta: float = 0.20  # P(decide invalidate | reusable)
    n_max: int = 24  # truncation
    on_trunc: str = "invalidate"  # decision for an undecided test (the conservative side)

    @property
    def up(self) -> float:
        return math.log((1 - self.beta) / self.alpha)

    @property
    def low(self) -> float:
        return math.log(self.beta / (1 - self.alpha))

    @property
    def hit(self) -> float:
        return math.log(self.tau1 / self.tau0)

    @property
    def miss(self) -> float:
        return math.log((1 - self.tau1) / (1 - self.tau0))


@dataclass
class SPRT:
    p: SPRTParams = field(default_factory=SPRTParams)
    llr: float = 0.0
    n: int = 0
    matches: int = 0
    decision: str | None = None  # "reuse" | "invalidate" | None while open
    truncated: bool = False

    def update(self, match: bool) -> str | None:
        if self.decision is not None:
            return self.decision
        self.n += 1
        self.matches += int(match)
        self.llr += self.p.hit if match else self.p.miss
        if self.llr >= self.p.up - 1e-12:
            self.decision = "reuse"
        elif self.llr <= self.p.low + 1e-12:
            self.decision = "invalidate"
        elif self.n >= self.p.n_max:
            self.decision = self.p.on_trunc
            self.truncated = True
        return self.decision

    @property
    def open(self) -> bool:
        return self.decision is None


def wald_lower_bound(p: SPRTParams) -> tuple[float, float]:
    """Wald's bound on E[n] under H0 (dependent, p=tau0) and H1 (reusable, p=tau1), design Prop. 2."""
    a, b = p.alpha, p.beta

    def kl(x: float, y: float) -> float:
        return x * math.log(x / y) + (1 - x) * math.log((1 - x) / (1 - y))

    h0 = ((1 - a) * math.log((1 - a) / b) + a * math.log(a / (1 - b))) / kl(p.tau0, p.tau1)
    h1 = ((1 - b) * math.log((1 - b) / a) + b * math.log(b / (1 - a))) / kl(p.tau1, p.tau0)
    return h0, h1


def operating_characteristic(p: SPRTParams, q: float) -> dict:
    """Exact P(reuse), P(invalidate), P(truncated) and E[n] of the truncated test when matches are Bern(q)."""
    states = {0: 1.0}  # number of matches -> probability, among open paths
    reuse = inval = en = 0.0
    for n in range(1, p.n_max + 1):
        nxt: dict[int, float] = {}
        for k, pr in states.items():
            for hit, w in ((1, q), (0, 1 - q)):
                kk, mass = k + hit, pr * w
                if mass == 0:
                    continue
                llr = kk * p.hit + (n - kk) * p.miss
                if llr >= p.up - 1e-12:
                    reuse += mass
                    en += mass * n
                elif llr <= p.low + 1e-12:
                    inval += mass
                    en += mass * n
                else:
                    nxt[kk] = nxt.get(kk, 0.0) + mass
        states = nxt
    trunc = sum(states.values())
    if p.on_trunc == "reuse":
        reuse += trunc
    else:
        inval += trunc
    return {"reuse": reuse, "invalidate": inval, "truncated": trunc, "E_n": en + trunc * p.n_max}
