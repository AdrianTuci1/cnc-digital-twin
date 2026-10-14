# Scena celulei CNC: props, asamblare, date și control

Ghidul pornește de la întrebarea „de unde luăm banda, CNC-ul, brațul și piesa, cum le punem
împreună și cum obținem datele". Răspunsul scurt: **extensia este deja scrisă, iar scena USD
lipsește**. Extensia nu inventează geometrie; citește o scenă care respectă un contract de căi
și de atribute. Tot ce urmează este despre cum se îndeplinește acel contract, cu efort minim
și fără cunoștințe prealabile de Isaac Sim.

Pentru conceptele de Kit, extensii și `omni.ui`, vedeți
[08-isaac-sim-guide.md](08-isaac-sim-guide.md). Aici este partea de scenă și de operare.

## Ce există și ce lipsește

`simulation/scripts/build-scene.py` compune scena din asset-uri reale plus mașina CNC din
primitive. Coloana „Stare" spune dacă prim-ul este creat de script (și deci există la prima
rulare) sau dacă mai cere un pas manual.

| Element din proces | Cine îl caută în cod | Ce trebuie să existe în scenă | Stare |
| --- | --- | --- | --- |
| Bandă de intrare | `CANDIDATE_PATHS["conveyor_infeed"]` | un prim, implicit `/World/Conveyor1` | creat de script (role A05) |
| Mașina CNC | `CANDIDATE_PATHS["cnc_spindle"]` | `/World/CNC/Spindle` + atribute `cncdt:process:*` | creată de script din primitive |
| Braț robotic | `CANDIDATE_PATHS["robot1"]` | `/World/Robot1`, rădăcină de articulație | creat de script (UR10) |
| Griper | `CANDIDATE_PATHS["robot_gripper"]` | `<flanșă>/Gripper` | creat de script, pe flanșă |
| Piesa fabricată | `CANDIDATE_PATHS["part"]` | `/World/Part`, corp rigid kinematic | creată de script |
| Bandă de inspecție | `CANDIDATE_PATHS["conveyor_qa"]` | un prim, implicit `/World/Conveyor2` | creat de script (role A05) |
| Cameră de inspecție | `CANDIDATE_PATHS["inspection_camera"]` | o cameră, implicit `/World/QA/Camera` | creată de script |
| Cameră de sortare | `CANDIDATE_PATHS["diverter_camera"]` | o cameră la lamă, implicit `/World/QA/DiverterCamera` | creată de script |
| Poartă de sortare | `DiverterGate` + `PartRouter` | `/World/Scrap/Piston` + lama fixă | creată de script |
| Piston de rejecție | `CANDIDATE_PATHS["reject_piston"]` | `/World/Scrap/Piston` cu `xformOp:translate` | creat de script |
| Mișcarea benzii | `ConveyorNode` (OmniGraph) | nod de conveyor pe prim-ul de role | pas manual, în interfață |

Restul există deja: panoul de operator, modelul de degradare, citirea senzorilor, publicarea
MQTT, captura de cadre QA, decizia de rută și acționarea opritorului.

**Ce se întâmplă dacă un prim lipsește:** `SceneSensors.bind()` îl sare, scrie un avertisment
în jurnal, iar dacă nu găsește niciun atribut de proces trece pe semnale sintetice. Panoul și
pipeline-ul funcționează în continuare; doar că datele nu mai vin din scenă. De aceea puteți
valida tot lanțul înainte de a avea scena finală.

## 1. Props: de unde le luăm

### Patru surse, de la rapid la fidel

