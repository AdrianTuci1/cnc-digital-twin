"""Arhitectura multi-task a clasificatorului de piese: familie și verdict.

Un singur backbone ResNet18 alimentează două capete: unul cu șase clase (familia piesei,
folosită la alegerea compartimentului din dulap) și unul binar (PASS/REJECT, folosit la
decizia dintre cutie și rebut). Antrenarea și inferența construiesc aceeași clasă, ca
``state_dict``-ul din ``model.pth`` să se încarce fără mapări.

Modulul are nevoie de ``torch`` și ``torchvision``; contractul pur rămâne în
``vision_inference_core``.
"""

from __future__ import annotations

import torch
from torch import nn
from torchvision.models import resnet18


class MultiHeadNet(nn.Module):
    """ResNet18 fără clasificatorul ImageNet, cu două capete liniare."""

    def __init__(self, families: int, classes: int = 2,
                 backbone: nn.Module | None = None) -> None:
        super().__init__()
        base = backbone or resnet18(weights=None)
        features = base.fc.in_features
        base.fc = nn.Identity()
        self.backbone = base
        self.family_head = nn.Linear(features, families)
        self.verdict_head = nn.Linear(features, classes)

    def forward(self, images: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        features = self.backbone(images)
        return self.family_head(features), self.verdict_head(features)
