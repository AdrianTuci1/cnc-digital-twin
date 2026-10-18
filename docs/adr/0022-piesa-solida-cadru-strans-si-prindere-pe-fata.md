# ADR 0022: Piesa se randează solidă, cadrul de pe flanșă se strînge, falcile închid pe fața piesei

Data: 2026-10-18
Stare: acceptată

## Context

Trei probleme apăreau în același timp în geamănul MuJoCo:

1. **Piesele arătau ca niște cioburi albe.** Meshul era corect din punct de vedere topologic, dar
   vîrfurile erau împărtășite între fața de sus și pereții laterali. La compilare, MuJoCo alegea
   pentru un astfel de vîrf normala unui singur triunghi — a flancului —, iar triunghiurile feței
   de sus erau iluminate ca niște plane înclinate. Efectul se vedea în toate cadrele, mai ales la
   piesa prinsă, și ascundea atît forma, cît și contactul cu falcile.
2. **Cadrul camerei de pe flanșă lăsa prea mult fundal.** La 60° pe verticală, roata de 104 mm ocupa
   ~345 px din 640, iar banda întunecată și raftul luminat luau restul. Piesa era vizibilă, dar
   verdictul se dădea pe o regiune mică din imagine.
3. **Prinderea nu ținea cont de forma piesei.** Piesa stă pe bandă la un yaw aleator, iar scula
   cobora cu falcile pe o direcție fixă. La hexagon, falcile închideau pe o coardă de 62,4 mm, nu
   pe lățimea dintre fețe (58,5 mm); prinderea „reușea” pentru că legătura rigidă prelua piesa, dar
   piesa se putea roti în falci, iar cadrele de pe flanșă arătau altă față la fiecare ciclu.

## Decizie

**Mesh cu normale plate.** Piesa se trimite în scenă ca un *triangle soup*: fiecare triunghi are
propriile trei vîrfuri, iar normala se calculează din triunghi și se repetă pe cele trei vîrfuri.
`spec.add_mesh(..., smoothnormal=False)` rămîne pornit, dar singur nu ajungea; vîrfurile repetate
sînt cele care fixează normala. Costul este un mesh de ~10 000 de vîrfuri în loc de ~1 700, fără
efect vizibil asupra pașilor de simulare.

**Material mai închis pentru piesa mașinată.** `part_brushed` coboară de la `0.86 0.88 0.90` la
`0.55 0.58 0.63`. Pe falcile albe, metalul aproape alb își pierdea conturul exact la contact;
blocul brut rămîne deschis, ca să se distingă de piesa finită.

**Alinierea falcilor la forma piesei.** `part.narrow_axes_deg(family)` găsește direcțiile din
planul piesei pe care lățimea este minimă, dintr-o căutare de 0,25° pe meshul nominal:

| Familie | Axe înguste | Prindere |
| --- | --- | --- |
| `wheel_100`, `pinion_56` | — (rotunde) | fără rotire |
| `gusset_72` | 30°, 90°, 150° | pe normala unei laturi |
| `hex_68` | 0°, 60°, 120° | pe normala unei muchii |
| `link_68` | 90° | pe axa scurtă |
| `plate_84` | 90° | pe latura de 56 mm |

`grasp_cycle._aligned` citește yaw-ul piesei din scenă, adaugă axele înguste și alege varianta cea
mai apropiată de orientarea de acum a falcilor, ca rotirea sculei să fie minimă. Un `spin` pozitiv
rotește falcile în sens invers în plan, deci diferența se scade; semnul este verificat pe hexagon,
unde `spin = -13°` aduce falcile de la 90° la 103°, exact normala unei muchii. Alinierea se aplică
la preluarea din universal și de pe banda 2, singurele locuri unde piesa are un yaw liber.

**Centrare măsurată, nu presupusă.** Falcile Panda sînt cuplate: cînd una atinge piesa, cealaltă
nu mai înaintează. La o piesă de 28 mm, o abatere de 1,5 mm lasă o falcă în aer, iar legătura
rigidă ar ascunde prinderea pe un vîrf. Poziția cerută are însă eroarea servo-urilor: la universal,
scula rămînea pînă la 9 mm sub țintă. `grasp_cycle._centered` măsoară după coborîre mijlocul
fețelor falcilor și centrul piesei, mută ținta în plan cu diferența și repetă așezarea de două
ori, cu toleranță de 0,8 mm. Cota rămîne cea calculată, ca vîrful falcii să nu intre în suport.
Abaterea măsurată după corecție este sub 0,1 mm.