| Sursă | Ce conține | Efort | Când o folosești |
| --- | --- | --- | --- |
| Asset-uri incluse în Isaac Sim | roboți (Franka, UR10), benzi, props de depozit, senzori | mic | prima scenă funcțională, demonstrații |
| SimReady / biblioteca Omniverse | asset-uri pregătite de digital twin: unități corecte, fizică, materiale | mic-mediu | când vrei aspect realist fără modelare |
| CAD-ul nostru (STEP/IGES) | mașina CNC reală, la dimensiunile reale | mare | când fidelitatea mașinii contează |
| Primitivere (cuburi, cilindri) | schelet fără nicio dependență | zero | exact ce creează `build-scene.py` |

Asset-urile incluse se deschid din **Window → Browsers → Assets** (numele exact al meniului
diferă între Isaac Sim 4.x și 5.x). În container, catalogul local stă sub
`/isaac-sim/Assets/Isaac/<versiune>`. Căutați „Conveyor", „UR10", „Franka", „CNC",
„Warehouse". SimReady apare în același browser, în secțiunea dedicată.

CAD-ul propriu se importă cu convertorul CAD (`omni.kit.converter.cad` / „CAD Import", care
cere extensia instalată) sau se exportă direct ca USD din FreeCAD ori Onshape. Pentru început
nu aveți nevoie de el: contractul se respectă și cu primitive.

**Licențe și depozitare.** Verificați licența fiecărui asset înainte de a-l folosi în afara
proiectului. Asset-urile nu se comit în git: `simulation/assets/` este ignorat, iar fișierele
ajung în S3 prin `make sim-push`. În container directorul este montat **read-only**, deci
scena nu se salvează direct în `/assets`.

### Ce props ne trebuie, concret

| Prop | Recomandare | De ce contează pentru date |
| --- | --- | --- |
| Bandă de intrare | orice bandă din Assets, sau un cub lung ca marcaj | nu are senzor propriu; starea ciclului vine din extensie, nu din geometrie |
| Mașina CNC | cub + cilindru pentru ax, sau CAD | pe prim-ul axului se scriu atributele de proces, singura sursă reală de vibrații și sarcină |
| Braț robotic | UR10 ori Franka din Assets | trebuie să fie **rădăcină de articulație**; de acolo vin eforturile per articulație |
| Piesa | un cub mic, refolosit | nu e citită de cod; o reprezintă `part_id`, iar ea apare în fotografia QA |
| Bandă de inspecție | a doua bandă | separă zona de inspecție de cea de intrare |
| Cameră de inspecție | `UsdGeom.Camera` | din ea se fotografiază piesa la ieșirea din mașină |
| Cameră de sortare | `UsdGeom.Camera`, obiectiv scurt | din ea se fotografiază piesa oprită la lamă; cadrul decide ruta |
| Piston de rejecție | un cub cu `xformOp:translate` | extensia îl împinge cu 0,12 m pe Z la verdict REJECT |

## 2. Contractul de scene

Extensia rezolvă fiecare sursă prin `CANDIDATE_PATHS` din
`simulation/extensions/cncdt.cnc_cell/cncdt/cnc_cell/telemetry/sensors.py`. Câștigă **prima
cale existentă** din listă. Implicit:

| Cheie | Prima cale | Rol |
| --- | --- | --- |
| `cnc_spindle` | `/World/CNC/Spindle` | atributele de proces |
| `robot1` | `/World/Robot1` | articulația |
| `conveyor_infeed` | `/World/Conveyor1` | banda de intrare |
| `conveyor_qa` | `/World/Conveyor2` | banda de inspecție |
| `inspection_camera` | `/World/QA/Camera` | camera QA de la ieșirea din mașină |
| `diverter_camera` | `/World/QA/DiverterCamera` | camera de la lamă, care decide ruta |
| `reject_piston` | `/World/Scrap/Piston` | pistonul de rejecție |

Sunt două variante când scena voastră folosește alte nume:

1. **Redenumiți prim-ul** în Stage (click dreapta → Rename) ca să corespundă contractului.
   Referințele interne rămân valide; se schimbă doar calea.
2. **Adăugați calea** în `CANDIDATE_PATHS`, în tuple-ul cheii potrivite. Este schimbare de cod,
   dar centralizează diferența într-un singur loc.

