# CNC Digital Twin — punct de intrare unic pentru operațiuni uzuale.
#
# `make help` listează tot. Regulile de simulare deleagă către ./tools/twinctl/twinctl,
# restul cheamă direct Terraform sau uneltele Python.

SHELL := /bin/bash
.DEFAULT_GOAL := help

# `.env` (dacă există) completează valorile de mai jos; variabilele de pe linia de comandă au
# prioritate față de `.env`. Aceleași variabile sunt citite și de twinctl, care își citește
# singur fișierul.
-include .env

ROOT        := $(shell cd "$(dir $(lastword $(MAKEFILE_LIST)))" && pwd)
TWINCTL     := $(ROOT)/tools/twinctl/twinctl
TF          := terraform
TF_DIR      := $(ROOT)/infra/terraform
ENVIRONMENT ?= dev
# Mediul virtual local. `make install` îl creează o singură dată; restul țintelor îl folosesc
# automat dacă există. Homebrew marchează Python-ul de sistem ca EXTERNALLY-MANAGED (PEP 668),
# deci instalarea directă în el este refuzată.
VENV        := $(ROOT)/.venv
PY          ?= $(shell test -x "$(VENV)/bin/python" && echo "$(VENV)/bin/python" || echo python3)
PROJECT_NAME ?= cncdt
AWS_REGION   ?= eu-central-1

# Terraform, AWS CLI și uneltele Python moștenesc aceste variabile din mediu.
export AWS_PROFILE AWS_REGION AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
export PROJECT_NAME ENVIRONMENT TF_STATE_BUCKET

# ---------------------------------------------------------------- ajutor
.PHONY: help
help: ## Afișează această listă
	@awk 'BEGIN {FS = ":.*?## "; printf "\n\033[1mCNC Digital Twin\033[0m\n"} \
		/^[a-zA-Z0-9_.-]+:.*?## / {printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo

# ---------------------------------------------------------------- ciclu complet
#   make up     creează tot, în ordine: platforma serverless, cheia SSH, instanța Isaac Sim
#   make down   șterge tot, în ordine inversă: Isaac Sim, apoi platforma (YES=1 fără confirmări)
.PHONY: up down
up: platform-up sim-init sim-up ## LANSEAZĂ TOT: platforma serverless + instanța Isaac Sim
down: ## ȘTERGE TOT (Isaac Sim + platformă); YES=1 sare peste confirmări
	$(TWINCTL) down $(if $(YES),--yes,)
	cd $(TF_DIR)/stacks/platform && $(TF) destroy -var-file=envs/$(ENVIRONMENT).tfvars $(if $(YES),-auto-approve,)

# ---------------------------------------------------------------- Isaac Sim (instanța GPU)
# Ciclul de viață cerut, din rădăcină:
#   make sim-up       lansează Isaac Sim (din AMI-ul golden, dacă există), pe spot
#   make sim-freeze   salvează starea instalată într-un AMI, apoi poți șterge instanța
#   make sim-down     șterge instanța; rămân AMI-ul și volumul de date persistent
#   make sim-data     starea volumului de date (se șterge doar cu `make sim-data-down`)
#   make sim-vpn      scrie profilul AWS VPN Client; porturile WebRTC rămân închise
#   make sim-dcv      afișează interfața grafică (Amazon DCV) și tunelul către ea
.PHONY: sim-init sim-up sim-stop sim-down sim-start sim-status sim-connect sim-url \
        sim-ssh sim-push sim-push-sim sim-freeze sim-ami sim-idle sim-vpn sim-dcv sim-logs \
        sim-selftest sim-assets sim-outputs sim-cost sim-spot sim-vpn-off sim-vpn-on \
        sim-data sim-data-up sim-data-down sim-data-snapshot
sim-init: ## Preflight: unelte, credențiale, cheie SSH, bucket de asset-uri
	$(TWINCTL) init
sim-up: ## LANSEAZĂ Isaac Sim: creează instanța GPU și afișează datele de conectare
	$(TWINCTL) up --wait
sim-stop: ## Oprește instanța doar dacă rulează on-demand (SIM_USE_SPOT=false); pe spot trimite la sim-down
	$(TWINCTL) stop
