# Documentație

Sursa de adevăr pentru decizii. Codul spune *ce* se întâmplă; documentele de aici spun *de ce*.

## Ghiduri

| Document | Ce conține | Stare |
| --- | --- | --- |
| [01-architecture.md](01-architecture.md) | Componentele, bucla închisă, fluxul de date, mediile | scris |
| [02-isaac-sim-remote.md](02-isaac-sim-remote.md) | Instanța GPU, streaming WebRTC, tunel, TURN | scris |
| [04-data-contracts.md](04-data-contracts.md) | Cele patru contracte de date, ferestrele, partițiile | scris |
| [05-feature-engineering.md](05-feature-engineering.md) | Hot path vs offline, Data Wrangler, rețeta | scris |
| [07-cost.md](07-cost.md) | Costurile pe componente și controalele de cost | scris |
| [08-isaac-sim-guide.md](08-isaac-sim-guide.md) | Kit, extensii, `omni.ui`, obținerea datelor | scris |
| [09-scena-celulei-cnc.md](09-scena-celulei-cnc.md) | Props, asamblarea scenei, contractul de căi, uzură, camera de sortare, poarta de la lamă | scris |
| [10-dataset-piese.md](10-dataset-piese.md) | Familiile de piese, generatorul, catalogul de defecte, etichete, separarea de inferență | scris |
| [11-geaman-mujoco.md](11-geaman-mujoco.md) | Al doilea geamăn, în MuJoCo: scenă, motor de uzură non-liniar, telemetrie, detector, panou, faze | scris, cu cod (faza 2) |

## Decizii (ADR)
| ADR | Decizie |
| --- | --- |
| [0001](adr/0001-terraform-ca-unealta-de-infrastructura.md) | Terraform, cu state separat pentru simulare și platformă |
| [0002](adr/0002-glue-ca-registru-de-schema.md) | Glue doar ca registru de schemă, fără job-uri sau crawlere |
| [0003](adr/0003-data-wrangler-pentru-feature-engineering.md) | Data Wrangler pentru feature engineering, cod pentru ferestrele temporale |
| [0004](adr/0004-webrtc-cu-turn.md) | WebRTC prin TURN, fără tunel TCP pentru media |
| [0005](adr/0005-cloudwatch-dashboards-pentru-vizualizare.md) | Dashboard-uri custom CloudWatch pentru vizualizare, fără QuickSight |
| [0006](adr/0006-rol-de-operator-fara-chei-statice.md) | Rol de operator asumat, fără utilizator IAM nou și fără chei statice |
| [0007](adr/0007-ceas-real-fara-fast-forward.md) | Ceas de perete în telemetrie, fără avansarea timpului; accelerarea vieții prin rata de uzură |
| [0008](adr/0008-doua-dashboard-uri-si-alarme-care-se-sting.md) | Două dashboard-uri CloudWatch și alarme de degradare care se sting la reparație |
| [0009](adr/0009-inspectie-vizuala-la-lama.md) | Inspecție vizuală la lama/piston, cu poartă de sortare și rută de rezervă |
| [0010](adr/0010-instanta-de-simulare-pe-spot.md) | Instanța de simulare pe piața spot, fără repornire automată |
| [0011](adr/0011-volum-de-date-persistent.md) | Volum EBS persistent pentru starea instanței, în stack separat, șters doar manual |
| [0012](adr/0012-geaman-mujoco-pentru-generare-de-telemetrie.md) | Al doilea geamăn, în MuJoCo, headless, pentru generarea de telemetrie și antrenare |
| [0013](adr/0013-colectare-cu-etichete-demonstratie-cu-model.md) | Colectare cu etichete, demonstrație cu model: eticheta nu intră pe calea de inferență |
| [0014](adr/0014-celula-simplificata-raft-cubic-fara-camera-fixa.md) | Celula se simplifică: raft cubic pe un nivel, verdictul de la camera brațului, mașină simbolică |
| [0015](adr/0015-colectare-pe-modal-cu-bucket-dedicat-si-oidc.md) | Colectarea lungă pe Modal, cu bucket dedicat și federare OIDC, fără chei statice |
| [0016](adr/0016-notebook-urile-ajung-in-repo-prin-s3.md) | Notebook-urile din Studio ajung în repo prin sincronizare în S3, cu output-urile golite |
| [0017](adr/0017-dulap-cu-trei-rafturi-si-doua-compartimente.md) | Dulap cu trei rafturi și două compartimente pe raft, cu apropiere înclinată la 45° |
| [0018](adr/0018-dulap-rotit-90-de-grade.md) | Dulapul brațului 2 este rotit 90°, cu fața vestică spre braț și două unghiuri de apropiere (înlocuit de 0023) |
| [0019](adr/0019-prinderea-fizica-si-texturi-in-geamanul-mujoco.md) | Prindere fizică în geamănul MuJoCo, cu legătură rigidă după contact, și texturi procedurale |
| [0020](adr/0020-placa-dreptunghiulara-in-a-sasea-familie.md) | A șasea familie este o placă dreptunghiulară, ca rularea să aibă o siluetă distinctă |
| [0021](adr/0021-doua-roti-si-patru-siluete-noi.md) | Doar două familii rămîn roți; restul primesc siluete de triunghi, hexagon și legătură |
| [0022](adr/0022-piesa-solida-cadru-strans-si-prindere-pe-fata.md) | Mesh cu normale plate, cameră de 46° în afara mîinii, întoarsă 4° la stânga și cu fețele din spate randate, falci aliniate la forma piesei și verdict pe ambele falci |
| [0023](adr/0023-dulap-la-vest-de-bratul-2.md) | Dulapul pe familii se mută la vest de brațul 2, cu fața estică deschisă spre braț |
| [0024](adr/0024-rotatie-continua-si-comenzi-rampate.md) | Scula se rotește continuu pe traseu, iar comenzile articulațiilor se rampează la viteza lor |
| [0025](adr/0025-incarcare-paralela-a-bratului-1.md) | Brațul 1 își încarcă următorul bloc cît brațul 2 sortează, din traiectoria înregistrată pe o copie a stării |

## Convenții

- Documentația și comentariile de design: română. Identificatorii din cod: engleză.
- Fiecare schimbare arhitecturală primește un ADR nou. ADR-urile nu se rescriu; se
  înlocuiesc printr-unul nou care îl menționează pe cel vechi.
- Diagramele stau în `docs/diagrams/` și sunt generate din sursă, nu desenate manual acolo
  unde se poate. Se regenerează cu `make diagrams`; detalii în
  [`diagrams/README.md`](diagrams/README.md).

## Diagrame

| Imagine | Ce arată |
| --- | --- |
| [architecture-loop.png](diagrams/architecture-loop.png) | bucla de date și control, peisaj, pe cinci coloane: clientul WebRTC și nodul AWS VPN, grupul Pipeline (EC2 cu Isaac Sim, IoT Core, Firehose), coloana din mijloc cu ML route (S3 raw, Data Wrangler, ML1 și ML2, pe două rânduri), Live route (Lambda-urile, pe un rând) și Observabilitate (CloudWatch, SNS, pe un rând), apoi Comandă (dispatcher plus simularea) și utilizatorul notificat |
| [architecture-platform.png](diagrams/architecture-platform.png) | platforma, peisaj: de la data lake la endpoint, operarea și guvernanța |
| [cnc-cell-layout.png](diagrams/cnc-cell-layout.png) | planul celulei CNC, văzut de sus, pe grid de 0,5 m (coloane A–L, rânduri 1–8), cu țintele de prindere și fluxul materialului |

