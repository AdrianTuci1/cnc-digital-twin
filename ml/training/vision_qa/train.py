"""Clasificator multi-task pe imaginile de piese, în script-mode SageMaker.

Intrarea este canalul `train`: directorul rulării de piese din S3, cu `images/`,
`images-train.jsonl`, `images-val.jsonl`, `images-test.jsonl`. Splitul vine pe `part_id`,
deci aceeași piesă nu apare în două seturi.

Modelul are două capete peste același ResNet18:

- **familia** (`family`, șase clase) — alege compartimentul din dulap;
- **verdictul** (`verdict`, PASS/REJECT) — decide dacă piesa ajunge în cutie ori la rebut.

`defect` și `seed` rămân în index doar pentru rapoarte, niciodată în intrarea modelului.

Ieșirea în `/opt/ml/model`: `model.pth` (greutăți, arhitectură, familii, prag),
`metrics.json`, plus codul de inferență în `code/`.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torch import nn
from torch.utils.data import DataLoader, Dataset
from torchvision import transforms
from torchvision.models import ResNet18_Weights, resnet18
from vision_model import MultiHeadNet

CLASSES = ("PASS", "REJECT")
REJECT = 1


def hyper(name: str, default: str) -> str:
    """Hiperparametrul din mediu, cu rezerve pentru `SM_HPS` și argumentele din linia de comandă."""
    value = os.environ.get("SM_HP_" + name.upper())
    if value is not None:
        return value
    raw = os.environ.get("SM_HPS")
    if raw:
        parsed = json.loads(raw)
        if name in parsed:
            return str(parsed[name])
    args = sys.argv[1:]
    if f"--{name}" in args:
        return args[args.index(f"--{name}") + 1]
    return str(default)


def env_int(name: str, default: int) -> int:
    return int(hyper(name, str(default)))


def env_float(name: str, default: float) -> float:
    return float(hyper(name, str(default)))


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def read_index(root: Path, name: str) -> list[dict]:
    path = root / name
    if not path.is_file():
        raise SystemExit(f"lipsește {path}; canalul trebuie să conțină indexul rulării")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def family_names(rows: list[dict]) -> tuple[str, ...]:
    """Familiile, în ordine stabilă, ca indicii din checkpoint să însemne același lucru."""
    return tuple(sorted({str(row["family"]) for row in rows}))


class PartsDataset(Dataset):
    def __init__(self, rows: list[dict], root: Path, transform, families: tuple[str, ...]) -> None:
        self.rows = rows
        self.root = root
        self.transform = transform
        self.family_index = {name: index for index, name in enumerate(families)}

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int):
        row = self.rows[index]
        image = Image.open(self.root / row["path"]).convert("RGB")
        verdict = REJECT if row["verdict"] == "REJECT" else 0
        return self.transform(image), self.family_index[str(row["family"])], verdict


def build_transforms(size: int):
    train = transforms.Compose([
        transforms.RandomResizedCrop(size, scale=(0.65, 1.0)),
        transforms.RandomHorizontalFlip(),
        transforms.RandomRotation(8),
        transforms.ColorJitter(brightness=0.15, contrast=0.15, saturation=0.1),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])
    val = transforms.Compose([
        transforms.Resize((size, size)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])
    return train, val


def predict(model, loader, device) -> dict[str, np.ndarray]:
    """Probabilitățile ambelor capete, în ordinea din loader."""
    model.eval()
    verdict_probs: list[float] = []
    verdict_labels: list[int] = []
    family_probs: list[np.ndarray] = []
    family_labels: list[int] = []
    with torch.no_grad():
        for images, families, verdicts in loader:
            family_logits, verdict_logits = model(images.to(device))
            family_probs.append(torch.softmax(family_logits, dim=1).cpu().numpy())
            verdict_probs.extend(
                torch.softmax(verdict_logits, dim=1)[:, REJECT].cpu().tolist())
            family_labels.extend(families.tolist())
            verdict_labels.extend(verdicts.tolist())
    return {
        "verdict_probs": np.asarray(verdict_probs),
        "verdict_labels": np.asarray(verdict_labels, dtype=int),
        "family_probs": (np.concatenate(family_probs) if family_probs
                         else np.zeros((0, 0), dtype=float)),
        "family_labels": np.asarray(family_labels, dtype=int),
    }


def report(probs: np.ndarray, labels: np.ndarray, threshold: float) -> dict:
    predicted = probs >= threshold
    truth = labels == REJECT
    tp = int((predicted & truth).sum())
    fn = int((~predicted & truth).sum())
    fp = int((predicted & ~truth).sum())
    tn = int((~predicted & ~truth).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall_reject = tp / (tp + fn) if tp + fn else 0.0
    recall_pass = tn / (tn + fp) if tn + fp else 0.0
    f1 = 2 * precision * recall_reject / (precision + recall_reject) if precision + recall_reject else 0.0
    return {
        "threshold": round(float(threshold), 4),
        "confusion": [[tn, fp], [fn, tp]],
        "precision_reject": round(precision, 4),
        "recall_reject": round(recall_reject, 4),
        "recall_pass": round(recall_pass, 4),
        "f1_reject": round(f1, 4),
        "balanced_accuracy": round((recall_reject + recall_pass) / 2, 4),
    }


def family_report(probs: np.ndarray, labels: np.ndarray,
                  families: tuple[str, ...]) -> dict:
    """Acuratețea capului de familie, cu matricea de confuzie și recall pe fiecare familie."""
    if len(labels) == 0:
        return {"accuracy": 0.0, "macro_recall": 0.0, "confusion": [],
                "recall_by_family": {}}
    predicted = probs.argmax(axis=1)
    size = len(families)
    confusion = [
        [int(((labels == actual) & (predicted == called)).sum()) for called in range(size)]
        for actual in range(size)
    ]
    recalls: dict[str, float | None] = {}
    for index, name in enumerate(families):
        support = int((labels == index).sum())
        recalls[name] = (round(float((predicted[labels == index] == index).mean()), 4)
                         if support else None)
    present = [value for value in recalls.values() if value is not None]
    return {
        "accuracy": round(float((predicted == labels).mean()), 4),
        "macro_recall": round(float(np.mean(present)) if present else 0.0, 4),
        "confusion": confusion,
        "recall_by_family": recalls,
    }


def choose_threshold(probs: np.ndarray, labels: np.ndarray, min_pass_recall: float) -> float:
    """Pragul care maximizează recall-ul pe REJECT cu recall pe PASS peste limita cerută."""
    best_t, best_recall = 0.5, -1.0
    for candidate in np.arange(0.05, 0.96, 0.01):
        predicted = probs >= candidate
        truth = labels == REJECT
        recall_pass = float(((~predicted) & ~truth).sum()) / max(int((~truth).sum()), 1)
        recall_reject = float((predicted & truth).sum()) / max(int(truth.sum()), 1)
        if recall_pass >= min_pass_recall and recall_reject > best_recall:
            best_t, best_recall = float(candidate), recall_reject
    if best_recall < 0:
        best_t = 0.5
    return best_t


def grouped_recall(rows: list[dict], probs: np.ndarray, threshold: float, key: str) -> dict:
    buckets: dict[str, list[tuple[float, str]]] = {}
    for row, prob in zip(rows, probs, strict=True):
        buckets.setdefault(row.get(key, ""), []).append((prob, row["verdict"]))
    result = {}
    for name, items in sorted(buckets.items()):
        reject = [p for p, verdict in items if verdict == "REJECT"]
        passed = [p for p, verdict in items if verdict == "PASS"]
        result[name] = {
            "images": len(items),
            "recall_reject": round(float(np.mean([p >= threshold for p in reject])), 4) if reject else None,
            "recall_pass": round(float(np.mean([p < threshold for p in passed])), 4) if passed else None,
        }
    return result


def copy_inference_code(model_dir: Path) -> None:
    """Pune contractul de inferență în ``code/``, ca artefactul să fie deployabil.

    Containerul de inferență încarcă exact ``code/inference.py`` din ``model.tar.gz``; fără el,
    antrenarea reușește, dar endpoint-ul nu are cum să citească ``model.pth``.
    """
    for name in ("inference.py", "vision_inference_core.py", "vision_model.py"):
        source = Path(__file__).with_name(name)
        if not source.is_file():
            print(f"atenție: lipsește {name}; artefactul nu va fi deployabil", flush=True)
            continue
        target = model_dir / "code" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def main() -> None:
    seed_everything(env_int("SEED", 42))
    epochs = env_int("EPOCHS", 10)
    batch_size = env_int("BATCH_SIZE", 64)
    lr = env_float("LR", 3e-4)
    size = env_int("IMAGE_SIZE", 224)
    workers = env_int("WORKERS", 4)
    min_pass_recall = env_float("MIN_PASS_RECALL", 0.90)
    family_weight = env_float("FAMILY_LOSS_WEIGHT", 0.5)

    channel = Path(os.environ.get("SM_CHANNEL_TRAIN", "/opt/ml/input/data/train"))
    model_dir = Path(os.environ.get("SM_MODEL_DIR", "/opt/ml/model"))
    model_dir.mkdir(parents=True, exist_ok=True)

    train_rows = read_index(channel, "images-train.jsonl")
    val_rows = read_index(channel, "images-val.jsonl")
    test_rows = read_index(channel, "images-test.jsonl")
    families = family_names(train_rows)
    unknown = {str(row["family"]) for row in [*val_rows, *test_rows]} - set(families)
    if unknown:
        raise SystemExit(f"familii în val/test care lipsesc din train: {sorted(unknown)}")
    print(f"imagini: train {len(train_rows)}, val {len(val_rows)}, test {len(test_rows)}; "
          f"familii: {', '.join(families)}", flush=True)

    train_transform, val_transform = build_transforms(size)
    train_loader = DataLoader(
        PartsDataset(train_rows, channel, train_transform, families),
        batch_size=batch_size, shuffle=True, num_workers=workers,
        pin_memory=True, drop_last=True)
    val_loader = DataLoader(
        PartsDataset(val_rows, channel, val_transform, families),
        batch_size=batch_size, num_workers=workers, pin_memory=True)
    test_loader = DataLoader(
        PartsDataset(test_rows, channel, val_transform, families),
        batch_size=batch_size, num_workers=workers, pin_memory=True)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    try:
        backbone = resnet18(weights=ResNet18_Weights.IMAGENET1K_V1)
    except Exception as error:  # fără internet, pornim de la greutăți aleatoare
        print(f"greutăți preantrenate indisponibile: {error}", flush=True)
        backbone = resnet18(weights=None)
    model = MultiHeadNet(len(families), len(CLASSES), backbone=backbone).to(device)

    # Canalul opțional `init` pornește antrenarea din greutățile unui model deja antrenat.
    # Este folosit pentru adaptarea la camera celulei: familiile rămîn aceleași, deci capul de
    # familie nu se rescrie de la zero, iar modelul nu uită formele învățate pe randări.
    init_dir = os.environ.get("SM_CHANNEL_INIT")
    if init_dir:
        # SageMaker desface arhiva canalului într-un subdirector propriu, după cum a fost
        # încărcată; căutarea recursivă evită să depindă de felul în care a fost împachetată.
        start = Path(init_dir)
        checkpoint = start / "model.pth"
        if not checkpoint.is_file():
            matches = sorted(start.rglob("model.pth"))
            if not matches:
                raise SystemExit(f"canalul init nu conține model.pth: {start}")
            checkpoint = matches[0]
        initial = torch.load(checkpoint, map_location="cpu", weights_only=False)
        if tuple(initial.get("families") or ()) != tuple(families):
            raise SystemExit(
                f"modelul de pornire are alte familii: {initial.get('families')}; "
                f"antrenarea are {list(families)}")
        model.load_state_dict(initial["state_dict"])
        print(f"pornire din {checkpoint}", flush=True)

    counts = np.bincount([REJECT if row["verdict"] == "REJECT" else 0 for row in train_rows],
                         minlength=len(CLASSES))
    class_weights = torch.tensor(counts.sum() / (len(CLASSES) * np.maximum(counts, 1)),
                                 dtype=torch.float32, device=device)
    print(f"clase train: {counts.tolist()}, greutăți: {class_weights.tolist()}", flush=True)

    verdict_criterion = nn.CrossEntropyLoss(weight=class_weights)
    family_criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

    best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
    best_score = -1.0
    best_epoch = 0
    history = []

    for epoch in range(1, epochs + 1):
        model.train()
        running = 0.0
        seen = 0
        for images, family_labels, verdict_labels in train_loader:
            images = images.to(device)
            family_labels = family_labels.to(device)
            verdict_labels = verdict_labels.to(device)
            optimizer.zero_grad(set_to_none=True)
            family_logits, verdict_logits = model(images)
            loss = (verdict_criterion(verdict_logits, verdict_labels)
                    + family_weight * family_criterion(family_logits, family_labels))
            loss.backward()
            optimizer.step()
            running += float(loss.item()) * len(images)
            seen += len(images)
        scheduler.step()

        val = predict(model, val_loader, device)
        verdict_metrics = report(val["verdict_probs"], val["verdict_labels"], 0.5)
        family_metrics = family_report(val["family_probs"], val["family_labels"], families)
        score = verdict_metrics["balanced_accuracy"] + family_metrics["macro_recall"]
        history.append({
            "epoch": epoch,
            "train_loss": round(running / max(seen, 1), 4),
            "val_balanced_accuracy": verdict_metrics["balanced_accuracy"],
            "val_recall_reject": verdict_metrics["recall_reject"],
            "val_recall_pass": verdict_metrics["recall_pass"],
            "val_family_accuracy": family_metrics["accuracy"],
            "val_family_macro_recall": family_metrics["macro_recall"],
        })
        print(json.dumps(history[-1]), flush=True)
        if score > best_score:
            best_score = score
            best_epoch = epoch
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}

    model.load_state_dict(best_state)
    val = predict(model, val_loader, device)
    threshold = choose_threshold(val["verdict_probs"], val["verdict_labels"], min_pass_recall)
    test = predict(model, test_loader, device)

    metrics = {
        "arch": "resnet18",
        "heads": ["family", "verdict"],
        "classes": list(CLASSES),
        "families": list(families),
        "image_size": size,
        "epochs": epochs,
        "batch_size": batch_size,
        "learning_rate": lr,
        "family_loss_weight": family_weight,
        "best_epoch": best_epoch,
        "threshold": round(float(threshold), 4),
        "minimum_pass_recall": min_pass_recall,
        "train_images": len(train_rows),
        "val_images": len(val_rows),
        "test_images": len(test_rows),
        "history": history,
        "val": report(val["verdict_probs"], val["verdict_labels"], threshold),
        "val_at_half": report(val["verdict_probs"], val["verdict_labels"], 0.5),
        "test": report(test["verdict_probs"], test["verdict_labels"], threshold),
        "val_family": family_report(val["family_probs"], val["family_labels"], families),
        "test_family": family_report(test["family_probs"], test["family_labels"], families),
        "test_by_family": grouped_recall(test_rows, test["verdict_probs"], threshold, "family"),
        "test_by_defect": grouped_recall(test_rows, test["verdict_probs"], threshold, "defect"),
    }
    torch.save({
        "state_dict": model.state_dict(),
        "arch": "resnet18",
        "heads": ["family", "verdict"],
        "classes": list(CLASSES),
        "families": list(families),
        "threshold": float(threshold),
        "image_size": size,
    }, model_dir / "model.pth")
    (model_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    copy_inference_code(model_dir)
    print(json.dumps({k: metrics[k] for k in ("best_epoch", "threshold", "test", "test_family")},
                     ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