**Marjă de ridicare pentru piesele subțiri.** La `link_68` (12 mm grosime), vîrful falcii ajungea
sub fața de jos a piesei și se oprea în banda 2; falcile se închideau la 28,4 mm fără niciun
contact, iar ciclul raporta o prindere ratată. `PICK_CLEARANCE_M` crește de la 1,5 mm la 3 mm;
după corecție, ambele falci ating piesa, iar deschiderea măsurată este 27,8 mm. Contactul vîrfului
cu banda rămîne posibil la piesele subțiri, dar nu mai blochează închiderea.

**Verdict pe ambele falci.** `grasp.close` numără contactele fiecărei falci separat și cere ca
ambele să atingă piesa în același pas. Contactul pe o singură falcă nu mai activează legătura
rigidă: piesa ar sta pe un vîrf și ar aluneca la prima accelerație. `session.json` primește
`left_contacts` și `right_contacts`; cîmpul `contact` rămîne „a atins piesa măcar o falcă”, iar
`success` cere ambele.

**Cîmp de vedere de 52°.** Montajul rămîne la (90, 0, 130) mm în sistemul ultimei verigi, cu
obiectivul spre punctul de strângere. La 52°, roata de 104 mm iese la ~400 px (~4,0 px/mm), iar
falcile rămîn încadrate. Gaura dezaxată de 2,5 mm ajunge la ~12 px, dintele ciobit de 4 mm la
~17 px. Spațiul liber al obiectivului rămîne ~42 mm, peste pragul de 20 mm.

**Umbra vine doar de la lumina celulei.** Modelul Panda își aduce un reflector „top" care se
lipește de fiecare braț și se rotește cu el. Cu umbra pornită, reflectorul arunca peste piesa
ținută o a doua siluetă, decalată, iar la 60% rezoluția hărții de umbre fața de sus ieșea
vălurită; efectul se citea ca piesă stricată. Reflectoarele `arm1_top` și `arm2_top` rămîn
pornite ca lumină de umplere, dar cu `castshadow=False`. `key_light` din celulă rămîne singurul
care aruncă umbră. Cadrele de pe flanșe opresc și umbra lui la randare (`Pov.shadows=False`):
silueta decalată de pe bandă se citea drept o a doua piesă, iar verdictul are nevoie de suprafața
piesei. Cadrul general al celulei păstrează umbrele, ca să se citească adîncimea scenei.

## Consecințe

- Toate cadrele arată piesa ca un solid, cu fața de sus uniform luminată; dispare și efectul de
  cioburi, și a doua siluetă dată de umbra brațului, care făceau contactul greu de citit.
- Prinderea pe contur este deterministă: la hexagon, deschiderea măsurată scade la 58,3 mm, cît
  lățimea reală; piesa nu se mai rotește în falci între preluare și așezare.
- `session.json` are două cîmpuri noi, aditive; un consumator vechi le ignoră. Nu se schimbă nicio
  schemă din `telemetry/schemas/`.
- Testele noi acoperă axele înguste ale fiecărei familii, normalele plate ale meshului compilat
  și faptul că reflectoarele brațelor nu aruncă umbră (`tests/unit/test_mujoco_scene.py`).
  Verificarea din `view.py --check` rămîne poarta pentru
  cameră: punctul de strângere în cadru, cel puțin 150 px pentru piesă și 20 mm spațiu liber.
- Numerele de rezoluție din ADR 0014 (60°, ~345 px, ~3,4 px/mm) descriu starea de atunci; acest
  ADR le înlocuiește.

## Alternative respinse

| Alternativă | Motivul respingerii |
| --- | --- |
| Doar `smoothnormal=False`, fără vîrfuri repetate | Testat: nu schimbă nimic. Compilatorul păstrează normalele medii ale vîrfurilor împărtășite. |
| Normalare manuală după compilare | `model.mesh_normal` este generat la compilare și se recalculează; o corecție după compilare s-ar pierde la următoarea scenă. |
| Cameră coaxială cu scula, mai aproape | La 5 cm de `link7`, obiectivul intră în carcasa încheieturii; măsurat, spațiul liber coboară la 0,8–17,6 mm, sub prag. |
| Cameră mai laterală, la 11 cm | Spațiul liber crește, dar piesa coboară la ~2,9 px/mm și se pierde rezoluția pentru defecte. |
| Rotirea piesei în scenă în locul sculei | Piesa trebuie să cadă pe bandă la un yaw arbitrar, ca în realitate; rotirea din scenă ar ascunde variația pe care trebuie să o învețe clasificatorul. |
| Păstrarea verdictului pe un singur contact | Treceau prinderi pe vîrf, cu piesa rotită; legătura rigidă ascundea problema pînă la așezare. |
