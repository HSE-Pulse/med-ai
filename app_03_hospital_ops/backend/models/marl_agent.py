"""Multi-Agent Deep Deterministic Policy Gradient (MADDPG) for hospital operations.

Each department is controlled by an independent actor that observes local state,
while the critic has access to all agents' observations and actions (centralized
training, decentralized execution).

Architecture:
  - Actor: 12-dim local state -> 64 -> 64 -> 4-dim continuous action
  - Critic: (12*N + 4*N) -> 128 -> 64 -> 1 Q-value
  - Ornstein-Uhlenbeck noise for exploration
  - Soft target updates (tau)
  - Curriculum learning stages
"""

from __future__ import annotations

import copy
import logging
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Network architectures
# ---------------------------------------------------------------------------

class ActorNetwork(nn.Module):
    """Actor network: maps local observation to continuous action.

    Architecture: obs_dim -> 64 -> 64 -> action_dim (tanh output)
    """

    def __init__(self, obs_dim: int = 12, action_dim: int = 4, hidden_dim: int = 64) -> None:
        super().__init__()
        self.fc1 = nn.Linear(obs_dim, hidden_dim)
        self.ln1 = nn.LayerNorm(hidden_dim)
        self.fc2 = nn.Linear(hidden_dim, hidden_dim)
        self.ln2 = nn.LayerNorm(hidden_dim)
        self.fc3 = nn.Linear(hidden_dim, action_dim)

        # Action scaling: output ranges match environment action space
        # [doctors: -3..3, nurses: -5..5, priority: 0..1, threshold: 0..1]
        self.action_low = torch.tensor([-3.0, -5.0, 0.0, 0.0])
        self.action_high = torch.tensor([3.0, 5.0, 1.0, 1.0])

        self._init_weights()

    def _init_weights(self) -> None:
        for m in [self.fc1, self.fc2]:
            nn.init.xavier_uniform_(m.weight)
            nn.init.zeros_(m.bias)
        nn.init.uniform_(self.fc3.weight, -3e-3, 3e-3)
        nn.init.zeros_(self.fc3.bias)

    def forward(self, obs: torch.Tensor) -> torch.Tensor:
        """Forward pass.

        Parameters
        ----------
        obs:
            Local observation tensor of shape (batch, obs_dim).

        Returns
        -------
        Action tensor of shape (batch, action_dim) in the valid range.
        """
        x = F.relu(self.ln1(self.fc1(obs)))
        x = F.relu(self.ln2(self.fc2(x)))
        raw = torch.tanh(self.fc3(x))

        # Scale from [-1, 1] to action range
        low = self.action_low.to(raw.device)
        high = self.action_high.to(raw.device)
        action = low + (raw + 1.0) * 0.5 * (high - low)
        return action


