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


def batched_actor_forward(
    actors: List["ActorNetwork"],
    obs: torch.Tensor,
) -> torch.Tensor:
    """Run N actors over their own observations in one batched pass.

    ``obs`` has shape (N, B, obs_dim); the result is (N, B, action_dim),
    where row i is ``actors[i](obs[i])``.

    Every department has its own actor, so the natural implementation is a
    Python loop of N small forward passes. Each pass is ~10 kernels over
    tensors of a few kilobytes, which on a GPU costs far more in launch
    latency than in arithmetic — at 14 agents that is ~140 launches per
    call, and this is called twice per update (current actors and target
    actors) plus once per environment step.

    Stacking the weights turns each layer into one ``bmm``. The stack is
    built with ``torch.stack`` rather than ``stack_module_state`` so it
    stays part of the autograd graph and gradients flow back to each
    actor's own parameters exactly as they would from a separate pass.
    """
    w1 = torch.stack([a.fc1.weight for a in actors])
    b1 = torch.stack([a.fc1.bias for a in actors])
    n1w = torch.stack([a.ln1.weight for a in actors])
    n1b = torch.stack([a.ln1.bias for a in actors])
    w2 = torch.stack([a.fc2.weight for a in actors])
    b2 = torch.stack([a.fc2.bias for a in actors])
    n2w = torch.stack([a.ln2.weight for a in actors])
    n2b = torch.stack([a.ln2.bias for a in actors])
    w3 = torch.stack([a.fc3.weight for a in actors])
    b3 = torch.stack([a.fc3.bias for a in actors])

    hidden = w1.shape[1]
    x = torch.baddbmm(b1.unsqueeze(1), obs, w1.transpose(1, 2))
    x = F.layer_norm(x, (hidden,)) * n1w.unsqueeze(1) + n1b.unsqueeze(1)
    x = F.relu(x)
    x = torch.baddbmm(b2.unsqueeze(1), x, w2.transpose(1, 2))
    x = F.layer_norm(x, (hidden,)) * n2w.unsqueeze(1) + n2b.unsqueeze(1)
    x = F.relu(x)
    raw = torch.tanh(torch.baddbmm(b3.unsqueeze(1), x, w3.transpose(1, 2)))

    low = actors[0].action_low.to(raw.device)
    high = actors[0].action_high.to(raw.device)
    return low + (raw + 1.0) * 0.5 * (high - low)


def clip_grads_per_actor(
    actors: List["ActorNetwork"],
    max_norm: float,
) -> None:
    """Clip each actor's gradients to ``max_norm``, independently, batched.

    Same arithmetic as calling ``clip_grad_norm_`` once per actor, but the
    norms for every actor are computed in a single ``_foreach_norm`` and the
    rescaling in a single ``_foreach_mul_`` — ~8 kernels per actor collapsed
    into a handful for all of them.
    """
    grouped = [[p.grad for p in a.parameters() if p.grad is not None] for a in actors]
    flat = [g for group in grouped for g in group]
    if not flat:
        return

    norms = torch._foreach_norm(flat)
    scales = []
    idx = 0
    for group in grouped:
        if not group:
            continue
        sq = torch.stack(norms[idx: idx + len(group)]).pow(2).sum()
        total = sq.sqrt()
        # Matches clip_grad_norm_: scale by max_norm/(total + 1e-6), capped
        # at 1.0 so gradients under the threshold are left untouched.
        coef = (max_norm / (total + 1e-6)).clamp(max=1.0)
        scales.extend([coef] * len(group))
        idx += len(group)
    torch._foreach_mul_(flat, scales)


