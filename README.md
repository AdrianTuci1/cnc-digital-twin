# CNC Digital Twin — Enterprise Serverless

https://github.com/user-attachments/assets/3f3ad091-1ff2-4093-8480-c95788d2b55a

**Demo video:** the full loop, 2 min 30 s, 1280×720, 6.3 MB. The repository copy lives in
[`docs/assets/cnc-mujoco-demo.mp4`](docs/assets/cnc-mujoco-demo.mp4); the original recording
stays in `~/Movies/cnc-mujoco.mov`, untouched. Compression details:
[`docs/assets/README.md`](docs/assets/README.md).

An industrial digital twin of a CNC machining and quality-inspection cell. The physics
simulation runs locally in MuJoCo on the CPU: two Franka arms, a CNC machine, two conveyors,
a rack with one compartment per part family, and a reject bin. Telemetry and inspection frames
go to AWS, two ML models score the stream, CloudWatch watches the line, and the verdict comes
back as a physical routing decision. All AWS infrastructure is defined in Terraform, and the
whole cycle from an empty account to a running demo and back is a handful of `make` targets.

The system demonstrates a closed loop:

1. **Simulation** — the MuJoCo cell runs the full material flow and publishes telemetry plus
   inspection frames.
2. **Ingestion** — MQTT over mTLS lands in IoT Core, then Firehose, then the S3 data lake.
3. **Hot path** — Lambda computes features per microbatch and calls the SageMaker endpoints.
4. **Monitoring and alerts** — CloudWatch aggregates metrics and alarms; SNS sends email.
5. **Command back** — the QA verdict selects the rack or the reject bin, while the operator
   injects wear and repairs it from the local panel and watches the next cycles in CloudWatch.

## Architecture

![Data and control loop: simulation → AWS → action in the simulation](docs/diagrams/architecture-loop.png)

The diagrams are generated from code (`docs/diagrams/architecture.py`, with `make diagrams`):
[data and control loop](docs/diagrams/architecture-loop.png) and
[platform](docs/diagrams/architecture-platform.png).

```text
  1. SIMULATION    MuJoCo twin, local (viewer + Tk panel, CPU)
                   two Panda arms, CNC, two conveyors, family rack, reject bin
        │  MQTT (mTLS): telemetry ── S3: QA frames
        ▼
  2. INGESTION     AWS IoT Core ──▶ Amazon Data Firehose (60 s microbatch)
                   ──▶ S3 data lake (partitioned Parquet, KMS, lifecycle)
        │  Lambda transform on every microbatch
        ▼
  3. HOT PATH      Lambda feature engineering ──▶ endpoint ML1 (anomalies)
                   Lambda qa-vision (S3 event) ──▶ endpoint ML2 (vision)
        │  metrics + verdict
        ▼
  4. MONITORING    CloudWatch: metrics, alarms, composite alarm, dashboards
  5. NOTIFICATIONS SNS ──▶ email (alarm_email_endpoints subscriptions)
        │  REJECT verdict / operator command
        ▼
  6. COMMAND       SQS ──▶ control-dispatcher ──▶ IoT command topic
                   Local panel ──▶ wear / repair in the running twin

  OFFLINE (model build):
  SageMaker Studio + Data Wrangler ──▶ training ──▶ model.tar.gz in S3
                                    ──▶ ML1 / ML2 endpoints on the hot path
```

### Stages and where they live

