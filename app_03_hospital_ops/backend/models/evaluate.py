"""Evaluate staffing policies against the current HospitalEnv.

Exists because the deployed MADDPG checkpoint was judged only by the clamps
that were bolted on around it in the live service, never by a like-for-like
comparison against the alternatives. The 2026-08-21 audit measured the live
policy emitting a saturated staff *cut* on 77 % of doctor decisions and 68 %
of nurse decisions, all of which the service clamped to zero — so production
was de facto rule-based while reporting itself as MARL.

Policies compared:

``none``
    Take no action. The counterfactual: what the hospital does if the
    optimizer is switched off. Every other policy has to beat this.
``rule``
    The pressure heuristic the live service already falls back to, copied
    from ``_apply_global_marl_sweep`` in app/main.py so the comparison is
    against what actually runs today, not an idealised rule.
``marl``
    A checkpoint's raw output, unclamped. This is what the policy actually
    wants to do.
``marl_clamped``
    The same checkpoint with the live service's non-negative clamp and
    rule-based top-up. This is exactly what production does today.

Usage::

    python -m app_03_hospital_ops.backend.models.evaluate \\
        --checkpoint /models/hospital_ops/final_model.pt \\
        --episodes 20
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List, Optional

import numpy as np

from shared.constants.hospital import STAFF_DEFAULTS

from ..simulation.environment import ACTION_DIM, DEPARTMENTS, HospitalEnv, STATE_DIM
from .marl_agent import MADDPGAgent

POLICIES = ("none", "max", "rule", "marl", "marl_clamped")


def _rule_action(dept) -> tuple:
    """The live service's pressure heuristic (app/main.py, rule fallback)."""
    pressure = float(dept.occupancy_ratio) + float(len(dept.queue)) / max(1, dept.capacity)
    if pressure >= 1.4:
        return 2, 4
    if pressure >= 1.0:
        return 1, 2
    if pressure >= 0.7:
        return 0, 1
    return 0, 0


def _reset_to_baseline(env: HospitalEnv) -> None:
    """Anchor staffing to ERP defaults, as the live sweep does each tick."""
    for name, dept in env.engine.departments.items():
        baseline = STAFF_DEFAULTS.get(name, {"doctors": 2, "nurses": 6})
        dept.staff.doctors = baseline["doctors"]
        dept.staff.nurses = baseline["nurses"]


def _actions_for(
    policy: str,
    env: HospitalEnv,
    obs: Dict[str, np.ndarray],
    agent: Optional[MADDPGAgent],
) -> Dict[str, np.ndarray]:
    actions: Dict[str, np.ndarray] = {}
    raw: Dict[str, np.ndarray] = {}
    if policy in ("marl", "marl_clamped") and agent is not None:
        raw = agent.select_actions(obs, explore=False)

    for name in env.active_departments:
        dept = env.engine.departments.get(name)
        if dept is None:
            actions[name] = np.zeros(ACTION_DIM, dtype=np.float32)
            continue

        if policy == "none":
            docs, nurses, prio, thresh = 0.0, 0.0, 0.0, 0.0
        elif policy == "max":
            # Upper bound: spend the whole action budget every step. Not a
            # deployable policy — it exists to prove the environment is
            # staffing-sensitive at all. If ``max`` cannot beat ``none`` on
            # wait time, no policy trained in this env can, and the problem
            # is the simulator, not the agent.
            docs, nurses, prio, thresh = 3.0, 5.0, 0.0, 0.0
        elif policy == "rule":
            d, n = _rule_action(dept)
            docs, nurses, prio, thresh = float(d), float(n), 0.0, 0.0
        else:
            a = raw.get(name)
            if a is None:
                docs, nurses, prio, thresh = 0.0, 0.0, 0.0, 0.0
            else:
                docs, nurses = float(a[0]), float(a[1])
                prio, thresh = float(a[2]), float(a[3])
                if policy == "marl_clamped":
                    # Live service behaviour: clamp cuts away, then top up
                    # with the rule when the policy proposed nothing.
                    docs, nurses = max(0.0, round(docs)), max(0.0, round(nurses))
                    if docs == 0 and nurses == 0:
                        d, n = _rule_action(dept)
                        docs, nurses = float(d), float(n)

        actions[name] = np.array([docs, nurses, prio, thresh], dtype=np.float32)
    return actions


