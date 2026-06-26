import os
import socket
import subprocess
import sys
import time

ROOT = '.'
COMPOSE_FILE = os.path.join(ROOT, 'docker-compose.kafka.yml')
DASHBOARD_DIR = os.path.join(ROOT, 'dashboard')
KAFKA_BOOTSTRAP = 'localhost:19092'

# Optional companion stacks. Each tuple is
# (compose_file, "host:port" probe, env-vars-to-export-when-up).
# If the compose file isn't running, we silently skip — the corresponding
# observability feature degrades to local-stdout only.
OBSERVABILITY = [
    ('docker-compose.otel.yml', ('localhost', 4337), {
        'OTEL_EXPORTER_OTLP_ENDPOINT': 'http://localhost:4337',
        'OTEL_EXPORTER_OTLP_PROTOCOL': 'grpc',
    }),
    ('docker-compose.loki.yml', ('localhost', 3100), {
        # Base URL only — shared/integration/logging_config.py appends
        # /loki/api/v1/push itself (don't double-suffix).
        'LOKI_URL': 'http://localhost:3100',
    }),
    ('docker-compose.redis.yml', ('localhost', 6379), {
        'REDIS_URL': 'redis://localhost:6379',
    }),
    ('docker-compose.grafana.yml', ('localhost', 9091), {}),  # passive — Prom scrapes services
]


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

services = [
    ('app_01_ed_triage.backend.app.main:app', '8201'),
    ('app_02_sepsis_icu.backend.app.main:app', '8202'),
    ('app_03_hospital_ops.backend.app.main:app', '8203'),
    ('app_04_oncology_ai.backend.app.main:app', '8204'),
    ('app_05_patient_journey.backend.api.main:app', '8205'),
    ('app_06_clinical_chat.backend.main:app', '8206'),
    ('app_07_data_ingestion.backend.api.main:app', '8207'),
    ('app_08_bed_management.backend.app.main:app', '8208'),
    ('app_09_waiting_list.backend.app.main:app', '8209'),
    ('app_10_clinical_scribe.backend.app.main:app', '8210'),
    ('app_14_ed_flow.backend.app.main:app', '8214'),
    ('app_15_erp.backend.app.main:app', '8215'),
    # Phase D — new services per MedAI Engineering Uplift Part 4
    ('app_16_trolley_watch.backend.app.main:app', '8216'),
    ('app_17_gdpr.backend.app.main:app', '8217'),
    ('app_18_xai.backend.app.main:app', '8218'),
    ('app_19_fhir.backend.app.main:app', '8219'),
    ('app_20_deterioration.backend.app.main:app', '8220'),
    ('app_21_discharge_lounge.backend.app.main:app', '8221'),
    ('app_22_alerts.backend.app.main:app', '8222'),
]


def start_kafka():
    if _port_open('localhost', 19092):
        print(f'Redpanda already healthy on {KAFKA_BOOTSTRAP} — skipping docker compose start.')
        return True
    print('Starting Redpanda (Kafka) via docker compose...')
    rc = subprocess.call(
        ['docker', 'compose', '-p', 'cancer', '-f', COMPOSE_FILE, 'up', '-d', '--wait'],
        cwd=ROOT,
    )
    if rc != 0:
        print(f'WARNING: docker compose returned {rc}. Services will fall back to MongoDB event_log.')
        return False
    print(f'Redpanda healthy on {KAFKA_BOOTSTRAP} (console: http://localhost:18080).')
    return True


def start_services(env):
    procs = []
    for mod, port in services:
        print(f'Starting {mod} on port {port}...')
        p = subprocess.Popen(
            [sys.executable, '-m', 'uvicorn', mod, '--host', '0.0.0.0', '--port', port],
            cwd=ROOT,
            env=env,
        )
        procs.append(p)
    print(f'All {len(services)} API services launched.')
    return procs


def start_dashboard():
    print('Starting dashboard (Vite) on http://localhost:3010 ...')
    # npm on Windows is npm.cmd — shell=True resolves it via PATHEXT.
    return subprocess.Popen(
        'npm run dev',
        cwd=DASHBOARD_DIR,
        shell=True,
    )


def detect_observability(env):
    """For each optional stack, if its host:port is reachable, set the
    corresponding env vars so backend services pick up the integration.
    Skip silently when the container isn't running."""
    for compose_file, (host, port), extra_env in OBSERVABILITY:
        if _port_open(host, port):
            for k, v in extra_env.items():
                env[k] = v
                print(f'  observability: {compose_file} reachable -> {k}={v}')
        else:
            print(f'  observability: {compose_file} not reachable on {host}:{port} (skipping)')


if __name__ == '__main__':
    kafka_up = start_kafka()

    env = os.environ.copy()
    if kafka_up:
        env['KAFKA_BOOTSTRAP'] = KAFKA_BOOTSTRAP

    print('Probing observability stack...')
    detect_observability(env)

    start_services(env)
    start_dashboard()

    print('Stack up. Ctrl+C to stop API/dashboard processes (Redpanda keeps running).')
    try:
        time.sleep(999999)
    except KeyboardInterrupt:
        print('Shutting down.')
