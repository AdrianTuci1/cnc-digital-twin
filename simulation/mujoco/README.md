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
make mj-check MJ_SAMPLES=2000   # degajarea camerei, pe mai multe configurații de braț
make mj-demo MJ_BASE_Z=0.55     # ciclul la cota documentată a piedestalului: cade, vezi mai jos
```

Fără Makefile, aceleași lucruri se scriu direct:

```bash
MUJOCO=simulation/mujoco/.venv/bin/python

# Verificarea implicită: compilează scena, așază piesele, rulează pași, raportează cifrele.
$MUJOCO simulation/mujoco/view.py --check

# Ciclul brațelor, punct cu punct: eroarea, unghiul, contactele și unde au ajuns piesele.
$MUJOCO simulation/mujoco/view.py --demo

# Același ciclu, cu un PNG la fiecare oprire, în renders/demo/ (director generat, ignorat de git).
$MUJOCO simulation/mujoco/view.py --demo --shots

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
camerei), `--seed`, `--defect`, `--blank-shape`, `--blank-x`/`--blank-y`, `--blank-yaw-deg`,
`--part-yaw-deg`, `--part-x`/`--part-y`, `--camera-z`, `--base-z` (cota piedestalului brațelor,
pentru măsurători), `--demo-settle` (câți pași se așteaptă la fiecare oprire), `--shots [DIR]`,
`--width`, `--height`, iar pentru viewer `--belts`/`--no-belts` și `--speed`. La pozițiile cerute
explicit, cota care lipsește rămâne cea din plan: `--blank-x 0.6` mută piesa pe banda 1, fără să
ceară și `--blank-y`.

Viewer-ul rulează în ritm real și tipărește în terminal, la fiecare secundă simulată, ce piesă e pe
ce bandă, cu ce viteză și cât mai are până la linia de oprire. `--speed 0` scoate ritmul real, ca
sesiunea să alerge cât poate.

Suita proiectului (`make test`) rulează fără `mujoco` și include partea pură a testelor
geamănului, `tests/unit/test_mujoco_scene.py`; testul care atinge scena se sare acolo și se
rulează cu `make mj-test`, din venv-ul care are motorul.

## Structură

| Fișier | Ce conține |
| --- | --- |
| `cncdt_mj/layout.py` | Așezarea în plan și înălțimile. Coordonatele `(x, y)` **nu** se transcriu: se importă din `docs/diagrams/cell_layout.py`, ca planul și scena să nu se poată contrazice. Înălțimile `Z` se scriu aici, cu fragmentul din documentație de care aparțin. **Fără MuJoCo.** |
| `cncdt_mj/part.py` | Cele două stări ale materialului: blocul brut (disc cu adaos de prelucrare) și roata dințată din generator. Geometria iese în milimetri, în planul `XY`, cu grosimea pe `Z`; se scalează în metri. Aici stau și pozițiile de așezare: `stopped_xy` pentru piesa așezată la linia de oprire a benzii, `on_belt_xy` pentru una cerută explicit. **Fără MuJoCo.** |
| `cncdt_mj/belt.py` | Benzile: direcția fiecărei benzi, viteza de suprafață și forța de antrenare cu limita de frecare Coulomb. **Fără MuJoCo.** |
| `cncdt_mj/checks.py` | Regulile verificării care lucrează pe nume de corpuri și pe cifre, nu pe starea motorului (de pildă care contacte ale unui braț sunt normale), plus pragurile verificării, împărțite cu `view.py`. **Fără MuJoCo.** |
| `cncdt_mj/moves.py` | Mișcările: ce puncte deservește fiecare braț, în ce ordine, din ce parte vine scula, cu ce rotire și prin ce puncte de trecere. Cotele punctelor de lucru ies din înălțimile documentate. **Fără MuJoCo.** |
| `cncdt_mj/scene.py` | Compunerea scenei: `scene/cell.xml` plus cele două brațe atașate programatic. Unul dintre cele trei module care importă `mujoco`. |
| `cncdt_mj/cell.py` | Celula în mișcare: cine e în contact cu banda, ce viteză are piesa, ce forță se scrie în `data.xfrc_applied`. Al doilea modul care importă `mujoco`. |
| `cncdt_mj/arms.py` | Brațele: numele articulațiilor și ale actuatoarelor, lungimea sculei măsurată în scenă, cinematica inversă, urmărirea liniei dintre două puncte și strângerea. Al treilea modul care importă `mujoco`. |
| `scene/cell.xml` | Șablonul celulei. Cifrele nu stau în el: se pun prin substituție, din `layout.scene_geometry()`. |
| `view.py` | Cele patru moduri: `--check`, `--render`, `--demo`, `--view`. |