Nu hardcodați căi în alt fișier: `CANDIDATE_PATHS` este singurul loc prevăzut pentru asta.

**Când se face legarea.** Extensia încearcă după `BIND_AFTER_FRAMES` (30 de cadre) de la
pornire, apoi reîncearcă la fiecare 120 de cadre până reușește. Puteți deschide scena și după
activarea extensiei; butonul **„Leagă senzorii"** din panou forțează o reîncercare imediată.

## 3. Asamblarea scenei

### Varianta rapidă: scriptul de schelet

`simulation/scripts/build-scene.py` creează toată ierarhia din contract, cu primitive ca
marcaje și cu atributele de proces deja autorate. Nu are nevoie de niciun asset extern.

Din Script Editor (Window → Script Editor) din Isaac Sim:

```python
exec(open("/sim/scripts/build-scene.py").read())
build_scene_on_open_stage()
```

Sau ca script independent, care scrie un fișier USD nou:

```bash
# în container
./python.sh /sim/scripts/build-scene.py --usd /logs/cell.usd
```

Scriptul este idempotent: prim-urile și atributele existente rămân neatinse, deci îl puteți
rula din nou după ce ați înlocuit un marker cu un asset real.

Ce creează:

```text
/World
├── CNC/Spindle            (cilindru + cncdt:process:*)
├── Robot1                 (cub-marcaj; se înlocuiește cu robotul real)
├── Conveyor1, Conveyor2   (cuburi lungi)
├── Part                   (cubul piesei)
├── QA/Camera              (UsdGeom.Camera, la ieșirea din mașină)
├── QA/DiverterCamera      (UsdGeom.Camera, la lamă; obiectiv 18 mm)
└── Scrap/Piston           (cub + xformOp:translate, opritorul de la lamă)
```

### Varianta manuală, în interfață

1. **File → New** (sau deschideți scena existentă) și salvați-o într-un loc scriibil.
2. În panoul **Stage**, creați un `Xform` sub `/World` pentru fiecare prim din tabelul de mai
   sus, cu exact numele din contract.
3. Trageți asset-urile din **Assets** în scenă. Un asset tras devine o **referință** și își
   păstrează propria rădăcină (de exemplu `/World/ur10`). Mutați-o sub prim-ul contractului
   sau redenumiți-o.
4. Pentru braț: verificați că prim-ul este rădăcină de articulație (are aplicat
   `PhysicsArticulationRootAPI`). Fără el, `load_articulation_class()` nu găsește articulația
   și eforturile per articulație lipsesc.
5. Pentru piston: dați clic pe prim, adăugați `xformOp:translate` și setați poziția de repaus.
   Extensia o citește ca poziție de repaus și o decalează cu 0,12 m la rejecție.
6. Adăugați atributele de proces pe `/World/CNC/Spindle` (secțiunea 4) sau rulați scriptul.

### Unde salvați scena

`/assets` este read-only în container. Salvați în `/logs/cell.usd` (scriibil, montat pe
`/opt/cncdt/logs`), apoi aduceți fișierul în `simulation/assets/` pe Mac și rulați
`make sim-push`. `sim-push` urcă `simulation/assets/` în
`s3://$SIM_ASSETS_BUCKET/sim-assets/latest/assets/`, iar bootstrap-ul îl sincronizează la
`/opt/cncdt/assets/` pe instanță. Deschiderea în Isaac Sim se face apoi din
**File → Open → /assets/cell.usd**.

Local, cu `docker-compose.isaac.yml`, mapați `CNCDT_ASSET_DIR` către `simulation/assets` și
salvați direct în el.

## 4. Atributele de proces: de unde vine „uzura" în date

Pe `/World/CNC/Spindle` se autoră cinci atribute `Float`. Sunt singura legătură între fizica
simulării și telemetrie.

