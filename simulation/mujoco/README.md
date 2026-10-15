# Geamănul MuJoCo al celulei CNC

A doua implementare a celulei, lângă cea din Isaac Sim: aceeași celulă, alt motor de fizică, ca
scopul ei real — ore de telemetrie brută și ferestre de antrenare pentru detectorul de anomalii —
să ruleze pe CPU, în afara Kit, fără instanță GPU. Decizia și consecințele ei sunt în
`docs/adr/0012-geaman-mujoco-pentru-generare-de-telemetrie.md`; descrierea celulei, înălțimile și
fluxul materialului sunt în `docs/11-geaman-mujoco.md`, care rămâne sursa de adevăr.

Directorul nu conține modele de roboți: brațele vin din pachetul `mujoco-menagerie`, iar piesa din
generatorul procedural existent, `simulation/extensions/cncdt.cnc_cell`. Nimic nu se copiază în
repo.

## Instalare

Mediul este separat de `.venv`-ul proiectului (ADR 0012, decizia 7) și este ignorat de git:

```bash
python3 -m venv simulation/mujoco/.venv
simulation/mujoco/.venv/bin/python -m pip install -r simulation/mujoco/requirements.txt
```

Verificat pe macOS arm64, Python 3.14.6, `mujoco==3.15.0`, `mujoco-menagerie==2026.10.1`.

## Comenzi

Din rădăcina repo-ului, țintele Makefile acoperă tot ce se rulează des:

```bash
make mj-venv     # o singură dată: creează venv-ul geamănului și instalează requirements.txt
make mj-check    # poarta: compilează scena, măsoară, raportează; iese cu 1 dacă ceva cade
make mj-test     # testele geamănului care ating motorul, din venv-ul lui
make mj-render   # scrie renders/cell.png și renders/inspection.png
make mj-demo     # rulează ciclul brațelor și tipărește cifrele fiecărei opriri
make mj-record   # colectează sesiuni de antrenare, câte una pe familie, în runs/
make mj-collect  # colectează (MJ_COLLECT_CYCLES cicluri pe familie) și urcă în bucketul data lake
make mj-collect-plan   # tipărește ce ar urca `make mj-collect`, fără să atingă bucketul
make mj-publish  # urcă în bucket sesiunile deja scrise în runs/
make mj-check MJ_SAMPLES=2000   # degajarea camerei, pe mai multe configurații de braț
make mj-demo MJ_FAMILIES="wheel_100 pinion_56"   # doar familiile cerute, ca proba să fie scurtă
make mj-record MJ_RECORD_CYCLES=20   # sesiune de antrenare: 20 de cicluri pe familie
make mj-collect MJ_COLLECT_CYCLES=20 MJ_COLLECT_SEED=100   # colectare lungă, urcată direct
```

`make mj-collect` are nevoie de `DATA_LAKE_BUCKET` în `.env` (ieșirea `data_lake_bucket` a
stack-ului `platform`) și de credențialele rolului de operator, luate cu
`eval "$(./scripts/assume-operator.sh)"`. Fără ele, refuză înainte să atingă bucketul.

Fără Makefile, aceleași lucruri se scriu direct:

```bash
MUJOCO=simulation/mujoco/.venv/bin/python

# Verificarea implicită: compilează scena, așază piesele, rulează pași, raportează cifrele.
$MUJOCO simulation/mujoco/view.py --check

# Ciclul brațelor, punct cu punct: eroarea, unghiul, contactele și unde au ajuns piesele.
$MUJOCO simulation/mujoco/view.py --demo

# Același ciclu, cu un PNG la fiecare oprire, în renders/demo/ (director generat, ignorat de git).
$MUJOCO simulation/mujoco/view.py --demo --shots

# Sesiunea de colectare: telemetrie, cadre, metadate și etichete, în runs/<familie>-<sămânță>/.
$MUJOCO simulation/mujoco/run.py --cycles 2
$MUJOCO simulation/mujoco/run.py --family pinion_56 --cycles 20 --out /tmp/sesiune
$MUJOCO simulation/mujoco/run.py --cycles 5 --no-frames   # doar telemetrie, mult mai rapid
$MUJOCO simulation/mujoco/run.py --cycles 5 --rate 50     # viața mașinii comprimată de 50 de ori
$MUJOCO simulation/mujoco/run.py --cycles 2 --inject belt_out:0.5 --inject cnc_tool:0.7

# Un cadru al celulei, din camera liberă (implicit renders/cell.png).
$MUJOCO simulation/mujoco/view.py --render

# Cadrul camerei de inspecție, la rezoluția ei nativă (renders/inspection.png).
$MUJOCO simulation/mujoco/view.py --render --camera camera_quality

# Viewer interactiv, cu benzile pornite. Pe macOS fereastra se creează doar pe firul principal,
# deci cere `mjpython`, care vine în wheel-ul `mujoco`; semnul că rulăm sub el este variabila
# `MJPYTHON_BIN`, fiindcă `sys.executable` rămâne interpretul din venv.
simulation/mujoco/.venv/bin/mjpython simulation/mujoco/view.py --view

# Aceeași fereastră, cu ciclul brațelor în locul benzilor goale: ăsta e modul în care se văd
# brațele mișcându-se.
simulation/mujoco/.venv/bin/mjpython simulation/mujoco/view.py --view --demo

# Cu amândouă piesele lăsate mai în amonte și în reluare lentă, ca transportul până la liniile de
# oprire să se vadă (altfel piesa mașinată pornește chiar sub cameră, iar cea brută chiar la linia
# benzii 1).
simulation/mujoco/.venv/bin/mjpython simulation/mujoco/view.py \
    --view --speed 0.5 --blank-x 0.6 --part-x 4.4
```

