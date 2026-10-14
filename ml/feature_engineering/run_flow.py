#!/usr/bin/env python3
"""Rulează un flux Data Wrangler exportat, ca job de procesare SageMaker.

Fluxul se construiește vizual în SageMaker Studio (vezi ``README.md``) și se exportă ca
fișier ``.flow``. Fișierul se comite în ``flows/``, iar acest script îl urcă în S3 și pornește
un job de procesare cu imaginea Data Wrangler.

De ce un job de procesare și nu „Run" din Studio: jobul este reproductibil, are un nume, un
rol, un tip de instanță și un rezultat în S3. Poate fi chemat din CI sau dintr-un pipeline
SageMaker, fără ca cineva să deschidă interfața.

Exemple::

    # Verifică cererea fără să cheme AWS
    python3 run_flow.py --flow flows/predictive_maintenance.flow \\
        --bucket cncdt-dev-datalake-123456789012 --dry-run

    # Pornește jobul și așteaptă rezultatul
    python3 run_flow.py --flow flows/predictive_maintenance.flow \\
        --bucket cncdt-dev-datalake-123456789012 \\
        --role-arn arn:aws:iam::123456789012:role/cncdt-dev-studio-role \\
        --wait
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

FLOW_MOUNT = "/opt/ml/processing/flow"
OUTPUT_MOUNT = "/opt/ml/processing/output"
DEFAULT_INSTANCE_TYPE = "ml.m5.4xlarge"
DEFAULT_VOLUME_SIZE_GB = 100
DEFAULT_MAX_RUNTIME_SECONDS = 86400
DEFAULT_OUTPUT_PREFIX = "features/predictive_maintenance/v1"
FLOW_PREFIX = "flows"
POLL_SECONDS = 15


def log(message: str) -> None:
    print(f"[data-wrangler] {message}", flush=True)


def die(message: str, code: int = 2) -> None:
    print(f"[data-wrangler][eroare] {message}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def _boto3_client(service: str, region: str | None = None) -> Any:
    try:
        import boto3  # type: ignore
    except ImportError:
        die("boto3 lipsește. Instalați-l sau rulați într-un mediu AWS.")
    return boto3.client(service, region_name=region) if region else boto3.client(service)


def resolve_image_uri(explicit: str | None, region: str) -> str:
    """Găsește imaginea containerului Data Wrangler.

    Ordinea: argumentul explicit, variabila de mediu ``CNCDT_DATA_WRANGLER_IMAGE``, apoi
    SDK-ul SageMaker. Dacă niciuna nu reușește, scriptul cere imaginea explicit, în loc să
    ghicească un URI care se schimbă între regiuni și versiuni.
    """
    if explicit:
        return explicit
    env_value = os.environ.get("CNCDT_DATA_WRANGLER_IMAGE", "")
    if env_value:
        return env_value
    try:
        from sagemaker import image_uris  # type: ignore
    except ImportError:
        die(
            "nu pot rezolva imaginea Data Wrangler: instalați SDK-ul SageMaker sau "
            "transmiteți --image-uri (sau CNCDT_DATA_WRANGLER_IMAGE)"
        )
    try:
        return image_uris.retrieve(framework="data-wrangler", region=region, version="1.x")
    except Exception as exc:  # pragma: no cover - depinde de SDK și regiune
        die(f"imaginea Data Wrangler nu a putut fi rezolvată: {exc}. Transmiteți --image-uri.")


def upload_flow(flow: str, bucket: str, region: str | None) -> str:
    """Urcă fluxul local în S3 și întoarce URI-ul. Un URI S3 este lăsat neschimbat."""
    if flow.startswith("s3://"):
        return flow
    path = Path(flow)
    if not path.is_file():
        die(f"fluxul nu există: {path}")
    client = _boto3_client("s3", region)
    key = f"{FLOW_PREFIX}/{path.name}"
    log(f"urc {path} în s3://{bucket}/{key}")
    client.upload_file(str(path), bucket, key)
    return f"s3://{bucket}/{key}"


def build_request(
    *,
    job_name: str,
    role_arn: str,
    image_uri: str,
    flow_s3_uri: str,
    output_s3_uri: str,
    instance_type: str,
    volume_size_gb: int,
    max_runtime_seconds: int,
    output_content_type: str,
    tags: dict[str, str],
) -> dict[str, Any]:
    """Cererea ``create_processing_job`` pentru containerul Data Wrangler.

    Containerul citește fluxul din ``/opt/ml/processing/flow`` și scrie în
    ``/opt/ml/processing/output``. Destinația reală a datelor este înscrisă în fluxul însuși;
    ``output_s3_uri`` este locul unde jobul urcă ce a produs.
    """
    return {
        "ProcessingJobName": job_name,
        "RoleArn": role_arn,
        "AppSpecification": {
            "ImageUri": image_uri,
            "ContainerArguments": [
                "--output-config",
                json.dumps({"output_content_type": output_content_type}),
            ],
        },
        "ProcessingResources": {
            "ClusterConfig": {
                "InstanceCount": 1,
                "InstanceType": instance_type,
                "VolumeSizeInGB": volume_size_gb,
            }
        },
        "ProcessingInputs": [
            {
                "InputName": "flow",
                "S3Input": {
                    "S3Uri": flow_s3_uri,
                    "LocalPath": FLOW_MOUNT,
                    "S3DataType": "S3Prefix",
                    "S3InputMode": "File",
                },
            }
        ],
        "ProcessingOutputConfig": {
            "Outputs": [
                {
                    "OutputName": "features",
                    "S3Output": {
                        "S3Uri": output_s3_uri,
                        "LocalPath": OUTPUT_MOUNT,
                        "S3UploadMode": "EndOfJob",
                    },
                }
            ]
        },
        "StoppingCondition": {"MaxRuntimeInSeconds": max_runtime_seconds},
        "Tags": [{"Key": key, "Value": value} for key, value in tags.items()],
    }


def wait_for_job(client: Any, job_name: str, timeout_seconds: int) -> str:
    """Așteaptă terminarea jobului, cu raportare periodică a stării."""
    deadline = time.time() + timeout_seconds
    last_status = ""
    while time.time() < deadline:
        response = client.describe_processing_job(ProcessingJobName=job_name)
        status = response.get("ProcessingJobStatus", "Unknown")
        if status != last_status:
            log(f"stare job: {status}")
            last_status = status
        if status in {"Completed", "Failed", "Stopped"}:
            if status != "Completed":
                log(f"motiv: {response.get('FailureReason', 'nespecificat')}")
            return status
        time.sleep(POLL_SECONDS)
    return "Timeout"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rulează un flux Data Wrangler ca job de procesare.")
    parser.add_argument("--flow", required=True, help="Calea locală sau URI-ul S3 al fișierului .flow")
    parser.add_argument("--bucket", required=True, help="Bucket-ul data lake pentru flux și rezultate")
    parser.add_argument("--role-arn", default="", help="Rolul de execuție SageMaker")
    parser.add_argument("--image-uri", default="", help="Imaginea containerului Data Wrangler")
    parser.add_argument("--output-prefix", default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--instance-type", default=DEFAULT_INSTANCE_TYPE)
    parser.add_argument("--volume-size-gb", type=int, default=DEFAULT_VOLUME_SIZE_GB)
    parser.add_argument("--max-runtime-seconds", type=int, default=DEFAULT_MAX_RUNTIME_SECONDS)
    parser.add_argument("--output-content-type", choices=("Parquet", "CSV"), default="Parquet")
    parser.add_argument("--job-name", default="")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", ""))
    parser.add_argument("--project", default=os.environ.get("PROJECT_NAME", "cncdt"))
    parser.add_argument("--environment", default=os.environ.get("ENVIRONMENT", "dev"))
    parser.add_argument("--dry-run", action="store_true", help="Tipărește cererea, fără apeluri AWS")
    parser.add_argument("--wait", action="store_true", help="Așteaptă terminarea jobului")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    region = (
        args.region
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "eu-central-1"
    )
    job_name = args.job_name or f"{args.project}-{args.environment}-data-wrangler-{int(time.time())}"
    output_s3_uri = f"s3://{args.bucket}/{args.output_prefix.strip('/')}/"

    if args.dry_run:
        image_uri = args.image_uri or os.environ.get("CNCDT_DATA_WRANGLER_IMAGE", "<image-uri-din-mediu>")
        role_arn = args.role_arn or "<role-arn>"
        flow_uri = args.flow if args.flow.startswith("s3://") else f"s3://{args.bucket}/{FLOW_PREFIX}/{Path(args.flow).name}"
    else:
        image_uri = resolve_image_uri(args.image_uri or None, region)
        if not args.role_arn:
            die("--role-arn este obligatoriu (sau folosiți --dry-run)")
        flow_uri = upload_flow(args.flow, args.bucket, region)
        role_arn = args.role_arn

    tags = {"Project": args.project, "Environment": args.environment, "ManagedBy": "terraform"}

    request = build_request(
        job_name=job_name,
        role_arn=role_arn,
        image_uri=image_uri,
        flow_s3_uri=flow_uri,
        output_s3_uri=output_s3_uri,
        instance_type=args.instance_type,
        volume_size_gb=args.volume_size_gb,
        max_runtime_seconds=args.max_runtime_seconds,
        output_content_type=args.output_content_type,
        tags=tags,
    )

    if args.dry_run:
        print(json.dumps(request, indent=2, ensure_ascii=False))
        log("dry-run: cererea nu a fost trimisă")
        return 0

    client = _boto3_client("sagemaker", region)
    log(f"pornesc jobul {job_name} pe {args.instance_type}")
    client.create_processing_job(**request)
    log(f"job pornit. Urmărire: aws sagemaker describe-processing-job --processing-job-name {job_name}")

    if args.wait:
        status = wait_for_job(client, job_name, args.max_runtime_seconds)
        log(f"stare finală: {status}. Rezultat în {output_s3_uri}")
        return 0 if status == "Completed" else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
