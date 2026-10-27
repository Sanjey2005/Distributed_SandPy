# Distributed SandPy

A job farm for running Python. A dispatcher takes code-execution jobs and spreads them across a pool of Jupyter workers. Redis holds the queue, decides which worker gets each job, and picks which dispatcher is in charge.

> **Where this started.** Each worker is built on [anukriti-ranjan/sandboxed-jupyter-code-exec](https://github.com/anukriti-ranjan/sandboxed-jupyter-code-exec), a single-machine FastAPI server that gives every user their own Jupyter kernel. The brief was to turn that into a distributed system ([`Distributed Code Sandbox Upgrade.pdf`](Distributed%20Code%20Sandbox%20Upgrade.pdf)). The dispatcher, the Redis coordination, the multi-worker setup, the dashboard and the AI tooling were built by [@Sanjey2005](https://github.com/Sanjey2005) and [@Shyam-GK](https://github.com/Shyam-GK).

## Architecture

```
                     ┌──────────────┐
  client ──────────► │    nginx     │  (optional: two dispatchers behind one port)
                     └──────┬───────┘
                ┌───────────┴───────────┐
         ┌──────▼──────┐         ┌──────▼──────┐
         │ dispatcher  │         │ dispatcher  │   FastAPI
         └──────┬──────┘         └──────┬──────┘
                │   ┌───────────────┐   │
                ├──►│     Redis     │◄──┤   job queues, worker counts,
                │   └───────────────┘   │   sessions, leader lock
       ┌────────┼────────────────┐
  ┌────▼────┐ ┌─▼───────┐ ┌──────▼──┐
  │ worker1 │ │ worker2 │ │ worker3 │   one Jupyter kernel per user session
  └─────────┘ └─────────┘ └─────────┘
```

## How a job moves through it

1. **`POST /submit`.** The code goes through a pattern scanner that blocks things like `os.system`, `subprocess` and `eval`, then a daily quota check (100 jobs per day for regular users).
2. **Same user, same worker.** If you already have a session on a worker, your job goes back to that worker. The mapping lives in Redis (`session:{user_id}`, 1 hour TTL).
3. **Otherwise, the least-busy worker.** A Lua script runs inside Redis, finds the worker with the fewest running jobs and increments its count in one atomic step. Two dispatchers can never claim the same slot. Each worker runs one job at a time.
4. **Dead workers are skipped.** A worker that fails its `/health` check gets its claim released, and the dispatcher tries the next one.
5. **Nothing free? Queue it.** The job goes on a Redis list. Jobs from `admin` and `premium` users go on a separate high-priority list that is drained first.
6. **One dispatcher drains the queue.** You can run several dispatchers behind nginx, and any of them can accept jobs. Only the leader pulls from the queue. Leadership is a Redis key taken with `SET NX EX 10` and renewed on every loop, so if the leader dies, another dispatcher takes over within about 10 seconds.
7. **Results stream back.** Status, stdout, images, HTML and Plotly output are saved in a Redis hash (`job:{job_id}`) and pushed live over `WS /ws/job/{job_id}`.

## Also in here

- **Workers** (`src/`): per-session kernels with package installs, reset, run history and `.ipynb` export.
- **File workspace**: uploads are split into byte shards across the healthy workers.
- **Services**: deploy a script as a long-running web app on a worker. One service runs per worker, and extra requests wait in a queue.
- **AI tools**: generate code, explain an error, review code, or race several models on the same prompt. Supported providers are OpenAI, Anthropic, Gemini, Groq and Ollama. There is also an agent loop (planner → coder → evaluator → debugger) that runs its own code on a worker and retries when it fails.
- **Dashboard** (`frontend/`): Next.js with a Monaco editor, worker status, the agent loop and service deploys.

## Run it locally

You need Docker and Python 3.10+. LLM keys are optional; you only need them for the AI features.

```bash
cp .env.example .env
docker compose up --build -d          # Redis + 3 workers on ports 5002-5004
```

Then start the dispatcher in another terminal:

```bash
cd dispatcher
pip install -r requirements.txt
REDIS_URL=redis://localhost:6379 \
WORKER1_URL=http://localhost:5002 \
WORKER2_URL=http://localhost:5003 \
WORKER3_URL=http://localhost:5004 \
uvicorn main:app --port 8000
```

Send it a job:

```bash
curl -s -X POST localhost:8000/submit \
  -H 'Content-Type: application/json' \
  -d '{"user_id": "demo", "code": "x = 41\nprint(x + 1)"}'

curl -s localhost:8000/job/<job_id>     # status and output
curl -s localhost:8000/cluster/status   # workers, queue depth, dispatcher load
```

Dashboard: `cd frontend && npm install && npm run dev`, then open http://localhost:3000.

## Not done yet

This started as a learning project. These parts are unfinished:

- Variables don't carry over between jobs yet. Jobs land on the right worker, but the dispatcher calls the worker's `/start_session` before every job, and that restarts the user's kernel. The worker's `/execute` already starts a session when there isn't one, so dropping that call should fix it.
- `docker compose up` doesn't start the dispatcher or nginx yet, so you start the dispatcher by hand as shown above. `nginx.conf` expects two dispatchers named `dispatcher-1` and `dispatcher-2`.
- The dashboard talks to the workers directly. It doesn't go through the dispatcher yet.
- `sandpy_dist.map()` and `POST /api/map` call a worker endpoint (`/api/map_chunk`) that doesn't exist yet, so MapReduce doesn't work.
- The danger scanner only matches substrings and is easy to get around. The container is the real boundary, and it has no seccomp profile, resource limits or network restrictions. Don't expose this to the internet.
- Passwords are hashed with salted SHA-256. Switch to bcrypt or argon2, and set `JWT_SECRET`, before any real use.

## Layout

```
dispatcher/                 dispatcher: queueing, worker claims, leader election, auth, AI endpoints
src/                        worker: Jupyter kernels, file shards, services, agent loop
frontend/                   Next.js dashboard
docker-compose.yml          Redis + 3 workers
nginx.conf                  gateway for running two dispatchers
run_all_tests.py            end-to-end checks against a running cluster
fastapi_jupyter_server.py   the original single-machine server, kept for reference
```