| Atribut | Cheie în telemetrie | Cine îl scrie |
| --- | --- | --- |
| `cncdt:process:spindleLoad` | `spindle_load` | extensia, la fiecare aplicare de degradare |
| `cncdt:process:vibration` | `vibration` | extensia, la fiecare aplicare de degradare |
| `cncdt:process:temperature` | `temp_c` | doar scena; extensia îl citește |
| `cncdt:process:coolantFlow` | `coolant_flow_lpm` | doar scena; extensia îl citește |
| `cncdt:process:axisFeed` | `axis_feed` | doar scena; extensia îl citește |

Scriptul le creează cu valori de referință coerente (sarcină 0,58; vibrații 1,35; temperatură
24 °C; debit 8,5 l/min; avans 0). Dacă vreți ca temperatura, debitul și avansul să varieze,
trebuie să le anime cineva — un script Kit care le scrie pe cadru. Altfel rămân constante, iar
celelalte chei ale eșantionului sunt completate din generatorul sintetic.

**Regula de decizie a extensiei:** dacă niciun atribut de proces nu există pe prim, citirea
trece integral pe semnale sintetice și scrie un avertisment. Dacă există cel puțin unul,
eșantionul pornește din semnalul sintetic și este suprascris cu valorile citite pentru cheile
disponibile. Așa lotul are întotdeauna aceleași chei.

## 5. Uzură progresivă și reparații

Uzura este modelată ca mărime continuă între 0 și 1, nu ca eveniment. Două procese:

- **uzura sculei** crește cât timp mașina taie; amplifică vibrațiile (`1 + index^1.5`) și
  sarcina (`1 + 0.7·index`) și scade calitatea suprafeței (`1 - index^1.2`);
- **frecarea articulațiilor** crește cât timp mașina se mișcă; amplifică efortul
  (`1 + 0.8·index`) și reduce viteza (`1 - 0.4·index`).

Rata de creștere se setează din mediu: `CNCDT_TOOL_WEAR_RATE` (implicit 0,0015 pe secundă) și
`CNCDT_JOINT_FRICTION_RATE` (implicit 0,0008). Valoarea 1,0 este limita de avarie. Pragul de
respingere automată pentru uzură este `surface_quality < 0.55`.

### Din panoul de operator

| Buton | Efect |
| --- | --- |
| Uzură +0.1 / Uzură +0.25 | adaugă la indexul de uzură al sculei |
| Frecare +0.25 | adaugă la indexul de frecare al articulațiilor |
| **Uzură sculă** (slider 0–1) | setează direct indexul, pentru control progresiv |
| **Frecare articulații** (slider 0–1) | setează direct indexul |
| Sculă nouă | resetează uzura sculei |
| Repară articulații | resetează frecarea |
| **Repară tot** | resetează ambele |
| Defect forțat | marchează următoarea piesă ca defectă |
| Reset degradare | resetează ambele plus defectul forțat |

Slider-ele cer o valoare țintă și o aplică imediat; valorile cresc oricum singure în timpul
ciclului, după ratele de mai sus.

### Din cloud sau din câmpul JSON

Comenzile ajung pe `<prefix>/command` și sunt tratate de `CellController.on_control_message`:

| Comandă | Payload | Efect |
| --- | --- | --- |
| `inject_tool_wear` | `{"amount": 0.2}` | adaugă la uzură |
| `inject_joint_friction` | `{"amount": 0.2}` | adaugă la frecare |
| `set_tool_wear` | `{"value": 0.6}` | setează direct uzura |
| `set_joint_friction` | `{"value": 0.4}` | setează direct frecarea |
| `replace_tool` | — | sculă nouă |
| `repair_joints` | — | repară articulațiile |
| `repair_all` | — | repară tot |
| `force_defect` | — | următoarea piesă defectă |
| `set_defect_probability` | `{"value": 0.3}` | probabilitatea de defect aleator |
| `pause` / `resume` / `start_cycle` / `stop_cycle` / `reset` | — | ciclul |

