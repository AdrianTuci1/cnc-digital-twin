output "volume_id" {
  description = "ID-ul volumului de date, folosit de stack-ul `sim` pentru atașare."
  value       = aws_ebs_volume.data.id
}

output "availability_zone" {
  description = "Zona volumului; instanța trebuie să pornească în aceeași zonă."
  value       = aws_ebs_volume.data.availability_zone
}

output "size_gb" {
  description = "Mărimea volumului, în GB."
  value       = aws_ebs_volume.data.size
}

output "iops" {
  description = "IOPS provisionate."
  value       = aws_ebs_volume.data.iops
}

output "throughput" {
  description = "Debitul provisionat, în MiB/s."
  value       = aws_ebs_volume.data.throughput
}

output "snapshot_id" {
  description = "Snapshot-ul din care a fost creat volumul, dacă a fost o restaurare."
  value       = aws_ebs_volume.data.snapshot_id
}
