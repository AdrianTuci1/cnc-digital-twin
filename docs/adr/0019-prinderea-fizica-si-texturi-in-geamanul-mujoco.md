# ADR 0019: Prinderea fizică a piesei și texturi procedurale în geamănul MuJoCo

- **Status:** acceptat
- **Data:** 2026-10-17
- **Decizat de:** echipa de proiect
- **Pornește de la:** ADR 0012 (geamănul MuJoCo), ADR 0017 (dulapul pe familii),
  [11-geaman-mujoco.md](../11-geaman-mujoco.md)

## Context

Până la runda asta, ciclul geamănului atinge punctele de lucru, dar **nu prinde piesa**: falcile
mâinii Panda se apropie de ea și se opresc, iar piesa rămâne pe bandă. Pentru antrenarea modelelor
de pick-up, cadrele „piesa între fălci" și „ridicare" arătau o atingere, nu o prindere, iar
`session.json` nu avea ce eticheta: nu exista niciun rezultat al prelucrării de care să depindă
eticheta.

Trei fapte măsurate în scenă limitează soluția:

1. **Deschiderea mâinii Panda este 80 mm** (0,04 m pe fiecare falcă), iar două familii au discuri
   mai mari: `wheel_100` are 104 mm, iar `flange_72` are 72 mm. Pentru discuri, falcile nu pot
   cuprinde diametrul; coboară peste fața de sus și apasă pe ea. Pentru `bushing_68`, `wheel_60` și
   `pinion_56` falcile închid pe diametru.
2. **Un disc liber împins de vîrfurile falcilor alunecă** cîțiva centimetri în timpul închiderii,
   deci o prindere ratată ar arăta ca un defect de fizică, nu ca o ratare.
3. **Weld-ul trebuie să existe din compilare.** MuJoCo nu permite adăugarea de constrângeri după
   `mj_step`; `eq_data` și `eq_active` se pot schimba, lista de egalități nu.

Cerința de produs a fost dublă: ciclul să fie credibil și impresionant la vedere, iar datele de
antrenare să poată distinge o prindere reușită de una ratată.

## Decizie

1. **Prinderea este fizică, cu legătură rigidă după contact.** `grasp.close` comandă actuatorul
   griperului spre închis și lasă fizica să oprească falcile pe piesă. După contact, `grasp.pick`
   scrie o egalitate `weld` predefinită între `armN_hand` și corpul piesei, cu poziția relativă de
   acum, și o activează. Piesa urmează brațul rigid, ca în celula din Isaac Sim
   (`grasping.py`, `/World/Robot1/GraspJoint`).
2. **Eliberarea deschide falcile întîi, apoi desface legătura.** Altfel piesa ar fi lăsată între
   vîrfuri încă închise, iar deschiderea ar împinge-o în lateral.
3. **O clemă invizibilă ține piesa pe suprafață.** O a doua egalitate `weld`, piesă–lume, se
   activează cît falcile se închid și, pe raft, imediat după desfacerea legăturii. Reprezintă
   sprijinul suprafeței, nu o prindere a brațului: fără ea, un disc de Ø104 mm care stă cu o parte
   peste marginea raftului se răsturna în primele 180 de trepte de așezare, iar un disc liber de pe
   bandă era împins de falci.
4. **Toate egalitățile se creează la compilarea scenei și pornesc inactive.** `grasp.add_constraints`
   adaugă o legătură pentru fiecare pereche braț–piesă și o clemă pentru fiecare piesă.
   `view.py --check` numără clemele și refuză o scenă în care vreuna pornește activă.
5. **Pozițiile de așezare au variație deterministă din sămînță.** Banda 2 și compartimentul
   raftului primesc o abatere de cel mult 20 mm, respectiv 18 mm, ținută pe suprafață de
   `part.on_surface_xy`. Fiecare ciclu arată altfel, iar eticheta rămîne aceeași.
6. **Texturile sînt procedurale și se declară în XML.** `cncdt_mj/textures.py` scrie trei PNG-uri
   (beton, cauciuc de bandă, metal periat) cu numpy și zlib, în `runs/textures`, iar
   `scene/cell.xml` le încarcă prin drumuri absolute. Randatorul pieselor folosește aceleași
   texturi, plus o lumină laterală de contur.

## Alternative respinse

- **Doar contact, fără weld.** Fizica ar ține piesa doar cît este strînsă, iar la discurile mari
  falcile apasă pe fața de sus: la prima schimbare de orientare piesa alunecă. Contactul singur nu
  dă nici un semnal binar de reușită pentru etichete.
- **Adăugarea texturilor programatic** (`spec.add_texture`, `spec.add_material`). În MuJoCo 3.15.0
  corupe heap-ul la compilări repetate: `MemoryError: std::bad_alloc` la `mount_body.to_frame()`,
  apoi `pybind11_object_dealloc(): Tried to deallocate unregistered instance!`. Reproducibil la a
  doua ori a treia compunere a scenei. Texturile din XML, cu drum absolut, sînt stabile la opt
  compilări repetate.
- **Texturi din fișiere comise în repo.** Sînt binare, se schimbă greu și nu au legătură cu
  geometria; generatorul le face determinist din cod.

## Consecințe

- Ciclul complet are șase operații de prindere — trei preluări (blocul de pe banda 1, piesa din
  universal, piesa de pe banda 2) și trei lăsări (universal, banda 2, raft) —, iar `session.json`
  scrie pentru fiecare: brațul, corpul, momentul, contactul, deschiderea măsurată și reușita.
- Verificarea finală are două toleranțe: 40 mm pentru blocul de pe masa de descărcare și 60 mm
  pentru piesa din raft. Compartimentul are 0,5 m, iar eroarea de regim a brațului la cel mai jos
  compartiment este 21–23 mm; toleranța de 20 mm a poziției sculei a fost ridicată la 25 mm pentru
  același motiv. O ratare adevărată rămîne la peste 100 mm.
- Legătura de prindere **nu slăbește cu uzura**: `gripper_slip` rămîne un eveniment al motorului de
  uzură, nu o prindere care alunecă în scenă. Cînd va fi modelat, `weld` va primi o forță maximă
  din sănătatea griperului.
- Texturile nu schimbă fizica și nu intră în contract; schimbă doar cadrele. O scenă fără
  `runs/textures` le regenerează la prima compilare.
- Costul de timp: ciclul crește de la ~57 s la ~70 s simulați, fiindcă fiecare prindere are 420 de
  trepte de închidere și 340 de eliberare, iar transportul se face cu piesa în mînă.

## Verificare

- `make mj-check` — scena se compilează, clemele sînt inactive, camerele au piesa în cadru.
- `make mj-demo` — ciclul complet, pentru fiecare familie: 6/6 prinderi, zero verificări căzute.
- `make mj-record` — sesiune cu cadre; `session.json` conține jurnalul prinderilor.
- `make test` și `make mj-test` — partea pură (texturi, contracte) și partea care atinge motorul.

## Notă (ADR 0021)

Lista familiilor s-a schimbat: `flange_72`, `bushing_68` și `wheel_60` au devenit
`gusset_72`, `hex_68` și `link_68`. Prinderea rămîne cea descrisă aici; singura ajustare de
geometrie este suportul universalului, mai îngust și mai înalt, ca falcile să poată închide pe
piesele înguste fără să atingă suportul.
