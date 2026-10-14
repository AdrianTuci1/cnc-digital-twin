# Acces privat la instanța de simulare prin AWS Client VPN.
#
# De ce: livestreaming-ul Isaac Sim nu are autentificare proprie — cine ajunge la portul de
# semnalizare poate prelua fluxul. În loc să deschidem porturile WebRTC către internet, le
# închidem complet și punem un endpoint Client VPN cu autentificare mutuală pe certificate.
# Clientul ajunge la instanță pe adresa ei privată, prin tunel.
#
# Cost: endpoint-ul se plătește pe oră cât timp există (0,10 USD/h în eu-central-1) plus
# 0,05 USD/h per conexiune. Modulul stă în stack-ul `sim`, deci `make sim-down` îl șterge și
# în repaus nu se plătește nimic.
#
# PKI: un CA propriu, un certificat de server și unul de client, generate de providerul `tls`.
# Cheia privată a clientului iese doar ca output sensibil, ca să fie scrisă în profilul .ovpn;
# nu ajunge în git și nu trece prin nicio resursă AWS.

locals {
  name = "${var.project_name}-${var.environment}-vpn"

  tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      Component   = "vpn"
      ManagedBy   = "terraform"
    },
    var.tags,
  )
}

# ---------------------------------------------------------------- PKI
resource "tls_private_key" "ca" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_self_signed_cert" "ca" {
  private_key_pem = tls_private_key.ca.private_key_pem

  subject {
    common_name  = "${local.name}-ca.internal"
    organization = var.project_name
  }

  validity_period_hours = var.ca_validity_hours
  is_ca_certificate     = true
  allowed_uses          = ["cert_signing", "crl_signing", "digital_signature"]
}

resource "tls_private_key" "server" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_cert_request" "server" {
  private_key_pem = tls_private_key.server.private_key_pem

  subject {
    common_name  = "${local.name}-server.internal"
    organization = var.project_name
  }
}

resource "tls_locally_signed_cert" "server" {
  cert_request_pem   = tls_cert_request.server.cert_request_pem
  ca_private_key_pem = tls_private_key.ca.private_key_pem
  ca_cert_pem        = tls_self_signed_cert.ca.cert_pem

  validity_period_hours = var.certificate_validity_hours
  allowed_uses          = ["digital_signature", "key_encipherment", "server_auth"]
}

resource "tls_private_key" "client" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_cert_request" "client" {
  private_key_pem = tls_private_key.client.private_key_pem

  subject {
    common_name  = var.client_common_name
    organization = var.project_name
  }
}

resource "tls_locally_signed_cert" "client" {
  cert_request_pem   = tls_cert_request.client.cert_request_pem
  ca_private_key_pem = tls_private_key.ca.private_key_pem
  ca_cert_pem        = tls_self_signed_cert.ca.cert_pem

  validity_period_hours = var.certificate_validity_hours
  allowed_uses          = ["digital_signature", "key_encipherment", "client_auth"]
}

resource "aws_acm_certificate" "server" {
  # Providerul AWS nu permite `domain_name` împreună cu `private_key`: pentru un certificat
  # importat câmpul rămâne gol, iar EC2 Client VPN respinge certificatul fără domeniu. Domeniul
  # este dedus de ACM din CN, de aceea CN-ul este un nume DNS valid (`...internal`).
  private_key       = tls_private_key.server.private_key_pem
  certificate_body  = tls_locally_signed_cert.server.cert_pem
  certificate_chain = tls_self_signed_cert.ca.cert_pem

  tags = merge(local.tags, { Name = "${local.name}-server" })

  lifecycle {
    create_before_destroy = true
  }
}

# CA-ul folosit ca lanț rădăcină pentru validarea certificatelor de client. ACM cere cheia
# privată la orice import, chiar și pentru un CA, așa că o trimitem pe cea care a semnat
# certificatele; este deja în state, pentru că semnează certificatul de client.
resource "aws_acm_certificate" "client_ca" {
  private_key      = tls_private_key.ca.private_key_pem
  certificate_body = tls_self_signed_cert.ca.cert_pem

  tags = merge(local.tags, { Name = "${local.name}-client-ca" })

  lifecycle {
    create_before_destroy = true
  }
}

# ---------------------------------------------------------------- rețea
resource "aws_security_group" "vpn" {
  name_prefix = "${local.name}-"
  description = "AWS Client VPN: tunel catre instanta de simulare."
  vpc_id      = var.vpc_id

  tags = merge(local.tags, { Name = "${local.name}-sg" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "tunnel" {
  for_each = toset(var.allowed_cidrs)

  security_group_id = aws_security_group.vpn.id
  description       = "Tunel Client VPN din ${each.value}"
  cidr_ipv4         = each.value
  from_port         = var.port
  to_port           = var.port
  ip_protocol       = var.transport_protocol

  tags = local.tags
}

# Fără această regulă, pachetele decapsulate rămân blocate la ieșirea din interfețele
# endpoint-ului: tunelul se ridică, clientul primește adresă și rute, dar nimic nu ajunge în
# VPC (simptom: timeout la orice port, zero pachete în tcpdump pe instanță). API-ul
# CreateSecurityGroup nu adaugă regula de ieșire pe care consola o pune implicit.
resource "aws_vpc_security_group_egress_rule" "vpc" {
  security_group_id = aws_security_group.vpn.id
  # Descrierile regulilor de security group acceptă doar ASCII, ca și cele IAM.
  description = "Trafic decapsulat catre VPC (${var.authorized_cidr})"
  cidr_ipv4   = var.authorized_cidr
  ip_protocol = "-1"

  tags = local.tags
}

# ---------------------------------------------------------------- endpoint
resource "aws_ec2_client_vpn_endpoint" "this" {
  description            = "Acces privat la ${var.project_name}-${var.environment}-sim"
  client_cidr_block      = var.client_cidr_block
  server_certificate_arn = aws_acm_certificate.server.arn
  split_tunnel           = true
  transport_protocol     = var.transport_protocol
  vpc_id                 = var.vpc_id
  security_group_ids     = [aws_security_group.vpn.id]

  # Autentificare mutuală: serverul se prezintă cu certificatul lui, clientul cu al lui.
  authentication_options {
    type                       = "certificate-authentication"
    root_certificate_chain_arn = aws_acm_certificate.client_ca.arn
  }

  # Jurnalele de conexiune în CloudWatch se plătesc separat; le lăsăm oprite.
  connection_log_options {
    enabled = false
  }

  tags = merge(local.tags, { Name = local.name })
}

# Asocierea rezolvă rutele: clientul primește ruta VPC-ului prin tabela de rutare a subreței.
resource "aws_ec2_client_vpn_network_association" "this" {
  client_vpn_endpoint_id = aws_ec2_client_vpn_endpoint.this.id
  subnet_id              = var.subnet_id
}

resource "aws_ec2_client_vpn_authorization_rule" "vpc" {
  client_vpn_endpoint_id = aws_ec2_client_vpn_endpoint.this.id
  target_network_cidr    = var.authorized_cidr
  authorize_all_groups   = true

  description = "Acces la reteaua VPC"
}
