# App 03: Hospital Operations DES-MARL

## Objective
Optimize hospital staffing and patient flow using a hybrid Discrete Event Simulation (DES) engine coupled with Multi-Agent Reinforcement Learning (MADDPG). Agents manage 8 departments to minimize wait times and maximize throughput.

## Architecture
- **DES Engine**: Priority queue-based event simulation with Poisson arrivals and log-normal service times
- **MARL**: MADDPG with 8 department agents, 12-dim observation, 4-dim continuous action
- **Curriculum Learning**: 5 stages from single-department to full complexity

## Departments
ED, ED Observation, Medicine, Med/Surg, Cardiology, Neurology, ICU, Discharge Lounge

## Quick Start
```bash
# Build patient flow dataset from MIMIC transfers
python -m app_03_hospital_ops.backend.data.build_dataset

# Train MARL agents
python -m app_03_hospital_ops.backend.models.train

# Start API
python -m uvicorn app_03_hospital_ops.backend.app.main:app --port 8203
```

## API: http://localhost:8203/docs