Consecința practică a împărțirii: `layout`, `part`, `belt`, `checks` și `moves` se importă din
`.venv`-ul proiectului, unde `mujoco` nu există (ADR 0012), iar tot ce atinge motorul se validează
rulând `view.py --check`. Lista modulelor de motor (`scene.py`, `cell.py`, `arms.py`) este scrisă
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

Cifrele de referință, la sămânța implicită 7: `nq/nv/nu = 32/30/16`, 33 de corpuri, 207 geome, zero
avertismente MuJoCo. Piesele se așază pe benzi cu 0,1 mm derivă, iar yaw-ul cerut se citește înapoi
din scenă (comparat pe cerc, deci 348,5° și −11,5° sunt același unghi).

Țintele de prindere, măsurate **în spațiu** de la baza brațului, față de raza de 0,855 m din plan:
brațul 1 ajunge la 0,758 m (preluare bandă 1), 0,760 m (mașină) și 0,776 m (așezare bandă 2);
brațul 2 la 0,541 m (preluare bandă 2), 0,769 m (cutia de piese bune) și 0,789 m (cutia de rebut).
Planul măsoară doar în plan, iar `docs/11-geaman-mujoco.md` spune explicit că o țintă poate părea
la îndemână în desen și să fie prea sus — verificarea de aici este cea în spațiu.

Se mai verifică trei lucruri la camera de inspecție: degajarea față de ambele brațe (poziția
nominală plus `--samples` configurații eșantionate, prag 0,02 m), încadrarea piesei — distanța,
abaterea de la ax și diametrul aparent în pixeli, față de pragul de 3,0 px/mm — și faptul că
niciun braț nu atinge un element al celulei la poziția nominală. Autocontactele unui braț
(`arm1_hand↔arm1_link5`) sunt normale și se ignoră.

## Brațele: ciclul din `--demo`

`--demo` este partea care se vede: piesele sunt aduse întâi la liniile de preluare
(`bring_parts_to_pickup`, cu benzile pornite), apoi fiecare braț își parcurge punctele, în ordinea
din `moves.ROUTES` — brațul 1 ia blocul de pe banda 1, îl duce în universal și lasă roata pe banda
2, iar brațul 2 preia roata și o așază în cutia verdictului. La fiecare oprire se tipăresc eroarea
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

La cota documentată a piedestalului, 0,55 m, ciclul **cade**: pereții cutiilor urcă până la nivelul
benzii, iar antebrațul trece prin ei (`arm2_link3↔belt_out`, 106 mm eroare la preluarea de pe banda
2, 41 mm la `bin_ok`). La 0,95 m trece: 17,7 mm eroare maximă, nicio verigă atinsă, 53,9 s
simulate. Cota asta ridică însă brațele peste anvelopa camerei de inspecție, deci `make mj-check`
cade, iar cele două cerințe nu pot coexista — alegerea este scrisă în `docs/11-geaman-mujoco.md`
§2 și §10. De aceea `make mj-demo` rulează implicit la 0,95 m, ca măsurătoare, iar
`make mj-demo MJ_BASE_Z=0.55` arată rularea care cade.

