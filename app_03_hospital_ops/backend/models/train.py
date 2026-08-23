"""Training loop for DES-MARL hospital operations optimizer.

Supports curriculum learning stages that progressively increase the
number of active departments and simulation complexity.

Usage:
    python -m app_03_hospital_ops.backend.models.train \
        --episodes 1000 --checkpoint-dir checkpoints/hospital_ops
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from ..simulation.environment import HospitalEnv, DEPARTMENTS, STATE_DIM, ACTION_DIM
from ..simulation.des_engine import DESConfig
from .marl_agent import MADDPGAgent, DEFAULT_CURRICULUM, CurriculumStage

logger = logging.getLogger(__name__)

# How often loss scalars are pulled back from the device for logging.
# Each read is a synchronisation point; see the note at the update() call.
_LOSS_LOG_EVERY = 50

DEFAULT_CHECKPOINT_DIR = Path(
    os.environ.get("MARL_CHECKPOINT_DIR", "/home/hari/hse/models/hospital_ops")
)


# ---------------------------------------------------------------------------
# Training configuration
# ---------------------------------------------------------------------------

class TrainingConfig:
    """Configuration for the training loop."""

    def __init__(
        self,
        total_episodes: int = 2000,
        max_steps_per_episode: int = 168,
        step_duration_hours: float = 1.0,
        batch_size: int = 64,
        buffer_capacity: int = 100_000,
        actor_lr: float = 1e-4,
        critic_lr: float = 1e-3,
        gamma: float = 0.99,
        tau: float = 0.005,
        updates_per_step: int = 1,
        checkpoint_interval: int = 100,
        log_interval: int = 10,
        eval_interval: int = 50,
        eval_episodes: int = 5,
        checkpoint_dir: Path = DEFAULT_CHECKPOINT_DIR,
        use_curriculum: bool = True,
        curriculum_stages: Optional[List[CurriculumStage]] = None,
        seed: int = 42,
        device: str = "cpu",
        staff_cost_weight: float = 0.0,
        per_agent_critic: bool = False,
        wait_penalty_cap: float = 5.0,
        queue_penalty_cap: float = 2.5,
    ) -> None:
        self.total_episodes = total_episodes
        self.max_steps_per_episode = max_steps_per_episode
        self.step_duration_hours = step_duration_hours
        self.batch_size = batch_size
        self.buffer_capacity = buffer_capacity
        self.actor_lr = actor_lr
        self.critic_lr = critic_lr
        self.gamma = gamma
        self.tau = tau
        self.updates_per_step = updates_per_step
        self.checkpoint_interval = checkpoint_interval
        self.log_interval = log_interval
        self.eval_interval = eval_interval
        self.eval_episodes = eval_episodes
        self.checkpoint_dir = checkpoint_dir
        self.use_curriculum = use_curriculum
        self.curriculum_stages = curriculum_stages or list(DEFAULT_CURRICULUM)
        self.seed = seed
        self.device = device
        self.staff_cost_weight = staff_cost_weight
        self.per_agent_critic = per_agent_critic
        self.wait_penalty_cap = wait_penalty_cap
        self.queue_penalty_cap = queue_penalty_cap


# ---------------------------------------------------------------------------
# Training metrics
# ---------------------------------------------------------------------------

class TrainingMetrics:
    """Tracks and records training metrics across episodes."""

    def __init__(self) -> None:
        self.episode_rewards: List[float] = []
        self.episode_wait_times: List[float] = []
        self.episode_throughputs: List[int] = []
        self.episode_lengths: List[int] = []
        self.losses: List[Dict[str, float]] = []
        self.curriculum_stage: List[str] = []
        self.wall_times: List[float] = []

    def record_episode(
        self,
        reward: float,
        mean_wait: float,
        throughput: int,
        length: int,
        stage: str,
        wall_time: float,
    ) -> None:
        self.episode_rewards.append(reward)
        self.episode_wait_times.append(mean_wait)
        self.episode_throughputs.append(throughput)
        self.episode_lengths.append(length)
        self.curriculum_stage.append(stage)
        self.wall_times.append(wall_time)

    def record_losses(self, losses: Dict[str, float]) -> None:
        self.losses.append(losses)

    def get_recent_stats(self, window: int = 50) -> Dict[str, float]:
        """Get statistics over the last ``window`` episodes."""
        if not self.episode_rewards:
            return {}

        recent_r = self.episode_rewards[-window:]
        recent_w = self.episode_wait_times[-window:]
        recent_t = self.episode_throughputs[-window:]

        return {
            "mean_reward": float(np.mean(recent_r)),
            "std_reward": float(np.std(recent_r)),
            "mean_wait_time": float(np.mean(recent_w)),
            "mean_throughput": float(np.mean(recent_t)),
            "min_reward": float(np.min(recent_r)),
            "max_reward": float(np.max(recent_r)),
        }

    def save(self, path: Path) -> None:
        """Save metrics to JSON."""
        data = {
            "episode_rewards": self.episode_rewards,
            "episode_wait_times": self.episode_wait_times,
            "episode_throughputs": self.episode_throughputs,
            "episode_lengths": self.episode_lengths,
            "curriculum_stage": self.curriculum_stage,
            "wall_times": self.wall_times,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Training loop
# ---------------------------------------------------------------------------

def run_evaluation(
    agent: MADDPGAgent,
    config: TrainingConfig,
    active_depts: List[str],
    n_episodes: int = 5,
) -> Dict[str, float]:
    """Run evaluation episodes without exploration noise.

    Returns aggregate stats over all eval episodes.
    """
    rewards = []
    wait_times = []
    throughputs = []

    for ep in range(n_episodes):
        env = HospitalEnv(
            mode="multi_agent",
            step_duration_hours=config.step_duration_hours,
            max_steps=config.max_steps_per_episode,
            active_departments=active_depts,
            seed=config.seed + 10000 + ep,
            staff_cost_weight=config.staff_cost_weight,
            wait_penalty_cap=config.wait_penalty_cap,
            queue_penalty_cap=config.queue_penalty_cap,
        )

        obs, info = env.reset()
        ep_reward = 0.0
        done = False

        while not done:
            actions = agent.select_actions(obs, explore=False)
            obs, reward, terminated, truncated, info = env.step(actions)
            if isinstance(reward, dict):
                ep_reward += float(np.mean(list(reward.values()))) if reward else 0.0
            else:
                ep_reward += float(reward)
            done = terminated or truncated

        rewards.append(ep_reward)
        wait_times.append(info.get("mean_wait_time", 0.0))
        throughputs.append(info.get("total_discharged", 0))
        env.close()

    return {
        "eval_mean_reward": float(np.mean(rewards)),
        "eval_std_reward": float(np.std(rewards)),
        "eval_mean_wait_time": float(np.mean(wait_times)),
        "eval_mean_throughput": float(np.mean(throughputs)),
    }


def train(config: Optional[TrainingConfig] = None) -> TrainingMetrics:
    """Run the full training loop with optional curriculum learning.

    Parameters
    ----------
    config:
        Training configuration. Uses defaults if None.

    Returns
    -------
    TrainingMetrics with the full history.
    """
    config = config or TrainingConfig()
    config.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    metrics = TrainingMetrics()
    np.random.seed(config.seed)

    # Curriculum setup
    if config.use_curriculum:
        stages = config.curriculum_stages
        current_stage_idx = 0
        current_stage = stages[current_stage_idx]
        active_depts = current_stage.departments
    else:
        current_stage = CurriculumStage(
            name="full", departments=list(DEPARTMENTS), min_episodes=config.total_episodes
        )
        active_depts = list(DEPARTMENTS)
        current_stage_idx = 0
        stages = [current_stage]

    # Initialize agent
    agent = MADDPGAgent(
        department_names=active_depts,
        obs_dim=STATE_DIM,
        action_dim=ACTION_DIM,
        actor_lr=config.actor_lr,
        critic_lr=config.critic_lr,
        gamma=config.gamma,
        tau=config.tau,
        batch_size=config.batch_size,
        buffer_capacity=config.buffer_capacity,
        device=config.device,
        per_agent_critic=config.per_agent_critic,
    )

    # Initialize environment
    env = HospitalEnv(
        mode="multi_agent",
        step_duration_hours=config.step_duration_hours,
        max_steps=config.max_steps_per_episode,
        active_departments=active_depts,
        seed=config.seed,
        staff_cost_weight=config.staff_cost_weight,
        wait_penalty_cap=config.wait_penalty_cap,
        queue_penalty_cap=config.queue_penalty_cap,
    )

    logger.info(f"Starting training: {config.total_episodes} episodes")
    logger.info(f"Active departments: {active_depts}")
    logger.info(f"Curriculum stage: {current_stage.name}")

    episodes_in_stage = 0

    for episode in range(1, config.total_episodes + 1):
        episode_start = time.time()
        agent.reset_noise()

        obs, info = env.reset(seed=config.seed + episode)
        episode_reward = 0.0
        step_count = 0
        done = False

        while not done:
            # Select actions
            actions = agent.select_actions(obs, explore=True)

            # Environment step
            next_obs, reward, terminated, truncated, info = env.step(actions)

            # Handle per-department rewards (dict) or scalar reward
            if isinstance(reward, dict):
                dept_rewards = reward
                scalar_reward = float(np.mean(list(reward.values()))) if reward else 0.0
            else:
                dept_rewards = {d: float(reward) for d in active_depts}
                scalar_reward = float(reward)

            dept_dones = {d: terminated or truncated for d in active_depts}

            # Store transition
            agent.store_transition(obs, actions, dept_rewards, next_obs, dept_dones)

            # Train.
            #
            # Loss scalars are only read back on logging steps. Each one is a
            # device sync, and update() produces n_agents + 1 of them; pulling
            # them every step stalled the CUDA pipeline for numbers nobody
            # looked at, and grew the in-memory loss list by 168 dicts per
            # episode on top of that.
            want_losses = (agent.training_step % _LOSS_LOG_EVERY) == 0
            for _ in range(config.updates_per_step):
                losses = agent.update(return_losses=want_losses)
                if losses:
                    metrics.record_losses(losses)

            obs = next_obs
            episode_reward += scalar_reward
            step_count += 1
            done = terminated or truncated

        # Record episode metrics
        wall_time = time.time() - episode_start
        mean_wait = info.get("mean_wait_time", 0.0)
        throughput = info.get("total_discharged", 0)

        metrics.record_episode(
            reward=episode_reward,
            mean_wait=mean_wait,
            throughput=throughput,
            length=step_count,
            stage=current_stage.name,
            wall_time=wall_time,
        )

        agent.episodes_completed = episode
        episodes_in_stage += 1

        # Logging
        if episode % config.log_interval == 0:
            stats = metrics.get_recent_stats(window=config.log_interval)
            logger.info(
                f"Episode {episode}/{config.total_episodes} | "
                f"Stage: {current_stage.name} | "
                f"Reward: {stats.get('mean_reward', 0):.2f} +/- {stats.get('std_reward', 0):.2f} | "
                f"Wait: {stats.get('mean_wait_time', 0):.2f}h | "
                f"Throughput: {stats.get('mean_throughput', 0):.0f} | "
                f"Time: {wall_time:.1f}s"
            )

        # Evaluation
        if episode % config.eval_interval == 0:
            eval_stats = run_evaluation(agent, config, active_depts)
            logger.info(f"  EVAL: reward={eval_stats['eval_mean_reward']:.2f}, "
                       f"wait={eval_stats['eval_mean_wait_time']:.2f}h")

        # Checkpoint
        if episode % config.checkpoint_interval == 0:
            ckpt_path = config.checkpoint_dir / f"checkpoint_ep{episode}.pt"
            agent.save_checkpoint(str(ckpt_path))
            metrics.save(config.checkpoint_dir / "training_metrics.json")

        # Curriculum advancement
        if config.use_curriculum and current_stage_idx < len(stages) - 1:
            recent_stats = metrics.get_recent_stats(window=50)
            # Compare per-step reward, not the episode sum — see the note on
            # CurriculumStage. The episode-sum comparison silently stopped
            # passing when the reward was rescaled, which would have pinned
            # training to stage 1 and left every actor but ED at random init.
            mean_per_step = (
                recent_stats.get("mean_reward", -1e9) / max(1, config.max_steps_per_episode)
            )
            stage_cap = current_stage.max_episodes or (3 * current_stage.min_episodes)
            hit_target = (
                episodes_in_stage >= current_stage.min_episodes
                and mean_per_step >= current_stage.target_reward
            )
            hit_cap = episodes_in_stage >= stage_cap
            should_advance = hit_target or hit_cap

            if should_advance:
                logger.info(
                    ">>> Stage %s complete after %d episodes (per-step reward "
                    "%.3f vs target %.3f) — %s",
                    current_stage.name, episodes_in_stage, mean_per_step,
                    current_stage.target_reward,
                    "target met" if hit_target else "episode cap reached",
                )
                current_stage_idx += 1
                current_stage = stages[current_stage_idx]
                active_depts = current_stage.departments
                episodes_in_stage = 0

                logger.info(f"\n>>> Advancing to curriculum stage: {current_stage.name}")
                logger.info(f">>> Active departments: {active_depts}")
                logger.info(f">>> Description: {current_stage.description}")

                # Update agent and environment
                agent.set_active_departments(active_depts)
                env.close()
                env = HospitalEnv(
                    mode="multi_agent",
                    step_duration_hours=config.step_duration_hours,
                    max_steps=config.max_steps_per_episode,
                    active_departments=active_depts,
                    seed=config.seed,
                    staff_cost_weight=config.staff_cost_weight,
                    wait_penalty_cap=config.wait_penalty_cap,
                    queue_penalty_cap=config.queue_penalty_cap,
                )

    # Final save
    final_path = config.checkpoint_dir / "final_model.pt"
    agent.save_checkpoint(str(final_path))
    metrics.save(config.checkpoint_dir / "training_metrics.json")

    env.close()
    logger.info("Training complete!")

    return metrics


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Train DES-MARL hospital operations optimizer")
    parser.add_argument("--episodes", type=int, default=2000, help="Total training episodes")
    parser.add_argument("--max-steps", type=int, default=168, help="Max steps per episode")
    parser.add_argument("--batch-size", type=int, default=64, help="Mini-batch size")
    parser.add_argument("--actor-lr", type=float, default=1e-4, help="Actor learning rate")
    parser.add_argument("--critic-lr", type=float, default=1e-3, help="Critic learning rate")
    parser.add_argument("--gamma", type=float, default=0.99, help="Discount factor")
    parser.add_argument("--tau", type=float, default=0.005, help="Soft update coefficient")
    parser.add_argument("--no-curriculum", action="store_true", help="Disable curriculum learning")
    parser.add_argument("--checkpoint-dir", type=str, default=str(DEFAULT_CHECKPOINT_DIR))
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--device", type=str, default="cpu", help="Torch device")
    parser.add_argument(
        "--staff-cost-weight", type=float, default=0.0,
        help=("Per-headcount reward penalty for staffing above the ERP "
              "baseline. 0.0 (default) reproduces the original reward, whose "
              "optimum is simply to spend the whole action budget every step."),
    )
    parser.add_argument(
        "--per-agent-critic", action="store_true",
        help=("Give the critic one Q head per department, each regressed on "
              "that department's own reward. Without it the critic predicts "
              "the mean reward across departments, which dilutes any single "
              "agent's contribution by 1/n and is why actors did not learn."),
    )
    parser.add_argument("--wait-penalty-cap", type=float, default=5.0,
                        help="Ceiling on the per-step wait penalty (hours).")
    parser.add_argument("--queue-penalty-cap", type=float, default=2.5,
                        help="Ceiling on the per-step queue-depth penalty.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cfg = TrainingConfig(
        total_episodes=args.episodes,
        max_steps_per_episode=args.max_steps,
        batch_size=args.batch_size,
        actor_lr=args.actor_lr,
        critic_lr=args.critic_lr,
        gamma=args.gamma,
        tau=args.tau,
        use_curriculum=not args.no_curriculum,
        checkpoint_dir=Path(args.checkpoint_dir),
        seed=args.seed,
        device=args.device,
        staff_cost_weight=args.staff_cost_weight,
        per_agent_critic=args.per_agent_critic,
        wait_penalty_cap=args.wait_penalty_cap,
        queue_penalty_cap=args.queue_penalty_cap,
    )

    metrics = train(cfg)
    final_stats = metrics.get_recent_stats(window=100)
    print(f"\nFinal stats (last 100 episodes):")
    for k, v in final_stats.items():
        print(f"  {k}: {v:.4f}")


if __name__ == "__main__":
    main()
