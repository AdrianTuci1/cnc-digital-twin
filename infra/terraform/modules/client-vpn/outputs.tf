output "endpoint_id" {
  description = "ID-ul endpoint-ului Client VPN."
  value       = aws_ec2_client_vpn_endpoint.this.id
}

output "dns_name" {
  description = "Numele DNS la care clientul se conectează."
  value       = aws_ec2_client_vpn_endpoint.this.dns_name
}

output "client_cidr_block" {
  description = "Pulul de adrese din care primesc IP clienții."
  value       = aws_ec2_client_vpn_endpoint.this.client_cidr_block
}

output "client_certificate" {
  description = "Certificatul de client, pentru profilul .ovpn."
  value       = tls_locally_signed_cert.client.cert_pem
  sensitive   = true
}

output "client_private_key" {
  description = "Cheia privată a certificatului de client, pentru profilul .ovpn."
  value       = tls_private_key.client.private_key_pem
  sensitive   = true
}

output "security_group_id" {
  description = "Grupul de securitate al endpoint-ului."
  value       = aws_security_group.vpn.id
}
