# Isaac Sim pe o instanță remote

Scopul: deschideți Isaac Sim pe o instanță AWS cu GPU și lucrați în el din browserul de pe
Mac. Pornirea și ștergerea se fac cu `./tools/twinctl/twinctl` sau cu `make sim-*`.

## Ce se creează

Modulul `sim-instance` creează, într-un singur stack:

- o instanță GPU (implicit `g5.2xlarge`, A10G, suficientă pentru Isaac Sim și pentru 2–3
  viewport-uri);
- un volum rădăcină gp3 criptat (implicit 300 GB), dimensionat pentru imaginile de container
  și pentru asset-urile USD;
- un security group care permite SSH doar din CIDR-ul detectat automat al Mac-ului
  (`SIM_SSH_ALLOWED_CIDRS=auto`) și, opțional, porturile de relay TURN;
- un rol IAM cu SSM core (pentru shell fără chei) și, opțional, CloudWatch Agent;
- o pereche de chei EC2, generată local dacă lipsește;
- opțional, un Elastic IP;
- un log group de bootstrap și o alarmă de status-check.

Separat, stack-ul `sim-data` creează **volumul EBS persistent de date**, care se atașează
instanței și se montează la `/opt/cncdt`. Detalii în
[Volumul de date persistent](#volumul-de-date-persistent).

AMI-ul implicit este **Amazon Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04)**
(`owner = amazon`): driverele NVIDIA cu sursă deschisă, Docker și container toolkit, fără
framework-uri de ML — Isaac Sim își aduce PyTorch în container. AMI-ul NVIDIA din Marketplace
cere abonare per cont și nu poate fi folosit automat, deci nu este implicit. Îl puteți impune
prin `SIM_AMI_ID`.

