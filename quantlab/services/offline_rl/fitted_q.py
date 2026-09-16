"""Fitted Q-iteration for discrete offline RL."""

from __future__ import annotations

import numpy as np
from sklearn.linear_model import Ridge


class FittedQ:
    def __init__(self, *, n_actions: int, gamma: float = 0.99, iterations: int = 10) -> None:
        self.n_actions = n_actions
        self.gamma = gamma
        self.iterations = iterations
        self._regressors = [Ridge() for _ in range(n_actions)]
        self._fitted = [False] * n_actions

    def fit(self, transitions: list[dict]) -> None:
        for _ in range(self.iterations):
            for action in range(self.n_actions):
                action_transitions = [t for t in transitions if t["action"] == action]
                if not action_transitions:
                    continue

                states = np.asarray([t["state"] for t in action_transitions], dtype=float)
                targets = []
                for transition in action_transitions:
                    reward = float(transition["reward"])
                    if transition["done"]:
                        targets.append(reward)
                    else:
                        next_q = self.predict_q(transition["next_state"])
                        targets.append(reward + self.gamma * max(next_q))
                self._regressors[action].fit(states, np.asarray(targets, dtype=float))
                self._fitted[action] = True

    def predict_q(self, state: list[float]) -> list[float]:
        features = np.asarray(state, dtype=float).reshape(1, -1)
        values: list[float] = []
        for action, regressor in enumerate(self._regressors):
            if self._fitted[action]:
                values.append(float(regressor.predict(features)[0]))
            else:
                values.append(0.0)
        return values

    def greedy(self, state: list[float], *, available: set[int] | None = None) -> int:
        q_values = self.predict_q(state)
        candidates = available if available is not None else set(range(self.n_actions))
        return max(candidates, key=lambda action: q_values[action])
