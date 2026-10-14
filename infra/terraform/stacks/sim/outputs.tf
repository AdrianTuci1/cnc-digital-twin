output "instance_id" {
  description = "ID-ul instanței de simulare."
  value       = module.sim.instance_id
}

output "public_ip" {
  description = "IP-ul public al instanței."
  value       = module.sim.public_ip
}

output "private_ip" {
  description = "IP-ul privat al instanței."
  value       = module.sim.private_ip
}

output "instance_type" {
  description = "Tipul instanței."
  value       = module.sim.instance_type
}

output "availability_zone" {
  description = "Zona de disponibilitate a instanței."
  value       = module.sim.availability_zone
}

output "ami_id" {
  description = "AMI-ul folosit. Copiați-l în tfvars pentru lansări reproductibile."
  value       = module.sim.ami_id
}

output "security_group_id" {
  description = "Security group-ul instanței."
  value       = module.sim.security_group_id
}

output "webrtc_url" {
  description = "URL-ul clientului WebRTC."
  value       = module.sim.webrtc_url
}

output "signaling_port" {
  description = "Portul de semnalizare."
  value       = module.sim.signaling_port
}

output "stream_port" {
  description = "Portul de stream."
  value       = module.sim.stream_port
}

output "ssh_command" {
  description = "Comandă SSH (dacă portul 22 este deschis)."
  value       = module.sim.ssh_command
}

output "ssm_shell_command" {
  description = "Shell prin Session Manager."
  value       = module.sim.ssm_shell_command
}

output "ssm_tunnel_commands" {
  description = "Port-forwarding prin Session Manager pentru streaming."
  value       = module.sim.ssm_tunnel_commands
}

output "bootstrap_log_command" {
  description = "Comandă pentru urmărirea logului de bootstrap."
  value       = module.sim.bootstrap_log_command
}

output "ssh_allowed_cidrs" {
  description = "CIDR-urile efectiv permise pe portul 22 după rezolvarea `auto`."
  value       = local.ssh_allowed_cidrs
}

output "key_name" {
  description = "Cheia SSH atașată."
  value       = module.sim.key_name
}

output "turn_enabled" {
  description = "Starea serverului TURN."
  value       = module.sim.turn_enabled
}

# ---------------------------------------------------------------- acces grafic (Amazon DCV)
output "dcv_enabled" {
  description = "Dacă desktopul GNOME și serverul DCV sunt instalate de bootstrap."
  value       = module.sim.dcv_enabled
}

output "dcv_url" {
  description = "URL-ul interfeței DCV din browser, după deschiderea tunelului SSH."
  value       = module.sim.dcv_url
}

output "dcv_tunnel_command" {
  description = "Tunel SSH către portul DCV. Portul rămâne închis public."
  value       = module.sim.dcv_tunnel_command
}

output "dcv_password_parameter" {
  description = "Parametrul SSM (SecureString) cu parola sesiunii DCV."
  value       = module.sim.dcv_password_parameter
}

# ---------------------------------------------------------------- Client VPN
output "vpn_enabled" {
  description = "Dacă tunelul Client VPN este activ."
  value       = var.vpn_enabled
}

output "vpn_endpoint_id" {
  description = "ID-ul endpoint-ului Client VPN."
  value       = var.vpn_enabled ? module.vpn[0].endpoint_id : ""
}

output "vpn_endpoint_dns_name" {
  description = "Numele DNS la care se conectează clientul VPN."
  value       = var.vpn_enabled ? module.vpn[0].dns_name : ""
}

output "vpn_client_cidr_block" {
  description = "Pulul de adrese al clienților VPN."
  value       = var.vpn_enabled ? module.vpn[0].client_cidr_block : ""
}

output "vpn_client_certificate" {
  description = "Certificatul de client, pentru profilul .ovpn."
  value       = var.vpn_enabled ? module.vpn[0].client_certificate : ""
  sensitive   = true
}

output "vpn_client_private_key" {
  description = "Cheia privată a certificatului de client, pentru profilul .ovpn."
  value       = var.vpn_enabled ? module.vpn[0].client_private_key : ""
  sensitive   = true
}

output "internal_url" {
  description = "Adresa de streaming prin tunel: IP privat:port."
  value       = var.vpn_enabled ? "${module.sim.private_ip}:${module.sim.signaling_port}" : ""
}