Câmpul de text din secțiunea „Telemetrie" acceptă exact acest JSON, deci puteți testa fără
niciun client MQTT.

### Ce ajunge efectiv în fizica scenei

`SceneSensors.apply_degradation_to_scene()` scrie:

- `cncdt:process:spindleLoad` și `:vibration` pe axul CNC, scalate cu uzura;
- `physxJoint:jointFriction` și `physics:jointFriction` pe `/World/Robot1`, crescând cu
  frecarea.

Pentru uzura sculei nu există un echivalent fizic direct — efectul ei se vede în atributele de
proces și în calitatea suprafeței. Frecarea are echivalent fizic, dar dacă PhysX-ul versiunii
folosite onorează atributul se confirmă abia în simulare. Datele publicate reflectă degradarea
chiar dacă efectul fizic nu se materializează.

## 6. Fotografierea piesei

Sunt două camere, fiecare cu un rol:

| Cameră | Cale | Rol în cadru | Când fotografiază |
| --- | --- | --- | --- |
| De inspecție | `/World/QA/Camera` | `inspection` | la ieșirea din mașină |
| De sortare | `/World/QA/DiverterCamera` | `sorting` | la lamă, pe piesa oprită în poartă |

Cadrul care decide ruta este cel de la lamă: acolo se află opritorul și lama care deviază
rebutul, deci acolo se vede exact ce decide. Camera de inspecție rămâne rezerva, folosită când
scena nu are încă a doua cameră.

Fluxul, pas cu pas, cu poarta pornită (`CNCDT_DIVERTER_ENABLED=true`, implicit):

1. ciclul intră în starea `INSPECTING` (la ~85–97% din durata ciclului);
2. `CellController._capture_snapshot()` rulează o singură dată per piesă;
3. captura folosește `diverter_camera` (rezerva: `inspection_camera`), cu rezoluție 1280×720,
   și scrie `camera_role` în sidecar;
4. se scriu `part-XXXXXX.png` și `part-XXXXXX.json` în `<cycle_id>/`;
5. piesa este oprită la lamă, iar fereastra de verdict se deschide pe biletul de poartă;
6. sidecar-ul JSON se urcă sub `raw/qa-snapshots/<cycle_id>/` și este cel care declanșează
   Lambda-ul de inspecție (regula S3 filtrează prefixul și sufixul `.json`);
7. Lambda-ul citește și PNG-ul din S3 și îl trimite **inline, în base64**, împreună cu
   vectorul de caracteristici și metadatele, într-un singur apel `InvokeEndpoint`;
8. verdictul vine înapoi pe topicul `qa-verdict`, închide așteptarea și aplică ruta.

Dacă Replicator nu este disponibil, se încearcă captura viewport-ului activ. Dacă nici aceea nu
reușește, se scrie doar JSON-ul, iar pipeline-ul rămâne testabil. Butonul **„Captură QA acum"**
face o captură manuală, fără să oprească piesa, utilă pentru poziționarea camerelor.

**Limita de mărime.** `InvokeEndpoint` acceptă 6 MB, iar base64 umflă datele cu 4/3, deci
Lambda-ul sare peste cadrele mai mari de 4 MB (`IMAGE_PAYLOAD_LIMIT_BYTES`) și continuă cu
vectorul de caracteristici, ca un cadru mare să nu piardă verdictul. Cu
`SEND_IMAGE_TO_ENDPOINT=false`, endpoint-ul primește doar caracteristici.