def evaluate_policy(
    policy: str,
    episodes: int,
    max_steps: int,
    seed: int,
    agent: Optional[MADDPGAgent] = None,
    staff_cost_weight: float = 0.0,
    wait_penalty_cap: float = 5.0,
    queue_penalty_cap: float = 2.5,
) -> Dict[str, Any]:
    """Run ``episodes`` episodes under ``policy`` and summarise the outcome."""
    waits: List[float] = []
    queues: List[float] = []
    occupancies: List[float] = []
    staff_totals: List[float] = []
    rewards: List[float] = []
    raw_docs: List[float] = []
    raw_nurses: List[float] = []

    for ep in range(episodes):
        env = HospitalEnv(mode="multi_agent", max_steps=max_steps, seed=seed + ep,
                          staff_cost_weight=staff_cost_weight,
                          wait_penalty_cap=wait_penalty_cap,
                          queue_penalty_cap=queue_penalty_cap)
        obs, _ = env.reset(seed=seed + ep)
        ep_reward = 0.0

        for _ in range(max_steps):
            # Every policy is anchored to the ERP baseline each step so the
            # comparison measures the action, not accumulated drift.
            _reset_to_baseline(env)
            if policy in ("marl", "marl_clamped") and agent is not None:
                for a in agent.select_actions(obs, explore=False).values():
                    raw_docs.append(float(a[0]))
                    raw_nurses.append(float(a[1]))
            actions = _actions_for(policy, env, obs, agent)
            obs, reward, terminated, truncated, _ = env.step(actions)
            ep_reward += float(np.mean(list(reward.values()))) if isinstance(reward, dict) else float(reward)

            for dept in env.engine.departments.values():
                if dept.total_served > 0:
                    waits.append(float(dept.avg_wait_time))
                queues.append(float(len(dept.queue)))
                occupancies.append(float(dept.occupancy_ratio))
                staff_totals.append(float(dept.staff.total))

            if terminated or truncated:
                break
        rewards.append(ep_reward)

    def _mean(xs):
        return float(np.mean(xs)) if xs else 0.0

    result = {
        "policy": policy,
        "episodes": episodes,
        "mean_wait_hours": _mean(waits),
        "p90_wait_hours": float(np.percentile(waits, 90)) if waits else 0.0,
        "mean_queue_len": _mean(queues),
        "mean_occupancy": _mean(occupancies),
        "mean_staff_total": _mean(staff_totals),
        "mean_episode_reward": _mean(rewards),
    }
    if raw_docs:
        result["raw_action_doctors_mean"] = _mean(raw_docs)
        result["raw_action_nurses_mean"] = _mean(raw_nurses)
        result["raw_negative_doctor_frac"] = float(np.mean([d < 0 for d in raw_docs]))
        result["raw_negative_nurse_frac"] = float(np.mean([n < 0 for n in raw_nurses]))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate hospital staffing policies")
    parser.add_argument("--checkpoint", type=str, default=None,
                        help="MADDPG checkpoint; required for the marl policies")
    parser.add_argument("--episodes", type=int, default=10)
    parser.add_argument("--max-steps", type=int, default=168)
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--policies", type=str, default=",".join(POLICIES))
    parser.add_argument("--json-out", type=str, default=None)
    parser.add_argument("--wait-penalty-cap", type=float, default=5.0)
    parser.add_argument("--queue-penalty-cap", type=float, default=2.5)
    parser.add_argument(
        "--staff-cost-weight", type=float, default=0.0,
        help=("Match the weight a checkpoint was trained with so its reward "
              "column is comparable. Wait/queue/staff columns are unaffected."),
    )
    args = parser.parse_args()

    agent = None
    wanted = [p.strip() for p in args.policies.split(",") if p.strip()]
    if any(p.startswith("marl") for p in wanted):
        if not args.checkpoint:
            raise SystemExit("--checkpoint is required for the marl policies")
        agent = MADDPGAgent(
            department_names=list(DEPARTMENTS),
            obs_dim=STATE_DIM,
            action_dim=ACTION_DIM,
            device="cpu",
        )
        agent.load_checkpoint(args.checkpoint)

    results = []
    for policy in wanted:
        res = evaluate_policy(policy, args.episodes, args.max_steps, args.seed, agent,
                              staff_cost_weight=args.staff_cost_weight,
                              wait_penalty_cap=args.wait_penalty_cap,
                              queue_penalty_cap=args.queue_penalty_cap)
        results.append(res)
        print(json.dumps(res), flush=True)

    header = f"{'policy':<14}{'wait_h':>9}{'p90_wait':>10}{'queue':>8}{'occ':>7}{'staff':>8}{'reward':>10}"
    print("\n" + header)
    print("-" * len(header))
    for r in results:
        print(f"{r['policy']:<14}{r['mean_wait_hours']:>9.3f}{r['p90_wait_hours']:>10.3f}"
              f"{r['mean_queue_len']:>8.2f}{r['mean_occupancy']:>7.3f}"
              f"{r['mean_staff_total']:>8.2f}{r['mean_episode_reward']:>10.2f}")

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(results, f, indent=2)


if __name__ == "__main__":
    main()
