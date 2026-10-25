"""Contractul de inferență SageMaker pentru clasificatorul de piese.

Containerul este ``pytorch-inference`` pe CPU. ``model.tar.gz`` trebuie să conțină
``model.pth`` (scris de ``ml/training/vision_qa/train.py``) și acest fișier sub ``code/``.
Cererea și răspunsul sunt cele din ``qa_vision_core``: imaginile pleacă inline, în base64,
iar răspunsul păstrează ordinea cadrelor.

Răspunsul are două capete: ``defect_probability`` (PASS/REJECT) și ``family`` cu
``family_probability`` (compartimentul din dulap). Un cadru fără imagine rămâne
``judged: false`` și nu primește familie.

Fără ``torch`` aici nu se poate face nimic; partea pură, testabilă, este în
``vision_inference_core``.
"""

from __future__ import annotations

import io
import json
import os
from collections.abc import Mapping, Sequence
from typing import Any

import torch
import vision_inference_core as core
from PIL import Image
from torchvision import transforms
from vision_model import MultiHeadNet

CLASSES = ("PASS", "REJECT")
REJECT = 1


def model_fn(model_dir: str) -> dict[str, Any]:
    """Încarcă greutățile și reconstruiește arhitectura cu două capete."""
    bundle = torch.load(
        os.path.join(model_dir, "model.pth"), map_location="cpu", weights_only=False
    )
    classes = tuple(bundle.get("classes") or CLASSES)
    families = tuple(bundle.get("families") or ())
    if not families:
        raise ValueError("model.pth nu conține lista familiilor; reantrenează cu cap de familie")
    model = MultiHeadNet(len(families), len(classes))
    model.load_state_dict(bundle["state_dict"])
    model.eval()
    size = int(bundle.get("image_size", 224))
    transform = transforms.Compose([
        transforms.Resize((size, size)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])
    return {"model": model, "transform": transform, "classes": classes,
            "families": families, "threshold": float(bundle.get("threshold", 0.5))}


def input_fn(input_data: Any, content_type: str = "application/json") -> list[dict[str, Any]]:
    """Decodifică intrările și fotografiile; un cadru fără poză rămâne fără imagine."""
    instances = core.parse_instances(input_data)
    rows: list[dict[str, Any]] = []
    for instance in instances:
        raw = core.image_bytes(instance)
        image = Image.open(io.BytesIO(raw)).convert("RGB") if raw else None
        rows.append({"part_id": str(instance.get("part_id", "")), "image": image})
    return rows


def predict_fn(rows: Sequence[Mapping[str, Any]], bundle: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Verdictul și familia fiecărui cadru, în ordinea cererii."""
    model = bundle["model"]
    transform = bundle["transform"]
    classes = tuple(bundle["classes"])
    families = tuple(bundle["families"])
    reject = classes.index("REJECT") if "REJECT" in classes else len(classes) - 1

    pending = [(index, row["image"]) for index, row in enumerate(rows) if row["image"] is not None]
    probabilities: list[float | None] = [None] * len(rows)
    family_predictions: list[tuple[str, float] | None] = [None] * len(rows)
    if pending:
        batch = torch.stack([transform(image) for _, image in pending])
        with torch.no_grad():
            family_logits, verdict_logits = model(batch)
            verdict_probs = torch.softmax(verdict_logits, dim=1)[:, reject].tolist()
            family_probs = torch.softmax(family_logits, dim=1)
            top_probs, top_indices = family_probs.max(dim=1)
        for (index, _), probability, family_index, family_probability in zip(
                pending, verdict_probs, top_indices.tolist(), top_probs.tolist(), strict=True):
            probabilities[index] = float(probability)
            family_predictions[index] = (families[family_index], float(family_probability))

    predictions: list[dict[str, Any]] = []
    for row, probability, family in zip(rows, probabilities, family_predictions, strict=True):
        if probability is None or family is None:
            predictions.append(core.prediction(row["part_id"], core.UNJUDGED_PROBABILITY,
                                               judged=False))
        else:
            predictions.append(core.prediction(row["part_id"], probability,
                                               family=family[0], family_probability=family[1]))
    return predictions


def output_fn(predictions: Sequence[Mapping[str, Any]], accept: str = "application/json") -> str:
    """Răspunsul JSON; Lambda-ul citește ``defect_probability`` din fiecare element."""
    return json.dumps(core.response(predictions), ensure_ascii=False)