class CriticNetwork(nn.Module):
    """Centralized critic: maps all agents' observations and actions to Q-value.

    Architecture: (obs_dim*N + action_dim*N) -> 128 -> 64 -> 1
    """

    def __init__(
        self,
        n_agents: int,
        obs_dim: int = 12,
        action_dim: int = 4,
        hidden_dim: int = 128,
    ) -> None:
        super().__init__()
        input_dim = n_agents * (obs_dim + action_dim)
        self.fc1 = nn.Linear(input_dim, hidden_dim)
        self.ln1 = nn.LayerNorm(hidden_dim)
        self.fc2 = nn.Linear(hidden_dim, hidden_dim // 2)
        self.ln2 = nn.LayerNorm(hidden_dim // 2)
        self.fc3 = nn.Linear(hidden_dim // 2, 1)

        self._init_weights()

    def _init_weights(self) -> None:
        for m in [self.fc1, self.fc2]:
            nn.init.xavier_uniform_(m.weight)
            nn.init.zeros_(m.bias)
        nn.init.uniform_(self.fc3.weight, -3e-3, 3e-3)
        nn.init.zeros_(self.fc3.bias)

    def forward(
        self,
        all_obs: torch.Tensor,
        all_actions: torch.Tensor,
    ) -> torch.Tensor:
        """Forward pass.

        Parameters
        ----------
        all_obs:
            Concatenated observations from all agents, shape (batch, n_agents * obs_dim).
        all_actions:
            Concatenated actions from all agents, shape (batch, n_agents * action_dim).

        Returns
        -------
        Q-value tensor of shape (batch, 1).
        """
        x = torch.cat([all_obs, all_actions], dim=-1)
        x = F.relu(self.ln1(self.fc1(x)))
        x = F.relu(self.ln2(self.fc2(x)))
        return self.fc3(x)


# ---------------------------------------------------------------------------
# Noise process
# ---------------------------------------------------------------------------

class OUNoise:
    """Ornstein-Uhlenbeck noise process for exploration."""

    def __init__(
        self,
        size: int,
        mu: float = 0.0,
        theta: float = 0.15,
        sigma: float = 0.2,
        sigma_decay: float = 0.999,   # Slower decay to maintain exploration through curriculum
        sigma_min: float = 0.05,
    ) -> None:
        self.size = size
        self.mu = mu
        self.theta = theta
        self.sigma = sigma
        self.sigma_decay = sigma_decay
        self.sigma_min = sigma_min
        self.state = np.full(size, mu)

    def reset(self) -> None:
        self.state = np.full(self.size, self.mu)

    def sample(self) -> np.ndarray:
        dx = self.theta * (self.mu - self.state) + self.sigma * np.random.randn(self.size)
        self.state += dx
        self.sigma = max(self.sigma_min, self.sigma * self.sigma_decay)
        return self.state.copy()


# ---------------------------------------------------------------------------
# Replay buffer
# ---------------------------------------------------------------------------

@dataclass
class Experience:
    """Single transition for the replay buffer."""
    obs: Dict[str, np.ndarray]
    actions: Dict[str, np.ndarray]
    rewards: Dict[str, float]
    next_obs: Dict[str, np.ndarray]
    dones: Dict[str, bool]


class ReplayBuffer:
    """Experience replay buffer for MADDPG."""

    def __init__(self, capacity: int = 100_000) -> None:
        self.buffer: Deque[Experience] = deque(maxlen=capacity)

    def push(self, experience: Experience) -> None:
        self.buffer.append(experience)

    def sample(self, batch_size: int) -> List[Experience]:
        return random.sample(self.buffer, min(batch_size, len(self.buffer)))

    def __len__(self) -> int:
        return len(self.buffer)


# ---------------------------------------------------------------------------
# Curriculum stages
# ---------------------------------------------------------------------------

@dataclass
class CurriculumStage:
    """Defines a curriculum learning stage."""
    name: str
    departments: List[str]
    min_episodes: int
    target_reward: float = -5.0
    description: str = ""


DEFAULT_CURRICULUM: List[CurriculumStage] = [
    CurriculumStage(
        name="stage_1_ed",
        departments=["ED"],
        min_episodes=100,
        target_reward=2.0,         # Achievable with normalized rewards [-10, 10]
        description="Single department: learn basic staffing in ED",
    ),
    CurriculumStage(
        name="stage_2_core",
        departments=["ED", "MAU", "Medicine", "ICU"],
        min_episodes=200,
        target_reward=0.0,         # Breakeven is good for 4 departments
        description="Core flow: ED -> assessment -> inpatient -> critical care",
    ),
    CurriculumStage(
        name="stage_3_extended",
        departments=["ED", "MAU", "SAU", "Medicine", "Surgery", "ICU", "Discharge_Lounge"],
        min_episodes=300,
        target_reward=-1.0,
        description="Extended: medical + surgical pathways with discharge",
    ),
    CurriculumStage(
        name="stage_4_all",
        departments=[
            "ED", "MAU", "AMAU", "SAU", "CDU",
            "Medicine", "Surgery", "Cardiology", "Respiratory", "Orthopaedics",
            "ICU", "HDU", "Day_Ward", "Discharge_Lounge",
        ],
        min_episodes=500,
        target_reward=-2.0,
        description="All 14 Irish HSE departments at full complexity",
    ),
]


# ---------------------------------------------------------------------------
# MADDPG Agent
# ---------------------------------------------------------------------------

class MADDPGAgent:
    """Multi-Agent Deep Deterministic Policy Gradient.

    Each department gets its own actor network (decentralized policy).
    A shared critic network evaluates joint state-action pairs (centralized).

    Parameters
    ----------
    department_names:
        List of department names this agent manages.
    obs_dim:
        Observation dimension per department.
    action_dim:
        Action dimension per department.
    actor_lr:
        Learning rate for actor networks.
    critic_lr:
        Learning rate for the critic network.
    gamma:
        Discount factor.
    tau:
        Soft target update coefficient.
    batch_size:
        Mini-batch size for training.
    buffer_capacity:
        Replay buffer capacity.
    device:
        Torch device string.
    """

    def __init__(
        self,
        department_names: List[str],
        obs_dim: int = 12,
        action_dim: int = 4,
        actor_lr: float = 1e-4,
        critic_lr: float = 1e-3,
        gamma: float = 0.99,
        tau: float = 0.005,
        batch_size: int = 64,
        buffer_capacity: int = 100_000,
        device: str = "cpu",
    ) -> None:
        self.department_names = list(department_names)
        self.n_agents = len(department_names)
        self.obs_dim = obs_dim
        self.action_dim = action_dim
        self.gamma = gamma
        self.tau = tau
        self.batch_size = batch_size
        self.device = torch.device(device)

        # Actor networks (one per department)
        self.actors: Dict[str, ActorNetwork] = {}
        self.target_actors: Dict[str, ActorNetwork] = {}
        self.actor_optimizers: Dict[str, optim.Adam] = {}
        self.noise_processes: Dict[str, OUNoise] = {}

        for dept in department_names:
            actor = ActorNetwork(obs_dim, action_dim).to(self.device)
            target_actor = copy.deepcopy(actor)
            self.actors[dept] = actor
            self.target_actors[dept] = target_actor
            self.actor_optimizers[dept] = optim.Adam(actor.parameters(), lr=actor_lr)
            self.noise_processes[dept] = OUNoise(action_dim)

        # Critic network (shared, centralized) — always sized for max 14 agents
        # so it doesn't need rebuilding during curriculum stage transitions
        self._max_agents = 14
        self.critic = CriticNetwork(self._max_agents, obs_dim, action_dim).to(self.device)
        self.target_critic = copy.deepcopy(self.critic)
        self.critic_optimizer = optim.Adam(self.critic.parameters(), lr=critic_lr)

        # Replay buffer
        self.replay_buffer = ReplayBuffer(capacity=buffer_capacity)

        # Training state
        self.training_step = 0
        self.episodes_completed = 0

    def select_actions(
        self,
        observations: Dict[str, np.ndarray],
        explore: bool = True,
    ) -> Dict[str, np.ndarray]:
        """Select actions for all departments.

        Parameters
        ----------
        observations:
            Dict mapping department name to observation array.
        explore:
            If True, add OU noise for exploration.

        Returns
        -------
        Dict mapping department name to action array.
        """
        actions: Dict[str, np.ndarray] = {}

        for dept in self.department_names:
            obs = observations.get(dept)
            if obs is None:
                actions[dept] = np.zeros(self.action_dim, dtype=np.float32)
                continue

            obs_tensor = torch.FloatTensor(obs).unsqueeze(0).to(self.device)
            with torch.no_grad():
                action = self.actors[dept](obs_tensor).cpu().numpy().squeeze(0)

            if explore:
                noise = self.noise_processes[dept].sample()
                action = action + noise.astype(np.float32)
                # Clip to valid range
                low = np.array([-3.0, -5.0, 0.0, 0.0])
                high = np.array([3.0, 5.0, 1.0, 1.0])
                action = np.clip(action, low, high)

            actions[dept] = action

        return actions

    def store_transition(
        self,
        obs: Dict[str, np.ndarray],
        actions: Dict[str, np.ndarray],
        rewards: Dict[str, float],
        next_obs: Dict[str, np.ndarray],
        dones: Dict[str, bool],
    ) -> None:
        """Store a transition in the replay buffer."""
        self.replay_buffer.push(Experience(
            obs=obs,
            actions=actions,
            rewards=rewards,
            next_obs=next_obs,
            dones=dones,
        ))

    def update(self) -> Dict[str, float]:
        """Perform one training update step.

        Returns
        -------
        Dict of loss values for logging.
        """
        if len(self.replay_buffer) < self.batch_size:
            return {}

        batch = self.replay_buffer.sample(self.batch_size)
        losses: Dict[str, float] = {}

        # Prepare batch tensors
        batch_obs = {
            dept: torch.FloatTensor(
                np.stack([exp.obs.get(dept, np.zeros(self.obs_dim)) for exp in batch])
            ).to(self.device)
            for dept in self.department_names
        }
        batch_actions = {
            dept: torch.FloatTensor(
                np.stack([exp.actions.get(dept, np.zeros(self.action_dim)) for exp in batch])
            ).to(self.device)
            for dept in self.department_names
        }
        batch_next_obs = {
            dept: torch.FloatTensor(
                np.stack([exp.next_obs.get(dept, np.zeros(self.obs_dim)) for exp in batch])
            ).to(self.device)
            for dept in self.department_names
        }

        # Concatenate all observations and actions, zero-padded to max 14 agents
        obs_parts = [batch_obs[d] for d in self.department_names]
        act_parts = [batch_actions[d] for d in self.department_names]
        next_obs_parts = [batch_next_obs[d] for d in self.department_names]

        # Pad to _max_agents width so critic input dimension is always the same
        pad_count = self._max_agents - len(self.department_names)
        if pad_count > 0:
            zero_obs = torch.zeros(self.batch_size, self.obs_dim, device=self.device)
            zero_act = torch.zeros(self.batch_size, self.action_dim, device=self.device)
            obs_parts.extend([zero_obs] * pad_count)
            act_parts.extend([zero_act] * pad_count)
            next_obs_parts.extend([zero_obs] * pad_count)

        all_obs = torch.cat(obs_parts, dim=-1)
        all_actions = torch.cat(act_parts, dim=-1)
        all_next_obs = torch.cat(next_obs_parts, dim=-1)

        # Target actions for next state
        with torch.no_grad():
            target_next_actions = []
            for dept in self.department_names:
                target_next_actions.append(
                    self.target_actors[dept](batch_next_obs[dept])
                )
            # Pad target actions too
            if pad_count > 0:
                target_next_actions.extend([torch.zeros(self.batch_size, self.action_dim, device=self.device)] * pad_count)
            all_target_next_actions = torch.cat(target_next_actions, dim=-1)

        # --- Update critic ---
        with torch.no_grad():
            target_q = self.target_critic(all_next_obs, all_target_next_actions)

        # Use mean reward across departments as the shared reward signal
        batch_rewards = torch.FloatTensor([
            np.mean([exp.rewards.get(d, 0.0) for d in self.department_names])
            for exp in batch
        ]).unsqueeze(1).to(self.device)

        batch_dones = torch.FloatTensor([
            float(any(exp.dones.get(d, False) for d in self.department_names))
            for exp in batch
        ]).unsqueeze(1).to(self.device)

        target_value = batch_rewards + self.gamma * (1 - batch_dones) * target_q
        current_q = self.critic(all_obs, all_actions)
        critic_loss = F.mse_loss(current_q, target_value)

        self.critic_optimizer.zero_grad()
        critic_loss.backward()
        torch.nn.utils.clip_grad_norm_(self.critic.parameters(), 0.5)
        self.critic_optimizer.step()
        losses["critic_loss"] = critic_loss.item()

        # --- Update actors ---
        for dept in self.department_names:
            # Compute actions with current policy for this agent
            current_dept_actions = self.actors[dept](batch_obs[dept])

            # Replace this department's actions in the joint action (padded to max agents)
            all_current_actions_list = []
            for d in self.department_names:
                if d == dept:
                    all_current_actions_list.append(current_dept_actions)
                else:
                    all_current_actions_list.append(batch_actions[d].detach())
            # Pad to max agents
            pad_needed = self._max_agents - len(self.department_names)
            if pad_needed > 0:
                all_current_actions_list.extend([torch.zeros(self.batch_size, self.action_dim, device=self.device)] * pad_needed)
            all_current_actions = torch.cat(all_current_actions_list, dim=-1)

            actor_loss = -self.critic(all_obs.detach(), all_current_actions).mean()

            self.actor_optimizers[dept].zero_grad()
            actor_loss.backward()
            torch.nn.utils.clip_grad_norm_(self.actors[dept].parameters(), 0.5)
            self.actor_optimizers[dept].step()
            losses[f"actor_loss_{dept}"] = actor_loss.item()

        # Soft target updates
        self._soft_update()
        self.training_step += 1

        return losses

    def _soft_update(self) -> None:
        """Soft update target networks."""
        for dept in self.department_names:
            for target_param, param in zip(
                self.target_actors[dept].parameters(),
                self.actors[dept].parameters(),
            ):
                target_param.data.copy_(
                    self.tau * param.data + (1 - self.tau) * target_param.data
                )

        for target_param, param in zip(
            self.target_critic.parameters(),
            self.critic.parameters(),
        ):
            target_param.data.copy_(
                self.tau * param.data + (1 - self.tau) * target_param.data
            )

    def reset_noise(self) -> None:
        """Reset all OU noise processes (call at episode start)."""
        for noise in self.noise_processes.values():
            noise.reset()

    def save_checkpoint(self, path: str) -> None:
        """Save all networks and optimizer states."""
        checkpoint = {
            "department_names": self.department_names,
            "training_step": self.training_step,
            "episodes_completed": self.episodes_completed,
            "critic_state": self.critic.state_dict(),
            "target_critic_state": self.target_critic.state_dict(),
            "critic_optimizer_state": self.critic_optimizer.state_dict(),
            "actors": {},
            "target_actors": {},
            "actor_optimizers": {},
        }
        for dept in self.department_names:
            checkpoint["actors"][dept] = self.actors[dept].state_dict()
            checkpoint["target_actors"][dept] = self.target_actors[dept].state_dict()
            checkpoint["actor_optimizers"][dept] = self.actor_optimizers[dept].state_dict()

        torch.save(checkpoint, path)
        logger.info(f"Checkpoint saved to {path}")

    def load_checkpoint(self, path: str) -> None:
        """Load networks and optimizer states from checkpoint."""
        checkpoint = torch.load(path, map_location=self.device, weights_only=False)

        self.training_step = checkpoint["training_step"]
        self.episodes_completed = checkpoint["episodes_completed"]

        self.critic.load_state_dict(checkpoint["critic_state"])
        self.target_critic.load_state_dict(checkpoint["target_critic_state"])
        self.critic_optimizer.load_state_dict(checkpoint["critic_optimizer_state"])

        for dept in self.department_names:
            if dept in checkpoint["actors"]:
                self.actors[dept].load_state_dict(checkpoint["actors"][dept])
                self.target_actors[dept].load_state_dict(checkpoint["target_actors"][dept])
                self.actor_optimizers[dept].load_state_dict(checkpoint["actor_optimizers"][dept])

        logger.info(f"Checkpoint loaded from {path} (step={self.training_step})")

    def set_active_departments(self, departments: List[str]) -> None:
        """Update active departments for curriculum learning.

        Creates new actors for new departments. Critic is NOT rebuilt —
        it was initialized for max 14 agents with zero-padding for inactive ones.
        Replay buffer is NOT cleared — old transitions remain valid.
        """
        new_depts = [d for d in departments if d not in self.actors]
        if new_depts:
            for dept in new_depts:
                actor = ActorNetwork(self.obs_dim, self.action_dim).to(self.device)
                target_actor = copy.deepcopy(actor)
                self.actors[dept] = actor
                self.target_actors[dept] = target_actor
                self.actor_optimizers[dept] = optim.Adam(
                    actor.parameters(), lr=1e-4
                )
                self.noise_processes[dept] = OUNoise(self.action_dim)

        self.department_names = list(departments)
        self.n_agents = len(departments)

        # Reset noise for exploration in new stage
        for dept in departments:
            if dept in self.noise_processes:
                self.noise_processes[dept].reset()
                self.noise_processes[dept].sigma = 0.2  # Reset exploration

        # Critic stays — sized for 14 agents, zero-padded for inactive
        # Replay buffer stays — old transitions still valid with zero-padding
        logger.info("Curriculum advance: %d departments active, critic preserved", self.n_agents)