| Stage | What it does | Terraform resources | Command |
| --- | --- | --- | --- |
| 0. Access | Operator role assumed from the admin identity; resources appear in CloudTrail as role sessions | `stacks/access` | `make access-up` |
| 1. Simulation | Local MuJoCo cell on the CPU: viewer, wear/repair panel, six part families, telemetry and QA frames | `simulation/mujoco/` | `make mj-view` |
| 2. Ingestion | IoT Core, topic rule, Firehose, S3 data lake, Glue as schema registry | `modules/iot-ingestion`, `modules/platform-storage` | `make platform-up` |
| 3. Model build | Studio, Data Wrangler, training, endpoints | `modules/sagemaker-studio`, `modules/sagemaker-endpoint`, `ml/` | `make platform-up` |
| 4. Hot path | Feature engineering, QA inference, control dispatcher | `lambdas/`, `modules/lambda-function`, SQS | `make platform-up` |
| 5. Monitoring | Metrics, alarms, composite alarm, dashboards, cost budget | `modules/observability`, `modules/budget` | `make platform-up` |
| 6. Notifications | SNS topic with email subscriptions, budget alerts | `modules/observability` (`alarm_email_endpoints`), `modules/budget` | `make platform-up` |
| 7. Training data | Corpus bucket and the Modal federated role that writes to it | `stacks/training-data` | `make training-data-up` |
| 8. Hygiene and cost | Monthly budget, daily EFS backup, leftover-resource audit | `modules/budget`, `modules/backup`, `scripts/audit.sh` | `make audit` |

Details: [`docs/01-architecture.md`](docs/01-architecture.md) and
[`docs/11-geaman-mujoco.md`](docs/11-geaman-mujoco.md).

## Full startup and teardown

The operator role is created once per account. After that, every resource is launched by a
session of that role and appears in CloudTrail as a role action, not a personal one.

```bash
cp .env.example .env              # set AWS_REGION, PROJECT_NAME
aws login --region eu-central-1   # or a profile with an access key
make access-up                    # once per account: creates the role, as the admin identity
```

In every work session, export the role credentials into the shell; Terraform and the twin
read them from the environment:

```bash
eval "$(./scripts/assume-operator.sh)"
aws sts get-caller-identity       # arn:aws:sts::<account>:assumed-role/cncdt-dev-operator/...
```

MFA, session duration and revocation: [`ops/runbooks/access.md`](ops/runbooks/access.md).

Then bring up the AWS platform and the local twin:

```bash
make platform-up                  # IoT Core, Firehose, Lambda, S3, CloudWatch, SageMaker
make mj-venv                      # once: dedicated venv for the MuJoCo twin
make mj-check                     # scene gate: compiles, measures, fails on a broken cell
make mj-view                      # viewer with the cell cycle and the wear panel
```

To feed the pipeline from the twin, add the flags:

```bash
make mj-view MJ_VIEW_ARGS="--demo --iot"                  # telemetry to IoT Core
make mj-view MJ_VIEW_ARGS="--demo --iot --route vision"   # sort with the vision endpoint
```

The vision route calls the endpoint through the AWS CLI, since the twin's venv has no
`boto3`; keep the operator credentials in the shell for it.

Before teardown, `make audit` shows what will remain in the account. The Studio home EFS is
retained by the domain on destroy, and the backup plan in `modules/backup` saves it daily.

```bash
make platform-down                # destroys the platform (asks for confirmation)
make audit                        # Terraform drift, leftover resources, current cost
```

The Terraform state bucket is not deleted by `make platform-down`: it belongs to the
`bootstrap` stack and survives on purpose, so the platform can be recreated immediately.
Cleanup details: [`ops/runbooks/audit.md`](ops/runbooks/audit.md).

## Commands by stage

### MuJoCo twin (local, CPU)

| Command | Effect |
| --- | --- |
| `make mj-venv` | Creates `simulation/mujoco/.venv` and installs the twin's requirements. |
| `make mj-check` | Compiles the scene, measures it, and exits non-zero if any check fails. |
| `make mj-demo` | Runs the arm cycle offscreen for every part family and prints each stop. |
| `make mj-view` | Opens the viewer with the cell cycle and the wear/repair panel. `MJ_VIEW_ARGS` adds `--iot` and `--route vision`. |
| `make mj-record` | Writes training sessions to `simulation/mujoco/runs/` (ignored by git). |
| `make mj-test` | Runs the twin's engine tests from its own venv. |
| `make mj-collect` | Collects a session and uploads it under `training-data/mujoco/`; needs `DATA_LAKE_BUCKET` and the operator role. |
| `make mj-collect-plan` | Prints what `mj-collect` would upload, without touching the bucket. |
| `make mj-modal-collect` | Runs the long collection on Modal, in parallel containers; needs the `modal` CLI. |