sim-down: ## ȘTERGE instanța de simulare (SG, cheie, VPN); volumul de date rămâne și se reatașează
	$(TWINCTL) down
sim-freeze: ## Salvează Isaac Sim instalat într-un AMI golden (KEEP=2, LIST=1, CLEAR=1, YES=1)
	$(TWINCTL) freeze $(if $(LIST),--list,) $(if $(CLEAR),--clear,) $(if $(KEEP),--keep $(KEEP),) $(if $(YES),--yes,)
sim-ami: ## Listează AMI-urile golden și arată care e folosit acum
	$(TWINCTL) freeze --list
sim-vpn: ## Scrie profilul AWS VPN Client (.ovpn) și adresa de streaming din tunel
	$(TWINCTL) vpn-config
sim-vpn-off: ## Șterge endpoint-ul Client VPN (oprește taxa orară); instanța rămâne neatinsă
	$(TWINCTL) vpn-off
sim-vpn-on: ## Recreează endpoint-ul Client VPN după `sim-vpn-off` (certificate noi)
	$(TWINCTL) vpn-on
sim-idle: ## Îngheață într-un AMI golden, apoi șterge instanța (rămâne volumul de date)
	$(TWINCTL) down --freeze $(if $(YES),--yes,)
sim-start: ## Repornește o instanță on-demand oprită; pe spot reluarea este sim-up
	$(TWINCTL) start
sim-status: ## Starea instanței + datele de conectare
	$(TWINCTL) status
sim-connect: ## Deschide tunelul de semnalizare și afișează datele clientului WebRTC
	$(TWINCTL) connect
sim-dcv: ## Interfața grafică (desktop GNOME + Amazon DCV): date de acces și tunel
	$(TWINCTL) dcv $(if $(OPEN),--open,)
sim-url: ## Afișează serverul de semnalizare al clientului WebRTC
	$(TWINCTL) url
sim-ssh: ## Shell sau comandă pe instanță (SSM ori SSH)
	$(TWINCTL) ssh
sim-push: ## Sincronizează simulation/ pe instanță, prin S3 (cere bucket)
	$(TWINCTL) push
sim-push-sim: ## Sincronizează simulation/ direct prin SSH, fără bucket S3
	$(TWINCTL) push-sim
sim-logs: ## Urmărește logurile Isaac Sim
	$(TWINCTL) logs
sim-selftest: ## Diagnoza mediului pe instanță (GPU, Docker, porturi)
	$(TWINCTL) selftest
sim-assets: ## Oglinda locală de conținut Isaac Sim: stare și pornirea sincronizării (~10 GB)
	$(TWINCTL) assets
sim-outputs: ## Toate ieșirile Terraform ale stack-ului sim
	$(TWINCTL) outputs
sim-cost: ## Estimare de cost pentru starea curentă
	$(TWINCTL) cost
sim-spot: ## Prețul spot pe zone, comparația cu on-demand și scorul de capacitate
	$(TWINCTL) spot
# Volumul EBS persistent de date. Se creează automat la primul `sim-up`, se atașează fiecărei
# instanțe și se montează la /opt/cncdt. Se șterge doar cu `sim-data-down`, iar snapshot-urile
# se fac doar cu `sim-data-snapshot`. Dacă volumul lipsește, `sim-up` îl recreează în aceeași
# zonă și reia automat ultimul snapshot.
sim-data: ## Starea volumului de date persistent (ID, zonă, dimensiune, snapshot-uri, cost)
	$(TWINCTL) data
sim-data-up: ## Creează volumul de date dacă lipsește (reia ultimul snapshot; FRESH=1 gol, SNAPSHOT=snap-...)
	$(TWINCTL) data-up $(if $(SNAPSHOT),--snapshot $(SNAPSHOT),) $(if $(FRESH),--fresh,)
sim-data-down: ## ȘTERGE volumul de date; YES=1 fără confirmare, SNAPSHOT=0 fără copie de siguranță
	$(TWINCTL) data-down $(if $(YES),--yes,) $(if $(filter 0,$(SNAPSHOT)),--no-snapshot,)
