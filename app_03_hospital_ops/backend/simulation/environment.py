"""DES-MARL Gymnasium environment for hospital operations optimization.

Wraps the DES engine into a gymnasium-compatible environment where each
department is controlled by a MARL agent that observes local state and
takes staffing/priority actions.

State space per department (12-dim):
    [patient_count, capacity_ratio, avg_wait_time, avg_los,
     admission_rate_1h, admission_rate_4h, staffing_ratio,
     pending_transfers_in, pending_transfers_out, acuity_mean,
     time_of_day_sin, time_of_day_cos]

Action space per department (4-dim):
    [staff_adjustment_doctors, staff_adjustment_nurses,
     transfer_priority, discharge_threshold]

Reward:
    -1 * mean_wait_time - 0.5 * overcrowding_penalty + 0.3 * throughput_bonus
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from .des_engine import DESConfig, DESEngine
# Single definition of the observation, kept gymnasium-free so the live
# service can import it too (see observation.py).
from .observation import build_dept_observation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

from shared.constants.hospital import DEPARTMENTS

from .observation import STATE_DIM, ACTION_DIM  # noqa: F401  (re-export)
N_DEPARTMENTS = len(DEPARTMENTS)


class HospitalEnv(gym.Env):
    """Multi-agent hospital operations environment.

    This environment supports both:
      - Single-agent mode: flat observation/action spaces (all departments concatenated)
      - Multi-agent mode: dict observation/action spaces keyed by department name

    Parameters
    ----------
    mode:
        ``"multi_agent"`` (default) or ``"single_agent"``.
    step_duration_hours:
        Simulation time advanced per step (default 1.0 hour).
    max_steps:
        Maximum steps per episode.
    active_departments:
        Subset of departments to control (for curriculum learning).
        If None, all departments are active.
    config:
        Optional DES engine configuration.
    """

    metadata = {"render_modes": ["human", "ansi"]}

    def __init__(
        self,
        mode: str = "multi_agent",
        step_duration_hours: float = 1.0,
        max_steps: int = 168,  # 1 week
        active_departments: Optional[List[str]] = None,
        config: Optional[DESConfig] = None,
        seed: int = 42,
        staff_cost_weight: float = 0.0,
    ) -> None:
        super().__init__()

        self.mode = mode
        self.step_duration = step_duration_hours
        self.max_steps = max_steps
        self.active_departments = active_departments or list(DEPARTMENTS)
        self._seed = seed
        # Per-headcount penalty applied to staffing above the ERP baseline.
        #
        # Default 0.0 preserves the original reward exactly. With no cost
        # term the reward is monotonically improved by adding staff, so the
        # optimal policy is "always spend the whole action budget" and the
        # learned agent cannot do better than a constant max-staffing rule —
        # measured on the 2026-08-21 evaluation, max staffing reaches
        # 15.13 h mean wait against 17.52 h for no action, and nothing that
        # allocates *selectively* can beat it. A non-zero weight is what
        # makes selective allocation the optimum and the agent worth having.
        self.staff_cost_weight = float(staff_cost_weight)

        # Initialize DES engine. Training must use internal Poisson
        # arrivals — without this the env runs empty and the agent
        # learns nothing useful from zero patient flow. The runtime
        # uses internal_arrivals=False because admissions come from
        # data_ingestion/Kafka there.
        des_config = config or DESConfig(seed=seed, internal_arrivals=True)
        self.engine = DESEngine(des_config)

        # Define spaces
        self._setup_spaces()

        # Episode state
        self._step_count = 0
        self._episode_rewards: List[float] = []
        self._recent_arrivals: Dict[str, List[float]] = {d: [] for d in DEPARTMENTS}
        self._prev_throughput: Dict[str, int] = {d: 0 for d in DEPARTMENTS}

    def _setup_spaces(self) -> None:
        """Configure observation and action spaces."""
        n_active = len(self.active_departments)

        if self.mode == "multi_agent":
            # Dict spaces keyed by department
            self.observation_space = spaces.Dict({
                dept: spaces.Box(
                    low=-np.inf, high=np.inf,
                    shape=(STATE_DIM,), dtype=np.float32,
                )
                for dept in self.active_departments
            })
            self.action_space = spaces.Dict({
                dept: spaces.Box(
                    low=np.array([-3.0, -5.0, 0.0, 0.0], dtype=np.float32),
                    high=np.array([3.0, 5.0, 1.0, 1.0], dtype=np.float32),
                    dtype=np.float32,
                )
                for dept in self.active_departments
            })
        else:
            # Flat spaces (all departments concatenated)
            self.observation_space = spaces.Box(
                low=-np.inf, high=np.inf,
                shape=(n_active * STATE_DIM,), dtype=np.float32,
            )
            self.action_space = spaces.Box(
                low=np.tile([-3.0, -5.0, 0.0, 0.0], n_active).astype(np.float32),
                high=np.tile([3.0, 5.0, 1.0, 1.0], n_active).astype(np.float32),
                dtype=np.float32,
            )

    def _get_dept_observation(self, dept_name: str) -> np.ndarray:
        """Delegate to the shared builder so train and serve cannot drift."""
        return build_dept_observation(
            self.engine, dept_name, self._recent_arrivals.get(dept_name, []),
        )

    def _get_observation(self) -> Any:
        """Get the full observation."""
        if self.mode == "multi_agent":
            return {
                dept: self._get_dept_observation(dept)
                for dept in self.active_departments
            }
        else:
            obs = np.concatenate([
                self._get_dept_observation(dept)
                for dept in self.active_departments
            ])
            return obs

    def _compute_reward(self) -> Any:
        """Compute normalized per-department rewards.

        Rewards always include a queue-depth signal, even when nothing
        has been served yet — that was the central bug in the original
        design (wait_penalty gated on total_served > 0 left the agent
        with no signal during queue build-up, so it learned to staff
        lightly and cut staff under load).

        Components (all bounded so no single term dominates):
          • wait_penalty   — always on; clipped to -5 to cap extreme values
          • queue_penalty  — proportional to queue depth (NEW); the most
                             direct "things are bad, staff up" signal
          • occ_penalty    — kicks in at 80 % (was 100 %) so the agent
                             starts staffing before the dept tips over
          • throughput     — capped so it can't dominate the wait signal
        """
        dept_rewards: Dict[str, float] = {}

        for dept_name in self.active_departments:
            dept = self.engine.departments.get(dept_name)
            if not dept:
                dept_rewards[dept_name] = 0.0
                continue

            # Wait penalty: always on, capped at -5 so a single outlier
            # patient (20+h wait) can't dominate the gradient signal.
            wait_h = dept.avg_wait_time if dept.total_served > 0 else 0.0
            wait_penalty = -min(wait_h, 5.0)

            # Queue depth — the direct early-warning signal. Linear up to
            # 50 patients, clipped at -2.5 beyond that.
            queue_len = len(dept.queue)
            queue_penalty = -0.05 * min(queue_len, 50)

            # Overcrowding: starts biting at 80 % rather than 100 % so the
            # agent learns to add capacity *before* the dept tips over.
            occ_penalty = -1.0 * max(0.0, dept.occupancy_ratio - 0.8)

            # Throughput: capped at 3.0 so the bonus can't outweigh
            # combined wait + queue penalties (was uncapped 0.3 * new,
            # which let the agent rationalise staff cuts as long as
            # throughput inched up).
            prev = self._prev_throughput.get(dept_name, 0)
            new_served = dept.total_served - prev
            throughput = 0.1 * min(new_served, 30)
            self._prev_throughput[dept_name] = dept.total_served

            # Staffing cost — only for headcount above the ERP baseline, so
            # a department staffed to establishment pays nothing and the
            # term prices the *decision*, not the ward. Zero by default.
            staff_cost = 0.0
            if self.staff_cost_weight:
                from shared.constants.hospital import STAFF_DEFAULTS
                baseline = STAFF_DEFAULTS.get(dept_name, {"doctors": 2, "nurses": 6})
                excess = max(0, dept.staff.total - (baseline["doctors"] + baseline["nurses"]))
                staff_cost = -self.staff_cost_weight * excess

            dept_rewards[dept_name] = float(np.clip(
                wait_penalty + queue_penalty + occ_penalty + throughput + staff_cost,
                -10.0, 5.0,
            ))

        if self.mode == "multi_agent":
            return dept_rewards
        # Single-agent: return mean across departments
        return float(np.mean(list(dept_rewards.values()))) if dept_rewards else 0.0

    def _apply_actions(self, action: Any) -> None:
        """Convert agent actions to DES engine commands."""
        actions_dict: Dict[str, Dict[str, float]] = {}

        if self.mode == "multi_agent":
            for dept_name in self.active_departments:
                if dept_name in action:
                    a = action[dept_name]
                    actions_dict[dept_name] = {
                        "staff_adjustment_doctors": float(a[0]),
                        "staff_adjustment_nurses": float(a[1]),
                        "transfer_priority": float(a[2]),
                        "discharge_threshold": float(a[3]),
                    }
        else:
            # Flat action: split into per-department chunks
            action_arr = np.array(action, dtype=np.float32)
            for i, dept_name in enumerate(self.active_departments):
                offset = i * ACTION_DIM
                a = action_arr[offset : offset + ACTION_DIM]
                actions_dict[dept_name] = {
                    "staff_adjustment_doctors": float(a[0]),
                    "staff_adjustment_nurses": float(a[1]),
                    "transfer_priority": float(a[2]),
                    "discharge_threshold": float(a[3]),
                }

        self.engine.apply_actions(actions_dict)

    def reset(
        self,
        *,
        seed: Optional[int] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Any, Dict[str, Any]]:
        """Reset the environment for a new episode."""
        super().reset(seed=seed)

        if seed is not None:
            self._seed = seed
            self.engine.config.seed = seed
            self.engine.rng = np.random.default_rng(seed)

        self.engine.reset()
        self._step_count = 0
        self._episode_rewards.clear()
        self._recent_arrivals = {d: [] for d in DEPARTMENTS}
        self._prev_throughput = {d: 0 for d in DEPARTMENTS}

        # Run a warm-up period (4 hours) to populate departments
        self.engine.run_until(4.0)

        obs = self._get_observation()
        info = {"simulation_time": self.engine.current_time}

        return obs, info

    def step(self, action: Any) -> Tuple[Any, float, bool, bool, Dict[str, Any]]:
        """Execute one environment step.

        Parameters
        ----------
        action:
            Agent action(s) - dict of arrays (multi-agent) or flat array (single-agent).

        Returns
        -------
        observation, reward, terminated, truncated, info
        """
        # Apply agent actions
        self._apply_actions(action)

        # Advance simulation
        events = self.engine.step(self.step_duration)

        # Track arrivals for rate computation
        for evt in events:
            if evt.event_type.name == "ARRIVAL":
                dept = evt.department
                if dept in self._recent_arrivals:
                    self._recent_arrivals[dept].append(evt.time)
                    # Keep only last 4 hours
                    cutoff = self.engine.current_time - 4.0
                    self._recent_arrivals[dept] = [
                        t for t in self._recent_arrivals[dept] if t > cutoff
                    ]

        # Compute reward (may be dict for multi-agent or float for single)
        reward = self._compute_reward()
        # Track scalar reward for episode stats
        if isinstance(reward, dict):
            scalar_reward = float(np.mean(list(reward.values()))) if reward else 0.0
        else:
            scalar_reward = float(reward)
        self._episode_rewards.append(scalar_reward)
        self._step_count += 1

        # Check termination
        terminated = False
        truncated = self._step_count >= self.max_steps

        # Get observation
        obs = self._get_observation()

        # Info
        metrics = self.engine.get_metrics()
        info = {
            "simulation_time": self.engine.current_time,
            "step": self._step_count,
            "mean_wait_time": metrics["mean_total_wait"],
            "total_discharged": metrics["total_discharged"],
            "active_patients": metrics["active_patients"],
            "episode_reward_sum": sum(self._episode_rewards),
        }

        return obs, reward, terminated, truncated, info

    def render(self, mode: str = "human") -> Optional[str]:
        """Render the current state."""
        state = self.engine.get_state()
        lines = [
            f"\n=== Hospital State (t={self.engine.current_time:.1f}h, step={self._step_count}) ===",
        ]
        for dept_name in self.active_departments:
            ds = state.get(dept_name, {})
            lines.append(
                f"  {dept_name:20s} | "
                f"Patients: {ds.get('patient_count', 0):3d} "
                f"(Q:{ds.get('queue_length', 0):2d}) | "
                f"Occ: {ds.get('occupancy_ratio', 0):.0%} | "
                f"AvgWait: {ds.get('avg_wait_time', 0):.2f}h | "
                f"Staff: {ds.get('staff_doctors', 0)}D/{ds.get('staff_nurses', 0)}N"
            )

        g = state.get("_global", {})
        lines.append(
            f"  {'TOTAL':20s} | "
            f"Active: {g.get('active_patients', 0)} | "
            f"Discharged: {g.get('discharged_patients', 0)} | "
            f"Events: {g.get('pending_events', 0)}"
        )

        output = "\n".join(lines)
        if mode == "human":
            print(output)
            return None
        return output

    def close(self) -> None:
        """Clean up resources."""
        pass

    def get_department_states(self) -> Dict[str, np.ndarray]:
        """Get individual department observations (for multi-agent use)."""
        return {
            dept: self._get_dept_observation(dept)
            for dept in self.active_departments
        }

    def get_all_states_flat(self) -> np.ndarray:
        """Get concatenated state vector for all active departments."""
        return np.concatenate([
            self._get_dept_observation(dept)
            for dept in self.active_departments
        ])