**Poziționarea camerelor.** Camera de inspecție stă deasupra piesei, la
`(INSPECT_X, 0, BELT_DECK_Z + 1.05)`. Camera de sortare stă deasupra lamei, la
`(DIVERTER_X - 0.5, 0, BELT_DECK_Z + 1.15)`, cu obiectiv de 18 mm ca să încapă piesa și
opritorul. Ajustați `xformOp:translate` și `xformOp:rotateXYZ` până când „Captură QA acum"
produce un cadru cu piesa și lamă în centru. Verificați imaginile în `/logs/qa-snapshots/`
(sau în bucket, dacă `CNCDT_QA_BUCKET` este setat).

## 7. Bucla de control completă

1. Extensia citește scena la ~10 Hz și publică telemetrie pe MQTT mTLS.
2. Firehose grupează la 60 s și Lambda-ul de feature engineering calculează caracteristicile.
3. La fiecare piesă, cadrul QA (de la lamă) ajunge în S3, iar Lambda-ul de inspecție trimite
   poza și caracteristicile la endpoint și emite verdictul.
4. Piesa este oprită la lamă până la verdict; verdictul închide așteptarea și aplică ruta
   (rebut ori mai departe), iar în lipsa lui intervine ruta de rezervă.
5. Comenzile de operator (uzură, reparație, ciclu) pot veni din cloud sau din panou.

Fără AWS, aceeași buclă se poate rula local cu `make docker-local && make publish-dev`; scena
Isaac Sim nu este necesară pentru a valida lanțul de date.

## 8. Traseul minim, pas cu pas

1. `make sim-up` — creează instanța GPU. `make sim-connect` — deschide clientul WebRTC.
2. `exec(open("/sim/scripts/build-scene.py").read()); build_scene_on_open_stage()` în Script
   Editor. Panoul extensiei apare, iar „Leagă senzorii" raportează căile găsite.
3. Dacă jurnalul arată avertismentul de trecere pe sintetic, atributele de proces lipsesc:
   rulați scriptul din nou sau verificați că prim-ul axului este chiar `/World/CNC/Spindle`.
4. `make sim-push` din Mac ca să trimiteți extensia și scripturile pe instanță.
5. În panou: **Pornește**, apoi **Ciclu nou**. Urmăriți sarcină, vibrații și temperatură.
6. **Uzură sculă** pe la 0,7 și lăsați ciclul să ruleze: rata de respingere crește. Apoi
   **Sculă nouă** și vedeți că revine.
7. **Captură QA acum** și verificați imaginea în `/logs/qa-snapshots/`.
8. `make sim-logs` pentru telemetrie; `make audit` după sesiune.

## 9. Manipulatorul cu griper

Brațul este `Robots/UniversalRobots/ur10/Legacy/ur10_short_suction.usd`, singurul UR10 din
conținut cu **rădăcina de articulație pe prim-ul rădăcină**, adică exact ce cere contractul:
`/World/Robot1` trebuie să fie chiar prim-ul cu `PhysicsArticulationRootAPI`, altfel
`load_articulation_class()` nu găsește articulația.

Brațul vine cu o ventuză. Scriptul o **dezactivează** (`SetActive(False)` pe prim-ul sculei) și
montează în locul ei griperul cu două falci:

| Asset | Rol |
| --- | --- |
| `Robots/UniversalRobots/ur10/grippers/short_gripper.usd` | griperul UR10, implicit |
| `Robots/Robotiq/2F-85/2f85.usd` | alternativa, dacă primul lipsește |

Griperul se referă pe un prim copil al **flanșei** (`wrist_3_link` la UR10), deci se mișcă o
dată cu brațul. Numele flanșei și al sculei diferă între variantele de UR10, așa că scriptul le
caută în subarborele brațului (`WRIST_LINK_CANDIDATES`, `TOOL_LINK_CANDIDATES`) și scrie în
jurnal ce a găsit. Dacă griperul își aduce propria rădăcină de articulație, scriptul o scoate:
PhysX acceptă o singură rădăcină de articulație pe ierarhie, iar cea care contează este a
brațului.

