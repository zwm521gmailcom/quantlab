import numpy as np
from quantlab.services.offline_rl.fitted_q import FittedQ


def test_greedy_prefers_higher_reward_action():
    # 两动作：状态常数；动作1总给 +1，动作2总给 -1
    transitions = []
    for _ in range(40):
        s = [1.0, 0.0]
        transitions.append({"state": s, "action": 1, "reward": 1.0, "next_state": s, "done": False})
        transitions.append({"state": s, "action": 2, "reward": -1.0, "next_state": s, "done": False})
    model = FittedQ(n_actions=3, gamma=0.5, iterations=5)
    model.fit(transitions)
    assert model.greedy(s, available={1, 2}) == 1
