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