**Prinderea.** Falcile din asset nu au articulații comandabile din script. Prinderea se face ca
în exemplele simple de pick-and-place: la „Prinde piesa" (buton în panou ori comanda JSON
`{"command": "grasp_part"}`), extensia creează o **articulație fixă** între griper și piesă, la
poziția relativă de la momentul prinderii, iar la „Lasă piesa" o șterge. Codul stă în
`cncdt/cnc_cell/grasping.py`, iar calea articulației este întotdeauna
`/World/Robot1/GraspJoint`.

Piesa are corp rigid **kinematic** și collider, deci poate fi prinsă, dar nu cade prin decor
cât timp banda nu o transportă. Când banda primește nodul de conveyor, piesa se poate comuta pe
dinamic (`kinematicEnabled = false`).

**Ce lipsește pentru un ciclu complet de pick-and-place:** mișcarea brațului. Extensia citește
eforturile articulațiilor, dar nu comandă traiectorii. Varianta susținută de NVIDIA este
`isaacsim.robot.manipulators` (`UR10` + `PickPlaceController`), care se leagă într-un script
Kit separat; până atunci, prinderea/eliberarea se pot proba manual din panou.

## 10. Sortarea: rebut ori piese bune

Decizia are o singură intrare, verdictul de inspecție, și o singură ieșire, ruta:

| Rută | Ce se întâmplă cu piesa |
| --- | --- |
| `GOOD` | trece de postul de inspecție și ajunge în lada de piese bune |
| `SCRAP` | este deviată în jgheabul de rebut |

Logica este pură și testată (`cncdt/cnc_cell/sorting.py`): `route_for_verdict()` mapează
verdictul pe rută, iar `PartRouter` o aplică în scenă prin două callback-uri. Verdicturile de
rebut recunoscute sunt `REJECT`, `REJECTED`, `FAIL`, `FAILED`, `DEFECT`, `SCRAP`, `NOK`; orice
alt verdict trece piesa mai departe și este scris în jurnal.

Mecanismul care execută ruta este schimbabil, fără să atingă decizia:

1. **poarta de rejecție** (implicită, în scenă) — la `SCRAP`, opritorul urcă 0,12 m în calea
   piesei, iar lama fixă înclinată la 35° o ghidează spre jgheab și lada de rebut; la `GOOD`,
   opritorul se retrage și piesa trece mai departe. Retragerea contează: fără ea, opritorul ar
   rămâne ridicat de la rebutul precedent și ar devia și piesele bune.
2. **joncțiunea de benzi** — `Props/Conveyors/ConveyorBelt_A20.usd` este un `Y_MERGE` cu două
   trasee de role independente (`Rollers`, `Rollers_01`). Ruta pornește exact unul dintre
   trasee, deci banda însăși alege ieșirea, fără nicio poartă în calea piesei. Traseul se
   leagă în extensie prin `controller.router.track`; vezi `conveyors-and-diverter.py`.

**Cine dă verdictul.** Implicit cloudul, pe topicul `qa-verdict`, prin
`control_dispatcher`; atunci când cloudul nu răspunde, extensia doar raportează previziunea
locală. Pentru demonstrații fără pipeline, `CNCDT_SORT_ON_LOCAL=true` lasă previziunea locală
să sorteze ea piesa, iar sursa deciziei rămâne scrisă în jurnal (`simulation`).

### Poarta de la lamă

Cu `CNCDT_DIVERTER_ENABLED=true` (implicit), decizia nu se aplică pe loc: piesa este **oprită la
lamă** și așteaptă verdictul. Clasa care ține starea este `DiverterGate`
(`cncdt/cnc_cell/diverter.py`), matematică pură plus callback-uri, testată fără Isaac Sim.