`--check` iese cu cod 1 dacă vreo verificare cade, ca să poată fi folosit ca poartă, iar `--demo`
iese cu cod 1 dacă vreo oprire a ciclului nu ajunge la punct ori dacă o verigă a brațului atinge
celula. Restul opțiunilor: `--steps`, `--samples` (configurații de braț eșantionate la degajarea
camerei), `--seed`, `--defect`, `--family` (familia de piese: alege piesa, blocul brut,
compartimentul din raft și cota de lucru), `--blank-shape`, `--blank-x`/`--blank-y`,
`--blank-yaw-deg`, `--part-yaw-deg`, `--part-x`/`--part-y`, `--camera-z`, `--base-z` (cota
piedestalului brațelor, pentru măsurători), `--demo-settle` (câți pași se așteaptă la fiecare
oprire), `--shots [DIR]`,
`--width`, `--height`, iar pentru viewer `--belts`/`--no-belts` și `--speed`. La pozițiile cerute
explicit, cota care lipsește rămâne cea din plan: `--blank-x 0.6` mută piesa pe banda 1, fără să
ceară și `--blank-y`.

Viewer-ul rulează în ritm real și tipărește în terminal, la fiecare secundă simulată, ce piesă e pe
ce bandă, cu ce viteză și cât mai are până la linia de oprire. `--speed 0` scoate ritmul real, ca
sesiunea să alerge cât poate.

Suita proiectului (`make test`) rulează fără `mujoco` și include partea pură a testelor
geamănului, `tests/unit/test_mujoco_scene.py`, plus contractele colectării
(`tests/unit/test_mj_telemetry.py`), motorul de uzură (`tests/unit/test_mj_wear.py`) și corpusul
de antrenare (`tests/unit/test_mj_export.py`); testul care
atinge scena se sare acolo și se rulează cu `make mj-test`, din venv-ul care are motorul.

## Structură

| Fișier | Ce conține |
| --- | --- |
| `cncdt_mj/layout.py` | Așezarea în plan și înălțimile. Coordonatele `(x, y)` **nu** se transcriu: se importă din `docs/diagrams/cell_layout.py`, ca planul și scena să nu se poată contrazice. Înălțimile `Z` se scriu aici, cu fragmentul din documentație de care aparțin. **Fără MuJoCo.** |
| `cncdt_mj/part.py` | Cele două stări ale materialului: blocul brut (disc cu adaos de prelucrare) și roata dințată din generator. Geometria iese în milimetri, în planul `XY`, cu grosimea pe `Z`; se scalează în metri. Aici stau și pozițiile de așezare: `stopped_xy` pentru piesa așezată la linia de oprire a benzii, `on_belt_xy` pentru una cerută explicit. **Fără MuJoCo.** |
| `cncdt_mj/belt.py` | Benzile: direcția fiecărei benzi, viteza de suprafață și forța de antrenare cu limita de frecare Coulomb. **Fără MuJoCo.** |
| `cncdt_mj/checks.py` | Regulile verificării care lucrează pe nume de corpuri și pe cifre, nu pe starea motorului (de pildă care contacte ale unui braț sunt normale), plus pragurile verificării, împărțite cu `view.py`. **Fără MuJoCo.** |
| `cncdt_mj/moves.py` | Mișcările: ce puncte deservește fiecare braț, în ce ordine, din ce parte vine scula, cu ce rotire și prin ce puncte de trecere. Cotele punctelor de lucru ies din înălțimile documentate. **Fără MuJoCo.** |
| `cncdt_mj/povs.py` | Cele patru puncte de vedere din care se strânge datasetul: camera, rezoluția, momentul de captură (un punct de lucru ori cota de pe bandă) și rolul din contract. **Fără MuJoCo.** |
| `cncdt_mj/telemetry.py` | Contractele: eșantionul brut cu exact câmpurile din `cnc.telemetry.v1.json`, metadatele cadrului de inspecție, etichetele sesiunii, plus un validator mic peste fișierele de schemă (fără dependența `jsonschema`). **Fără MuJoCo.** |
| `cncdt_mj/wear.py` | Motorul de uzură: sănătatea celor șapte componente pe timpul lor de lucru, efectele în mărimile din contract, eșecurile discrete cu hazard crescător și acțiunile de operator, cu etichetele lor. **Fără MuJoCo.** |
| `cncdt_mj/export.py` | Corpusul de antrenare: așezarea cheilor în bucket, manifestul și verificările care opresc urcarea unui corpus cu găuri. **Fără MuJoCo.** |
| `cncdt_mj/scene.py` | Compunerea scenei: `scene/cell.xml` plus cele două brațe atașate programatic, cu camerele de pe flanșe. Unul dintre modulele care importă `mujoco`. |
| `cncdt_mj/cell.py` | Celula în mișcare: cine e în contact cu banda, ce viteză are piesa, ce forță se scrie în `data.xfrc_applied`. Al doilea modul care importă `mujoco`. |
| `cncdt_mj/arms.py` | Brațele: numele articulațiilor și ale actuatoarelor, lungimea sculei măsurată în scenă, cinematica inversă, urmărirea liniei dintre două puncte și strângerea. Importă `mujoco`. |
| `cncdt_mj/sensors.py` | Din starea motorului în mărimi de proces: efortul articulațiilor, forța falcilor, poziția și viteza pieselor, sarcina de contact. Importă `mujoco`. |
| `cncdt_mj/camera.py` | Randarea offscreen: scrierea PNG fără dependențe externe, cadrele știute ale camerei libere și ținerea minte a randatoarelor. Importă `mujoco`. |
| `scene/cell.xml` | Șablonul celulei. Cifrele nu stau în el: se pun prin substituție, din `layout.scene_geometry()`. |
| `view.py` | Cele patru moduri: `--check`, `--render`, `--demo`, `--view`. |
| `run.py` | Sesiunea de colectare: cicluri întregi, uzura care se acumulează peste ele, telemetrie, cadre, fișierele sesiunii și verificarea lor la sfârșit. |
| `publish.py` | Urcarea corpusului în bucketul data lake, cu AWS CLI: plan fără `--upload`, credențiale de operator, manifest scris la rădăcina rulării. Doar biblioteca standard. |

