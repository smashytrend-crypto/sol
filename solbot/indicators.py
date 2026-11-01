"""Lekkie, przyrostowe wskaźniki techniczne (bez numpy)."""
from __future__ import annotations

import math
from collections import deque


class RollingStats:
    """Średnia i odchylenie standardowe w oknie N."""

    def __init__(self, n: int):
        self.n = n
        self.q: deque[float] = deque()
        self.s = 0.0
        self.s2 = 0.0

    def push(self, x: float):
        self.q.append(x)
        self.s += x
        self.s2 += x * x
        if len(self.q) > self.n:
            y = self.q.popleft()
            self.s -= y
            self.s2 -= y * y

    @property
    def ready(self) -> bool:
        return len(self.q) >= self.n

    @property
    def mean(self) -> float:
        return self.s / len(self.q) if self.q else 0.0

    @property
    def std(self) -> float:
        k = len(self.q)
        if k < 2:
            return 0.0
        var = max(self.s2 / k - (self.s / k) ** 2, 0.0)
        return math.sqrt(var)


class EMA:
    def __init__(self, n: int):
        self.a = 2 / (n + 1)
        self.v: float | None = None

    def push(self, x: float) -> float:
        self.v = x if self.v is None else self.v + self.a * (x - self.v)
        return self.v


class RSI:
    """RSI Wildera."""

    def __init__(self, n: int = 14):
        self.n = n
        self.prev: float | None = None
        self.gain = 0.0
        self.loss = 0.0
        self.count = 0

    def push(self, x: float) -> float | None:
        if self.prev is None:
            self.prev = x
            return None
        ch = x - self.prev
        self.prev = x
        g, l = max(ch, 0.0), max(-ch, 0.0)
        self.count += 1
        if self.count <= self.n:
            self.gain += g / self.n
            self.loss += l / self.n
            if self.count < self.n:
                return None
        else:
            self.gain = (self.gain * (self.n - 1) + g) / self.n
            self.loss = (self.loss * (self.n - 1) + l) / self.n
        if self.loss == 0:
            return 100.0
        rs = self.gain / self.loss
        return 100 - 100 / (1 + rs)


class Window:
    """Ostatnie N wartości (min/max/pierwsza)."""

    def __init__(self, n: int):
        self.q: deque[float] = deque(maxlen=n)

    def push(self, x: float):
        self.q.append(x)

    @property
    def ready(self) -> bool:
        return len(self.q) == self.q.maxlen

    def max(self) -> float:
        return max(self.q)

    def min(self) -> float:
        return min(self.q)

    def first(self) -> float:
        return self.q[0]