sim-data-snapshot: ## Snapshot manual al volumului (LIST=1 afișează, KEEP=2 șterge peste 2, KEEP=0 șterge tot)
	$(TWINCTL) data-snapshot $(if $(LIST),--list,) $(if $(KEEP),--keep $(KEEP),) $(if $(YES),--yes,)

# ---------------------------------------------------------------- platformă (restul resurselor)
#   make platform-up    creează bucket-ul de state și aplică tot stack-ul de platformă
#   make platform-down  șterge complet resursele de platformă (bucket-ul de state rămâne)
.PHONY: platform-up platform-down platform-backend bootstrap platform-plan \
        tf-init tf-plan tf-apply tf-destroy tf-fmt tf-validate
platform-up: platform-backend tf-init ## LANSEAZĂ restul resurselor (IoT, Firehose, Lambda, S3, CloudWatch, SageMaker)
	cd $(TF_DIR)/stacks/platform && $(TF) plan -var-file=envs/$(ENVIRONMENT).tfvars -out=plan.out
	cd $(TF_DIR)/stacks/platform && $(TF) apply plan.out
platform-down: tf-destroy ## ȘTERGE COMPLET resursele de platformă (cere confirmare)
bootstrap: ## Creează bucket-ul de state și scrie envs/<ENVIRONMENT>.backend.hcl
	$(ROOT)/scripts/tf-backend.sh
platform-backend: bootstrap
platform-plan: tf-init ## Plan pentru mediul curent, fără aplicare
	cd $(TF_DIR)/stacks/platform && $(TF) plan -var-file=envs/$(ENVIRONMENT).tfvars -out=plan.out
tf-init: ## terraform init pentru stack-ul de platformă al mediului curent
	cd $(TF_DIR)/stacks/platform && $(TF) init -backend-config=envs/$(ENVIRONMENT).backend.hcl
tf-plan: ## terraform plan pentru mediul curent
	cd $(TF_DIR)/stacks/platform && $(TF) plan -var-file=envs/$(ENVIRONMENT).tfvars -out=plan.out
tf-apply: ## Aplică planul scris de tf-plan
	cd $(TF_DIR)/stacks/platform && $(TF) apply plan.out
tf-destroy: ## terraform destroy pentru mediul curent (cere confirmare)
	cd $(TF_DIR)/stacks/platform && $(TF) destroy -var-file=envs/$(ENVIRONMENT).tfvars
tf-fmt: ## Formatează toate fișierele Terraform
	$(TF) fmt -recursive $(TF_DIR)
