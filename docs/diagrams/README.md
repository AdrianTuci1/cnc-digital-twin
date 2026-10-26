# Diagrame generate din cod

Diagramele de arhitectură sunt definite în `architecture.py` cu biblioteca
[`diagrams`](https://diagrams.mingrammer.com/) („Diagram as Code"), nu desenate manual. Se
regenerează cu o comandă, deci urmează codul când arhitectura se schimbă.

Planul celulei (`cnc-cell-layout.svg` și `.png`) este scris direct ca SVG, fără nicio
dependență Python, din coordonatele pe care le împarte cu scena MJCF a geamănului MuJoCo
([ADR 0012](../adr/0012-geaman-mujoco-pentru-generare-de-telemetrie.md), plan în
[11-geaman-mujoco.md](../11-geaman-mujoco.md)).

Ambele imagini de arhitectură se citesc **stânga→dreapta, pe coloane**: fiecare etapă își are
coloana. O diagramă produce o singură imagine, fără variante.

| Fișier | Ce arată |
| --- | --- |
| `architecture-loop.png` | bucla de date și control: grupul Simulation and ingestion (geamănul MuJoCo local, IoT Core, Firehose, pe un rând), coloana din mijloc cu ML route (S3 raw, Data Wrangler, cele două modele, pe două rânduri), Live route (Lambda-urile, pe un rând) și Observability (CloudWatch, SNS, pe un rând), apoi Command (dispatcher-ul, pe un rând) și operatorul notificat |
| `architecture-platform.png` | platforma: de la data lake la endpoint, operarea și guvernanța |
| `cnc-cell-layout.png` | planul celulei CNC, văzut de sus, pe un grid de 0,5 m: banda 1, brațul 1 care încarcă și descarcă mașina, CNC-ul, banda 2 cu sens unic, brațul 2 pe latura sudică a ei, dulapul pe familii cu trei rafturi suprapuse și câte două compartimente de 0,5 m pe raft, la vest de braț, cu fața estică deschisă, lada de rebut de sub capătul de vest al benzii 2, camerele de pe flanșele brațelor, țintele de prindere și fluxul materialului |

Fiecare imagine de arhitectură are lângă ea sursa Graphviz (`.gv`), scrisă de script, utilă la
depanat layout-ul. Fișierul conține căi absolute către pictogramele din mediul virtual în care a
fost generat, deci se citește, iar randarea lui manuală cere același mediu.

## Rulare

```bash
brew install graphviz          # o singură dată: binarul `dot`, cerut de randare
brew install librsvg           # o singură dată: `rsvg-convert`, pentru PNG-ul planului
pip install diagrams           # pachetul Python
make diagrams                  # ambele diagrame de arhitectură
make cell-layout               # planul celulei (SVG + PNG)
python docs/diagrams/architecture.py loop       # doar bucla
python docs/diagrams/architecture.py platform   # doar platforma
python docs/diagrams/cell_layout.py --table     # doar tabelele de coordonate, în Markdown
```

Numele imaginilor ies din script: `architecture-<nume>.png`, `cnc-cell-layout.png`.

## De ce două diagrame

O singură imagine cu toate componentele devine greu de citit: arhitectura are două audiențe,
ca și dashboard-urile CloudWatch (ADR 0008). Cea dintâi urmărește fluxul, a doua urmărește
ciclul de viață al modelelor și operarea. Când adaugi o componentă, pune-o acolo unde o caută
cine o citește, nu unde încape.

## Layout-ul

Așezarea se gândește pe **coloane** (etapele fluxului) și **rânduri** (ramuri paralele ori
laterale). Un rang Graphviz înseamnă o coloană, iar nodurile care cad pe ranguri vecine și
sunt aliniate ajung pe același rând.

Bucla de date și control are patru coloane:

1. grupul **Simulation and ingestion** (geamănul MuJoCo local, IoT Core, Firehose), pe un
   singur rând;
2. coloana din mijloc, cu trei grupuri una sub alta, pe aceleași două coloane de noduri:
   **ML route** (cele două modele pe un rând, S3 raw și Data Wrangler pe rândul de
   dedesubt), **Live route** (cele două Lambda-uri, pe un rând) și **Observability**
   (CloudWatch, SNS, pe un rând);
3. grupul **Command** (dispatcher-ul), iar la capătul notificării SNS stă, fără grup,
   pictograma de operator.

Geamănul nu are un nod de rețea înaintea lui: viewer-ul MuJoCo și panoul rulează local, pe
mașina operatorului, iar telemetria pleacă de acolo direct în IoT Core.

Alinierea coloanei din mijloc se face cu două instrucțiuni `rank=same` scrise la rădăcină,
după ce toate grupurile s-au închis: `newrank` lasă rangul să treacă peste marginile
grupurilor, iar chenarele rămân întregi. Avantajul este că ruta ML, ruta live și
observabilitatea cad pe aceleași două coloane, deci se citesc ca un singur bloc.

Fluxul se citește într-un singur sens, fără nicio muchie care se întoarce; singura muchie
colorată este cea de comandă, de la dispatcher înapoi în simulare.

Rândul de citire al buclei este: pipeline-ul scrie brutul în S3 și invocă Lambda-ul de
transformare, ruta ML pregătește caracteristicile offline (Data Wrangler) și hrănește cele
două modele, verdictul modelelor intră în Lambda-urile rutei live, iar **doar Lambda-urile
scriu metrice** în CloudWatch. SNS iese din grup către utilizator și trimite mai departe
comanda. Săgeata dintre modele și Lambda-uri arată în diagramă drumul verdictului; în
realitate Lambda-ul este cel care invocă endpoint-ul, cum scrie pasul 3 din
`docs/01-architecture.md`.

În grupul **ML route** nu se desenează nicio săgeată între S3 raw, Data Wrangler și cele două
modele: pașii stau în textul documentației, iar muchiile dintre ele sunt invizibile, doar ca
să țină nodurile în aceeași grilă. Singurele săgeți ale grupului pornesc de la modele către
Lambda-urile rutei live.

Nodurile au nume scurte — MuJoCo twin, IoT Core, Firehose, S3 raw, Data Wrangler, ML1, ML2,
CloudWatch, SNS, Dispatcher, Operator — iar **muchiile nu poartă etichete**: ce se întâmplă
pe fiecare muchie stă în `docs/01-architecture.md`. Geamănul poartă pictograma de client,
fiindcă rulează local, pe mașina operatorului. Camera de la lamă, coada SQS și certificatul
IoT rămân în textul documentației, fără nod propriu.

Etichetele și titlurile diagramelor sunt în engleză, fiindcă imaginile se citesc din
`README.md`, care este în engleză; restul documentației rămâne în română.

Pictogramele vin din bibliotecă. Data Wrangler nu are pictogramă
proprie nici în bibliotecă, nici în pachetul oficial AWS de pictograme de arhitectură
(verificat pe ediția 31.07.2026; pentru SageMaker are doar AI, Ground Truth și Studio Lab, iar
ca pictograme de resursă Notebook, Train, Model, Canvas, Shadow Testing și Geospatial ML),
așa că nodul folosește pictograma de bază de date a serviciului Athena, cu numele schimbat.
Cele două pictograme SageMaker sunt cele două endpoint-uri serverless: ML1 pentru anomalii și
ML2 pentru viziune. În diagrama platformei stau sub un singur nod, ca promovarea să rămână o
singură muchie.

## Gridul planului de celulă

Planul are originea (0, 0) în colțul sud-vest al celulei, +X spre est și +Y spre nord, iar
conversia la lumea MuJoCo este identitatea: planul `(x, y)` devine lumea `(x, y)`, cu +Z în sus.
Celula măsoară 6,0 × 4,0 m și este tăiată în celule de 0,5 m, cu **coloanele A–L** spre est și
**rândurile 1–8** spre nord, așa cum se vede pe marginile desenului. Un element se numește pe
scurt după celula lui: brațul 1 stă în **F6**, mașina ocupă **G6–I7**, banda 2 în **B4–J5**,
brațul 2 în **F4**, dulapul pe familii în **D2–E4**, lada de rebut în **A4–B5**.

Tabelul de coordonate și verificarea razei de atingere se tipăresc cu `--table`; el este cel
lipit în `docs/11-geaman-mujoco.md`, deci nu se transcriu cifre manual. Când se mută o
componentă, se schimbă doar lista din script și se regenerează desenul și tabelele.

Ce nu este în plan: înălțimile (Z) și geometria interioară a mașinii. Raza de atingere de
0,855 m este proiectată în plan, deci o țintă poate părea la îndemână și să fie prea sus sau
prea jos; verificarea în spațiu se face în scenă, când ea există.

## Capcane

- **Layout-ul îl face Graphviz, nu noi.** Prea multe noduri și prea multe clustere produc
  imagini înghesuite și etichete suprapuse. Ce se poate spune într-o etichetă (certificatul
  IoT, schema Glue, coada SQS) nu primește un nod propriu.
- **Rândurile din coloana din mijloc se țin cu `same_rank()` la rădăcină.** Ruta ML, ruta
  live și observabilitatea nu se pot alinia singure, fiindcă fiecare etapă avansează cu o
  coloană; perechile scrise la final le aduc pe aceleași două coloane. Ordinea rândurilor în
  interiorul unei perechi o hotărăște Graphviz (la ruta ML, rândul modelelor iese deasupra
  celui cu S3 raw și Data Wrangler), deci nu o forțați din liste sau din ponderi de muchii.
- **`rank=same` la rădăcină are nevoie de `newrank`.** Fără atribut, Graphviz scoate nodurile
  din clustere și chenarele dispar. Cu el, chenarele rămân întregi și rangul trece peste
  marginile grupurilor.
- **Id-urile nodurilor au nevoie de ghilimele** în instrucțiunea de rang: sunt hexazecimale,
  iar cele care încep cu cifră ar fi citite ca numere și rupte în bucăți.
- **Un lanț de noduri pe același rang poate citi de jos în sus**, fiindcă Graphviz aliniază
  intrarea coloanei cu nodurile care o alimentează. Săgețile rămân lizibile; dacă ordinea
  contează, lasă nodurile pe coloane succesive.
- **Lățimea etichetei trebuie să încapă în pictogramă** (1,4 țoli). Rândurile mai late ies din
  cutie, iar vârful săgeții care intră din lateral ajunge peste text. Ține rândurile la
  ~16 caractere; Graphviz avertizează cu „node size too small for label".
- **Clusterele au nevoie de margine interioară** (`group()` din script), altfel chenarul taie
  etichetele nodurilor de la margine.
- **Nodurile izolate atârnă.** Un cluster cu noduri fără nicio muchie ajunge într-un colț, ca
  o anexă. Dacă o componentă nu are legătură în diagramă, las-o în textul documentației.