**Ce lipsește: prinderea.** Mâna Panda se deschide 83 mm, blocul brut are 104 mm și universalul
110 mm, deci falcile nu pot cuprinde piesa — de aceea o ating razant pe toată cursa. Celula reală
din Isaac Sim prinde la fel: cu o legătură rigidă (`grasping.py`, `/World/Robot1/GraspJoint`).
Geamănul o va oglindi cu o egalitate de tip weld, pornită și oprită prin `data.eq_active`. Până
atunci ciclul nu ridică nimic, iar `--demo` tipărește unde a lăsat fiecare piesă, fără să cadă
din asta.

## Camera de inspecție

Camera stă la **0,85 m deasupra suprafeței benzii 2**, adică `Z = 1,70 m` în lume, și randează la
**640×480** cu **9° pe verticală**. Acoperă 178 × 134 mm la nivelul benzii, adică **3,6 px/mm** pe
piesă: roata de 104 mm intră pe ~55% din lățimea cadrului (362 px în `--check`, ~355 px în
`renders/inspection.png`). Cota este parametru (`--camera-z`, ori `layout.inspection_camera_z()`),
nu o constantă comună cu celelalte camere, iar intrinsics-urile se calculează din geometrie, în
`layout.inspection_camera_intrinsics()`, nu se afirmă.

**De ce nu la 2,20 m ca restul camerelor**: acolo piesa de ~104 mm ajunge la vreo 10 pixeli, iar
cel mai mic defect din catalog — o gaură dezaxată cu 2,5 mm — ar cădea sub un pixel, deci nicio
clasă de defect nu ar fi separabilă. La 0,85 m, un dinte ciobit de 4 mm iese pe ~14 pixeli și
dezaxarea de 2,5 mm pe ~9.

**De ce nu mai jos**: pe plaja articulațiilor, geomele unui braț urcă peste axa benzii 2 până la
~1,63 m în lume, adică ~0,78 m peste suprafața benzii (eșantionare uniformă, 2000 de
configurații). Cu capul optic la 1,60 m, golul minim față de brațul 2 este −0,022 m, iar la
1,50 m, −0,071 m: capul ar fi înăuntrul brațelor. Un cap optic la 0,25 m peste bandă, cum spunea
prima variantă, chiar intra în ele: 0,012 m față de brațul 1 și −0,078 m față de brațul 2. Capul a
urcat peste anvelopă, iar obiectivul s-a îngustat de la 30° la 9° ca acoperirea — deci rezoluția pe
piesă — să rămână aceeași.

**Cum stă**: consola camerei se prinde de colțul sud-vest al mașinii CNC și se așază pe capacul ei,
la 1,90 m; de capătul ei atârnă capul optic pe o tijă subțire, până la 1,70 m. Are 0,79 m, fiindcă
prinderea călătorește cu masa mașinii, iar axul optic stă peste banda 2. Anvelopa brațelor
rămâne dedesubt: cea mai mică distanță măsurată este 0,138 m (brațul 1) și 0,052 m (brațul 2) la
eșantionarea implicită a verificării, 200 de configurații; la 2000 de configurații coboară la
0,057 m și 0,034 m, tot peste pragul de 0,02 m. Capul optic nu poate sta chiar în punctul camerei
din plan: o cameră care privește în jos trebuie să fie deasupra subiectului ei, iar capul stă
deasupra axei benzii 2, la mijlocul distanței dintre locul de așezare (x=3,4) și cel de preluare
(x=2,7).