| Moment | Ce se întâmplă |
| --- | --- |
| Piesa ajunge în dreptul lamei | `gate.arrived()` ridică opritorul, deschide fereastra și reține biletul (piesă, ciclu, cadru) |
| Verdictul cloud sosește | `gate.verdict()` închide așteptarea și aplică ruta; sursa este autoritară |
| Sfârșitul ciclului, fără verdict cloud | `gate.supply_local()` închide fereastra: previziunea locală (dacă `CNCDT_SORT_ON_LOCAL=true`) ori ruta de rezervă |
| Verdict pentru altă piesă | Este numărat ca întârziat (`stale_verdicts`); lama nu se mișcă sub piesa greșită |
| O a doua piesă în poartă | Nu este oprită; banda ar împinge piesa deja oprită |

**Ruta de rezervă este rebutul.** `CNCDT_DIVERTER_FAIL_SAFE` (implicit `scrap`, se poate pune
`good`) decide ce se întâmplă cu o piesă fără verdict. Implicitul este deliberat pesimist: lada
de piese bune este cea care ajunge la client, iar o piesă neverificată se poate reexamina dacă
ajunge în rebut.

**Ce se vede în panou.** Secțiunea „Sortare la lamă" arată piesa din poartă, cât așteaptă și
numărătorile (`așteptări`, `cloud`, `local`, `rezervă`). Butonul **Eliberează poarta** golește
poarta manual, fără să aplice nicio rută — util dacă o piesă a rămas oprită după o pană.

**Ce lipsește ca piesa să circule singură:** nodul de conveyor de pe bandă. Fără el, banda este
doar decor fizic, iar piesa stă pe loc; pașii sunt în `conveyors-and-diverter.py`.

## Capcane

- **În conținutul Isaac Sim nu există nicio mașină CNC.** Secțiunile folosite de celulă
  (`Robots`, `Props`, `Materials`, `Sensors`, `Environments/Simple_Warehouse`) conțin roboți,
  benzi, props de depozit, hale, materiale și senzori — nicio mașină-unealtă. `/World/CNC` este
  compus din primitive de `build-scene.py`, iar mașina reală se aduce ca USD din CAD.
- **`/assets` este read-only în container.** Salvați scena în `/logs` ori local, apoi
  `make sim-push`.
- **Asset-urile trase în scenă vin cu rădăcina lor.** Redenumiți sau adăugați calea în
  `CANDIDATE_PATHS`; altfel legarea nu găsește prim-ul.
- **Fără rădăcină de articulație pe braț, eforturile per articulație lipsesc** și rămâne doar
  semnalul sintetic pentru acea cheie.
- **Temperatura, debitul și avansul nu sunt animate de extensie.** Le scrie doar scena; altfel
  rămân constante la valoarea autorată.
- **Uzura sculei nu are echivalent fizic direct.** Efectul ei este în atributele de proces și
  în verdictul de inspecție.
- **Captura consumă timp.** Se face o dată per piesă, la intrarea în `INSPECTING`.
- **Cadrul care decide ruta vine de la lamă, nu de la ieșirea din mașină.** Cu poarta pornită,
  camera folosită este `diverter_camera`; dacă scena nu o are, se ia `inspection_camera` și
  unghiul poate să nu cuprindă opritorul.
- **O piesă fără verdict merge la rebut.** Implicit `CNCDT_DIVERTER_FAIL_SAFE=scrap`. Dacă
  pipeline-ul nu răspunde niciodată, toate piesele ajung în rebut — semnul că lipsește
  verdictul, nu că sunt defecte.
- **Un cadru peste 4 MB este sărit.** Lambda-ul continuă cu vectorul de caracteristici;
  verdictul nu se pierde din cauza mărimii pozei.
- **Numele meniurilor și API-urile diferă între versiuni.** Pentru articulații, diferența este
  izolată în `load_articulation_class()`; pentru asset-uri, verificați browserul versiunii
  instalate.
- **Porturile WebRTC se schimbă între versiuni.** Citiți-le din `.env`, conform
  [02-isaac-sim-remote.md](02-isaac-sim-remote.md).