Consecința practică a împărțirii: `layout`, `part`, `belt`, `checks`, `moves`, `povs`, `telemetry`,
`wear` și `export` se importă din
`.venv`-ul proiectului, unde `mujoco` nu există (ADR 0012), iar tot ce atinge motorul se validează
rulând `view.py --check`. Lista modulelor de motor (`scene.py`, `cell.py`, `arms.py`, `camera.py`,
`sensors.py`) este scrisă
explicit în `tests/unit/test_mujoco_scene.py`: un modul nou care importă `mujoco` trebuie declarat
acolo, altfel testul cade.

### De ce scena se compune în Python, nu în MJCF

MJCF nu poate aduce singur modelele din Menagerie: atributul `<attach model="...">` **nu
funcționează** în MuJoCo 3.15.0 (încercat cu patru forme de cale), iar `modeldir` nu există pe
`<compiler>`. Deci `scene/cell.xml` ține doar componentele celulei, iar brațele se atașează cu
`MjSpec.attach(child, prefix=..., frame=body.to_frame())` și `copy_during_attach = True` pentru al
doilea braț. Prefixele de nume (`arm1_`, `arm2_`) țin cele două brațe separate, iar celula își
scrie propriile poziții nominale, fiindcă ale modelului copil se importă și s-ar ciocni.

## Ce verifică `--check`

Cifrele de referință, la sămânța implicită 7: `nq/nv/nu = 32/30/16`, 33 de corpuri, 214 geome, zero
avertismente MuJoCo. Piesele se așază pe benzi cu 0,1 mm derivă, iar yaw-ul cerut se citește înapoi
din scenă (comparat pe cerc, deci 348,5° și −11,5° sunt același unghi).

Țintele de prindere, măsurate **în spațiu** de la baza brațului, față de raza de 0,855 m din plan:
brațul 1 ajunge la 0,704 m (preluare bandă 1), 0,739 m (mașină) și 0,723 m (așezare bandă 2);
brațul 2 la 0,461 m (preluare bandă 2) și între 0,310 m (locul 1) și 0,637 m (locul 5) la raft.
Planul măsoară doar în plan, iar `docs/11-geaman-mujoco.md` spune
explicit că o țintă poate părea la îndemână în desen și să fie prea sus — verificarea de aici este
cea în spațiu.

Se mai verifică trei lucruri la camera de inspecție: degajarea față de ambele brațe (poziția
nominală plus `--samples` configurații eșantionate, prag 0,02 m), încadrarea piesei — distanța,
abaterea de la ax și diametrul aparent în pixeli, față de pragul de 3,0 px/mm — și faptul că
niciun braț nu atinge un element al celulei la poziția nominală. Autocontactele unui braț
(`arm1_hand↔arm1_link5`) sunt normale și se ignoră.

## Brațele: ciclul din `--demo`

`--demo` este partea care se vede: piesele sunt aduse întâi la liniile de preluare
(`bring_parts_to_pickup`, cu benzile pornite), apoi fiecare braț își parcurge punctele, în ordinea
din `moves.ROUTES` — brațul 1 ia blocul de pe banda 1, îl duce în universal și lasă roata pe banda
2, iar brațul 2 preia roata și o așază în compartimentul familiei ei, pe raft (`--family` alege
familia, deci și compartimentul). La fiecare oprire se tipăresc eroarea
rămasă (poziție și rotire a sculei), cea mai mare abatere de pe drum, câți pași a durat, contactele
și, la sfârșit, unde au ajuns piesele. Iese cu cod 1 dacă o oprire nu ajunge la punct ori dacă o
**verigă** a brațului atinge celula; atingerea falcilor se tipărește fără să fie eșec, fiindcă
falcile sunt scula.

Un punct se atinge așa: `arms.solve_point` caută configurația prin cinematica inversă amortizată pe
jacobianul corpului mâinii, pornind din două sămânțe — comanda curentă și poziția nominală — și
tăind soluția la capetele actuatoarelor; dacă rotirea cerută de plan nu prinde ținta la 2 mm,
rotirea se caută din 45° în 45°. Apoi `arms.follow_line` merge în linie dreaptă în spațiul sculei,
din 10 mm în 10 mm, la 0,15 m/s, fiecare punct rezolvat din cel dinainte. Falcile rămân deschise
toată cursa: actuatorul lor primește comanda nominală, iar `arms.set_joint_controls` scrie doar cele
șapte articulații.

Lungimea sculei nu se presupune: se măsoară în scenă, de la flanșă la punctul de strângere, și iese
**102,9 mm** pe amândouă brațele. Ea intră în retragerea de deasupra punctului
(`moves.approach_height`), care la așezarea pe banda 2 rămâne de câțiva centimetri — acolo raza
brațului se termină.