**Axa de privire**: camerele MuJoCo privesc de-a lungul axei lor locale **−Z**, nu +Z (verificat cu
o scenă de probă: două cutii, una deasupra și una dedesubt). Prima variantă întorcea camera cu
`quat="0 1 0 0"`, ceea ce o făcea să privească spre cer; `renders/inspection.png` era numai cer și
pământ. Acum orientarea este implicită („în jos"), iar `--check` verifică explicit că piesa intră
în cadru, ca bug-ul să nu poată reveni tăcut.

Ce **nu** rezolvă camera: 3,6 px/mm înseamnă că diferențele sub ~0,3 mm nu sunt măsurabile, iar
uzura camerei (`docs/11-geaman-mujoco.md`, secțiunea 3) adaugă blur proporțional cu `1 - h_camera`
peste ele. Cadrul este suficient pentru catalogul actual de defecte, nu pentru unul mai fin: dacă
defectele se rafînează, se schimbă întâi distanța ori rezoluția, nu clasificatorul.

Camerele de prindere stau pe flanșa fiecărui braț, cu 60° pe verticală; ele nu au rol de inspecție.

## Benzile: forță în cod, linie de oprire în plan

MuJoCo nu are bandă transportoare nativă, așa că transportul se împarte în două: forța care
târăște piesa o calculează `belt.py` (pur), iar `cell.py` o scrie la fiecare pas în
`data.xfrc_applied` pentru piesele care ating platforma benzii. Forța e proporțională cu diferența
dintre viteza de suprafață (0,12 m/s) și viteza piesei, dar limitată de frecarea Coulomb
(`|F| <= mu * N`): peste limită contactul alunecă, iar piesa rămâne în urmă. `speed_scale` este
locul unde intră uzura benzii, care scade viteza reală cu până la 8% (`docs/11-geaman-mujoco.md`
§3b); deocamdată nimeni nu îl cheamă cu altceva decât 1.

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

## Stare cunoscută

La sămânța 7, `view.py --check` este **verde**: scena se compilează fără avertismente, țintele cad
în raza brațelor, camera de inspecție are piesa în cadru la 3,6 px/mm și rămâne la cel puțin
0,052 m de brațe pe configurațiile eșantionate. Transportul pe benzi merge: amândouă piesele ajung
la linia de oprire a benzii și se opresc 13 mm peste ea, fără să cadă din geometrie.

`view.py --demo` este **verde la 0,95 m** (`make mj-demo`): 17,7 mm eroare maximă pe cele 15
opriri, nicio verigă a brațelor atinsă, 53,9 s simulate, iar amândouă piesele rămân pe benzile lor.
La 0,55 m, cota documentată, ciclul **cade** cu 5 constatări, fiindcă antebrațul nu intră în cutii,
ale căror pereți urcă până la nivelul benzii (`make mj-demo MJ_BASE_Z=0.55`). Cifrele ciclului și
calea lui sunt în secțiunea „Brațele" de mai sus.

Ce mai lipsește, în ordinea din `docs/11-geaman-mujoco.md`, secțiunea 8:

1. **Prinderea piesei.** Falcile ating piesa fără să o cuprindă (mâna se deschide 83 mm, discul are
   104 mm): prinderea se modelează ca legătură rigidă, ca în celula din Isaac Sim, cu o egalitate
   `weld` pornită prin `data.eq_active`. De ea atârnă și „fără piese pierdute în geometrie".
2. **Stările mașinii CNC și `run.py`.** `IDLE`/`LOADING`/`CUTTING`/`UNLOADING`/`INSPECTING`, sesiunea
   cu durată, sămânță, scenariu și ieșire nu sunt scrise, deci criteriul „o sesiune de 10 minute
   simulează un ciclu complet" nu se poate dovedi încă.
3. **Decizia asupra cotei piedestalului** (0,55 m cu cutiile mutate, ori 0,95 m cu camera de
   inspecție mutată), care blochează legarea uzurii de fizică: poziția camerei este și ea o mărime
   din telemetrie. Vezi `docs/11-geaman-mujoco.md` §2 și §10.
4. **Mișcarea laterală a pieselor pe bandă nu e măsurată la impact.** Într-o cursă lungă, o piesă
   poate aluneca spre șină; de verificat când intră brațele, fiindcă de acolo vine poziția de
   prindere.

Închis în runda de față: brațele în mișcare — `moves.py` (punctele, ordinea, apropierea, trecerile),
`arms.py` (cinematica inversă, urmărirea liniei, lungimea sculei măsurată), `view.py --demo` cu
cifrele fiecărei opriri și `make mj-demo` —, plus cele două lecții de cale (ridicarea după fiecare
punct, trecerea pe la vest de mașină) și testele pure noi pentru treceri și pentru ordinea pașilor.