The viewer needs a display and, on macOS, `mjpython` (the `make` target uses it). The wear panel
is a separate Tk process, so `make mj-view` keeps working when Tk is missing.

### Serverless platform

| Command | Effect |
| --- | --- |
| `make platform-up` | Creates the state bucket if needed and applies the platform stack: IoT Core, Firehose, Lambda, S3, CloudWatch, SageMaker. |
| `make platform-plan` | Shows the changes without applying them. |
| `make platform-down` | Destroys all platform resources (asks for confirmation). |

For another environment: `make platform-up ENVIRONMENT=staging`.

### Hygiene, cost and backup

| Command | Effect |
| --- | --- |
| `make audit` | Full inventory: Terraform drift, tagged resources missing from state, leftover EFS, open Studio apps, endpoints, instances, budget. Exit 0 clean, 1 items to check, 2 partial audit. |
| `make platform-plan` | Also shows the differences from reality, not only from code. |

The monthly budget (`monthly_budget_usd`) notifies by email at 50% and 80% of actual spend
and at 100% of the forecast. The `backup-jobs-failed` alarm fires when the Studio EFS backup
did not succeed.

## Repository layout

| Path | Role |
| --- | --- |
| `docs/` | Architecture docs, guides, ADRs. Start with [`docs/README.md`](docs/README.md). |
| `infra/terraform/` | Infrastructure as code: `bootstrap/` for the state backend, `stacks/access/` for the operator role, reusable `modules/`, per-environment `stacks/`. |
| `simulation/mujoco/` | The MuJoCo twin: scene, wear model, control panel, telemetry, corpus export. |
| `simulation/extensions/` | Procedural part generator; its label manifest feeds the twin and the vision dataset. |
| `telemetry/` | Data contracts (JSON Schema). |
| `lambdas/` | Lambda functions: feature engineering, QA inference, control dispatcher. |
| `ml/` | SageMaker: offline feature engineering, training, inference, pipelines. |
| `tests/` | Unit, end-to-end and integration tests. |
| `ops/` | Operational runbooks. Start with [`ops/runbooks/access.md`](ops/runbooks/access.md) for credentials and [`ops/runbooks/audit.md`](ops/runbooks/audit.md) for cleanup. |
| `scripts/` | Local utilities (Terraform state backend, resource audit, operator credentials). |

## Environments

| Environment | Terraform stack | Purpose |
| --- | --- | --- |
| access | `infra/terraform/stacks/access` (local state) | The operator role assumed from the admin identity. Applied once per account. |
| training-data | `infra/terraform/stacks/training-data` (local state) | Corpus bucket and the Modal federated role. |
| dev | `infra/terraform/stacks/platform` + `envs/dev.tfvars` | Full pipeline, minimal cost, synthetic data. |
| staging | `infra/terraform/stacks/platform` + `envs/staging.tfvars` | Validation before production. |
| prod | `infra/terraform/stacks/platform` + `envs/prod.tfvars` | Final demonstration, provisioned endpoints. |

## Local requirements

`aws` CLI v2 with configured credentials, `terraform` >= 1.10 (for `use_lockfile`), `jq`,
`git`, `rsync`, and Python 3.11+ for the project venv. Graphviz is needed only to regenerate
the diagrams (`make diagrams`).

The MuJoCo twin runs on CPU and needs a display for the viewer. On macOS the viewer starts
through `mjpython`, which the `make` target already uses.

The simulation has no GPU line item. The recurring cost is the platform: serverless endpoints
bill per invocation, the Studio home EFS bills while it exists, and CloudWatch dashboards and
alarms have a small monthly charge. Estimates and the cost model:
[`docs/07-cost.md`](docs/07-cost.md).
