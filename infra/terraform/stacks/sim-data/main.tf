# Gardul de lansare (ops/runbooks/launch-guard.md). Nu creează nimic în AWS: doar refuză planul
# și aplicarea care ar crea sau ar extinde resurse plătite, cât timp `launch_confirmed` nu este
# `true`. Îl pune pe `true` doar `tools/twinctl/twinctl`, pe căile de lansare, după ce a verificat
# `INFRA_LAUNCH_ENABLED` din `.env`; verificat pe viu, precondiția NU se evaluează la `destroy`,
# deci ștergerea rămâne permisă. Acesta e nivelul care închide ocolirea prin `terraform apply`
# rulat direct din acest director, de aceea mesajul conține comenzile complete de rulat.
resource "terraform_data" "launch_guard" {
  lifecycle {
    precondition {
      condition     = var.launch_confirmed
      error_message = <<-EOT
        Crearea volumului de date este blocată deliberat: `launch_confirmed` este `false`.

        Volumul EBS persistent al simulării se plătește lunar, și când nu rulează nicio instanță,
        deci nu se creează fără o confirmare explicită. Refuzul apare înainte de orice apel la AWS,
        deci NU este o eroare de credențiale, de profil sau de rețea.

        Ca să lansezi deliberat:
          pune în .env:  INFRA_LAUNCH_ENABLED=true
          apoi rulează:  make sim-data-up    (sau ./tools/twinctl/twinctl data-up)

        Ori, direct din acest director:
          terraform apply -var launch_confirmed=true

        Ștergerea nu este afectată: `make sim-data-down` face întâi un snapshot și apoi șterge
        volumul, iar `terraform destroy` rămâne permis, cu sau fără `-var launch_confirmed=true`.
        Detalii: ops/runbooks/launch-guard.md
      EOT
    }
  }
}

locals {
  common_tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      Repository  = "cnc-digital-twin"
      ManagedBy   = "terraform"
    },
    var.tags,
  )

  name = "${var.project_name}-${var.environment}-sim-data"
}

# Volumul de date al simulării: ține tot ce merită păstrat între instanțe (oglinda de conținut
# Isaac Sim, cache-urile de shader, codul simulării, asset-urile). Instanța spot este efemeră și
# se termină la întrerupere; volumul rămâne și se reatașează la fiecare `make sim-up`.
#
# Volumul stă în stack separat, cu state separat, tocmai ca `make sim-down` (care șterge tot
# stack-ul `sim`) să nu îl atingă. Singura cale prin care dispare este `make sim-data-down`,
# care cere confirmare tastată și face întâi un snapshot. Snapshot-urile se fac tot manual, cu
# `make sim-data-snapshot`, iar dacă volumul lipsește, `make sim-up` îl recreează reluând
# automat cel mai nou snapshot.
resource "aws_ebs_volume" "data" {
  availability_zone = var.availability_zone
  size              = var.size_gb
  type              = "gp3"
  iops              = var.iops
  throughput        = var.throughput
  encrypted         = true
  kms_key_id        = var.kms_key_id == "" ? null : var.kms_key_id

  # Doar la refacerea dintr-un snapshot: schimbarea valorii înlocuiește volumul, deci se lasă
  # gol în afară de o restaurare deliberată dintr-un snapshot anume. Gol fiind, twinctl reia
  # automat cel mai nou snapshot al volumului, ca o recreare să nu piardă starea.
  snapshot_id = var.snapshot_id == "" ? null : var.snapshot_id

  tags = merge(local.common_tags, { Name = local.name })
}