tf-validate: ## Validează toate stack-urile
	@for d in $(TF_DIR)/stacks/*/ $(TF_DIR)/bootstrap; do \
		echo "==> $$d"; (cd "$$d" && $(TF) init -backend=false -input=false >/dev/null && $(TF) validate) || exit 1; \
	done

# ---------------------------------------------------------------- documentație
# Diagramele sunt generate din cod (`docs/diagrams/architecture.py`), nu desenate manual.
# Cer `graphviz` instalat (`brew install graphviz`) și pachetul `diagrams` în mediul virtual:
# `$(PY) -m pip install diagrams`.
diagrams: ## Regenerează diagramele de arhitectură din docs/diagrams/
	cd $(ROOT) && $(PY) docs/diagrams/architecture.py

# Planul celulei, văzut de sus, iese din coordonatele scrise o singură dată în
# `docs/diagrams/cell_layout.py`; aceleași valori ajung în scena MJCF. PNG-ul cere
# `rsvg-convert` (`brew install librsvg`); fără el rămâne doar SVG-ul.
.PHONY: cell-layout
cell-layout: ## Regenerează planul celulei, văzut de sus (docs/diagrams/cnc-cell-layout.png)
	cd $(ROOT) && $(PY) docs/diagrams/cell_layout.py

# ---------------------------------------------------------------- acces (rol de operator)
# Stack-ul `access` are state local și se aplică o singură dată per cont, cu profilul de ADMIN,
# înainte să existe rolul. După aceea, credențialele de zi cu zi se iau cu
# `eval "$(./scripts/assume-operator.sh)"`, iar `make access-down` revocă accesul.
#
# Credențialele se rezolvă prin AWS CLI și se transmit providerului ca variabile de mediu:
# providerul Terraform nu știe să citească sesiunile scrise de `aws login` (`login_session`).
ACCESS_DIR     ?= $(TF_DIR)/stacks/access
ACCESS_PROFILE ?= default
PROFILE_NAME   ?= $(PROJECT_NAME)
OPERATOR_MFA_SERIAL ?=
REQUIRE_MFA    ?= false
SESSION_HOURS  ?= 4
ACCESS_VARS    := -var "project_name=$(PROJECT_NAME)" -var "environment=$(ENVIRONMENT)" \
                  -var "region=$(AWS_REGION)" -var "aws_profile=" \
                  -var "require_mfa=$(REQUIRE_MFA)" -var "max_session_duration_hours=$(SESSION_HOURS)"

.PHONY: access-init access-plan access-up access-show access-profile access-down
access-init: ## terraform init pentru stack-ul de acces (state local)
	terraform -chdir=$(ACCESS_DIR) init -input=false

access-plan: access-init ## Plan pentru rolul de operator (cu profilul de admin)
	@eval "$$(aws configure export-credentials --profile $(ACCESS_PROFILE) --format env)" && \
		terraform -chdir=$(ACCESS_DIR) plan $(ACCESS_VARS)

access-up: access-init ## Creează rolul de operator; se rulează cu profilul de admin
	@eval "$$(aws configure export-credentials --profile $(ACCESS_PROFILE) --format env)" && \
		terraform -chdir=$(ACCESS_DIR) apply $(ACCESS_VARS)

access-show: access-init ## Afișează ARN-ul rolului, linkul de comutare și blocul de profil
	@terraform -chdir=$(ACCESS_DIR) output -raw role_arn
	@echo
	@terraform -chdir=$(ACCESS_DIR) output -raw console_switch_role_url
	@echo
	@terraform -chdir=$(ACCESS_DIR) output -raw profile_block

access-profile: access-init ## Scrie profilul de asumare în ~/.aws/config
	@role_arn="$$(terraform -chdir=$(ACCESS_DIR) output -raw role_arn)"; \
  aws configure set region "$(AWS_REGION)" --profile $(PROFILE_NAME); \
  aws configure set role_arn "$$role_arn" --profile $(PROFILE_NAME); \
  aws configure set source_profile "$(ACCESS_PROFILE)" --profile $(PROFILE_NAME); \
  echo "profil '$(PROFILE_NAME)' scris: asumă $$role_arn din profilul '$(ACCESS_PROFILE)'"; \
  echo "puneți AWS_PROFILE=$(PROFILE_NAME) în .env"
	@if [ -n "$(OPERATOR_MFA_SERIAL)" ]; then \
  aws configure set mfa_serial "$(OPERATOR_MFA_SERIAL)" --profile $(PROFILE_NAME); \
  echo "MFA cerut la asumare, cu $(OPERATOR_MFA_SERIAL)"; \
else \
  echo "fără MFA în profil; pentru MFA folosiți scripts/assume-operator.sh"; \
fi

access-down: ## Șterge rolul de operator (revocă accesul la proiect)
	@eval "$$(aws configure export-credentials --profile $(ACCESS_PROFILE) --format env)" && \
		terraform -chdir=$(ACCESS_DIR) destroy $(ACCESS_VARS)

# ---------------------------------------------------------------- operațiuni
#   make audit  răspunde la: mai coincide codul cu AWS? au rămas resurse în urmă?
#   Coduri de ieșire: 0 curat, 1 elemente de verificat, 2 audit parțial (fără AWS CLI).
.PHONY: audit
audit: ## Inventar: derivă Terraform, resurse rămase, costuri
	@$(ROOT)/scripts/audit.sh || { code=$$?; printf '\naudit: cod %s — 1 = elemente de verificat, 2 = audit parțial fără credențiale. Vezi ops/runbooks/audit.md\n' "$$code"; exit $$code; }

# ---------------------------------------------------------------- aplicație
.PHONY: install venv lint test test-unit test-integration e2e
venv: ## Creează mediul virtual local (.venv), ignorat de git
	@test -x "$(VENV)/bin/python" || python3 -m venv "$(VENV)"
	@"$(VENV)/bin/python" -m pip install -q --upgrade pip
install: venv ## Instalează dependențele Python pentru ml/, lambdas/ și generatorul de piese
	"$(VENV)/bin/python" -m pip install -e "$(ROOT)/ml[dev]"
	"$(VENV)/bin/python" -m pip install -r "$(ROOT)/simulation/extensions/cncdt.cnc_cell/requirements.txt"
test: test-unit ## Rulează suita de teste rapidă
test-unit: ## Teste unitare (simulare, contract, Lambda, feature engineering), fără AWS
	cd $(ROOT) && $(PY) -m pytest tests -m "not aws" -q
test-integration: ## Teste de integrare (necesită credențiale AWS)
	cd $(ROOT) && $(PY) -m pytest tests/integration -m aws -q
e2e: ## Buclă completă cu publicatorul de dezvoltare (fără Isaac Sim)
	$(ROOT)/tests/e2e/run_closed_loop.sh
lint: ## ruff + terraform fmt check + shellcheck
	cd $(ROOT) && $(PY) -m ruff check lambdas ml simulation/extensions || true
	$(TF) fmt -check -recursive $(TF_DIR)
	command -v shellcheck >/dev/null && shellcheck -x $(TWINCTL) $(ROOT)/scripts/*.sh $(ROOT)/simulation/scripts/*.sh || echo "shellcheck absent, sar peste"

# ---------------------------------------------------------------- date & ML
.PHONY: publish-dev features-recipe features-build features-run features-verify train-pm train-qa docker-local
publish-dev: ## Publică telemetrie sintetică (simulează celula CNC fără Isaac Sim)
	cd $(ROOT)/telemetry/dev_publisher && $(PY) -m pip install -q -r requirements.txt && $(PY) publish.py --cycles 20 --defect-rate 0.2
features-recipe: ## Regenerează rețeta Data Wrangler din cod
	cd $(ROOT)/ml/feature_engineering && $(PY) build_features.py --print-recipe > flows/predictive_maintenance.recipe.json
	@git -C $(ROOT) diff --stat -- ml/feature_engineering/flows || true
features-build: ## Construiește setul de antrenare (offline, fără AWS): INPUT=... OUTPUT=...
	cd $(ROOT)/ml/feature_engineering && $(PY) build_features.py \
		--input "$${INPUT:-../../tests/data/telemetry.jsonl}" \
		--output "$${OUTPUT:-/tmp/cncdt-features.jsonl}" --format "$${FORMAT:-jsonl}"
features-verify: ## Verifică rețeta și construiește un set de probă, apoi îl raportează
	cd $(ROOT) && $(PY) -m pytest tests/unit/test_feature_engineering.py -q
	cd $(ROOT)/ml/feature_engineering && $(PY) build_features.py --print-recipe | diff -q - flows/predictive_maintenance.recipe.json \
		&& echo "rețeta este sincronizată cu codul"
features-run: ## Rulează fluxul Data Wrangler ca job de procesare (FLOW=..., BUCKET=..., ROLE_ARN=...)
	$(PY) $(ROOT)/ml/feature_engineering/run_flow.py \
		--flow "$${FLOW:-$(ROOT)/ml/feature_engineering/flows/predictive_maintenance.flow}" \
		--bucket "$(BUCKET)" --role-arn "$(ROLE_ARN)" $${EXTRA_ARGS:---wait}
docker-local: ## Pornește broker MQTT + MinIO local pentru dezvoltare
	docker compose -f $(ROOT)/simulation/docker/docker-compose.local.yml up -d
train-pm: ## Antrenare model predictive maintenance (local, date din S3 sau fișier)
	cd $(ROOT)/ml && $(PY) training/predictive_maintenance/train.py --data $${DATA:-data/pm_sample.csv} --output artifacts/pm
train-qa: ## Antrenare model vision QA (necesită GPU/torch)
	cd $(ROOT)/ml && $(PY) training/vision_qa/train.py --data $${DATA:-data/qa} --output artifacts/qa --epochs 5