Două lecții de drum, amândouă măsurate:

1. **Ridicarea după fiecare punct.** Fără ea, drumul spre punctul următor pornește de la cota de
   lucru și mătură banda cu scula: blocul brut chiar era împins de pe banda 1, până pe podea
   (`z = 9 mm` în loc de 859 mm). Cu ridicare, amândouă piesele rămân pe benzile lor.
2. **Punctul de trecere pe la vest de mașină** (`moves.VIA`). Dreapta dintre universal și așezarea
   pe banda 2 iese din mașină exact prin colțul ei sud-vestic; brațul nu are planificator de
   traiectorie, deci intră în carcasă. Măsurat: fără punctul de trecere scula rămânea cu 880 mm de
   țintă și mâna pe mașină, cu el eroarea maximă a ciclului este 17,7 mm.

Piedestalul brațelor este cota care nu se poate decide din plan. La 0,55 m ciclul **cade**: ca să
ridice piesa de pe banda 2, brațul 2 trebuie să treacă cu antebrațul peste platformă, iar acolo
platforma este deasupra lui (`arm2_link3↔belt_out`, 106 mm eroare la punctul de deasupra), iar
locurile din raft devin de neatins (`arm2_link3↔rack`, peste 1 m eroare). La 0,95 m, cota
documentată acum, `make mj-demo` încheie ciclul pentru toate cele cinci familii: 17,7 mm eroare
maximă pe cele 80 de opriri, nicio verigă atinsă, 56,4 s simulate. Cota ridică însă brațele peste
axa benzii 2, deci camera fixă urcă la cota documentată a camerelor, 2,20 m; alegerea este scrisă în
`docs/11-geaman-mujoco.md` §2 și §10. `view.py --base-z` coboară cota pentru măsurători.

Raftul, pe rând: la 0,95 m, toate cele cinci locuri se ating cu eroare sub 18 mm (`--family` alege
familia), în orice variantă; la 0,55 m, locurile se ating cu peste 1 m eroare la al cincilea. Deci
mutarea cutiilor în raft a scos piedica pe care o puneau cutiile adânci; ce a rămas la 0,55 m este
banda 2 în sine, nu sortarea.

**Ce lipsește: prinderea.** Mâna Panda se deschide 83 mm, blocul brut are 104 mm și universalul
110 mm, deci falcile nu pot cuprinde piesa — de aceea o ating razant pe toată cursa. Celula reală
din Isaac Sim prinde la fel: cu o legătură rigidă (`grasping.py`, `/World/Robot1/GraspJoint`).
Geamănul o va oglindi cu o egalitate de tip weld, pornită și oprită prin `data.eq_active`. Până
atunci ciclul nu ridică nimic, iar `--demo` tipărește unde a lăsat fiecare piesă, fără să cadă
din asta.

## Camera de inspecție

Camera stă la cota documentată a camerelor, **2,20 m** în lume, adică **1,35 m deasupra suprafeței
benzii 2**, și randează la **640×480** cu **5,6° pe verticală**. Acoperă **176 × 132 mm** la nivelul
benzii, adică **3,6 px/mm** pe piesă: roata de 104 mm intră pe ~59% din lățimea cadrului (366 px în
`--check`). Cota este parametru (`--camera-z`, ori `layout.inspection_camera_z()`), iar
intrinsics-urile se calculează din geometrie, în `layout.inspection_camera_intrinsics()`, nu se
afirmă. Sub cota documentată geometria refuză compunerea scenei: `camera_geometry` ridică
`ValueError`.

**De ce nu mai jos**: pe plaja articulațiilor, geomele unui braț urcă peste axa benzii 2 până la
~2,10 m în lume, adică ~1,15 m peste baza de la 0,95 m (cotul), iar capul optic stă deasupra lor.
La cota documentată, golul minim măsurat pe 2000 de configurații eșantionate este 0,115 m față de
brațul 1 și 0,095 m față de brațul 2.

**De ce 5,6° și nu un obiectiv normal**: la 2,20 m un obiectiv care acoperă ~1,5 m din cadru lasă
piesa de ~104 mm la vreo 10 pixeli, iar cel mai fin defect din catalog — o gaură dezaxată cu 2,5 mm
— ar cădea sub un pixel, deci nicio clasă de defect nu ar fi separabilă. Obiectivul s-a îngustat
până când acoperirea la nivelul benzii — deci rezoluția pe piesă — a ajuns aceeași cu cea a
variantei de dinainte: un dinte ciobit de 4 mm iese pe ~14 pixeli și dezaxarea de 2,5 mm pe ~9.

**Cum stă**: consola camerei se prinde pe capacul mașinii CNC, la 0,25 m vest de axul ei (`H6`), și
se așază pe el, la 1,90 m; de capătul ei atârnă capul optic pe o tijă subțire, iar brațul consolei
trece imediat deasupra capului optic. Anvelopa brațelor rămâne dedesubt: cea mai mică distanță
măsurată este 0,115 m (brațul 1) și 0,095 m (brațul 2) la 2000 de configurații eșantionate, peste
pragul de 0,02 m. Capul optic nu poate sta chiar în punctul camerei din plan: o cameră care privește
în jos trebuie să fie deasupra subiectului ei, iar capul stă deasupra axei benzii 2, la mijlocul
distanței dintre locul de așezare (x=3,4) și cel de preluare (x=2,7).