AMI-ul nu are server grafic: desktopul GNOME și Amazon DCV se instalează la bootstrap
(`enable_dcv`), vezi secțiunea [Interfața grafică](#interfața-grafică-amazon-dcv).

## Pornire

```bash
cp .env.example .env          # completați AWS_PROFILE, AWS_REGION, SIM_SSH_KEY_NAME
make sim-init                 # preflight: chei, bucket de asset-uri, verificări
make sim-up                   # creează instanța; durează ~3-5 minute până e gata bootstrap-ul
make sim-status               # ID, stare, IP, porturi, cost estimat
make sim-connect              # deschide tunelul de porturi și afișează link-ul
```

La finalul lui `sim-up` (și la `sim-status`) twinctl afișează blocul **Date de conectare**:
IP-ul public (Elastic IP), IP-ul privat, zona, starea, porturile de semnalizare și de stream,
starea TURN/STUN, URL-ul clientului WebRTC și comenzile SSH / Session Manager. Același bloc
apare și la `make sim-start` (repornirea unei instanțe oprite).

**Capcană la repornire.** Cota de vCPU pentru instanțe G este exact 8 în acest cont, iar
`g5.2xlarge` cere tocmai 8. Cât timp instanța este încă `stopping`, AWS socotește vCPU-urile ei
ca ocupate, iar `make sim-start` eșuează cu `VcpuLimitExceeded`. Așteptați starea `stopped`
(`make sim-status`) înainte de a porni din nou.

`sim-up` așteaptă terminarea bootstrap-ului. Bootstrap-ul, scris în
`infra/terraform/modules/sim-instance/templates/user_data.sh.tftpl`, face, în ordine:

1. pachete de bază și `jq`;
2. runtime NVIDIA pentru Docker (`nvidia-container-toolkit`, `nvidia-ctk runtime configure`);
3. montarea volumului de date persistent la `/opt/cncdt`, dacă `SIM_DATA_VOLUME_ENABLED=true`
   (formatare ext4 o singură dată, copierea conținutului din AMI, intrare `nofail` în
   `/etc/fstab`);
4. workspace-ul `/opt/cncdt/{scripts,logs,assets,secrets,models,cache}` și scripturile de
   operare (`start-isaac.sh`, `stop-isaac.sh`, `fetch-assets.sh`, `selftest.sh`);
5. coturn, dacă `enable_turn = true` (din `.env`: `SIM_ENABLE_TURN`);
6. CloudWatch Agent, dacă `install_cloudwatch_agent = true` (din `.env`:
   `SIM_INSTALL_CLOUDWATCH_AGENT`; metrice GPU în namespace-ul `CNCDT/Simulation`);
7. sincronizarea asset-urilor din `s3://$SIM_ASSETS_BUCKET/sim-assets/latest/`;
8. pornirea automată a simulării, dacă `SIM_AUTOSTART=true`.

`twinctl` scrie `sim.auto.tfvars` din variabilele din `.env` documentate în `.env.example`
(`SIM_*`, `ISAAC_*`); restul parametrilor stack-ului se pun în
`infra/terraform/stacks/sim/terraform.tfvars`.

Starea bootstrap-ului se vede cu `./tools/twinctl/twinctl logs --bootstrap`.

## Containerul Isaac Sim

```bash
docker run --gpus all --network host --ipc host \
  -e ACCEPT_EULA=Y -e PRIVACY_CONSENT=Y \
  -v /opt/cncdt/assets:/assets:ro \
  -v /opt/cncdt/simulation:/sim:ro \
  -v /opt/cncdt/cache/kit:/isaac-sim/kit/cache \
  -v /opt/cncdt/logs:/logs \
  nvcr.io/nvidia/isaac-sim:5.0.0 \
  bash -lc './isaac-sim.streaming.sh'
```

`--network host` este obligatoriu: WebRTC are nevoie de UDP pe porturi proprii, iar maparea
implicită de porturi nu le acoperă. Cache-urile montate evită recompilarea shader-elor la
fiecare repornire.

`simulation/scripts/start-isaac.sh` face exact asta, plus verificarea runtime-ului NVIDIA și
afișarea URL-ului. Îl puteți rula direct pe instanță, prin `make sim-ssh`.

## Porturi

| Port | Protocol | Rol |
| --- | --- | --- |
| 8211 | TCP | Semnalizare WebRTC (`ISAAC_SIGNALING_PORT`) |
| 49100 | UDP | Media WebRTC (`ISAAC_STREAM_PORT`) |
| 3478 | TCP+UDP | TURN (coturn) |
| 49160–49200 | UDP | Interval de releu TURN |
| 8443 | TCP | Amazon DCV (interfața grafică); doar prin tunel, niciodată public |

Porturile se schimbă între versiunile Isaac Sim: citiți-le din `.env`, nu le hardcodați.
`simulation/scripts/stream-url.sh` construiește URL-ul din variabile.

## Tunelul și limita lui

```bash
./tools/twinctl/twinctl connect            # implicit: SSM dacă există, altfel SSH
./tools/twinctl/twinctl connect --mode ssh
./tools/twinctl/twinctl connect --stop     # închide tunelurile pornite
```

URL-ul folosit de browser:

```text
http://127.0.0.1:8211/streaming/webrtc-client/?server=127.0.0.1
```

**Limita importantă:** tunelul este TCP. Semnalizarea trece, media UDP nu. Pagina se încarcă,
dar fluxul video nu pornește prin tunel. Soluția este TURN cu `iceTransportsType: relay`, care
retransmite media prin TCP (vezi [ADR 0004](adr/0004-webrtc-cu-turn.md)). Dacă acceptați
expunerea UDP, puteți porni fără TURN, dar atunci setați `webrtc_public_ports = true` în
variabilele Terraform, ceea ce deschide porturile 49100 și intervalul de releu către
internet — cu riscurile aferente.

## Acces direct, prin IP-ul public

Când `SIM_WEBRTC_PUBLIC_PORTS=true`, porturile de streaming se deschid doar către CIDR-urile
din `SIM_SSH_ALLOWED_CIDRS` (cu `auto` este IP-ul stației de pe care rulați `make sim-up`), iar
browserul se conectează direct la instanță, fără tunel:

```text
http://<IP_PUBLIC>:8211/streaming/webrtc-client/?server=<IP_PUBLIC>&port=8211
```

`start-isaac.sh` citește IP-ul public din IMDS și îl trimite lui Isaac Sim prin
`--/app/livestream/publicEndpointAddress`, pentru că interfața din VPC nu are decât IP privat,
iar IP-ul public EC2 este NAT 1:1 peste ea. Fără acest pas, candidatul ICE anunțat browserului
rămâne adresa privată și media nu pornește chiar dacă porturile sunt deschise. Dacă o versiune
viitoare de Isaac Sim folosește altă cheie, o suprascrieți cu `ISAAC_EXTRA_ARGS`.

Nu există autentificare pe livestream: oricine ajunge la portul de semnalizare poate prelua
sesiunea. De aceea regulile se deschid către IP-ul operatorului, nu către internet.

## Interfața grafică (Amazon DCV)

AMI-ul Amazon Deep Learning pornește fără server grafic: nu are GNOME, GDM3 sau Xorg. Fără ele
nu există nici desktop, nici sesiune DCV. Bootstrap-ul instanței (`enable_dcv = true` în
`stacks/sim`) instalează `ubuntu-desktop`, dezactivează Wayland în `/etc/gdm3/custom.conf`,
generează `/etc/X11/xorg.conf` cu `nvidia-xconfig --preserve-busid --enable-all-gpus
--connected-monitor=DFP-0` (nu există monitor fizic) și instalează pachetele Amazon DCV
(`nice-dcv-server`, `nice-dcv-web-viewer`, `nice-dcv-gl`).

Portul 8443 rămâne închis public. Se ajunge la el prin tunel, la fel ca semnalizarea WebRTC:

```bash
make sim-dcv OPEN=1                 # datele de acces + tunelul, în fundal
make sim-dcv                        # doar datele de acces
```

Apoi, în browser: `https://localhost:8443/`. Utilizatorul este `ubuntu`, iar parola DCV este
generată la bootstrap și păstrată în SSM Parameter Store (`SecureString`), nu în Terraform:

```bash
aws ssm get-parameter --name /cncdt-dev-sim/dcv-password --with-decryption \
  --query 'Parameter.Value' --output text
```

Sesiunea este de tip *console* (`create-session = true`, `owner = ubuntu`): DCV se atașează la
Xorg-ul de pe `:0`, deci vede exact desktopul pe care l-ar vedea consola serială. Containerul
Isaac Sim rămâne headless (`runheadless.sh`), cu livestreaming WebRTC; desktopul servește
administrării instanței și inspecției. Pentru a vedea interfața Isaac Sim în DCV ar trebui ca
containerul să scrie pe `DISPLAY=:0` în loc de `--no-window`, ceea ce este o schimbare separată.

Licența nu se cumpără: pe EC2, serverul DCV își ia singur licența dintr-un bucket administrat
de AWS, `dcv-license.<regiune>`. Are nevoie doar de `s3:GetObject` pe
`arn:aws:s3:::dcv-license.<regiune>/*`, permisiune dată de modulul de instanță. Fără ea,
`dcvserver` cade pe licența de evaluare de 30 de zile și apoi nu mai poate ține sesiuni;
simptomul în `/var/log/dcv/server.log` este `Unable to retrieve license object ... Access
Denied`. Verificarea este `Retrieved license object from AWS S3 bucket 'dcv-license.<regiune>'`.

Verificarea rapidă că totul e în regulă, pe instanță:

```bash
pgrep -a Xorg                      # serverul X rulează pe :0
systemctl is-active dcvserver      # dcvserver este activ
dcv list-sessions                  # sesiunea de consolă a utilizatorului ubuntu
ss -lntp | grep 8443               # portul DCV ascultă
```

## Piața spot

Instanța de simulare se cumpără de pe piața spot (`SIM_USE_SPOT=true` în `.env`, scris ca
`use_spot` în tfvars), fiindcă este cu aproximativ jumătate mai ieftină: pe 2026-10-05,
`g5.2xlarge` costa în `eu-central-1` între 0,74 și 0,90 USD/oră pe spot, față de 1,5156 USD/oră
on-demand. Munca unei sesiuni este întreruptibilă prin construcție: instalarea se reface din
AMI-ul golden, asset-urile stau în S3, iar telemetria ajunge în data lake.

Cererea este `one-time` cu terminare la întrerupere, iar asta înseamnă că **nimic nu repornește
singur instanța**: dacă AWS ia capacitatea, instanța se termină și rămâne terminată până când
cineva rulează `make sim-up`. La trei momente se schimbă ceva față de on-demand:

| Moment | Ce se întâmplă |
| --- | --- |
| Întrerupere de către AWS | Primiți un anunț cu două minute (în metadata instanței și ca eveniment EventBridge `EC2 Spot Instance Interruption Warning`), apoi instanța și discul se termină. AWS detașează volumul de date, care rămâne în stack-ul lui. Revenirea: `make sim-up`, care recreează instanța și reatașează același volum. |
| `make sim-stop` | AWS acceptă oprirea doar pentru cererile spot `persistent`, iar cererea de aici este `one-time`, deci `twinctl stop` refuză și trimite la `make sim-down` urmat de `make sim-up`: instanța se recreează, iar volumul de date se reatașează. Cu `SIM_USE_SPOT=false`, oprirea și pornirea funcționează normal. |
| `make sim-down` | Ștergere completă a instanței: cererea `one-time` se închide când instanța se termină, deci nu rămâne nimic în urmă. Volumul de date persistent nu este atins; se șterge doar cu `make sim-data-down`. |

Prețul spot se schimbă, dar rămâne sub cel on-demand: `make sim-spot` afișează prețul pe
fiecare zonă, comparația cu on-demand și scorul de capacitate al AWS (1 = puțin probabil,
10 = foarte probabil). Scorul este o recomandare pe termen scurt, calculată pentru o cerere de
flotă, deci nu decide nimic: proba reală este `make sim-up`. Dacă AWS nu are capacitate,
aplicarea eșuează cu `InsufficientInstanceCapacity`, mesajul lui `twinctl up` spune asta, iar
soluția este o reîncercare sau alt tip de instanță prin `SIM_INSTANCE_TYPE`.

Cu `SIM_USE_SPOT=false`, instanța se cumpără on-demand, la dublu, iar oprirea și pornirea nu au
nicio particularitate. Decizia și alternativele respinse sunt în
[ADR 0010](adr/0010-instanta-de-simulare-pe-spot.md).

## Volumul de date persistent

Starea acumulată în timpul lucrului — oglinda de conținut Isaac Sim (~10 GB), cache-ul Kit și
de shadere, modelele exportate, `simulation/` sincronizat cu `make sim-push` — stă pe un volum
EBS separat, care supraviețuiește instanței spot. Volumul are propriul stack Terraform
(`infra/terraform/stacks/sim-data`, cu state local) și se montează la `/opt/cncdt`, peste
directorul de stare din AMI.

```bash
make sim-data                    # ID, zonă, dimensiune, instanța care îl are, snapshot-uri, cost
make sim-data-up                 # creează volumul dacă lipsește (altfel nu face nimic)
make sim-data-up SNAPSHOT=snap-… # creează volumul dintr-un snapshot anume (restaurare)
make sim-data-up FRESH=1         # creează un volum gol, ignorând snapshot-urile
make sim-data-snapshot           # snapshot manual al volumului
make sim-data-snapshot LIST=1    # listează snapshot-urile existente
make sim-data-snapshot KEEP=2    # șterge snapshot-urile peste cele mai noi două (KEEP=0 = toate)
make sim-data-down               # ȘTERGE volumul (face întâi un snapshot; SNAPSHOT=0 sare peste)
```

`make sim-up` creează volumul automat dacă nu există: alege zona cu cea mai ieftină capacitate
spot pentru `SIM_INSTANCE_TYPE`, o scrie în `SIM_DATA_VOLUME_AZ` și o impune și instanței,
fiindcă un volum EBS aparține unei singure zone. Reluarea automată se uită doar la
snapshot-urile etichetate `cncdt-<mediu>-sim-data-*` (cele făcute de `make sim-data-snapshot`),
nu la snapshot-urile discului rădăcină sau la AMI-uri, iar un snapshot mai mare decât
`SIM_DATA_VOLUME_GB` este refuzat cu un mesaj clar, în loc să eșueze la AWS. Un snapshot cerut
explicit, prin `SIM_DATA_VOLUME_SNAPSHOT` ori `make sim-data-up SNAPSHOT=…`, se folosește o
singură dată: `twinctl` îl golește apoi din `.env`, ca o recreare ulterioară să urmeze iar cel
mai nou snapshot. Dacă volumul a fost șters, `make sim-up` îl recreează în aceeași zonă și
**reia automat cel mai nou snapshot**, deci o ștergere nu pierde starea; `FRESH=1` cere explicit
un volum gol.

Volumul poate avea două forme, după snapshot-ul din care pornește:

1. **Snapshot al volumului de date**, etichetat `cncdt-<mediu>-sim-data-*`: rădăcina conține
   direct conținutul lui `/opt/cncdt`, deci se montează acolo.
2. **Snapshot al unui disc rădăcină**, cum este `snap-06f17127618e7ae67`: rădăcina este un
   sistem de operare întreg, iar starea utilă stă în `<volum>/opt/cncdt`. `user_data`
   recunoaște structura, montează volumul la `/mnt/cncdt-persist` și expune
   `<volum>/opt/cncdt` la `/opt/cncdt` printr-un bind mount, cu două linii în `/etc/fstab`.
   Un astfel de snapshot este minim 200 GB, iar un volum nou nu poate fi mai mic decât
   snapshot-ul din care pornește: `SIM_DATA_VOLUME_GB` este cel puțin atât, iar `twinctl` scrie
   în clar dacă snapshot-ul ales este un disc rădăcină.

La primul boot, bootstrap-ul formatează volumul (ext4, eticheta `cncdt-data`), copiază în el
conținutul din AMI, ca să nu descarce din nou oglinda, și abia apoi scrie în `/opt/cncdt`.
Liniile din `/etc/fstab` au `nofail`: dacă volumul lipsește, instanța pornește totuși, doar
fără starea persistentă.

`make sim-down` nu atinge volumul: șterge instanța, discul rădăcină, security group-ul, cheia
și VPN-ul, iar volumul se reatașează la următorul `up`. Ștergerea lui cere
`make sim-data-down` (cu volumul detașat, deci după `make sim-down`), iar snapshot-urile se fac
exclusiv manual. Costul este permanent: ≈19 USD/lună pentru 200 GB gp3, plătit și când nu
rulează nicio instanță. Detalii și alternative respinse:
[ADR 0011](adr/0011-volum-de-date-persistent.md).

## Oprire și ștergere

```bash
make sim-stop                      # oprește instanța doar pe on-demand; pe spot, vezi mai sus
make sim-start                     # repornește din aceeași stare, afișează datele de conectare
make sim-down                      # șterge instanța, discul rădăcină, SG, cheia, VPN-ul
make sim-data                      # starea volumului de date persistent
make sim-data-down                 # ȘTERGE volumul de date (cere volumul detașat)

./tools/twinctl/twinctl stop       # echivalentul de nivel jos al comenzilor de mai sus
./tools/twinctl/twinctl down --yes # ștergere fără confirmare
```

`down` cere tastarea lui `da`, iar `--yes` sare peste confirmare. Șterge instanța, discul
rădăcină, security group-ul, Elastic IP-ul, cheia SSH, rolul IAM, log group-ul de bootstrap și,
dacă e pornit, endpoint-ul Client VPN. **Discul rădăcină este efemer la `down`:** ce nu a ajuns
în S3 ori pe volumul de date se pierde (inclusiv scenele USD salvate în altă parte decât
`/opt/cncdt`). Urcarea asset-urilor se face cu `make sim-push`, care arhivează `simulation/` în
`s3://$SIM_ASSETS_BUCKET/sim-assets/latest/`.

### Păstrarea stării: patru variante

| Variantă | Comenzi | Ce rămâne | Cost în repaus |
| --- | --- | --- | --- |
| Volum de date persistent | `make sim-up` / `make sim-down` | starea de sub `/opt/cncdt`: oglinda de conținut, cache-ul, modelele, `simulation/` | ≈ 19 USD/lună (200 GB gp3), permanent |
| Instanța oprită, discul rădăcină păstrat | `make sim-stop` / `make sim-start` (doar on-demand) | tot discul, inclusiv oglinda de conținut și patch-urile | ≈ 19 USD/lună (200 GB gp3) |
| Înghețare în AMI, apoi ștergere | `make sim-idle` (îngheață și șterge tot) sau `make sim-freeze` dacă vreți doar AMI-ul | un AMI golden din starea de acum; `SIM_AMI_ID` indică spre el, deci `make sim-up` pornește din el | ≈ 3 USD/lună (snapshot, doar blocurile folosite) |
| Snapshot manual, apoi ștergere | `make sim-data-snapshot`, apoi `make sim-data-down` | un snapshot pe care îl refaceți cu `make sim-data-up SNAPSHOT=…` | ≈ 3 USD/lună, doar pe blocurile modificate |

Pe spot, varianta „instanța oprită” nu există: AWS acceptă oprirea doar pentru cererile
`persistent`, iar cererea de aici este `one-time`. Pauza pe spot este `make sim-down` urmat de
`make sim-up` — instanța se șterge, compute-ul se oprește imediat, iar volumul de date rămâne și
se reatașează la revenire. Înghețarea în AMI (`make sim-idle`) scurtează reconstrucția, însă
volumul de date se plătește oricum: el ține oglinda și cache-ul.

Recomandarea: `make sim-down` pentru pauze scurte, iar `sim-freeze` + `sim-idle` pentru pauze
lungi, când vă interesează și un AMI la zi. Volumul de date se șterge doar când proiectul chiar
se oprește, după un `make sim-data-snapshot`.

**Atenție la `SIM_AMI_ID`:** dacă `.env` indică un AMI golden diferit de cel din care a fost
creată instanța din state, orice aplicare completă (`make sim-up`) **înlocuiește instanța** și
odată cu ea se șterge discul rădăcină. Volumul de date nu este afectat: este detașat și
reatașat la instanța nouă. Verificați cu `./tools/twinctl/twinctl up --plan` dacă planul
conține `delete create` pe `module.sim.aws_instance.sim` înainte de a aplica.

## Client VPN: cost și stingere

Endpoint-ul Client VPN se taxează orar pentru fiecare subrețea asociată, **conectat sau nu**
(≈ 0.10 USD/oră în `eu-central-1`, adică ≈ 72 USD/lună), plus 0.05 USD/oră per client conectat.
Nu există „oprire” ca la o instanță: ca să nu mai coste, endpoint-ul trebuie șters.

```bash
make sim-vpn-off      # șterge endpoint-ul și regulile de tunel; instanța rămâne neatinsă
make sim-vpn-on       # îl recreează (certificate noi), apoi `make sim-vpn` scrie profilul .ovpn
```

Ambele comenzi lucrează țintit pe `module.vpn` și pe regulile de tunel din security group-ul
instanței (22 de resurse la ștergere), deci nu declanșează înlocuirea instanței aflată în
așteptare. Regulile publice de WebRTC nu sunt atinse: accesul direct, prin IP-ul public,
rămâne cum este. `make sim-vpn-off` scrie și `SIM_VPN_ENABLED=false` în `.env`, ca o aplicare
ulterioară să nu recreeze endpoint-ul.

## Diagnostic

```bash
./tools/twinctl/twinctl selftest              # GPU, Docker, runtime, porturi, disc, AWS
./tools/twinctl/twinctl logs --follow         # logurile Isaac Sim
./tools/twinctl/twinctl logs --bootstrap      # logul de bootstrap
./tools/twinctl/twinctl status --deep         # stare + verificări
./tools/twinctl/twinctl cost                  # estimare de cost pentru orele de funcționare
./tools/twinctl/twinctl spot                  # preț spot pe zone + scorul de capacitate
./tools/twinctl/twinctl outputs               # toate ieșirile Terraform
```

Dacă browserul afișează pagina dar nu pornește fluxul: verificați că TURN rulează
(`ss -lunpt | grep 3478` pe instanță) și că clientul este configurat pe `relay`.

## Prerechizite

- AWS CLI v2 și profil configurat (`aws sts get-caller-identity` trebuie să funcționeze).
- Terraform ≥ 1.5.
- Docker local doar pentru `make docker-local` (broker MQTT și MinIO).
- Pentru extragerea certificatelor IoT: `simulation/scripts/gen-iot-certs.sh` după ce
  stack-ul de platformă este aplicat.
