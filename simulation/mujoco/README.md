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

```bash
MUJOCO=simulation/mujoco/.venv/bin/python

# Verificarea implicită: compilează scena, așază piesele, rulează pași, raportează cifrele.
$MUJOCO simulation/mujoco/view.py --check

# Un cadru al celulei, din camera liberă (implicit renders/cell.png).
$MUJOCO simulation/mujoco/view.py --render

# Cadrul camerei de inspecție, la rezoluția ei nativă (renders/inspection.png).
$MUJOCO simulation/mujoco/view.py --render --camera camera_quality

# Viewer interactiv. Pe macOS fereastra se creează doar pe firul principal, deci cere `mjpython`,
# care vine în wheel-ul `mujoco`:
simulation/mujoco/.venv/bin/mjpython simulation/mujoco/view.py --view
```

`--check` iese cu cod 1 dacă vreo verificare cade, ca să poată fi folosit ca poartă. Restul
opțiunilor: `--steps`, `--samples` (configurații de braț eșantionate la degajarea camerei),
`--seed`, `--defect`, `--blank-shape`, `--blank-x`/`--blank-y`, `--blank-yaw-deg`,
`--part-yaw-deg`, `--camera-z`, `--width`, `--height`.

## Structură

| Fișier | Ce conține |
| --- | --- |
| `cncdt_mj/layout.py` | Așezarea în plan și înălțimile. Coordonatele `(x, y)` **nu** se transcriu: se importă din `docs/diagrams/cell_layout.py`, ca planul și scena să nu se poată contrazice. Înălțimile `Z` se scriu aici, cu fragmentul din documentație de care aparțin. **Fără MuJoCo.** |
| `cncdt_mj/part.py` | Cele două stări ale materialului: blocul brut (disc cu adaos de prelucrare) și roata dințată din generator. Geometria iese în milimetri, în planul `XY`, cu grosimea pe `Z`; se scalează în metri. **Fără MuJoCo.** |
| `cncdt_mj/scene.py` | Compunerea scenei: `scene/cell.xml` plus cele două brațe atașate programatic. Singurul modul care importă `mujoco`. |
| `scene/cell.xml` | Șablonul celulei. Cifrele nu stau în el: se pun prin substituție, din `layout.scene_geometry()`. |
| `view.py` | Cele trei moduri: `--check`, `--render`, `--view`. |

Consecința practică a împărțirii: `layout` și `part` se importă din `.venv`-ul proiectului, unde
`mujoco` nu există (ADR 0012), iar tot ce atinge motorul se validează rulând `view.py --check`.

### De ce scena se compune în Python, nu în MJCF

MJCF nu poate aduce singur modelele din Menagerie: atributul `<attach model="...">` **nu
funcționează** în MuJoCo 3.15.0 (încercat cu patru forme de cale), iar `modeldir` nu există pe
`<compiler>`. Deci `scene/cell.xml` ține doar componentele celulei, iar brațele se atașează cu
`MjSpec.attach(child, prefix=..., frame=body.to_frame())` și `copy_during_attach = True` pentru al
doilea braț. Prefixele de nume (`arm1_`, `arm2_`) țin cele două brațe separate, iar celula își
scrie propriile poziții nominale, fiindcă ale modelului copil se importă și s-ar ciocni.

## Ce verifică `--check`

Cifrele de referință, la sămânța implicită 7: `nq/nv/nu = 32/30/16`, 33 de corpuri, 208 geome, zero
avertismente MuJoCo. Piesele se așază pe benzi cu 0,1 mm derivă, iar yaw-ul cerut se citește înapoi
din scenă.

Țintele de prindere, măsurate **în spațiu** de la baza brațului, față de raza de 0,855 m din plan:
brațul 1 ajunge la 0,758 m (preluare bandă 1), 0,671 m (mașină) și 0,846 m (așezare bandă 2);
brațul 2 la 0,541 m (preluare bandă 2) și 0,850 m (fiecare cutie). Planul măsoară doar în plan, iar
`docs/11-geaman-mujoco.md` spune explicit că o țintă poate părea la îndemână în desen și să fie
prea sus — verificarea de aici este cea în spațiu.

Se mai verifică degajarea camerei de inspecție față de ambele brațe (poziția nominală plus
`--samples` configurații eșantionate) și faptul că niciun braț nu atinge un element al celulei.

## Camera de inspecție

Camera stă la **0,25 m deasupra suprafeței benzii 2**, adică `Z ≈ 1,10 m` în lume, și randează la
**640×480** cu **30° pe verticală**. Acoperă 179 × 134 mm la nivelul benzii, adică **3,6 px/mm** pe
piesă: roata de 104 mm intră pe ~58% din lățimea cadrului. Cota este parametru (`--camera-z`, ori
`layout.inspection_camera_z()`), nu o constantă comună cu celelalte camere, iar intrinsics-urile se
calculează din geometrie, în `layout.inspection_camera_intrinsics()`, nu se afirmă.

De ce nu la 2,20 m ca restul camerelor: acolo piesa de ~104 mm ajunge la vreo 10 pixeli, iar cel
mai mic defect din catalog — o gaură dezaxată cu 2,5 mm — ar cădea sub un pixel, deci nicio clasă
de defect nu ar fi separabilă. La 0,25 m, un dinte ciobit de 4 mm iese pe ~14 pixeli și dezaxarea
de 2,5 mm pe ~9.

Ce **nu** rezolvă camera: 3,6 px/mm înseamnă că diferențele sub ~0,3 mm nu sunt măsurabile, iar
uzura camerei (`docs/11-geaman-mujoco.md`, secțiunea 3) adaugă blur proporțional cu `1 - h_camera`
peste ele. Cadrul este suficient pentru catalogul actual de defecte, nu pentru unul mai fin: dacă
defectele se rafînează, se schimbă întâi distanța ori rezoluția, nu clasificatorul.

Camerele de prindere stau pe flanșa fiecărui braț, cu 60° pe verticală; ele nu au rol de inspecție.

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

La sămânța 7, `view.py --check` **cade** cu patru constatări. Două sunt conflicte reale de
geometrie, două sunt defecte ale verificării:

1. **Camera de inspecție intră în calea brațelor.** Suportul ei stă la punctul din plan
   `(3,20, 1,55)`, care este în raza brațului 2, iar consola duce capul optic spre nord, deasupra
   benzii. La poziția nominală distanțele sunt 0,500 m (brațul 1) și 0,265 m (brațul 2), dar pe
   cele 200 de configurații eșantionate coboară la 0,012 m, respectiv −0,078 m (suprapunere), sub
   pragul de 0,02 m. De reparat înainte de a folosi scena pentru date.
2. **Yaw-ul nu se compară cu wrap.** Verificarea declară că `blank_01` nu se citește înapoi, dar
   348,5° cerut și −11,5° citit sunt același unghi: comparația din `view.py::check` trebuie făcută
   modulo 360.
3. **Regula „un braț atinge un element al celulei” este prea largă.** Prinde autocontactele
   brațului (`arm1_hand↔arm1_link5`), care sunt normale; trebuie să se uite doar la contactele
   dintre un braț și geomele celulei.
4. **`tests/unit/test_mujoco_scene.py` nu există încă.** El trebuie să verifice, în `.venv`-ul
   proiectului, că `layout` și `part` se importă fără `mujoco` și că înălțimile din
   `layout.DOCUMENTED_HEIGHTS` se potrivesc cu propozițiile din `docs/11-geaman-mujoco.md`; partea
   care atinge scena se sare cu `pytest.importorskip("mujoco")`.