**Axa de privire**: camerele MuJoCo privesc de-a lungul axei lor locale **−Z**, nu +Z (verificat cu
o scenă de probă: două cutii, una deasupra și una dedesubt). Prima variantă întorcea camera cu
`quat="0 1 0 0"`, ceea ce o făcea să privească spre cer; `renders/inspection.png` era numai cer și
pământ. Acum orientarea este implicită („în jos"), iar `--check` verifică explicit că piesa intră
în cadru, ca bug-ul să nu poată reveni tăcut.

Ce **nu** rezolvă camera: 3,6 px/mm înseamnă că diferențele sub ~0,3 mm nu sunt măsurabile, iar
degradarea camerei (`docs/11-geaman-mujoco.md`, secțiunea 3) va adăuga blur proporțional cu
`1 - h_camera` peste ele — deocamdată cadrele ies curate.
Cadrul este suficient pentru catalogul actual de defecte, nu pentru unul mai fin: dacă
defectele se rafînează, se schimbă întâi distanța ori rezoluția, nu clasificatorul.

Camerele de prindere stau pe flanșa fiecărui braț, cu 60° pe verticală, la 640×480 — aceeași
rezoluție ca a camerei fixe, ca două cadre să se poată pune unul peste altul. Vezi secțiunea
„Colectarea datelor de antrenare" pentru ce se vede în ele.

## Colectarea datelor de antrenare

`run.py` este pipeline-ul: parcurge cicluri întregi și scrie exact ce cere antrenarea. `make
mj-record` îl cheamă o dată pe familie, iar sesiunile ajung în `runs/<familie>-<sămânță>/`
(director generat, ignorat de git).

```
runs/wheel_100-7/
  samples.jsonl        # eșantioane brute, 10 Hz: exact câmpurile din cnc.telemetry.v1.json
  snapshots.jsonl      # metadatele cadrelor de inspecție: exact cnc.qa.snapshot.v1.json
  frames.jsonl         # indexul cadrelor: cale, punct de vedere, moment, eticheta piesei
  readings.jsonl       # diagnostic: efort pe braț, forță de strângere, poziția piesei, contacte
  wear.jsonl           # sănătatea fiecărei componente, aliniată cu eșantioanele pe sim_time_s
  events.jsonl         # etichetele: eșecurile componentelor și intervențiile de operator
  frames/cyc-000001/   # PNG-urile, unul pe punct de vedere și moment
  session.json         # parametrii, versiunea MuJoCo, uzura, totalurile, etichetele ciclurilor
```

**Urcarea în bucket** este `make mj-collect`: scrie fiecare familie într-un director de rulare
propriu (`runs/run-<UTC>/<familie>-<sămânță>/`, `MJ_COLLECT_CYCLES` cicluri pe familie) și apoi
urcă tot sub prefixul corpusului, senzorii și cadrele împreună:

```
training-data/mujoco/dt=<zi>/run-<UTC>/manifest.json      # ce conține corpusul, cu ce contract și de unde se citește
training-data/mujoco/dt=<zi>/run-<UTC>/<familie>-<sămânță>/{samples.jsonl, frames.jsonl, …, frames/}
```

Prefixul `datasets/` este al corpusului offline, nu al căii live: `raw/telemetry/` este Parquet de
ferestre scris de Firehose și citit de tabelul Glue, iar `raw/qa-snapshots/` este chiar
declanșatorul Lambda-ului de inspecție. Corpusul se citește direct, cu
`ml/feature_engineering/build_features.py` ori cu `cncdt_ml.offline`. Motivul întreg: `docs/11-geaman-mujoco.md` §4.

`simulation/mujoco/publish.py` face urcarea cu **AWS CLI** (`aws s3 cp --recursive`), deci nu se
adaugă `boto3` în niciunul dintre venv-uri. Verificarea dinaintea urcării stă în
`cncdt_mj/export.py`, pur și testat: fișiere lipsă ori goale, rânduri de cadre și PNG-uri pe disc
față de totalul din `session.json`, rânduri de uzură față de eșantioane. Un corpus cu găuri se
refuză, iar fără `--upload` comanda doar citește și tipărește planul:

```bash
# Ce s-ar urca, cu manifestul corpusului, fără să atingă bucketul.
.venv/bin/python simulation/mujoco/publish.py --run-dir simulation/mujoco/runs \
    --bucket exemplu-data-lake

# Urcare directă, dintr-un director de rulare anume, sub un prefix ales.
.venv/bin/python simulation/mujoco/publish.py --run-dir simulation/mujoco/runs \
    --bucket "$DATA_LAKE_BUCKET" --prefix training-data/mujoco --upload
```

`publish.py` are nevoie doar de biblioteca standard, deci merge din `.venv`-ul proiectului: nu are
de ce să intre în venv-ul geamănului, unde stă motorul.

**Ce garantează corpusul pentru un model de uzură.** `wear.jsonl` are un rând la fiecare eșantion,
aliniat pe `ts_ms` și `cycle_id` cu `samples.jsonl`, cu sănătatea tuturor celor șapte componente,
indicatorii din contract și scala de viteză a benzilor; ceasul seriei are pas median 100 ms, cu
pași de 124–190 ms la hotarul dintre cicluri, numărați în `coverage.gaps`. Efectele se văd în
măsurători: viteza benzii coboară de la 0,12000 la 0,11652 m/s la `h = 0,638`, iar efortul brațului 1
urcă la 38,6 Nm la `h = 0,435`. La `--rate 1` un ciclu mișcă sănătatea cu 0,006, deci
`MJ_COLLECT_RATE` are implicit 20 (o colectare de 5 cicluri ajunge la `h ≈ 0,48`), iar rata folosită
se scrie în `session.json`. Etichetele de eșec sunt stohastice: 5 cicluri la rata 20 nu prind
niciunul, `--rate 50 --cycles 10` prinde 6, iar `--rate 50 --cycles 5` cu trei injecții prinde
`tool_break` — rețetele sunt măsurate, în `docs/11-geaman-mujoco.md` §4. `make mj-collect` urcă prin
`--require-wear`, deci un corpus fără nicio componentă sub `h = 0,9` și fără eșecuri este refuzat, iar
cifrele de uzură ale fiecărei sesiuni ajung în `manifest.json`.

**Uzura** este a sesiunii, nu a ciclului: scena se recompune la fiecare ciclu, modelul de uzură nu
(`cncdt_mj/wear.py`, vezi `docs/11-geaman-mujoco.md` §3). Fiecare interval are timpul lui de lucru —
benzile cară, brațul se mișcă, mașina taie —, iar ce nu lucrează nu se uzează. Două pârghii de pe
linia de comandă:

```bash
# două cicluri cu viața mașinii comprimată de 50 de ori, pornind cu banda 2 pe jumătate uzată
$MUJOCO simulation/mujoco/run.py --cycles 2 --rate 50 --inject belt_out:0.5

# fereastra de așchiere mai lungă, ca scula să apuce să se uzeze într-o sesiune scurtă
$MUJOCO simulation/mujoco/run.py --cycles 5 --machining-s 120
```

Fără ele, un ciclu de 65 s uzează prea puțin ca să se vadă ceva: benzile ajung la 99,7% din
sănătate, scula la 99,4%, iar în `events.jsonl` nu apare nimic. Cu `--rate`, aceeași sesiune prinde
eșecuri reale (`belt_slip`, `spindle_bearing`, `tool_break`) și le scrie ca etichete.

**Mașina nu se animă.** Nu are axe în scenă: prelucrarea este fereastra `CUTTING` de după ce brațul
1 lasă piesa în universal, iar cifrele ei — sarcină de ax, vibrație, temperatură, avans, debit de
răcire, `tool_wear_index` — ies din motorul de uzură.

**Cele patru puncte de vedere** sunt registrul din `cncdt_mj/povs.py`, iar pe ciclu ies cinci
cadre:

| Punct de vedere | Cameră | Moment | Rol |
| --- | --- | --- | --- |
| `arm1_wrist` | pe flanșa brațului 1 | la preluarea blocului brut și în universal | — |
| `arm2_wrist` | pe flanșa brațului 2 | la preluarea roții de pe banda 2 | `sorting` — verdictul de calitate |
| `process` | fixă, deasupra benzii 2 | când piesa trece prin dreptul ei | `inspection` |
| `cell` | liberă, cadru larg | când piesa ajunge în locul familiei | — |

Verdictul care decide ruta este cadrul de pe **flanșa brațului 2**: el vede piesa ținută în falci,
de aproape, la ~2,6 px/mm. Cadrul fix rămâne pentru comparație și pentru dataset, la 3,6 px/mm.
Niciunul nu se dublează: cele două cadre cu rol intră în `snapshots.jsonl`, toate patru intră în
`frames.jsonl`, alături de familie, defect și verdict.

**Verificarea la sfârșitul sesiunii** validează fiecare eșantion și fiecare cadru de inspecție
contra fișierelor de schemă, numără cadrele față de plan și tipărește dimensiunile; ieșirea este
„Sesiune: verde" ori „Sesiune: CĂZUTĂ", cu codul de ieșire potrivit.

**Determinism**: două rulări cu aceeași sămânță și același director de ieșire dau fișiere identice
bit cu bit — eșantioane, metadate, indexul cadrelor, diagnosticul și toate PNG-urile (verificat cu
`diff -r` pe două sesiuni de câte două cicluri). Singura cifră care diferă este timpul de ceas real
din `session.json`.

Ce se vede azi în cadre, cinstit: cadrul de proces se ia **la începutul ciclului**, fiindcă în
geamăn piesa mașinată stă deja sub camera fixă când începe ciclul, iar banda o duce întâi la linia
de preluare a brațului 2. Cadrul de pe flanșa brațului 1 arată falcile deasupra universalului gol,
fiindcă prinderea nu este modelată. Amândouă se schimbă cu prinderea, nu cu randarea.


## Benzile: forță în cod, linie de oprire în plan

MuJoCo nu are bandă transportoare nativă, așa că transportul se împarte în două: forța care
târăște piesa o calculează `belt.py` (pur), iar `cell.py` o scrie la fiecare pas în
`data.xfrc_applied` pentru piesele care ating platforma benzii. Forța e proporțională cu diferența
dintre viteza de suprafață (0,12 m/s) și viteza piesei, dar limitată de frecarea Coulomb
(`|F| <= mu * N`): peste limită contactul alunecă, iar piesa rămâne în urmă. `speed_scale` este
locul unde intră uzura benzii, care scade viteza reală cu până la 8% (`docs/11-geaman-mujoco.md`
§3b); `run.py` îl cheamă cu o scală **pe fiecare bandă**, din sănătatea benzii respective. Se vede
în `readings.jsonl`: într-un ciclu nominal `belt_speed_mps` scade de la 0,119999 la 0,119968, iar
cu benzile injectate la 0,5 sănătate ciclul se lungește cu ~0,4 s, fiindcă transportul la liniile de
preluare ține 4% mai mult.

**Cine oprește piesa este banda însăși**: nu există geom de opritor, fiindcă nici celula reală nu
are nevoie de unul. `layout.belt_stop_x` dă cota din lungul benzii până la care banda mai împinge
piesa — chiar ținta de preluare — iar `belt.Belt.reached_stop` o întrebuințează la fiecare pas: de
acolo piesa primește forță zero și alunecă singură, până o oprește frecarea. Brațul prinde un disc,
deci cota din lungul benzii nu trebuie să fie exactă, iar cât anume alunecă peste linie se măsoară
în scenă, nu se presupune.

**Capcana care a costat o rundă de măsurători**: dacă platforma benzii are frecare mare, frecarea
statică dintre piesă și platformă anulează forța aplicată — piesa stă pe loc, deși codul o împinge.
Platformele au deci `friction="0.05 0.01 0.001" priority="1"` (prioritatea face frecarea benzii să
câștige în fața celei a piesei, altfel MuJoCo ia maximul element cu element și iese 1,0), iar
aderența benzii este modelată exclusiv de forța exterioară din `belt.py`. Șinele păstrează frecare
mare, ca să țină piesa în canal. O piesă atinge platforma abia după primul pas, deci prima linie de
transport din viewer poate să o arate încă „în afara benzii".

Măsurat cu amândouă piesele lăsate în amonte (`--blank-x 0.6 --part-x 4.4`): amândouă merg cu
0,111–0,112 m/s, adică viteza de suprafață, și se opresc 13 mm peste linie — blocul la 2,713 m pe
banda 1, piesa mașinată la 2,687 m pe banda 2 — fiindcă atâta le trebuie ca să piardă 0,12 m/s la
frecarea benzii (`v² / (2 * mu * g)`). De acolo nu se mai mișcă. O cursă de 600 s simulate, cu
300 000 de pași, se termină în 21 s de ceas real (14 400 de pași pe secundă).

## Materialul: două stări, amândouă în scenă

Fluxul are două momente, iar scena le arată pe amândouă simultan, ca fiecare să aibă un subiect în
cadru:

1. **`blank_01`** — blocul brut, pe banda 1, în ținta de preluare a brațului 1. Este un disc cu
   adaos de prelucrare (rază 52 mm, grosime 18 mm), nu piesa finită.
2. **`part_01`** — roata dințată ieșită din CNC, pe banda 2, sub camera de inspecție.

Transformarea dintre ele se petrece în mașină și **nu** se modelează aici: generatorul descrie
rezultatul mașinării, nu piesa de intrare. Poziția și rotația fiecăreia sunt aleatorii din sămânță
(`--blank-x`, `--blank-y`, `--blank-yaw-deg`, `--part-yaw-deg`), cu împrăștiere mică, ca ținta de
prindere să rămână în raza brațului. Yaw-ul se citește înapoi din scenă, fiindcă este eticheta care
va însoți cadrul de inspecție.

## Raftul pe familii

Cutiile de piese bune și de rebut au fost înlocuite cu un **raft cu trei niveluri a câte două
locuri**, șase locuri pentru cinci familii de piese din generator (`docs/10-dataset-piese.md`), plus
o **ladă de rebut** sub capătul de vest al benzii 2. Al șaselea loc rămâne gol. Motivul nu este
estetic: cutiile aveau pereți până la nivelul benzii, iar antebrațul brațului 2 trecea prin ei.

Raftul este o **scară**, nu un dulap cu rafturi suprapuse, iar treptele urcă **spre vest, departe de
braț**: 0,85 m, 0,97 m, 1,09 m, cu pasul de 0,12 m, iar fiecare treaptă are 0,167 m adâncime și
0,25 m lățime pe loc. Ordinea nu e o preferință: lângă bază brațul are cel mai puțin loc — placa
lui de bază coboară la 0,82 m, antebrațul mătură amprenta raftului la ~1,20 m în timpul apropierii,
iar încheietura trece la ~0,153 m peste podeaua locului în care coboară scula. De aceea treapta de
lângă braț stă la nivelul benzii, iar scara urcă spre vest; pasul de 0,12 m are două margini
măsurate: sub el, antebrațul ar da în vârful treptei de sus; peste el, încheietura s-ar sprijini pe
marginea de est a treptei de dedesubt, cu buza ei cu tot.

Fiecare treaptă e un bloc plin până la podeaua ei, peste care vin buzele de 20 mm și despărțitorul
dintre cele două locuri ale treptei, de 30 mm. **Despărțitorul se retrage 3,5 cm de la marginea de
est a treptei**: scula se lasă în locul treptei vecine, iar încheietura brațului rămâne deasupra
muchiei dintre trepte, chiar la colțul lui — cu despărțitorul până în margine, veriga se sprijină pe
el și scula nu mai ajunge la punct.

Ordinea locurilor, de la treapta de lângă braț spre vest și, în treaptă, de la nord la sud, este
ordinea familiilor din generator — `wheel_100`, `flange_72`, `bushing_68`, `wheel_60`, `pinion_56`
— și un test o verifică, ca planul, scena și clasificatorul să nu se despartă. Locul are 0,25 m
lățime, deci cea mai mare piesă (Ø104) are circa 70 mm joc de fiecare parte. Codurile citite de pe
raft sunt amânate deliberat (`docs/10-dataset-piese.md`).

Lada de rebut are marginea la 0,62 m, sub nivelul benzii, și **fără perete la est**, chiar sub
capătul de vest al benzii: piesa neconformă nu are punct de lucru, rămâne pe bandă până la capătul
ei și cade în ladă. Un perete chiar acolo ar prinde-o și ar ține-o afară.

## Stare cunoscută

La sămânța 7, `view.py --check` este **verde**: scena se compilează fără avertismente, țintele cad
în raza brațelor, camera de inspecție are piesa în cadru la 3,6 px/mm și rămâne la cel puțin
0,095 m de brațe pe 2000 de configurații eșantionate. Transportul pe benzi merge: amândouă piesele
ajung la linia de oprire a benzii și se opresc 13 mm peste ea, fără să cadă din geometrie.

`view.py --demo` este **verde** (`make mj-demo`), la cota documentată a piedestalului: 17,7 mm
eroare maximă pe cele 80 de opriri — 16 pe fiecare dintre cele cinci familii —, nicio verigă a
brațelor atinsă, 56,4 s simulate, iar amândouă piesele rămân pe benzile lor. Cifrele ciclului și
calea lui sunt în secțiunea „Brațele" de mai sus.

`make mj-record` este **verde** pe toate cele cinci familii, cu un ciclu fiecare: cinci cadre pe
ciclu, 653–675 de eșantioane în contract pe sesiune, ~65 s simulați în ~3 s de ceas real (circa
22 de ori mai repede decât ritmul real). Cadrul de proces, de pe flanșa brațului 2 și cel general
se citesc cu ochiul; universul gol și poziția falcilor se schimbă când intră prinderea.

Uzura intră în fizică prin două locuri, amândouă măsurate: scala de viteză a fiecărei benzi și
efortul scalat al brațelor. Restul — vibrația, temperatura, avansul, debitul de răcire, indicii de
uzură — sunt citirile pe care le compune `wear.py`, sub numele din contract. Liniile de eșec și
intervențiile de operator ajung în `events.jsonl`, iar traiectoria sănătății, pe componente, în
`wear.jsonl`; amândouă sunt aliniate pe `sim_time_s` cu eșantioanele.

Ce mai lipsește, în ordinea din `docs/11-geaman-mujoco.md`, secțiunea 8:

1. **Prinderea piesei.** Falcile ating piesa fără să o cuprindă (mâna se deschide 83 mm, discul are
   104 mm): prinderea se modelează ca legătură rigidă, ca în celula din Isaac Sim, cu o egalitate
   `weld` pornită prin `data.eq_active`. De ea atârnă și „fără piese pierdute în geometrie", plus
   ordinea reală a cadrului de proces (piesa ajunge sub cameră după ce brațul 1 o lasă pe bandă).
2. **Axele mașinii CNC.** `CUTTING` există ca fereastră de timp, cu cifrele care ies din uzură, dar
   scena nu are axe: când le va crește, aceleași coloane se citesc din motor, iar contractul rămâne
   neschimbat. Tot acolo intră și degradarea camerei (`blur` proporțional cu `1 - h_camera`), care
   azi nu există: cadrele ies curate.
3. **Publicarea live.** `run.py` scrie fișiere, iar `make mj-collect` le urcă offline, în
   `training-data/mujoco/`; urcarea cadrelor sub `raw/qa-snapshots/` și publicarea eșantioanelor pe MQTT
   se leagă în pasul următor. Contractele sunt identice, deci nimic din fișierele sesiunii nu se
   schimbă atunci. Detalii: `docs/11-geaman-mujoco.md` §4.
4. **Mișcarea laterală a pieselor pe bandă nu e măsurată la impact.** Într-o cursă lungă, o piesă
   poate aluneca spre șină; de verificat când intră brațele, fiindcă de acolo vine poziția de
   prindere.

Închis în runda de față: **colectarea cu urcare în bucket** — `cncdt_mj/export.py` (cheile,
manifestul, verificările corpusului), `publish.py` (urcare cu AWS CLI, plan fără `--upload`) și
țintele `make mj-collect`, `make mj-collect-plan`, `make mj-publish`, cu testele pure din
`tests/unit/test_mj_export.py`. Urcarea pe bucketul real nu s-a putut verifica din sesiunea de
lucru: cere credențialele rolului de operator (`eval "$(./scripts/assume-operator.sh)"`), iar
sesiunea expirase; s-a verificat planul pe sesiuni reale și refuzul când credențialele lipsesc.

Închis în runda de dinainte: motorul de uzură (`wear.py`: sănătate pe șapte componente, integrare
exactă pe timpul de lucru, eșecuri discrete cu hazard crescător, acțiuni de operator), legarea lui
la sesiune (`--rate`, `--inject`, `--machining-s`, `wear.jsonl`, `events.jsonl`, scala de viteză pe
fiecare bandă) și testele pure din `tests/unit/test_mj_wear.py`.

Închis în runda de dinaintea ei: brațele în mișcare — `moves.py` (punctele, ordinea, apropierea,
trecerile), `arms.py` (cinematica inversă, urmărirea liniei, lungimea sculei măsurată),
`view.py --demo` cu cifrele fiecărei opriri și `make mj-demo` —, plus cele două lecții de cale
(ridicarea după fiecare punct, trecerea pe la vest de mașină) și testele pure noi pentru treceri și
pentru ordinea pașilor. Tot acolo: camerele de pe flanșe (`scene.wrist_camera_quat`, orientarea
calculată la compunere) și pipeline-ul de colectare (`povs.py`, `sensors.py`, `camera.py`,
`telemetry.py`, `run.py`) cu ținta `make mj-record`, verificat pe toate cele cinci familii, plus
testele pure din `tests/unit/test_mj_telemetry.py`.