class CriticNetwork(nn.Module):
    """Centralized critic over the joint state-action.

    Architecture: (obs_dim*N + action_dim*N) -> 128 -> 64 -> n_heads

    ``n_heads=1`` is the original shared-value critic. ``n_heads=n_agents``
    gives each department its own Q head over the same joint input, which is
    what makes per-agent credit assignment possible: head i is trained
    against department i's own reward, so actor i's loss reflects what it
    did to its own ward rather than to a 14-way average.

    The shared-value form was measurably unlearnable here. Its target was the
    *mean* reward across departments, so one agent's staffing decision moved
    the signal by ~1/14 and was swamped by the other thirteen; the
    2026-08-21 evaluation found actors saturating at the action bounds with
    no improvement across 600 episodes of any curriculum stage, and a
    retrained policy still worse than taking no action at all.
    """

    def __init__(
        self,
        n_agents: int,
        obs_dim: int = 12,
        action_dim: int = 4,
        hidden_dim: int = 128,
        n_heads: int = 1,
    ) -> None:
        super().__init__()
        input_dim = n_agents * (obs_dim + action_dim)
        self.n_heads = n_heads
        self.fc1 = nn.Linear(input_dim, hidden_dim)
        self.ln1 = nn.LayerNorm(hidden_dim)
        self.fc2 = nn.Linear(hidden_dim, hidden_dim // 2)
        self.ln2 = nn.LayerNorm(hidden_dim // 2)
        self.fc3 = nn.Linear(hidden_dim // 2, n_heads)

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
    """Defines a curriculum learning stage.

    ``target_reward`` is compared against the mean **per-step** reward over
    the recent window, not the episode sum. It used to be compared against
    the episode sum, which silently broke when the reward function was
    rescaled on 2026-05-27: the old reward had an uncapped
    ``0.3 * new_served`` throughput bonus and produced episode sums around
    +385, comfortably above the +2.0 gate, so the curriculum advanced. The
    capped reward produces episode sums around -170, so *no* stage target
    was reachable any more and training would spend all 2000 episodes on
    stage 1 — leaving 9 of the 10 department actors at random
    initialisation. Per-step keeps the gate stable across future rescales.

    ``max_episodes`` is a hard fallback so a stage can never consume the
    whole budget even if its target is never met. Curriculum completion is
    the point; hitting a reward bar early is a bonus.
    """
    name: str
    departments: List[str]
    min_episodes: int
    target_reward: float = -5.0
    max_episodes: int = 0          # 0 -> 3 x min_episodes, applied by the trainer
    description: str = ""


DEFAULT_CURRICULUM: List[CurriculumStage] = [
    CurriculumStage(
        name="stage_1_ed",
        departments=["ED"],
        min_episodes=100,
        target_reward=-0.9,        # per-step; ED alone plateaus near -1.0
        max_episodes=250,
        description="Single department: learn basic staffing in ED",
    ),
    CurriculumStage(
        name="stage_2_core",
        departments=["ED", "MAU", "Medicine", "ICU"],
        min_episodes=200,
        target_reward=-1.2,        # per-step, averaged over 4 departments
        max_episodes=450,
        description="Core flow: ED -> assessment -> inpatient -> critical care",
    ),
    CurriculumStage(
        name="stage_3_extended",
        departments=["ED", "MAU", "SAU", "Medicine", "Surgery", "ICU", "Discharge_Lounge"],
        min_episodes=300,
        target_reward=-1.4,        # per-step, averaged over 7 departments
        max_episodes=600,
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
        target_reward=-1.6,        # per-step, averaged over 14 departments
        max_episodes=100_000,      # last stage: use the remaining budget
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
        per_agent_critic: bool = False,
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

        self.actor_lr = actor_lr
        for dept in department_names:
            actor = ActorNetwork(obs_dim, action_dim).to(self.device)
            target_actor = copy.deepcopy(actor)
            self.actors[dept] = actor
            self.target_actors[dept] = target_actor
            self.actor_optimizers[dept] = optim.Adam(actor.parameters(), lr=actor_lr)
            self.noise_processes[dept] = OUNoise(action_dim)

        # One optimizer covering every actor, with a param group per actor.
        #
        # Adam's state is per-parameter and the actors' parameter sets are
        # disjoint, so a single optimizer produces bit-identical updates to
        # the n_agents separate ones — but it steps in one fused call rather
        # than n_agents calls, which was 25 % of update() time on GPU.
        # ``self.actor_optimizers`` is retained because the checkpoint format
        # stores per-actor optimizer state; the two are kept in sync by
        # sharing the same parameter objects and by the state translation in
        # save_checkpoint / load_checkpoint.
        self._rebuild_actor_optimizer()

        # Critic network (shared, centralized) — always sized for max 14 agents
        # so it doesn't need rebuilding during curriculum stage transitions
        self._max_agents = 14
        # One Q head per department when per-agent credit assignment is on;
        # a single shared head reproduces the original behaviour.
        self.per_agent_critic = per_agent_critic
        n_heads = self._max_agents if per_agent_critic else 1
        self.critic = CriticNetwork(
            self._max_agents, obs_dim, action_dim, n_heads=n_heads,
        ).to(self.device)
        self.target_critic = copy.deepcopy(self.critic)
        self.critic_optimizer = optim.Adam(self.critic.parameters(), lr=critic_lr)

        # Replay buffer
        self.replay_buffer = ReplayBuffer(capacity=buffer_capacity)

        # Training state
        self.training_step = 0
        self.episodes_completed = 0

    def _rebuild_actor_optimizer(self) -> None:
        """(Re)create the fused actor optimizer, one param group per actor.

        Called at construction and whenever a curriculum stage introduces new
        actors. Any Adam moment state already accumulated for existing
        parameters is carried across, keyed by the parameter objects
        themselves, so advancing a stage never silently resets the optimizer
        for the departments that were already training.
        """
        old_state = getattr(self, "_actor_optimizer", None)
        carried: Dict[torch.Tensor, Any] = {}
        if old_state is not None:
            for group in old_state.param_groups:
                for p in group["params"]:
                    if p in old_state.state:
                        carried[p] = old_state.state[p]

        groups = [
            {"params": list(self.actors[d].parameters()), "lr": self.actor_lr}
            for d in self.department_names
        ]
        self._actor_optimizer = optim.Adam(groups, lr=self.actor_lr)
        for p, st in carried.items():
            self._actor_optimizer.state[p] = st

    def _actor_param_index(self) -> Dict[str, List[torch.Tensor]]:
        """Department -> its parameter tensors, in a stable order."""
        return {d: list(self.actors[d].parameters()) for d in self.department_names}

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

        present = [d for d in self.department_names if observations.get(d) is not None]
        for dept in self.department_names:
            if observations.get(dept) is None:
                actions[dept] = np.zeros(self.action_dim, dtype=np.float32)
        if not present:
            return actions

        # One host-to-device copy for every department's observation, one
        # device-to-host copy for every department's action. The previous
        # loop did a separate round trip per department — 28 transfers per
        # environment step at 14 agents, each carrying 48 bytes, so the call
        # was pure latency. The forward passes still run per department
        # because each has its own actor weights, but they now queue back to
        # back without a synchronising copy between them.
        obs_batch = np.stack([observations[d] for d in present]).astype(np.float32)
        obs_t = torch.from_numpy(obs_batch).to(self.device, non_blocking=True)

        with torch.no_grad():
            out = batched_actor_forward(
                [self.actors[d] for d in present], obs_t.unsqueeze(1),
            ).squeeze(1)
        out_np = out.cpu().numpy()

        low = np.array([-3.0, -5.0, 0.0, 0.0], dtype=np.float32)
        high = np.array([3.0, 5.0, 1.0, 1.0], dtype=np.float32)
        for i, dept in enumerate(present):
            action = out_np[i]
            if explore:
                action = action + self.noise_processes[dept].sample().astype(np.float32)
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

    def update(self, return_losses: bool = False) -> Dict[str, float]:
        """Perform one training update step.

        Parameters
        ----------
        return_losses:
            Read the loss scalars back to the host. Every ``.item()`` is a
            device synchronisation that stalls the CUDA pipeline, and this
            method produced ``n_agents + 1`` of them per call — on a 14-agent
            stage that is 15 syncs per environment step, for numbers the
            trainer only logs every 50 steps. Off by default; the trainer
            asks for them on logging steps.

        Returns
        -------
        Dict of loss values for logging (empty unless ``return_losses``).
        """
        if len(self.replay_buffer) < self.batch_size:
            return {}

        batch = self.replay_buffer.sample(self.batch_size)
        losses: Dict[str, float] = {}
        n_dep = len(self.department_names)
        bsz = len(batch)

        # Prepare batch tensors.
        #
        # Built as three contiguous numpy arrays and moved to the device in
        # three transfers rather than 3 x n_agents small ones. At 14 agents
        # that is 42 host-to-device copies per update collapsed into 3, each
        # of which was individually far too small to saturate the bus.
        obs_np = np.zeros((n_dep, bsz, self.obs_dim), dtype=np.float32)
        act_np = np.zeros((n_dep, bsz, self.action_dim), dtype=np.float32)
        next_obs_np = np.zeros((n_dep, bsz, self.obs_dim), dtype=np.float32)
        for i, dept in enumerate(self.department_names):
            o = [e.obs.get(dept) for e in batch]
            a = [e.actions.get(dept) for e in batch]
            no = [e.next_obs.get(dept) for e in batch]
            for j in range(bsz):
                if o[j] is not None:
                    obs_np[i, j] = o[j]
                if a[j] is not None:
                    act_np[i, j] = a[j]
                if no[j] is not None:
                    next_obs_np[i, j] = no[j]

        obs_t = torch.from_numpy(obs_np).to(self.device, non_blocking=True)
        act_t = torch.from_numpy(act_np).to(self.device, non_blocking=True)
        next_obs_t = torch.from_numpy(next_obs_np).to(self.device, non_blocking=True)

        batch_obs = {d: obs_t[i] for i, d in enumerate(self.department_names)}
        batch_actions = {d: act_t[i] for i, d in enumerate(self.department_names)}
        batch_next_obs = {d: next_obs_t[i] for i, d in enumerate(self.department_names)}

        # Concatenate all observations and actions, zero-padded to max 14 agents
        obs_parts = [batch_obs[d] for d in self.department_names]
        act_parts = [batch_actions[d] for d in self.department_names]
        next_obs_parts = [batch_next_obs[d] for d in self.department_names]

        # Pad to _max_agents width so critic input dimension is always the same
        pad_count = self._max_agents - len(self.department_names)
        if pad_count > 0:
            zero_obs = torch.zeros(bsz, self.obs_dim, device=self.device)
            zero_act = torch.zeros(bsz, self.action_dim, device=self.device)
            obs_parts.extend([zero_obs] * pad_count)
            act_parts.extend([zero_act] * pad_count)
            next_obs_parts.extend([zero_obs] * pad_count)

        all_obs = torch.cat(obs_parts, dim=-1)
        all_actions = torch.cat(act_parts, dim=-1)
        all_next_obs = torch.cat(next_obs_parts, dim=-1)

        # Target actions for next state — one batched pass over every
        # target actor rather than n_agents separate forwards.
        with torch.no_grad():
            tgt = batched_actor_forward(
                [self.target_actors[d] for d in self.department_names], next_obs_t,
            )
            target_next_actions = [tgt[i] for i in range(n_dep)]
            # Pad target actions too
            if pad_count > 0:
                target_next_actions.extend([torch.zeros(bsz, self.action_dim, device=self.device)] * pad_count)
            all_target_next_actions = torch.cat(target_next_actions, dim=-1)

        # --- Update critic ---
        with torch.no_grad():
            target_q = self.target_critic(all_next_obs, all_target_next_actions)

        batch_dones = torch.FloatTensor([
            float(any(exp.dones.get(d, False) for d in self.department_names))
            for exp in batch
        ]).unsqueeze(1).to(self.device)

        if self.per_agent_critic:
            # Each head is regressed on its *own* department's reward, so the
            # gradient reaching actor i is about ward i rather than a 14-way
            # average that its own contribution barely moves.
            rew_np = np.zeros((bsz, self._max_agents), dtype=np.float32)
            for i, dept in enumerate(self.department_names):
                for j, exp in enumerate(batch):
                    rew_np[j, i] = exp.rewards.get(dept, 0.0)
            batch_rewards = torch.from_numpy(rew_np).to(self.device)
            target_value = batch_rewards + self.gamma * (1 - batch_dones) * target_q
            current_q = self.critic(all_obs, all_actions)
            # Padded heads carry no department, so they must not contribute
            # to the loss or they drag the shared trunk toward zero.
            mask = torch.zeros(self._max_agents, device=self.device)
            mask[:n_dep] = 1.0
            critic_loss = (((current_q - target_value) ** 2) * mask).sum() / (bsz * n_dep)
        else:
            # Original behaviour: one shared value regressed on the mean
            # reward across departments.
            batch_rewards = torch.FloatTensor([
                np.mean([exp.rewards.get(d, 0.0) for d in self.department_names])
                for exp in batch
            ]).unsqueeze(1).to(self.device)
            target_value = batch_rewards + self.gamma * (1 - batch_dones) * target_q
            current_q = self.critic(all_obs, all_actions)
            critic_loss = F.mse_loss(current_q, target_value)

        self.critic_optimizer.zero_grad()
        critic_loss.backward()
        torch.nn.utils.clip_grad_norm_(self.critic.parameters(), 0.5)
        self.critic_optimizer.step()

        # --- Update actors ---
        #
        # One critic pass for all agents instead of n_agents passes.
        #
        # The per-agent loss is -Q(s, (a_i = pi_i(o_i), a_{-i} = replay)),
        # so each agent needs its own joint-action vector. Those vectors are
        # stacked into a single (n_agents * batch) frame and pushed through
        # the critic once. Because each actor's parameters appear in exactly
        # one block, the gradient of the summed loss with respect to actor i
        # is identical to the gradient of loss i alone — the maths is
        # unchanged, but 14 forward+backward passes over the critic become
        # one, and 14 small kernels become one large one. This is where the
        # GPU time was going: the nets are small enough that per-launch
        # overhead dominated the arithmetic.
        obs_rep = all_obs.detach().repeat(n_dep, 1)

        cur_stacked = batched_actor_forward(
            [self.actors[d] for d in self.department_names], obs_t,
        )
        cur_actions = [cur_stacked[i] for i in range(n_dep)]
        # Off-agent slots carry the *replay buffer* actions, as canonical
        # MADDPG requires — not the other actors' current outputs.
        buf_actions = [batch_actions[d].detach() for d in self.department_names]

        blocks = []
        for i in range(n_dep):
            parts = [cur_actions[i] if j == i else buf_actions[j] for j in range(n_dep)]
            if pad_count > 0:
                parts.extend([zero_act] * pad_count)
            blocks.append(torch.cat(parts, dim=-1))
        all_current_actions = torch.cat(blocks, dim=0)

        q = self.critic(obs_rep, all_current_actions)
        if self.per_agent_critic:
            # Block i holds agent i's joint action; take agent i's own head
            # from it, i.e. the diagonal over (block, head).
            q = q.view(n_dep, bsz, self._max_agents)
            ar = torch.arange(n_dep, device=q.device)
            own_q = q[ar, :, ar]                      # (n_dep, bsz)
        else:
            own_q = q.view(n_dep, bsz)
        # Mean within each agent's block, then sum across agents, so every
        # actor sees exactly the gradient scale it saw when updated alone.
        per_agent_loss = -own_q.mean(dim=1)
        actor_loss_total = per_agent_loss.sum()

        actor_list = [self.actors[d] for d in self.department_names]
        self._actor_optimizer.zero_grad(set_to_none=True)
        actor_loss_total.backward()
        clip_grads_per_actor(actor_list, 0.5)
        self._actor_optimizer.step()
        # The critic accumulated gradients from the actor backward. It is
        # zeroed at the top of the next critic update, but clear it here too
        # so a caller that inspects critic grads never sees actor leakage.
        self.critic_optimizer.zero_grad(set_to_none=True)

        if return_losses:
            losses["critic_loss"] = critic_loss.item()
            per_agent_cpu = per_agent_loss.detach().cpu()
            for i, dept in enumerate(self.department_names):
                losses[f"actor_loss_{dept}"] = float(per_agent_cpu[i])

        # Soft target updates
        self._soft_update()
        self.training_step += 1

        return losses

    def _soft_update(self) -> None:
        """Soft update target networks.

        Uses the fused multi-tensor ops. The parameter-at-a-time version
        issued three kernels per tensor across every actor and the critic —
        roughly 90 launches per update on a 14-agent stage, each moving a
        few kilobytes. ``_foreach_*`` performs the same arithmetic
        (``target = tau * param + (1 - tau) * target``) over the whole list
        in a handful of launches.
        """
        params: List[torch.Tensor] = []
        targets: List[torch.Tensor] = []
        for dept in self.department_names:
            for t_p, p in zip(
                self.target_actors[dept].parameters(),
                self.actors[dept].parameters(),
            ):
                targets.append(t_p.data)
                params.append(p.data)
        for t_p, p in zip(self.target_critic.parameters(), self.critic.parameters()):
            targets.append(t_p.data)
            params.append(p.data)

        if not targets:
            return
        torch._foreach_mul_(targets, 1.0 - self.tau)
        torch._foreach_add_(targets, params, alpha=self.tau)

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
            "per_agent_critic": self.per_agent_critic,
            "critic_state": self.critic.state_dict(),
            "target_critic_state": self.target_critic.state_dict(),
            "critic_optimizer_state": self.critic_optimizer.state_dict(),
            "actors": {},
            "target_actors": {},
            "actor_optimizers": {},
        }
        # Actor optimizer state now lives in one fused optimizer, but the
        # on-disk format is per-actor. Split it back out so checkpoints stay
        # readable by anything expecting the original layout.
        fused = self._actor_optimizer
        for dept in self.department_names:
            checkpoint["actors"][dept] = self.actors[dept].state_dict()
            checkpoint["target_actors"][dept] = self.target_actors[dept].state_dict()
            params = list(self.actors[dept].parameters())
            checkpoint["actor_optimizers"][dept] = {
                "state": {
                    i: fused.state[p] for i, p in enumerate(params) if p in fused.state
                },
                "param_groups": [{
                    k: v for k, v in fused.param_groups[0].items() if k != "params"
                } | {"params": list(range(len(params)))}],
            }

        torch.save(checkpoint, path)
        logger.info(f"Checkpoint saved to {path}")

    def load_checkpoint(self, path: str) -> None:
        """Load networks and optimizer states from checkpoint."""
        checkpoint = torch.load(path, map_location=self.device, weights_only=False)

        self.training_step = checkpoint["training_step"]
        self.episodes_completed = checkpoint["episodes_completed"]

        # A checkpoint records which critic form it was trained with. Adopt
        # it, so a caller that constructed the agent with the default (shared
        # value) can still load a per-agent-critic checkpoint and vice versa
        # — the live service builds the agent without knowing either way.
        saved_per_agent = bool(checkpoint.get("per_agent_critic", False))
        if saved_per_agent != self.per_agent_critic:
            logger.info(
                "Checkpoint uses %s critic; rebuilding to match.",
                "per-agent" if saved_per_agent else "shared-value",
            )
            self.per_agent_critic = saved_per_agent
            n_heads = self._max_agents if saved_per_agent else 1
            self.critic = CriticNetwork(
                self._max_agents, self.obs_dim, self.action_dim, n_heads=n_heads,
            ).to(self.device)
            self.target_critic = copy.deepcopy(self.critic)
            self.critic_optimizer = optim.Adam(
                self.critic.parameters(), lr=self.critic_optimizer.param_groups[0]["lr"],
            )

        # Critic state is only needed to resume training; inference uses the
        # actors alone. Never let a critic mismatch stop the actors loading.
        try:
            self.critic.load_state_dict(checkpoint["critic_state"])
            self.target_critic.load_state_dict(checkpoint["target_critic_state"])
            self.critic_optimizer.load_state_dict(checkpoint["critic_optimizer_state"])
        except (RuntimeError, ValueError, KeyError) as exc:
            logger.warning(
                "Could not restore critic from %s (%s); actors still load, but "
                "resuming training from this checkpoint would restart the critic.",
                path, exc,
            )

        for dept in self.department_names:
            if dept in checkpoint["actors"]:
                self.actors[dept].load_state_dict(checkpoint["actors"][dept])
                self.target_actors[dept].load_state_dict(checkpoint["target_actors"][dept])

        # Rebuild the fused optimizer against the freshly loaded parameters,
        # then re-attach each actor's saved Adam moments by position. Missing
        # or malformed per-actor state is skipped rather than fatal — a
        # checkpoint is still useful for inference without optimizer moments.
        self._rebuild_actor_optimizer()
        saved_opt = checkpoint.get("actor_optimizers") or {}
        restored = 0
        for dept in self.department_names:
            entry = saved_opt.get(dept)
            if not entry or "state" not in entry:
                continue
            params = list(self.actors[dept].parameters())
            for idx, st in entry["state"].items():
                i = int(idx)
                if i < len(params):
                    self._actor_optimizer.state[params[i]] = {
                        k: (v.to(self.device) if isinstance(v, torch.Tensor) else v)
                        for k, v in st.items()
                    }
                    restored += 1
        if saved_opt and not restored:
            logger.warning(
                "Checkpoint %s carried no usable actor optimizer state; "
                "actors will train from fresh Adam moments.", path,
            )

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

        # The fused actor optimizer covers exactly the active departments,
        # so it has to be rebuilt whenever the stage changes. Adam moments
        # for departments that were already training are carried across.
        self._rebuild_actor_optimizer()

        # Reset noise for exploration in new stage
        for dept in departments:
            if dept in self.noise_processes:
                self.noise_processes[dept].reset()
                self.noise_processes[dept].sigma = 0.2  # Reset exploration

        # Critic stays — sized for 14 agents, zero-padded for inactive
        # Replay buffer stays — old transitions still valid with zero-padding
        logger.info("Curriculum advance: %d departments active, critic preserved", self.n_agents)
