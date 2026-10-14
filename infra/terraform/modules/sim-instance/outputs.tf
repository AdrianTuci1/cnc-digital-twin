output "instance_id" {
  description = "ID-ul instanței EC2 de simulare."
  value       = aws_instance.sim.id
}

output "instance_arn" {
  description = "ARN-ul instanței."
  value       = aws_instance.sim.arn
}

output "instance_type" {
  description = "Tipul instanței lansate."
  value       = aws_instance.sim.instance_type
}

output "ami_id" {
  description = "AMI-ul folosit efectiv. Util pentru a fixa versiunea în var.ami_id."
  value       = aws_instance.sim.ami
}

output "availability_zone" {
  description = "Zona de disponibilitate a instanței."
  value       = aws_instance.sim.availability_zone
}

output "public_ip" {
  description = "Adresa IP publică (Elastic IP dacă este alocat)."
  value       = var.allocate_eip ? aws_eip.sim[0].public_ip : aws_instance.sim.public_ip
}

output "private_ip" {
  description = "Adresa IP privată."
  value       = aws_instance.sim.private_ip
}

output "security_group_id" {
  description = "Security group-ul instanței."
  value       = aws_security_group.sim.id
}

output "iam_role_name" {
  description = "Rolul IAM atașat instanței."
  value       = aws_iam_role.sim.name
}

output "key_name" {
  description = "Numele cheii SSH atașate (gol dacă se folosește doar Session Manager)."
  value       = local.key_name
}

output "ssh_private_key_path" {
  description = "Calea locală a cheii private folosite pentru SSH."
  value       = var.ssh_key_public_key_path == "" ? "" : replace(pathexpand(var.ssh_key_public_key_path), ".pub", "")
}

output "signaling_port" {
  description = "Portul de semnalizare WebRTC."
  value       = var.signaling_port
}

output "stream_port" {
  description = "Portul de stream WebRTC."
  value       = var.stream_port
}

output "webrtc_url" {
  description = "Endpoint-ul de semnalizare al clientului nativ WebRTC (host:port). Isaac Sim 5.0 nu mai servește pagina /streaming/webrtc-client în container."
  value       = "${var.allocate_eip ? aws_eip.sim[0].public_ip : aws_instance.sim.public_ip}:${var.signaling_port}"
}

output "media_port_range" {
  description = "Intervalul UDP pentru media WebRTC, care trebuie deschis în firewall."
  value       = var.media_port_range
}

output "ssh_command" {
  description = "Comandă SSH gata de rulat (necesită portul 22 deschis)."
  value = local.key_name == null || local.key_name == "" ? "" : format(
    "ssh -i %s ubuntu@%s",
    replace(pathexpand(var.ssh_key_public_key_path), ".pub", ""),
    var.allocate_eip ? aws_eip.sim[0].public_ip : aws_instance.sim.public_ip,
  )
}

output "ssm_shell_command" {
  description = "Shell interactiv prin Session Manager, fără port SSH deschis."
  value       = "aws ssm start-session --target ${aws_instance.sim.id}"
}

output "ssm_tunnel_commands" {
  description = "Comenzi de port-forwarding prin Session Manager pentru porturile de streaming."
  value = [
    "aws ssm start-session --target ${aws_instance.sim.id} --document-name AWS-StartPortForwardingSession --parameters '{\"portNumber\":[\"${var.signaling_port}\"],\"localPortNumber\":[\"${var.signaling_port}\"]}'",
    "aws ssm start-session --target ${aws_instance.sim.id} --document-name AWS-StartPortForwardingSession --parameters '{\"portNumber\":[\"${var.stream_port}\"],\"localPortNumber\":[\"${var.stream_port}\"]}'",
  ]
}

output "bootstrap_log_command" {
  description = "Comandă pentru citirea logului de bootstrap prin SSM."
  value       = "aws ssm start-session --target ${aws_instance.sim.id} --document-name AWS-StartInteractiveCommand --parameters command='tail -f /var/log/cncdt-bootstrap.log'"
}

output "bootstrap_log_group" {
  description = "Log group-ul CloudWatch al instanței."
  value       = aws_cloudwatch_log_group.bootstrap.name
}

output "status_alarm_name" {
  description = "Alarma de status-check a instanței."
  value       = aws_cloudwatch_metric_alarm.status_check.alarm_name
}

output "turn_enabled" {
  description = "Dacă serverul TURN/STUN este activ pe instanță."
  value       = var.enable_turn
}

# ---------------------------------------------------------------- acces grafic (Amazon DCV)
output "dcv_enabled" {
  description = "Dacă desktopul GNOME și serverul DCV sunt instalate de bootstrap."
  value       = var.enable_dcv
}

output "dcv_port" {
  description = "Portul TCP al serverului DCV."
  value       = var.dcv_port
}

output "dcv_tunnel_command" {
  description = "Tunel SSH către interfața DCV din browser. Portul 8443 rămâne închis public."
  value = var.enable_dcv ? format(
    "ssh -i %s -L %d:localhost:%d -N ubuntu@%s",
    replace(pathexpand(var.ssh_key_public_key_path), ".pub", ""),
    var.dcv_port,
    var.dcv_port,
    var.allocate_eip ? aws_eip.sim[0].public_ip : aws_instance.sim.public_ip,
  ) : ""
}

output "dcv_url" {
  description = "URL-ul clientului DCV din browser, după deschiderea tunelului."
  value       = var.enable_dcv ? "https://localhost:${var.dcv_port}/" : ""
}

output "dcv_password_parameter" {
  description = "Numele parametrului SSM (SecureString) cu parola sesiunii DCV."
  value       = var.enable_dcv ? "/${local.name}/dcv-password" : ""
}

output "vpc_id" {
  description = "VPC-ul în care rulează instanța."
  value       = var.vpc_id == "" ? data.aws_vpc.default[0].id : var.vpc_id
}

output "vpc_cidr_block" {
  description = "CIDR-ul VPC-ului, pentru regulile de autorizare ale tunelului."
  value       = var.vpc_id == "" ? data.aws_vpc.default[0].cidr_block : data.aws_vpc.selected[0].cidr_block
}

output "subnet_id" {
  description = "Subrețea în care rulează instanța."
  value       = local.subnet_id
}

output "data_volume_id" {
  description = "Volumul EBS de date atașat (gol dacă instanța folosește doar discul rădăcină)."
  value       = var.data_volume_id
}
