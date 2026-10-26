# Geamăn digital MuJoCo: telemetrie pentru antrenare

Plan de implementare pentru al doilea geamăn al celulei CNC. Decizia arhitecturală stă în
[ADR 0012](adr/0012-geaman-mujoco-pentru-generare-de-telemetrie.md); documentul de față spune
**ce** se construiește, în ce ordine și cum se verifică.

Stare: faza 0 (documentația) s-a terminat, iar din faza 2 există scena MJCF din
`simulation/mujoco/`, cu piesa generată, cele două brațe, punctele de vedere de proces și de
prindere și dulapul pe
familii, plus transportul pe benzi (`belt.py`, `cell.py`) cu linie de oprire în plan. Verificarea
scenei,
`view.py --check`, este **verde**: țintele cad în raza brațelor, camerele de pe flanșe au punctul
de strângere în cadru, iar
amândouă piesele parcurg banda la viteza de suprafață și se opresc la 13 mm peste linia de oprire a
benzii. `view.py --demo` rulează ciclul complet — bandă, brațe, universal, schimb de material,
bandă, raft — pentru toate cele șase familii (**verde**), cu prindere fizică la fiecare dintre
cele șase preluări pe ciclu: falcile se închid pe piesă, o leagă de mînă, o ridică și o lasă pe
suprafața următoare. Eroarea maximă a sculei este 7,7 mm în compartimentul sudic al raftului,
fără nicio verigă atinsă. Colectarea datelor
există: `make mj-record` scrie sesiuni întregi — telemetrie pe contract, metadatele cadrelor de
inspecție, PNG-urile punctelor de vedere de proces și de prindere, plus etichetele pieselor —
pentru toate cele șase familii (**verde**, zece cadre pe ciclu, dintre care șase de prindere).
Fereastra `CUTTING` există ca timp de așchiere, fără axe animate, iar mărimile axului vin din
scenariul sesiunii. Scena are texturi procedurale — beton pe podea, cauciuc pe benzi, metal periat
pe cadre și pe piese —, generate din cod și scrise lîngă rulări, ca să nu intre binare în repo.
Starea amănunțită stă în `simulation/mujoco/README.md`.
Uzura și panoul de control (butoane de injectare și reparație) există în viewer; detectorul,
endpointul local și randarea degradată sunt încă pe hârtie.

## Cuprins

1. [Scop și non-scop](#1-scop-și-non-scop)
2. [Scena: componente și reprezentarea în MJCF](#2-scena-componente-și-reprezentarea-în-mjcf)
3. [Motorul de uzură](#3-motorul-de-uzură)
4. [Telemetrie și contracte](#4-telemetrie-și-contracte)
5. [Detecția anomaliilor și timpul de anticipare](#5-detecția-anomaliilor-și-timpul-de-anticipare)
6. [Interfața de control](#6-interfața-de-control)
7. [Structura de fișiere](#7-structura-de-fișiere)
8. [Faze și criterii de acceptare](#8-faze-și-criterii-de-acceptare)
9. [Riscuri și capcane](#9-riscuri-și-capcane)
10. [Decizii lăsate deschise](#10-decizii-lăsate-deschise)

## 1. Scop și non-scop

**Scop.** O celulă CNC simulată care rulează headless, pe CPU, ore întregi, și scoate două
lucruri: telemetrie brută compatibilă cu hot path-ul și ferestre gata de antrenare pentru un
detector de anomalii. Uzura este non-liniară, produce eșecuri mecanice reale în simulare, iar
episoadele de eșec sunt etichetate, ca să se poată măsura un preaviz.

**Non-scop.** Fidelitatea vizuală, interfața operatorului (rămâne Isaac Sim, cu DCV și WebRTC),
fizica așchierii, înlocuirea pipeline-ului existent, orice resursă AWS nouă.

## 2. Scena: componente și reprezentarea în MJCF

Fiecare componentă din promptul de cerință are corespondent în MJCF. MuJoCo nu are bandă
transportoare nativă, deci banda este un geom de tip cutie cu frecare mică, peste care un modul de
forțe tangențiale împinge corpurile aflate în contact cu ea; banda își scoate forța la o cotă din
plan, chiar ținta de preluare, iar de acolo piesa alunecă 13 mm până o oprește frecarea. Capcana
măsurată: cu frecare mare pe platformă, frecarea statică anulează
forța aplicată și piesa stă pe loc, deci platforma are `priority="1"` și frecare mică, iar aderența
benzii o dă exclusiv forța exterioară.

| Componentă | Reprezentare MJCF | Senzori | Actuatori / forțe |
| --- | --- | --- | --- |
| Bandă 1 (alimentare) | cutie (platformă cu frecare mică și prioritară, șine cu frecare mare), fără opritor: piesa se așază în amonte și banda o împinge până la linia de oprire din plan | viteză de suprafață, alunecare, număr de piese | forță tangențială per pas, aplicată prin `data.xfrc_applied` corpurilor în contact, cu limita `mu * N` |
| Braț 1, deservirea mașinii | Franka Emika Panda (7 DoF + griper) din `mujoco-menagerie`; preia piesa de pe banda 1, o încarcă în CNC, apoi o descarcă și o așază pe banda 2 | efort/curent pe articulații, forță de strângere, contact cu piesa | `position`/`motor` pe articulații, actuator pe degetele griperului; legătură rigidă `weld` după contact |
| Mașină CNC, 3 axe | un cub simbolic de 1,0 × 0,8 m, cu capacul la 1,90 m: cabinet, masă, universal și capac, fără axe în scenă; stările din contract, iar `CUTTING` este o fereastră de timp | vibrație RMS și vârf, sarcină ax, temperatură, avans | profil de sarcină și vibrație aplicat ca forță pe corpul axului, în funcție de stare și uzură |
| Braț 2 (sortare) | același model Panda, pe latura sudică a benzii 2, la 0,25 m de șina ei sudică | ca la brațul 1 | ca la brațul 1 |
| Bandă 2 (evacuare) | ca banda 1, cu un singur sens de mers, spre vest, și linia ei de oprire la ținta de preluare a brațului 2 | viteza, numărul de piese | ca la banda 1 |
| Dulap pe familii | trei rafturi suprapuse, fiecare despărțit în două compartimente de 0,50 m, în coloana `D2–E4`; podelele la 0,85 / 1,16 / 1,41 m, fața estică deschisă, iar piesa se lasă înclinat, la 50° în compartimentul sudic și 30° în cel nordic, în compartimentul familiei | numărul de piese pe compartiment | — |
| Ladă rebut | o ladă pe podea, sub capătul de vest al benzii 2, cu trei pereți scurți și latura de est deschisă; piesa neconformă rămâne pe bandă, trece de linia de oprire a brațului 2 și cade de la capătul benzii | numărul de rebuturi | — |
| Camere | punctele de vedere de proces: câte o cameră pe flanșa fiecărui braț și camera liberă. Verdictul de calitate îl dă cadrul de pe flanșa brațului 2, care vede piesa ținută în falci — rolul `sorting` din `cnc.qa.snapshot.v1.json`; rolul `inspection` a rămas definit în schemă, dar geamănul nu-l mai produce. Secvența de prindere adaugă cîte trei cadre pe fiecare flanșă | cadru RGB offscreen + scor de calitate | — |

Stările mașinii CNC sunt exact enumerația din `cnc.telemetry.v1.json`: `IDLE`, `LOADING`,
`CUTTING`, `UNLOADING`, `INSPECTING`. Ciclul complet — bandă 1 → braț 1 → CNC → braț 1 →
bandă 2 (inspecție) → braț 2 → compartimentul familiei — produce `cycle_id` și `part_id` cu
aceleași formate (`cyc-000001`, `part-000001`).

**Prinderea este fizică.** Fiecare preluare comandă actuatorul griperului spre închis, iar contactul
dintre vîrfurile falcilor și piesă oprește închiderea. După ce **ambele** falci au contact în
același pas, `grasp.py` scrie o egalitate `weld` predefinită între mînă și piesă, la poziția
relativă de acum, și o activează: piesa urmează
brațul rigid, ca o prindere reală de mare forță, și nu alunecă la schimbarea orientării. La lăsare
falcile se deschid întîi, apoi legătura se desface, ca deschiderea să nu împingă piesa în lateral.
O a doua egalitate, tot `weld`, ține piesa de lume cît se închid falcile și pe raft după ce brațul o
lasă: este clema invizibilă a suprafeței, care înlocuiește frecarea de sprijin acolo unde o
constrângere rigidă este mai stabilă. Toate egalitățile există în scenă de la compilare — MuJoCo nu
acceptă constrângeri noi după aceea — și pornesc inactive; `session.json` scrie, pentru fiecare
ciclu, jurnalul prinderilor, iar `view.py --check` refuză o scenă în care vreo clemă pornește
activă.

**Limita prinderii adevărate.** Deschiderea maximă a mâinii Panda este 80 mm. `wheel_100` are
discul de 100 mm, deci falcile coboară peste fața de sus și apasă pe ea; legătura rigidă este cea
care ține piesa, exact cum celula din Isaac Sim o leagă de griper. `plate_84` are 84 × 56 mm și se
prinde pe latura scurtă, iar celelalte piese (`gusset_72`, `hex_68`, `link_68`, `pinion_56`) închid
pe contur, pe lățimea lor. Ambele căi sînt raportate la fel: contact pe fiecare falcă
(`left_contacts`, `right_contacts`), deschiderea măsurată și reușita. Un contact pe o singură
falcă nu mai activează legătura rigidă, fiindcă piesa ar sta pe un vîrf și ar aluneca la prima
accelerație.

**Alinierea la formă.** La preluarea din universal și de pe banda 2, piesa are un yaw liber, iar
falcile au o singură direcție de închidere. `part.narrow_axes_deg` dă axele pe care lățimea piesei
este minimă — 0°, 60°, 120° la hexagon, 30°, 90°, 150° la triunghi, 90° la legătură și la placă,
niciuna la roți —, iar `grasp_cycle._aligned` rotește scula în jurul verticalei cu cea mai mică
abatere pînă la axa potrivită. La hexagon, falcile închid astfel pe lățimea reală de 58,3 mm, nu
pe o coardă de 62,4 mm.

Centrarea se măsoară în scenă: după coborîre, `_centered` compară mijlocul fețelor falcilor cu
centrul piesei, mută ținta cu diferența din plan și repetă așezarea, cu toleranță de 0,8 mm.
Falcile sînt cuplate, deci o abatere de 1,5 mm la o piesă de 28 mm lasă o falcă în aer. La
universal, eroarea servo-urilor ajungea la 9 mm; după corecție, abaterea măsurată este sub 0,1 mm.
Pentru piesele subțiri, punctul de strângere se ridică cu 3 mm (`PICK_CLEARANCE_M`): la `link_68`,
vîrful falcii se oprea în banda 2, iar falcile se închideau la 28,4 mm fără contact.

**Variația de poziție.** Locurile de așezare de pe banda 2 și din compartimentul raftului primesc o
abatere deterministă din sămînță, ținută pe suprafață de `part.on_surface_xy`: fiecare ciclu arată
altfel, iar eticheta rămîne aceeași. Piesa din universal are și o marjă de coborîre, fiindcă
vîrfurile falcilor ajung sub centrul feței de prindere.

**Texturi.** Podeaua, benzile, cadrele, mașina, universalul și piesele au texturi procedurale —
beton, cauciuc, metal periat — generate din numpy și zlib în `cncdt_mj/textures.py`, fără binare în
repo. Se scriu în `runs/textures` și se declară în `scene/cell.xml` cu drumuri absolute: texturile
adăugate programatic (`spec.add_texture`) corup heap-ul la compilări repetate în MuJoCo 3.15.0, iar
XML-ul le încarcă stabil la fiecare scenă. Randatorul pieselor folosește aceleași texturi, plus o
lumină laterală de contur, ca un disc de metal să nu se piardă în fundal.

Randarea camerei se face cu `mujoco.Renderer`. Pentru sesiunile în care interesează doar
telemetria, randarea se oprește complet, iar degradarea imaginii se aplică doar cadrelor cerute
(inspecție), ca să nu plătească timp de generare. Verificat: randarea offscreen funcționează
fără fereastră, pe CPU, în afara Kit.

**Rezoluția nu poate rămâne la implicitul de 160×120.** Diferența cea mai grosieră de rezolvat
este adâncimea unui dinte ciobit, `chip_depth = 4 mm`, iar cea mai fină este dezaxarea radială a
unei găuri, 2,5 mm. De separarea lor depinde clasificatorul, iar cadrul care dă verdictul vine acum
din camera de pe flanșa brațului 2, care fotografiază piesa ținută în falci: stă la **15,6 cm** de
punctul de strângere, cu **46° pe verticală** și **640×480**, iar roata de ~104 mm iese la
**~377 px**, adică **~3,6 px/mm**. La scara asta, cel mai fin defect din catalog — o gaură
dezaxată cu 2,5 mm — ajunge la **~9 px**, iar dintele ciobit de 4 mm la **~14 px**, deci fiecare
clasă se poate separa. Pragul `checks.MIN_WRIST_PART_PX = 150 px` prinde o cameră montată greșit
ori prea depărtată, la care piesa ajunge un blob de câteva zeci de pixeli, de neclasificat. Blurul
din secțiunea 3 se adaugă peste aceste cadre, proporțional cu `1 - h_camera`, deci o cameră
proiectată exact la limita de rezoluție pierde piesa, nu doar defectul.

**Camerele de pe flanșe.** Catalogul Menagerie al modelului Panda nu are cameră, deci celula o
adaugă pe flanșa fiecărui braț, cu **46° pe verticală** și **640×480**. Cîmpul de vedere a fost
strîns de la 60°, unde roata ocupa 345 px și jumătate din cadru rămînea bandă și raft, apoi de la
52° după mutarea montajului. Montajul stă pe o consolă laterală — la (140, 0, 140) mm în sistemul
ultimei verigi —, nu pe axa sculei: acolo, la 5 cm de `link7`, obiectivul cădea **în** carcasa
încheieturii, iar toate razele cadrului loveau materialul brațului la mai puțin de 20 mm de
lentilă. Nici montajul de 90 mm nu era în afara mîinii: lentila stătea la 38 mm de cochilia ei, în
deschiderea dintre pereți, iar razele cadrului ieșeau afară, deci cadrul arăta scena **prin** braț.
La 120 mm lentila a ieșit din cochilie, dar privea mîna aproape din lung, iar pereții concavi,
văzuți din interior, lăsau banda să treacă prin material. La 140 mm lateral și 140 mm sus, lentila
are 78 mm pînă la cochilie în orice direcție și vede mîna de sus, din exterior.

**Brațul se vedea tot prin mînă.** Consola stă deasupra mîinii, iar razele de jos ale cadrului
intră în cavitatea ei: pereții văzuți din interior sînt fețe din spate, eliminate implicit de
randator, deci banda se vedea prin material. Două corecții lucrează împreună. Orientarea se
rotește cu `WRIST_CAMERA_YAW_DEG = 4,0°` spre stînga imaginii, în jurul axei locale `Y`, după ce
s-a țintit punctul de strângere; mîna iese astfel din zona centrală a cadrului. Cadrele de pe
flanșe randează și fețele din spate (`Pov.backfaces=True`, `mjRND_CULL_FACE = 0`), deci pereții
concavi ai mîinii sînt opaci. Punctul de strângere cade la 4,0° la dreapta axului optic, cu marjă
pînă la marginea cadrului.

Orientarea obiectivului se calculează la compunerea scenei, nu se scrie de mână: așa cum vine din
catalog, camera privea **înapoi**, spre cot, iar punctul de strângere cădea la −143 mm adâncime,
adică în spatele obiectivului. Măsurat acum cu `view.py --check`: punctul de strângere e la
156 mm, piesa de 100 mm ia 362 px din 640, iar spațiul liber pînă la materialul brațului este de
**83 mm** în cîmp și **78 mm** în toate direcțiile, peste pragurile `checks.MIN_LENS_CLEARANCE_M`
și `checks.MIN_LENS_SURROUND_M`. Reflectoarele aduse de modelul Panda
(`arm1_top`, `arm2_top`) luminează în continuare, dar nu mai aruncă umbră: altfel silueta lor,
rotită cu brațul, cădea peste piesa ținută și fața de sus ieșea vălurită. Umbra rămîne doar de la
`key_light` din celulă, iar cadrele de pe flanșe o opresc complet la randare (`Pov.shadows=False`):
silueta decalată de pe bandă arăta ca o a doua piesă. Cadrul general păstrează umbrele.

**Cele trei puncte de vedere** fizice — cîte o cameră pe flanșa fiecărui braț și camera liberă —
sunt registrul din `cncdt_mj/povs.py`, care este pur: nume de cameră, rezoluție, moment de captură
și rol. Cadrul de proces se ia la punctele de lucru ale ciclului, imediat după ce scula s-a așezat
pe poziția de jos; secvența de prindere folosește aceleași camere de pe flanșe. Pe ciclu ies zece
cadre: patru de proces
(două de pe flanșa brațului 1 — blocul brut în falci și piesa în universal —, unul de pe flanșa
brațului 2, cu verdictul, și unul general al celulei, cu piesa în locul familiei ei) plus șase de
prindere, cîte trei pe fiecare braț: apropiere, piesa între fălci și ridicare.

### Poziționarea în celulă: grid de 0,5 m

![Planul celulei CNC, văzut de sus](diagrams/cnc-cell-layout.png)

Celula are 6,0 × 4,0 m, cu originea (0, 0) în colțul sud-vest, **+X spre est** și **+Y spre
nord**. Gridul are celule de 0,5 m: **coloanele A–L** spre est, **rândurile 1–8** spre nord, deci
fiecare element are o referință scurtă — banda 1 stă în `A7–F7`, brațul 1 în `F6`, mașina ocupă
`G6–I7`, banda 2 în `B4–J5`, brațul 2 în `F4`, dulapul pe familii în `D2–E4`, lada de rebut în
`A4–B5`, iar camerele de pe flanșe în `G5` (brațul 1) și `E4` (brațul 2).
Conversia la lumea MuJoCo este identitatea: planul `(x, y)` devine lumea
`(x, y)`, cu +Z în sus, deci scena preia cifrele ca atare.

Desenul și tabelele de mai jos ies din aceeași listă de coordonate
(`docs/diagrams/cell_layout.py`), regenerată cu `make cell-layout`; nimic nu se transcrie manual,
ca planul și scena să nu se poată contrazice.

| Element | Centru (x, y) | Amprentă (m) | Celule de grid |
| --- | --- | --- | --- |
| Bandă 1 (alimentare) | (1,60, 3,30) | 2,4 × 0,4 | A7–F7 |
| Braț 1 (Panda #1) | (2,95, 2,65) | bază 0,25 Ø | F6 |
| Cameră pe brațul 1 (flanșă) | (3,17, 2,42) | punct | G5 |
| Mașină CNC 3 axe | (3,90, 3,05) | 1,0 × 0,8 | G6–I7 |
| Bandă 2 (evacuare, un singur sens) | (2,70, 2,00) | 3,8 × 0,4 | B4–J5 |
| Braț 2 (Panda #2) | (2,70, 1,55) | bază 0,25 Ø | F4 |
| Cameră pe brațul 2 (flanșă) | (2,45, 1,72) | punct | E4 |
| Dulap pe familii (3 rafturi × 2 compartimente) | (2,07, 1,29) | 0,30 × 1,0 | D2–E4 |
| Raftul 1, compartimentul A (wheel_100) — podea 0,85 m | (2,20, 1,04) | 0,30 × 0,50 | D2–E3 |
| Raftul 1, compartimentul B (gusset_72) — podea 0,85 m | (2,20, 1,54) | 0,30 × 0,50 | D3–E4 |
| Raftul 2, compartimentul A (hex_68) — podea 1,16 m | (2,20, 1,04) | 0,30 × 0,50 | D2–E3 |
| Raftul 2, compartimentul B (link_68) — podea 1,16 m | (2,20, 1,54) | 0,30 × 0,50 | D3–E4 |
| Raftul 3, compartimentul A (pinion_56) — podea 1,41 m | (2,20, 1,04) | 0,30 × 0,50 | D2–E3 |
| Raftul 3, compartimentul B (plate_84) — podea 1,41 m | (2,20, 1,54) | 0,30 × 0,50 | D3–E4 |
| Ladă rebut | (0,59, 2,00) | 0,6 × 0,4 | A4–B5 |

Fiecare țintă de prindere cade în raza de 0,855 m a brațului care o deservește, măsurată în plan:

| Braț | Țintă | Distanță în plan (m) | În raza de 0,855 m |
| --- | --- | --- | --- |
| Braț 1 | preluare de pe banda 1 | 0,70 | da |
| Braț 1 | încărcare/descărcare CNC | 0,72 | da |
| Braț 1 | așezare pe banda 2 | 0,72 | da |
| Braț 2 | preluare de pe banda 2 | 0,45 | da |
| Braț 2 | raftul 1, compartimentul A (wheel_100) | 0,71 | da |
| Braț 2 | raftul 1, compartimentul B (gusset_72) | 0,50 | da |
| Braț 2 | raftul 2, compartimentul A (hex_68) | 0,71 | da |
| Braț 2 | raftul 2, compartimentul B (link_68) | 0,50 | da |
| Braț 2 | raftul 3, compartimentul A (pinion_56) | 0,71 | da |
| Braț 2 | raftul 3, compartimentul B (plate_84) | 0,50 | da |

Fluxul materialului, desenat punctat pe plan: banda 1 intră din `A7` spre est și lasă **blocul
brut** — un disc de metal, nu piesa finită — la capătul ei, în `F7`; brațul 1 îl ridică și îl
încarcă în mașină pe fața vestică. Abia după ce piesa este poziționată în dispozitiv intră
generatorul procedural: geometria de pe masa mașinii devine piesa familiei, cu clasa ei de defect,
iar eticheta iese din același `GearParams` care a produs geometria, deci nu se pot desincroniza.
După ce mașina termină, brațul 1 o descarcă tot el și o așază pe banda 2, în `G5`,
la o rotație aleatoare. Banda 2 merge într-un singur sens, spre vest. Brațul 2, aflat pe latura
sudică a benzii, în `F4`, o ridică din `F5` și o lasă în compartimentul familiei ei, în dulapul
din `D2–E4`, cu podelele rafturilor la 0,85 / 1,16 / 1,41 m, iar podeaua de jos la nivelul
suprafeței benzii.

Dulapul pe familii are **trei rafturi suprapuse, fiecare despărțit în două compartimente** de un
panou vertical la mijloc, deci șase compartimente de 0,50 m lățime. Gabaritul lui este 0,30 × 1,00 m,
cu colțul sud-vest în (1,92, 0,79), iar mijlocul la y = 1,29. Dulapul stă la **vest de brațul 2**,
cu lungimea pe direcția nord-sud, iar **fața estică, spre braț, este deschisă**; lateralele de
nord și sud, spatele de vest, fundul și capacul formează carcasa. Podelele rafturilor stau la
0,85 / 1,16 / 1,41 m, iar fața de sus a dulapului la 1,69 m. Fața de intrare este la x = 2,22 m
(0,48 m vest de baza brațului 2), punctele de așezare la x = 2,20 m, iar cei 0,30 m de adâncime
se întind spre vest, în afara anvelopei brațului. Astfel brațul se rotește aproximativ 90° între
preluarea de pe bandă și raft, iar antebrațul rămâne în culoarul dintre dulap și bandă.

Apropierea este înclinată, cu două unghiuri măsurate: **50° în compartimentul sudic**, cu o
abatere de 30° spre sud, și **30° drept în cel nordic**. Compartimentul sudic are raftul 3 aproape
de limita de atingere; la 50° cu abatere spre sud intră fără contacte, cu **7,7 mm** la punctul de
jos și 0,9° de răsucire (măsurat pe `wheel_100`), iar cel nordic cu **4,0 mm** și 0,5° pe
`gusset_72`. Punctul de trecere stă la x = 2,48 m, adică
0,26 m est de fața deschisă, la 5 cm peste punctul de lucru și la cel puțin 0,35 m de axul
brațului; raftul de sus se atinge direct, fără trecere, fiindcă drumul urcă pe deasupra dulapului.
Retragerea de deasupra rafturilor păstrează 4 cm până la limita de atingere.

De ce un dulap adânc nu merge cu fața spre braț. Rafturile suprapuse cer ca scula să intre înclinat
pe fața deschisă; dacă fața ar fi spre braț, raftul de deasupra ar ajunge peste antebrațul brațului
2, care coboară sub încheietură. Cu fața estică la 0,48 m de bază, antebrațul rămâne în afara
carcasei, iar compartimentul de 0,50 m ține discul cel mai mare, Ø104, cu peste 0,2 m de joc.

Fiecare compartiment ține o familie de piese, în ordinea din plan (`docs/diagrams/cell_layout.py`)
și din generator (`cnc_cell/parts/gear.py`): raftul 1 — `wheel_100`, `gusset_72`; raftul 2 —
`hex_68`, `link_68`; raftul 3 — `pinion_56` și `plate_84`. Compartimentele se umplu de la
sud la nord, iar fiecare poartă chiar ținta familiei
ei. Un test verifică potrivirea celor două ordini, ca dulapul să nu adune familiile în altă ordine
decât le învață clasificatorul. Codurile citite de pe raft sunt amânate deliberat: vezi
`docs/10-dataset-piese.md`.

Dulapul înlocuiește cutiile adânci de dinainte, care aveau pereți până la 0,85 m și opreau
antebrațul brațului 2 (`arm2_link3↔bin_ng`). Decizia despre rafturi este în ADR 0017; orientarea
rotită cu 90°, cu două unghiuri de apropiere, este în ADR 0018, iar mutarea dulapului la vest de
braț, cu fața estică deschisă, este în ADR 0023. Măsurătorile care au dus la formă sunt în
ADR 0014. Prinderea fizică și texturile sunt în ADR 0019.

Rebutul are o cale fără braț, ca în plan: piesa neconformă rămâne pe bandă, trece de linia de
oprire a brațului 2 și cade de la capătul de vest al benzii 2 în lada din `A4–B5`. Marginea lăzii
stă la 0,62 m, sub nivelul benzii, ca piesa să nu se sprijine pe perete, iar latura de est nu are
perete deloc: e chiar sub capătul benzii, iar un perete acolo ar prinde piesa. Pistonul de
rejecție din scena Isaac Sim nu se dublează aici — banda duce rebutul până la capăt, fără ca
vreun braț să-l atingă.

Rotația aleatoare de pe banda 2 nu este un detaliu de fizică, ci o decizie despre dataset: dă
clasificatorului de calitate aceeași piesă din unghiuri diferite, iar defectele din catalog sunt
diferențe relative între dinți și cercul de găuri, deci rămân vizibile la orice rotire. Yaw-ul
așezării se loghează însă împreună cu cadrul și cu eticheta, altfel nu se poate construi o
partiție de evaluare pe unghiuri nevăzute și nu se poate distinge invarianța la rotire de noroc.

Înălțimile propuse, pentru când se scrie MJCF-ul: suprafețele de lucru ale benzilor la 0,85 m,
masa mașinii la 0,75 m, capacul de protecție la 1,90 m, podeaua raftului de jos la 0,85 m,
adică la nivelul suprafeței benzii 2, podelele celorlalte două rafturi la 1,16 m și 1,41 m, fața
de sus a dulapului la 1,69 m, iar lățimea unui compartiment este de 0,50 m, cu adâncimea de
0,30 m. Marginea lăzii de rebut stă la 0,62 m, adică sub nivelul benzii, ca piesa să treacă peste
ea. Baza brațelor pe piedestal la 0,95 m. Culoarele
libere: 0,60 m între capătul benzii 1 și fața vestică a mașinii, 0,45 m între marginea nordică a
benzii 2 și fața sudică a mașinii (brațul 1 lucrează pe deasupra benzii, nu prin culoar), 0,25 m
între marginea sudică a benzii 2 și axul brațului 2, adică 0,13 m până la piedestalul lui, iar
dulapul se oprește la 0,01 m sud de șina benzii 2, ca antebrațul să aibă loc. Brațul 1 are
0,72 m până la axul mașinii și 0,72 m până la locul de așezare pe banda 2, iar brațul 2 are 0,45 m
până la punctul de preluare și 0,71 m în plan până la compartimentul sudic, respectiv 0,50 m până
la cel nordic, adică 0,72–0,85 m în spațiu, în funcție de raft. Ambele brațe
lucrează pe deasupra benzilor, nu prin culoarele
dintre ele; distanța mică dintre axul brațului 2 și banda 2 se verifică la montaj, în scenă, nu
din plan.

Ce nu spune planul: înălțimile relative dintre brațe și ținte (raza de 0,855 m este proiectată în
plan, deci o țintă poate părea la îndemână și să fie prea sus) și geometria interioară a mașinii.
Amândouă se verifică abia în scenă, la faza 2. Verificarea din scenă prinde o atingere, nu și o
traiectorie care trece razant pe deasupra.

Piedestalul brațelor este cota care nu se poate decide din plan, iar scena o măsoară. Cutiile
adânci de dinainte dădeau sentința „0,55 m nu se poate": pereții lor urcau până la 0,85 m, iar
antebrațul trecea prin ei (`arm2_link3↔bin_ng`), cu 41 mm eroare rămasă la cutia de piese bune.
Dulapul nu mai pune pereți în calea antebrațului: e deschis spre braț, fața estică stă la 0,48 m
vest de baza brațului 2, iar cei 0,30 m de adâncime se întind spre vest, în afara carcasei. Nu
scoate însă piedica de la preluare:
ca să ridice piesa de pe banda 2, brațul 2 trebuie să treacă cu antebrațul peste platformă, iar la
0,55 m platforma este deasupra lui — măsurat, `arm2_link3↔belt_out` la punctul de deasupra, cu
106 mm eroare rămasă, iar compartimentele dulapului nu mai pot fi atinse (`arm2_link3↔rack`). La
0,95 m `view.py --demo` încheie ciclul pentru toate cele șase familii, cu eroarea maximă a sculei
de 7,7 mm în compartimentul sudic și 4,0 mm în cel nordic, fără nicio
verigă atinsă. Cota
documentată a piedestalului este 0,95 m (secțiunea 10), iar `view.py --base-z` o poate coborî
pentru măsurători.

## 3. Motorul de uzură

Cod pur, fără MuJoCo, ca să poată fi testat și reproductibil (`cncdt_mj/wear.py`). Cinci mecanisme:

**a) Sănătate cu rată de uzură crescătoare.** Pentru fiecare componentă `i`, sănătatea
`h_i ∈ (0, 1]` pornește de la 1 și scade cu

```
dh_i/dt = -k_i · (1 + α_i · u_i) · h_i^{p_i},   u_i = sarcina normalizată, p_i ∈ (0, 1)
```

Exponentul `p_i < 1` face uzura lentă la început și accelerată spre avarie, adică exact
comportamentul non-liniar cerut. Constantele `k_i` se aleg pe timpi nominali, nu pe valori de
catalog: la sarcină nominală, CNC-ul ajunge la „degradat" în ~30 de minute de așchiere, brațele
în ~90 de minute de mișcare, benzile în câteva ore. Ca timpul nominal să fie chiar cel scris,
`k_i` este împărțit la factorul de sarcină, `1 + α_i u_i`: altfel factorul ar scurta viața sub
cea documentată.

Ecuația este o ecuație Bernoulli, iar motorul o integrează **exact**: cu `q = 1 - p`,
`d(h^q)/dt = -q c`, deci `h^q(t) = h^q(0) - q c t`. Timpul până la degradat nu depinde astfel de
pasul de integrare — același rezultat la pasul de 2 ms al sesiunii și la un pas de o oră. Toate
timpii se scalează dintr-o singură variabilă, care comprimă viața mașinii fără să atingă ceasul
(ADR 0007): `--rate`.

**Uzura curge cu timpul de lucru, nu cu ceasul.** Fiecare interval al ciclului are un `Work`, adică
cine lucrează: banda cât cară, brațul cât se mișcă, scula, axul și răcirea cât taie. Ce nu lucrează
nu se uzează. Uzura se acumulează **peste cicluri**, fiindcă scena se recompune la fiecare ciclu,
iar modelul de uzură este al sesiunii.

**b) Drift de calibrare.** `drift_i = d_i · (1 - h_i)`, aplicat ca abatere lentă: viteza benzii
scade cu până la 8%, articulațiile capătă un offset de poziție, axele CNC capătă eroare de
poziționare. Driftul este cel care face uzura vizibilă în medii și deviații, nu doar în nivel.

**c) Zgomot dependent de uzură.** `σ_i = σ0_i + s_i · (1 - h_i)`, adăugat peste mărimile
senzorilor (vibrație, sarcină de ax, debit de răcire, temperatură), cu sămânță fixă.

**d) Eșecuri discrete.** Hazard crescător `λ_i(t) = λ0_i · exp(γ_i · (1 - h_i))`, evaluat per
pas, cu generator cu sămânță. Evenimentele sunt cele cerute, câte unul pe componentă: piesa
scăpată din griper (`gripper_slip`), alunecarea de bandă (`belt_slip`), scula ruptă
(`tool_break`, ține până la reparație), rulmentul axului (`spindle_bearing`) și pierderea răcirii
(`coolant_loss`). Fiecare eveniment scrie o linie în `events.jsonl` **la momentul lui** (`ts`,
componentă, tip, `h`, durată), care este **eticheta** pentru detector; intervențiile de operator
intră în același fișier, cu `event: "action"`.

**e) Degradarea camerei.** Blur gaussian cu rază proporțională cu `1 - h_camera`, zgomot aditiv
pe pixel, ocazional cadru lipsă. Scorul de calitate (`1 - (1 - h_camera)` ponderat cu blurul
aplicat) intră în metadatele cadrului. **Neimplementat**: cadrele ies deocamdată curate.

**Legătura cu fizica**. Uzura nu rămâne o cifră în telemetrie, ci schimbă simularea:

| Componentă | Efectul în geamăn azi | Ce se vede în date |
| --- | --- | --- |
| Bandă | scala vitezei de suprafață scade cu driftul (`speed_scale` pe fiecare bandă), iar banda care alunecă stă pe loc cât ține evenimentul | `belt_speed_mps` în diagnostic, timpi de ciclu mai lungi, mai puține eșantioane pe secundă de ceas |
| Brațe | efortul măsurat în scenă se scalează cu `1 + 0,8 (1 - h)` | `joint_effort` mai mare, efort pe braț în diagnostic |
| Griper | falcile se închid pe piesă și o leagă rigid de mînă; legătura nu slăbește încă odată cu uzura | jurnal de prinderi în `session.json` (contact pe fiecare falcă, deschidere, reușită), evenimente |
| CNC | amplitudinea vibrației și sarcina axului cresc cu uzura rulmentului și a sculei, debitul de răcire scade, avansul scade, temperatura urcă | `vibration`, `spindle_load`, `axis_feed`, `coolant_flow_lpm`, `temp_c`, `tool_wear_index`, `surface_quality` |
| Cameră | — | scor de calitate, verzi la inspecția vizuală |

**Mașina nu se animează**: nu are axe în scenă, iar prelucrarea intră în dataset ca **fereastră de
timp** cu starea `CUTTING` (`--machining-s`, implicit 12 s pe ciclu), în care se uzează scula, axul
și răcirea și cresc mărimile de proces. Când scenei îi vor crește axele, aceleași cifre se citesc
din motor și nimic din contract nu se schimbă.

**Acțiuni de operator** (în oglindă cu `degradation/model.py`): `inject_tool_wear`,
`inject_joint_friction`, `replace_tool`, `repair_joints` — numele care există deja în
`cnc.control.v1.json` —, plus altele locale geamănului, fiindcă schema de control este închisă:
`inject_wear(componentă, cât)`, `repair(componentă)`, `inject_fault_burst(componentă, magnitudine,
durată)` — scade sănătatea brusc și ridică temporar rata de uzură de 25 de ori, scenariul pe care
detectorul trebuie să îl prindă —, `set_wear_rate(multiplier)` și `reset_wear`. Din linia de comandă
se ajung cu `--rate` (compresia vieții) și `--inject componentă:cât` (pornește sesiunea cu o
componentă deja uzată), iar în sesiune fiecare acțiune lasă o linie în `events.jsonl`.

În viewer, uzura se avansează pe pas din bucla de desenare, nu din ciclu, deci munca fiecărei
componente se reconstruiește cu `wear.demo_work`: benzile și brațele lucrează mereu, iar scula,
axul și răcirea doar cât starea ciclului este `CUTTING`. Fără `cutting`, gauge-ul CNC rămîne la
zero deși mașina taie.

## 4. Telemetrie și contracte

Două ieșiri, amândouă pe contracte existente.

**Eșantion brut, ~10 Hz**, scris în JSONL (și publicabil pe MQTT, pentru paritate cu calea
live), cu exact câmpurile din `telemetry/schemas/cnc.telemetry.v1.json`. Nu se adaugă câmpuri:
schema are `additionalProperties: false`, iar Lambda-ul de feature engineering oricum aruncă
câmpurile din afara contractului.

**Ferestre de 60 s**, scrise în Parquet (sau CSV pentru inspecție rapidă), cu exact cele 27 de
coloane din `cncdt_ml.schema.ENRICHED_COLUMNS`, produse de funcțiile existente
`cncdt_ml/features.py::compute_window_features` și `enriched_record`. Partiționarea pe disc
respectă `dt_year/dt_month/dt_day/dt_hour`, ca fișierele să poată fi citite cu aceleași
interogări Athena ca datele din lake.

Două mărimi nu au coloană în contract: uzura benzii de alimentare și calitatea camerei. Pentru
bandă se emit eșantioane brute normale, cu `device_id` propriu
(`cnc-cell-01-conveyor`), pe câmpurile care există deja. Calitatea camerei rămâne deocamdată în
metadatele locale ale sesiunii, pentru că nu are nicio coloană în contract.

Contractul este la versiunea `1.1.0`: față de `1.0.0` a adăugat `joint_friction_index_arm1` și
`joint_friction_index_arm2`, ca fiecare braț să aibă element propriu în CloudWatch (ADR 0026).
Un câmp nou intră simultan în tabelul din Terraform, în `telemetry/schemas/` și în
`cncdt_ml/schema.py` — cele trei se schimbă împreună, altfel Firehose cade la conversia Parquet.

Fișierele unei sesiuni, așa cum le scrie `make mj-record` în `simulation/mujoco/runs/<familie>-<sămânță>/`:

```
runs/wheel_100-7/
  samples.jsonl        # eșantioane brute, 10 Hz, exact câmpurile din cnc.telemetry.v1.json
  snapshots.jsonl      # metadatele cadrelor de inspecție, exact câmpurile din cnc.qa.snapshot.v1.json
  frames.jsonl         # indexul cadrelor: cale, punct de vedere, moment, eticheta piesei
  readings.jsonl       # diagnostic: efort pe braț, forță de strângere, poziția piesei, contacte
  wear.jsonl           # sănătatea fiecărei componente, aliniată pe sim_time_s cu eșantioanele
  events.jsonl         # etichetele: eșecurile componentelor și intervențiile de operator
  frames/cyc-000001/   # PNG-urile, unul pe punct de vedere și moment
  session.json         # parametrii, versiunea mujoco, sămânța, uzura, totalurile, etichetele ciclurilor
```

`frames.jsonl` este datasetul de antrenare al vederii: fiecare rând leagă un PNG de familia,
defectul și verdictul piesei din el, plus identificatorii de trasabilitate ai ciclului. Cadrele de
inspecție apar în două locuri, fiindcă au două întrebuințări: în `snapshots.jsonl` intră doar cadrul
cu rol din contract (`sorting` — flanșa brațului 2), exact ce s-ar
publica pe MQTT și în bucketul de date, iar în `frames.jsonl` intră toate, inclusiv cele de
prindere, care sunt pentru antrenare, nu pentru verdict. Rolul `inspection` a rămas definit în
`cnc.qa.snapshot.v1.json`, dar geamănul nu-l mai produce, fiindcă verdictul îl dă camera de pe
flanșa brațului 2. `readings.jsonl` ține mărimile care n-au
coloană în contract, ca să nu se strecoare în eșantionul de pe hot path.

`wear.jsonl` și `events.jsonl` sunt perechea pentru antrenarea detectorului: primul dă traiectoria
sănătății pe fiecare componentă (singurele urme din contract sunt `tool_wear_index`,
`joint_friction_index`, `joint_friction_index_arm1`, `joint_friction_index_arm2` și
`surface_quality`), al doilea dă momentele: `event: "failure"` cu
`kind`, `health`, `severity` și `duration_s`, respectiv `event: "action"` cu comanda de operator.
`session.json` scrie și tabelul componentelor cu timpii nominali, rata folosită și sănătatea de la
sfârșit, ca o sesiune să se poată reproduce fără să ghicească parametrii.

Lipsește încă `windows.parquet`: ferestrele de 60 s se exportă în faza 4, când detectorul are ce
evalua, iar atunci primesc fiecare și eticheta ei din `events.jsonl`.

### Colectarea și urcarea în bucket

```bash
eval "$(./scripts/assume-operator.sh)"     # credențialele rolului de operator
make mj-collect                            # 5 cicluri pe familie, apoi urcare în bucket
make mj-collect-plan                       # doar planul: ce ar urca, fără să atingă bucketul
make mj-publish                            # urcă sesiunile deja scrise în simulation/mujoco/runs
```

`make mj-collect` scrie fiecare familie într-un director de rulare propriu
(`runs/run-<UTC>/<familie>-<sămânță>/`, cu `MJ_COLLECT_CYCLES` cicluri pe familie), apoi urcă tot
sub prefixul corpusului. Este calea scurtă, locală: câteva cicluri, cât să vezi fișierele și să
verifici planul de urcare.

**Pentru ore de viață de mașină, colectarea rulează pe Modal**, în containere efemere paralele care
scriu direct în bucketul corpusului, cu identitate federată (ADR 0015). Codul este
`simulation/mujoco/modal_collect.py`, iar calea din Makefile:

```bash
make mj-modal-plan                         # ce ar lansa, fără să lanseze nimic
make mj-modal-collect                      # 25 de cicluri pe familie, cadre pe GPU T4
make mj-modal-collect MJ_MODAL_CYCLES=500 MJ_MODAL_SESSIONS=2
make mj-modal-collect MJ_MODAL_FRAMES=false    # doar senzori, pe CPU
```

`MJ_MODAL_CYCLES` dă ciclurile fiecărei sesiuni, `MJ_MODAL_SESSIONS` câte sesiuni ale aceleiași
familii rulează în paralel, cu semințe vecine (`MJ_MODAL_REQUIRE_WEAR`, implicit `true`, păstrează
poarta de uzură, iar `MJ_MODAL_KEEP_LOCAL=true` păstrează copia din volumul Modal după urcare).
Fiecare sesiune are propriul container, iar la final un singur container urcă rularea.

**Cadrele rulează pe GPU.** Cu `MJ_MODAL_FRAMES=true` (implicit), containerul primește un T4 și
`MUJOCO_GL=egl`, iar `run.py` scrie aceleași zece cadre pe ciclu ca local: patru de proces și șase
de prindere. Cum telemetria are deja corpusul ei, sesiunea scrie **doar cadrele** (`--only-frames`):
`frames/`, `frames.jsonl` și `session.json`, fără eșantioane, uzură ori evenimente. Rezultatul urcă
sub un prefix propriu, `training-data/grasp/`, prin `publish_frames.py`, care verifică fiecare rînd
al indexului înainte de urcare. `MJ_MODAL_TELEMETRY=true` readuce sesiunea completă sub
`training-data/mujoco/`, cu poarta de uzură. Pentru o probă care nu atinge bucketul, ambele intrări
acceptă `--no-upload`: datele rămîn în volumul Modal, sub numele rulării.

```
training-data/grasp/dt=<zi>/run-<UTC>/manifest.json
training-data/grasp/dt=<zi>/run-<UTC>/<familie>-<sămânță>/{frames.jsonl, session.json, frames/}
```

Fără cadre (`MJ_MODAL_FRAMES=false`), sesiunile rămîn pe CPU și urcă tot corpusul de senzori sub
`training-data/mujoco/`:

```
training-data/mujoco/dt=<zi>/run-<UTC>/manifest.json
training-data/mujoco/dt=<zi>/run-<UTC>/<familie>-<sămânță>/{samples.jsonl, frames.jsonl, …, frames/}
```

**Imaginile de calitate ale pieselor** nu ies din ciclul celulei, ci din randatorul de piese:
`render_parts.py` reconstruiește fiecare variantă din manifest, o așază pe un plan și o
fotografiază din patru unghiuri, cu un seed de randare independent de geometrie. Local:
`make mj-parts-dataset` (manifest + imagini) ori `make mj-parts-images` (doar cadrele). Pe Modal,
`make mj-modal-parts` generează manifestul într-un container CPU, randează feliile în containere
GPU T4 și urcă totul sub `training-data/parts/dt=<zi>/run-<UTC>/`; `PARTS_SHARDS` dă numărul de
containere (implicit 8), iar `PARTS_COUNT`, `PARTS_VIEWS` și `PARTS_SIZE` dimensiunea setului.
Fiindcă fiecare felie păstrează poziția globală a piesei, unghiurile nu depind de împărțire.

**Colectarea de uzură rulează fără camere.** Modelul de uzurare citește senzori tabulari, nu poze:
`make mj-collect MJ_COLLECT_FRAMES=false` pune `--no-frames`, iar o sesiune scade de la ~1,9 MB pe
ciclu la ~0,9 MB. Cadrele rămân pentru clasificatorul de piese, care chiar are nevoie de ele.

`datasets/` este un prefix propriu, nu `raw/`. `raw/telemetry/` este Parquet de ferestre de 60 s,
scris de Firehose și citit de tabelul Glue (secțiunea „Cum ajunge același conținut pe calea live"),
iar `raw/qa-snapshots/` este calea live: fiecare scriere acolo cheamă Lambda-ul de inspecție. Un
corpus de antrenament nu are ce căuta nici în tabel, nici în declanșator — se citește direct, cu
`cncdt_ml.offline` ori cu `ml/feature_engineering/build_features.py`, care știu să deschidă JSON
Lines. Partiționarea pe `dt=` păstrează convenția din `curated/qa-verdicts/dt=<zi>/`, iar numele
rulării face ca două colectări să nu se amestece niciodată.

**Transportul este AWS CLI** (`aws s3 cp <sesiune> s3://<bucket>/<prefix>/<sesiune> --recursive`,
plus manifestul), nu un SDK: credențialele vin din rolul de operator, iar așa nu se adaugă `boto3`
în niciunul dintre cele două venv-uri. Bucketul se ia din `DATA_LAKE_BUCKET` (`.env`, ieșirea
`bucket_name` din stack-ul `training-data`), iar
`--run-dir`, `--prefix`, `--run` și `--dt`
se pot da direct lui `simulation/mujoco/publish.py`.

**Verificarea dinaintea urcării** stă în `cncdt_mj/export.py` (pur, testat în
`tests/unit/test_mj_export.py`): fișiere lipsă ori goale, numărul de rânduri din `frames.jsonl` și
numărul de PNG-uri de pe disc față de totalul din `session.json`, numărul de rânduri de uzură față
de eșantioane. Un corpus cu găuri se refuză înainte de urcare, când se vede ieftin; altfel s-ar
vedea la antrenare. Singurele fișiere goale tolerate sunt `events.jsonl` (o sesiune fără eșecuri)
și cele de cadre ale unei sesiuni care declară zero cadre în `session.json`; o sesiune care a
pierdut cadrele declarate este în continuare refuzată. Fără `--upload`, comanda doar citește corpusul și tipărește planul, deci se
poate verifica o colectare fără să atingă bucketul.

**`manifest.json`** este contractul cu antrenarea: lista fișierelor sesiunii, sesiunile cu familia,
sămânța, ciclurile, eșantioanele, cadrele, octeții și numărul de piese pe defect, totalurile,
familiile, versiunile de schemă și parametrii de uzură din `session.json`, plus revizia git din
care s-a generat corpusul. Un job nou află din el unde sunt fișierele și cu ce contract au fost
scrise, fără să ghicească din numele de directoare. Tot în manifest intră și acoperirea de uzură
(secțiunea de mai jos).

### Ce poate învăța un model din corpus

Întrebarea la care răspunde secțiunea asta este „de unde știm că datele ajung ca să învățăm uzura pe
serii de timp?". Răspunsul are două părți: ce garantează **formatul** sesiunii și ce garantează
**colectarea**. Amândouă se măsoară, iar cifrele de mai jos sunt citite din sesiuni reale, nu
presupuse.

**Formatul.** `wear.jsonl` are un rând la fiecare eșantion, aliniat cu `samples.jsonl` pe `ts_ms` și
pe `cycle_id` (verificat: aliniere exactă pe toată sesiunea), iar rândul conține sănătatea tuturor
celor șapte componente (`<componentă>_health`) plus indicii derivați din contract
(`tool_wear_index`, `joint_friction_index`, `joint_friction_index_arm1`,
`joint_friction_index_arm2`, `surface_quality`), scala de viteză a benzilor
(`belt_speed_scale`) și temperatura. Eșantionul însuși are exact coloanele din
`cnc.telemetry.v1.json`, inclusiv cele care se mișcă cu uzura: `spindle_load`, `vibration`,
`temp_c`, `coolant_flow_lpm`, `axis_feed`, `joint_effort`. Diagnosticul (`readings.jsonl`) adaugă
mărimile care n-au coloană în contract — viteza fiecărei benzi, efortul și forța de strângere pe
fiecare braț, poziția și viteza pieselor, contactele.

Ceasul seriei este regulat: pas median **100 ms** (10 Hz, ca hot path-ul), cu pași mai mari la
hotarul dintre cicluri — **124–190 ms** măsurat — fiindcă acolo scena se recompune. Pașii mari sunt
numărați, nu ascunși: `coverage.gaps` din manifest spune câți sunt.

Uzura se vede în măsurători, nu doar în etichete. Măsurat pe o sesiune cu uzură reală: viteza benzii
coboară de la **0,12000** la **0,11652 m/s** când benzile ajung la `h = 0,638`, iar efortul brațului
1 urcă până la **38,6 Nm** la `h = 0,435` (față de 30,2 Nm cu brațul sănătos). Sarcina axului,
vibrația, temperatura, avansul și debitul de răcire vin din același motor de uzură.

**Colectarea.** Formatul este necesar, dar nu suficient: la rata nominală (`--rate 1`) un ciclu
mișcă sănătatea cu **0,006** (1,000 → 0,994), deci un corpus de rutină ar fi complet și fără nimic
de învățat. De aceea `make mj-collect` comprimă viața mașinii: `MJ_COLLECT_RATE` (implicit 20), iar
rata folosită se scrie în fiecare `session.json`, ca traiectoria să poată fi rescalată. Cu
implicitul, o colectare de 5 cicluri pe familie ajunge la `h ≈ 0,48`: benzile și brațul 1 „worn",
scula „degraded".

**Etichetele de eșec** sunt stohastice (hazard crescător, tras cu sămânța sesiunii), deci nu vin
singure dintr-o colectare scurtă. Rețetele de mai jos au fost rulate, ca să nu se ghicească:

| Combinație | Ce iese |
| --- | --- |
| `--rate 20 --cycles 5` (implicitul) | degradare pe 6 componente, `h_min ≈ 0,48`, **0 eșecuri** |
| `--rate 50 --cycles 10` | `h_min = 0,02`, **6 eșecuri** (`belt_slip` ×5, `tool_break`) |
| `--rate 50 --cycles 5 --inject cnc_tool:0.2 --inject arm1:0.2 --inject belt_out:0.2` | `h_min = 0,03`, **4 evenimente** (3 injecții și `tool_break`) |
| `--rate 200 --cycles 5` cu aceleași trei injecții | **10 evenimente**, 4 tipuri de eșec (`gripper_slip`, `tool_break`, `belt_slip`, `spindle_bearing`) |

Fiecare eșec are `kind`, `health`, `severity` și `duration_s`, deci un episod se poate delimita, iar
preavizul se poate măsura — exact metrica din secțiunea 5. Injecțiile apar ca `action`
(`inject_wear`), nu ca eșec: cine citește corpusul vede ce a fost cerut de operator și ce s-a
stricat singur.

**Poarta.** `make mj-collect` urcă prin `publish.py --require-wear`: un corpus în care nicio
componentă nu a coborât sub `h = 0,9` și în care nu s-a înregistrat niciun eșec este refuzat, cu
motivul scris în clar. Iar cifrele intră în manifest, deci răspunsul la întrebare se citește direct
din bucket: `coverage` pe fiecare sesiune (`rows`, `dt_ms`, `gaps`, `health_min`,
`health_min_by_component`, `worn`, `failures`, `actions`) și `totals.health_min`, `totals.failures`,
`totals.carries_wear`.

Ce **nu** conține încă corpusul: ferestrele de 60 s (`windows.parquet`, faza 4) și degradarea
cadrelor de cameră — cadrele ies curate, fără blur proporțional cu `1 - h_camera`.

### Cum ajunge același conținut pe calea live

Ce scrie sesiunea local este exact ce se publică: rândurile din `samples.jsonl` sunt sarcinile
MQTT, cu `topic` inclus, iar metadatele din `snapshots.jsonl` sunt sidecar-ul care declanșează
inspecția. `cncdt_mj/iot.py` este publicatorul geamănului: `paho-mqtt` peste mTLS, cu certificatul
și endpoint-ul din `simulation/secrets/` (scrise de `simulation/scripts/gen-iot-certs.sh`).
`view.py --iot` trimite eșantioanele viewer-ului, iar `run.py --iot` pe cele ale sesiunii de
colectare; ambele numără **confirmările** brokerului (`PUBACK`), nu doar punerea în coadă, fiindcă
o politică IoT greșită se vede abia la confirmare.

- **Eșantioanele** pleacă pe `cncdt/<env>/cnc/<cell>/telemetry`, iar regula IoT Core selectează
  mesajul întreg, adaugă topicul și marcajul de timp MQTT. Firehose îl convertește în Parquet
  (Snappy), partiționat pe oră, sub
  `raw/telemetry/dt_year=.../dt_month=.../dt_day=.../dt_hour=.../`, cu microbatch de 60 s și
  Lambda-ul de transformare în zbor (secțiunea 4 din `docs/01-architecture.md`).
- **Cadrele de inspecție** devin pereche PNG + JSON în bucketul data lake, sub `raw/qa-snapshots/`:
  scrierea fișierului JSON este chiar evenimentul care cheamă Lambda-ul de inspecție (notificarea
  S3 filtrează pe `filter_prefix = "raw/qa-snapshots/"`), iar Lambda-ul citește fotografia cu cheia
  din `image_key`, deci numele de fișier al geamănului (`<punct de vedere>-<moment>.png`) intră ca
  atare. Verdictul se scrie în `curated/qa-verdicts/` și se publică pe
  `cncdt/<env>/cnc/<cell>/qa-verdict`, de unde celula acționează pistonul de rejecție.
- **Identitatea** dispozitivului vine din modulul `iot-ingestion` (lucrul `cnc-cell-01`, certificat
  mTLS, politică), iar geamănul citește din mediu aceleași nume ca extensia: `CNCDT_ENVIRONMENT`,
  `CNCDT_DEVICE_ID`, `CNCDT_CELL_ID`, `CNCDT_TELEMETRY_TOPIC`, `CNCDT_QA_PREFIX`.

Pe calea live a inspecției, viewer-ul nu urcă perechea PNG + JSON în `raw/qa-snapshots/`: cu
`--route vision` trimite direct cadrul de sortare endpoint-ului, iar verdictul decide ruta în
aceeași rulare. Urcarea perechii în bucket rămâne calea sesiunii de colectare. Urcarea
**offline**, a corpusului de antrenare, merge pe alt prefix (`training-data/mujoco/`): cele două
căi au cititori diferiți și rămân separate.

## 5. Detecția anomaliilor și timpul de anticipare

**Vectorul de caracteristici** este cel din contract, plus mărimi strict locale, disponibile
pentru experiment: `spindle_load_mean/p95/std`, `vibration_rms/peak/std`, `coolant_flow_lpm`,
`temp_c_mean`, `axis_feed_mean`, `joint_effort_mean/max`, `sample_count`, `machine_state`,
plus `cycle_time_s` și `belt_speed_std`, care există în simulare și nu în schema publicată.

`tool_wear_index`, `joint_friction_index` și cele două coloane per braț **nu** intră în vector,
deși rămân în contract și în dashboard-ul de degradare: sunt `1 - h`, adică starea internă a
motorului de uzură, nu o
citire de senzor. Un detector care le primește are răspunsul în intrare, iar pe linia reală
coloanele nu există (ADR 0013). `MODEL_FEATURE_ORDER` din `lambdas/feature_engineering/core.py`
este lista care decide.

**Antrenare**: RCF local, doar pe ferestre din sesiuni sănătoase, cu aceleași hiperparametri
pentru toate componentele. **Scor**: fiecare fereastră nouă primește un scor de anomalie.
**Alarmă**: scor peste percentila 99 a distribuției sănătoase, pe două ferestre consecutive.

**Evaluare pe episoade etichetate**, raportată ca tabel, nu ca impresie:

| Metrică | Definiție | Țintă |
| --- | --- | --- |
| Preaviz median | `t(eșec) - t(alarmă)`, în secunde de ceas real | ≥ 120 s |
| Rată de detecție | episoade cu alarmă înainte de eșec / episoade totale | ≥ 0,9 |
| Alarmă falsă | alarme pe oră în sesiuni sănătoase | ≤ 1 / 8 h |

Două precizări de onestitate, care se repetă și în `ml/models/`:

1. **RCF nu prezice.** Este un detector nesupervizat de puncte anormale; preavizul vine din
   faptul că traiectoria de uzură este monotonă și lentă, deci fereastra devine anormală cu
   minute înainte ca mașina să cedeze, plus din regula de alarmă. „Cu câteva minute înainte" se
   obține prin proiectarea semnalului de uzură, nu prin algoritm.
2. **Dacă se cere predicție în sens strict**, adică `P(eșec în următoarele H minute)`, modelul
   potrivit este supervizat, pe aceleași ferestre, cu eticheta din `events.csv`. Infrastructura
   de date este aceeași; `ml/src/cncdt_ml/labels.py` există deja pentru partea de etichetare.

Pentru AWS, `mujoco` se înlocuiește cu un export de ferestre în S3: aceleași coloane, același
`$schema`. Antrenarea RCF scrie un artefact pe care containerul built-in `randomcutforest:1` îl
citește direct; Lambda-ul trimite cele nouă valori din `MODEL_FEATURE_ORDER` ca
`{"instances": [{"data": {"features": {"values": [...]}}}]}` și citește
`{"scores": [{"score": ...}]}`. Contractele ambelor modele sunt descrise în
`ml/inference/README.md`.

Simularea folosește modelele prin două endpoint-uri serverless, amândouă oprite în `dev` până
există un artefact:

| Endpoint | Imagine | Artefact | Lambda |
| --- | --- | --- | --- |
| predictive maintenance | `randomcutforest:1` | `models/predictive-maintenance/<versiune>/model.tar.gz` | feature engineering |
| inspecție vizuală | `pytorch-inference` CPU | `models/vision-qa/<versiune>/model.tar.gz` | `qa_vision_inference` |

Pornirea se face din `stacks/platform/envs/dev.tfvars`, după ce notebook-ul a scris artefactul:
`predictive_maintenance_model_data_url` ori `vision_qa_model_data_url`, plus flagul
`enable_*_endpoint`. Pragurile se copiază din aceleași rezultate:
`predictive_maintenance_anomaly_threshold` este percentila 99 din `metrics.json`, iar
`vision_qa_defect_threshold` este pragul din `deployment.json`. Endpoint-ul serverless nu are
cost când nu este invocat, iar oprirea se face cu flagul pe `false` și `make platform-up`. Fără
endpoint, Lambda-ul de inspecție numără cadrele în `QaModelMissing` și nu publică verdicte.

Serverless Inference exclude data capture, deci `stacks/platform` pornește captura doar când
`ml_endpoint_deployment_mode = "provisioned"`; modulul refuză combinația în plan, cu un mesaj
explicit. Prima invocare după deployment plătește cold start-ul containerului (zeci de secunde
la RCF, până la câteva minute la PyTorch), deci se face o încălzire înainte de a lăsa celula să
trimită cadre.

Modelul de viziune este antrenat pe cadre de sus, cu piesa singură în imagine
(`training-data/parts/`), iar cadrul QA din celulă este unul de 1280×720 cu piesa pe bandă,
luat de camera brațului de sortare. Înainte de promovare, comparați cele două distribuții pe
câteva zeci de cadre reale; dacă diferența de încadrare contează, datasetul se regenerează din
cadrele aceleiași camere, nu se compensează din prag.

Modelul are două capete peste același ResNet18: familia piesei (șase clase) și verdictul
PASS/REJECT. Verdictul alege între compartiment și rebut, iar familia alege compartimentul din
dulap. Când încrederea capului de familie coboară sub pragul stabilit în celulă, piesa urmează
aceeași rută de rezervă ca un cadru nejugat, ca un nume ghicit să nu trimită piesa în cutia
greșită.

## 6. Interfața de control

Vocabularul reutilizează `cnc.control.v1.json` acolo unde există deja comanda
(`inject_tool_wear`, `inject_joint_friction`, `replace_tool`, `repair_joints`, `pause`,
`resume`, `reset`). Comenzile noi — `inject_fault_burst`, `set_wear_rate`, `reset_wear` — sunt
locale geamănului, pentru că schema de control este închisă (`additionalProperties: false`) și
un nume nou acolo cere bump de versiune plus alinierea Lambda-ului de comenzi.

Trei intrări, toate locale:

1. **Consolă (REPL)** — merge headless, în CI, în rulări de noapte: `fault grip cnc 0.4`,
   `repair all`, `rate 20`, `status`. Este încă pe listă; comenzile pure există în `control.py`.
2. **Viewer MuJoCo** — când există display (macOS de dezvoltare, sesiunea DCV a instanței):
   `make mj-view` (ori `view.py --view --demo`) deschide viewer-ul și panoul Tkinter din `panel.py`, cu butoane pentru
   `CNC wear +0.1`, `CNC wear +0.25`, `New CNC tool`, `Repair all` și `Reset wear`, plus
   `Inject +0.25` și `Repair arm` pentru fiecare braț. Panoul citește starea și trimite comenzile
   prin linii JSON; `--no-panel`
   îl oprește, iar `view.py` continuă dacă mediul nu are Tkinter. Necesită fereastră, deci nu
   rulează în CI.
3. **Endpoint HTTP local** (`127.0.0.1`), pentru scenarii scriptate: aceleași comenzi ca în
   REPL, plus un `GET /status` care întoarce sănătatea componentelor și ultimul scor de anomalie.
   Este încă pe listă.

Panoul rămâne o unealtă de dezvoltare. Operarea se uită în dashboard-urile CloudWatch (ADR 0005).

## 7. Structura de fișiere

```
simulation/mujoco/          # există
  README.md                # instalare, comenzi, structură, intrinsics, stare cunoscută
  requirements.txt         # mujoco==3.15.0, mujoco-menagerie==2026.10.1, numpy, mapbox_earcut
  view.py                   # --check / --render / --demo / --view, peste scena compusă de cncdt_mj.scene
  panel.py                  # panoul Tkinter de uzură și reparații, pornit de view.py --view
  run.py                    # sesiunea de colectare: cicluri, telemetrie, cadre, fișierele sesiunii
  render_parts.py           # imaginile de calitate ale pieselor: manifest, cadre, felii paralele
  publish.py                # urcă în bucket corpusul scris de run.py, cu AWS CLI; plan fără --upload
  publish_frames.py         # urcă doar cadrele (training-data/grasp/); plan fără --upload
  publish_parts.py          # urcă imaginile de piese (training-data/parts/); plan fără --upload
  modal_collect.py          # aplicația Modal: sesiuni pe CPU/GPU T4, datasetul de piese, urcare
  runs/                     # sesiunile scrise de `make mj-record`; ignorate de git
  scene/cell.xml            # șablonul celulei; cifrele se pun prin substituție, nu stau în el
  cncdt_mj/
    __init__.py             # căile pachetului
    layout.py               # așezarea în plan (din docs/diagrams/cell_layout.py) și înălțimile; fără MuJoCo
    part.py                 # blocul brut și piesa din generatorul procedural; fără MuJoCo
    belt.py                 # direcțiile benzilor, viteza de suprafață, forța cu limita de frecare; fără MuJoCo
    checks.py               # regulile verificării pe nume de corpuri (contacte normale); fără MuJoCo
    moves.py                # punctele de lucru ale fiecărui braț: ordine, apropiere, treceri; fără MuJoCo
    povs.py                 # punctele de vedere de proces și de prindere: cameră, rezoluție, moment, rol; fără MuJoCo
    part_images.py          # planul imaginilor de calitate: unghiuri, nume, index, split-uri; fără MuJoCo
    part_render.py          # randatorul unei variante de piesă, pe scenă minimă texturată; importă mujoco
    textures.py             # PNG-uri procedurale: beton, cauciuc de bandă, metal periat; fără MuJoCo
    telemetry.py            # eșantionul pe contract, metadatele cadrului, fișierele sesiunii; fără MuJoCo
    wear.py                 # motorul de uzură: sănătate, efecte în contract, eșecuri, acțiuni; fără MuJoCo
    control.py              # comenzile panoului și ale viewer-ului: injectare, reparație, reset; fără MuJoCo
    export.py               # corpusul de antrenare: chei în bucket, manifest, verificări; fără MuJoCo
    scene.py                # compunerea: cell.xml plus cele două brațe; importă mujoco
    cell.py                 # contactul cu banda, viteza piesei, forța scrisă în data.xfrc_applied; importă mujoco
    arms.py                 # cinematica inversă, urmărirea căii, strângerea; importă mujoco
    grasp.py                # falcile pe piesă, clema de suprafață și legătura weld; importă mujoco
    grasp_cycle.py          # ciclul complet cu prindere, variație de poziție și verificări; importă mujoco
    sensors.py              # din MjData în mărimi de proces: efort, falci, piese, contacte; importă mujoco
    camera.py               # randare offscreen (PNG scris de mână) și cadrele sesiunii; importă mujoco
```

```
simulation/mujoco/cncdt_mj/  # de scris, în fazele 4-5
  detector.py               # RCF local + regula de alarmă (pur, numpy)
  report.py                 # preaviz, rată de detecție, alarme false (pur)
tests/unit/
  test_mujoco_scene.py      # există: layout/part/checks fără MuJoCo, înălțimile față de acest document, camerele de pe flanșe; partea care atinge motorul se rulează cu `make mj-test`
  test_mj_telemetry.py      # există: contractele eșantionului și ale cadrului, planul capturii, fișierele sesiunii
  test_mj_textures.py       # există: PNG valid, determinist, fără rescrierea fișierelor existente
  test_mj_wear.py           # există: ecuația și independența de pas, efectele în contract, eșecurile, acțiunile
  test_mj_control.py        # există: butoanele panoului, injectarea, reparația, starea trimisă viewer-ului
  test_mj_export.py         # există: cheile din bucket, manifestul, refuzul unui corpus cu găuri
  test_mj_detector.py       # prag, alarmă susținută, preaviz pe episod sintetic
```

Injecția de defecțiuni, reparația și rata de uzură nu mai au un modul propriu (`faults.py`):
trăiesc în `wear.py`, ca acțiuni care își lasă eticheta în `events.jsonl`. `control.py` le
expune ca mesaje JSON, iar panoul din `panel.py` și viewer-ul le cheamă prin aceeași interfață;
consola REPL și endpointul local rămân de scris peste același strat.

**De ce scena nu se poate scrie declarativ.** MJCF nu poate aduce singur modelele din
`mujoco-menagerie`: `<attach model="...">` nu funcționează în MuJoCo 3.15.0 — încercat cu patru
forme de cale, toate sfârșite cu „could not find model" — iar `modeldir` nu există pe `<compiler>`.
`scene/cell.xml` ține deci doar componentele celulei, iar cele două brațe se atașează programatic,
cu `MjSpec.attach(child, prefix=..., frame=body.to_frame())` și `copy_during_attach = True` pentru
al doilea exemplar. Celula își scrie propriile poziții nominale, fiindcă cele ale modelului copil
se importă și s-ar ciocni între cele două brațe.

Regula din `AGENTS.md` se respectă: identificatorii în engleză, comentariile de design în
română, logica pură separată de cea care atinge motorul de fizică — `layout.py`, `part.py`,
`belt.py`, `moves.py`, `checks.py`, `povs.py`, `telemetry.py`, `textures.py` și `export.py` se
importă din `.venv`-ul proiectului, unde `mujoco` nu există, iar `tests/unit/test_mujoco_scene.py`
verifică exact ce module ating motorul: `scene.py`, `cell.py`, `arms.py`, `grasp.py`,
`grasp_cycle.py`, `sensors.py`, `camera.py`, `part_render.py`.

## 8. Faze și criterii de acceptare

| Fază | Ce se livrează | Criteriu de acceptare |
| --- | --- | --- |
| 0 | ADR 0012 + documentul de față | revizuite, fără cod |
| 1 | `wear.py` + teste pure | `pytest` verde fără MuJoCo instalat; uzura e monotonă, mărginită și se accelerează la sănătate mică; ferestrele au exact coloanele din contract |
| 2 | `scene/cell.xml`, `belt.py`, `cell.py`, `povs.py`, `sensors.py`, `camera.py`, `run.py` | o sesiune de 10 minute simulează un ciclu complet (bandă 1 → braț 1 → CNC → braț 1 → bandă 2 → braț 2 → cutie) fără piese pierdute în geometrie; timpul de generare se măsoară și se notează în README |
| 3 | scrierea sesiunii (JSONL cu eșantioane, cadre, metadate, uzură și evenimente; apoi Parquet) | aceeași sămânță produce fișiere identice; un test de paritate compară vectorii de caracteristici cu cei din scena Isaac Sim pe scenariu nominal |
| 4 | `detector.py`, `report.py` + evaluare | tabelul de metrici din secțiunea 5, generat de o comandă, pe minimum 20 de episoade de eșec și 8 ore sănătoase |
| 5 | `control.py` + degradarea camerei | injectarea și reparația din REPL se văd în telemetrie în aceeași fereastră; cadrul de cameră degradat păstrează piesa detectabilă până la `h` critic |
| 6 | (opțional) SageMaker RCF + card de model | antrenare din ferestrele exportate, card în `ml/models/`, cost justificat în PR |

Starea de azi: din faza 2 există partea de scenă — `layout.py`, `part.py`, `checks.py`, `scene.py`,
`scene/cell.xml`, `textures.py` și `view.py` —, transportul pe benzi (`belt.py`, `cell.py`, cu linia
de oprire din plan și viewer cu benzile pornite) și **ciclul complet cu prindere fizică**
(`grasp.py`, `grasp_cycle.py`): `moves.py` ține punctele de lucru ale fiecărui braț, `arms.py` le
atinge cu cinematică inversă, urmărind linii drepte în spațiul sculei, iar la fiecare punct falcile
se închid pe piesă și legătura `weld` o ține de mînă. `view.py --check` este verde, `view.py --demo`
rulează ciclul pentru toate cele șase familii, iar testele geamănului acoperă partea pură
(`tests/unit/test_mujoco_scene.py`) și contractele colectării
(`tests/unit/test_mj_telemetry.py`).

Din faza 1 există motorul de uzură: `cncdt_mj/wear.py` ține sănătatea celor șapte componente,
o integrează exact pe timpul de lucru al fiecăreia, compune mărimile de așchiere, trage eșecurile
discrete și primește acțiunile de operator. Testele pure sunt `tests/unit/test_mj_wear.py`.

Din faza 3 există scrierea sesiunii: `make mj-record` parcurge un ciclu pe fiecare familie și scrie
cele șapte ieșiri (zece cadre pe ciclu, dintre care șase de prindere), iar `samples.jsonl`
respectă întocmai contractul. `run.py --only-frames` sare peste telemetrie și scrie doar cadrele,
indexul și `session.json`, pentru corpusul de imagini.
Există și **colectarea cu urcare în bucket**: `make mj-collect` scrie sesiunile într-un director de
rulare propriu și le urcă sub `training-data/mujoco/dt=<zi>/run-<UTC>/`, cu manifestul corpusului
lângă ele; `cncdt_mj/export.py` verifică sesiunile înainte, iar `publish.py` urcă prin AWS CLI
(secțiunea 4, „Colectarea și urcarea în bucket").

Criteriul fazei 2 este **atins în simulare**: un ciclu complet ține ~82 s simulați în regim, după
primul ciclu care încarcă blocul de pe bandă, deci `--cycles 10` acoperă cele zece minute, iar
douăsprezece rulări de probă (șase familii × două
semințe) s-au încheiat cu 6/6 prinderi pe ciclu și fără piese pierdute. Ce rămîne deschis: mașina nu are axe
(fereastra `CUTTING` este timp, nu mișcare), degradarea camerei nu se aplică încă, iar legătura de
prindere nu slăbește cu uzura. Toleranțele verificării sînt 25 mm pentru poziția sculei și 60 mm
pentru locul piesei în raft: compartimentul cel mai jos și cel mai apropiat de bază cere brațul
îndoit la limită, iar eroarea de regim acolo este 7,7 mm.

Fiecare fază se poate opri fără să lase ceva rupt: faza 1 nu are dependențe de MuJoCo, faza 2 nu
atinge contractele publicate.

## 9. Riscuri și capcane

- **Banda cere forțe proprii.** MuJoCo nu are bandă nativă; modulul de forțe tangențiale trebuie
  să fie stabil la pas mare, altfel piesele tremură sau cad prin geometrie. Se validează cu un
  test de transport: o piesă parcurge banda în timpul așteptat, la trei pași de integrare
  diferiți.
- **Determinismul are limite.** Aceeași versiune de MuJoCo, același nucleu, aceeași sămânță dau
  rezultate identice; o altă versiune poate schimba ultimele zecimale. De aceea versiunea se
  fixează în `requirements.txt`, iar `session.json` o notează.
- **Două Python-uri.** Kit (Isaac Sim) și proiectul rulează pe versiuni diferite de Python.
  Geamănul MuJoCo nu se importă din extensia Isaac Sim, nici invers; comunicarea, dacă va fi
  nevoie, se face prin fișiere sau prin rețea.
- **Licențele modelelor.** Modelul Panda din `mujoco-menagerie` este Apache-2.0; fiecare model
  folosit se verifică la adoptare, iar atribuirea se păstrează.
- **Preavizul se măsoară în ceas real.** La 60 s per fereastră, „câteva minute" înseamnă cel
  puțin două ferestre; sesiunile de evaluare trebuie să dureze real sau să folosească o rată de
  uzură mărită, cu mențiunea explicită în raport.
- **Divergența dintre geamănuri.** Cel mai probabil mod de a strica proiectul este să ajustezi
  uzura într-un geamăn și să uiți celălalt. Testul de paritate din faza 3 este plasa de
  siguranță; dacă el cade des, semnul este că motoarele trebuie unificate.
- **Volumul de date.** 10 Hz × 6 dispozitive ≈ 5,2 milioane de eșantioane pe zi. Parquet cu
  compresie și partiționare pe oră ține lucrurile rezonabile, dar sesiunile de noapte se
  planifică pe disc, nu pe bunăvoință.

## 10. Decizii lăsate deschise

1. **Unificarea motoarelor de uzură** (geamăn MuJoCo și extensia Isaac Sim) — se decide după
   faza 3, când contractul de caracteristici e dovedit stabil.
2. **Bump de schemă pentru uzura benzii și calitatea camerei** — se decide după faza 4, în
   funcție de dacă detectorul le cere în lake.
3. **Predicție supervizată peste RCF** — se decide după faza 4, când se vede cât preaviz dă
   detecția nesupervizată.
4. **Paralelizare pe GPU (MJX sau MuJoCo Warp)** — se decide când timpul de generare devine
   limita reală a antrenării.
5. **Cota piedestalului brațelor** — închisă la 0,95 m. Dulapul, deschis spre braț, cu fața
   estică la 0,48 m vest de baza brațului 2 și adânc de 0,30 m spre vest, a scos piedica pe care o
   puneau pereții cutiilor, dar a rămas banda 2: ca să ridice piesa
   de pe ea, brațul 2 trebuie să treacă cu antebrațul peste platformă, iar la 0,55 m platforma este
   deasupra lui (`arm2_link3↔belt_out`, 106 mm eroare), iar compartimentele dulapului devin de
   neatins. La 0,95 m ciclul se încheie pentru toate cele șase familii, fără nicio verigă atinsă.
   Verdictul de calitate rămâne al camerei de pe flanșa brațului 2, care fotografiază piesa ținută
   în falci, la ~2,6 px/mm.

## 11. Demonstrația live: IoT Core, alarmă și reparație

Traseul demonstrației: eșantioanele geamănului ajung în IoT Core, regula IoT le trimite la
Firehose, Lambda-ul le taie în ferestre de 60 s și cheamă endpoint-ul RCF, iar scorul ajunge în
CloudWatch. Alarma `process-anomaly` se aprinde când trei ferestre din zece minute sunt marcate
anormale, iar notificarea pleacă pe SNS.

### Pregătire, o singură dată

```bash
make platform-plan && make platform-up     # cere identitatea de admin
./simulation/scripts/gen-iot-certs.sh      # certificatul thing-ului în simulation/secrets/
```

După primul apply, confirmă abonarea la SNS din emailul trimis de AWS; fără confirmare,
notificarea nu ajunge. `alarm_email_endpoints` din `envs/dev.tfvars` trebuie să conțină adresa.

### Rularea

```bash
set -a; source simulation/secrets/iot-endpoint.env; set +a
simulation/mujoco/.venv/bin/python simulation/mujoco/run.py \
  --cycles 60 --rate 1 --iot --start-ms "$(date +%s)000"
```

`--start-ms` mută ceasul sesiunii pe ceasul real, ca ferestrele să cadă în minutele de acum:
altfel datele ajung într-un interval din trecut, iar dashboard-ul le arată lângă „acum".
`--iot` publică fiecare eșantion pe `cncdt/<mediu>/cnc/<celulă>/telemetry` cu certificatul
dispozitivului; fără el, sesiunea scrie doar local. Pentru demonstrația cu sortare, adaugă
`--route vision` la aceeași comandă: verdictul endpoint-ului alege atunci între raft și rebut.

Pentru uzură injectată de la început, adaugă `--inject cnc_tool:0.5` (sau alta componentă).
Reparația live se face din viewer, nu din sesiunea de colectare:

```bash
simulation/mujoco/.venv/bin/mjpython simulation/mujoco/view.py --view --demo --iot
```

Viewer-ul rulează ciclul, apoi rămîne deschis (`--hold`, implicit cu `--iot`) și publică
eșantioane din exact `WearModel` pe care îl repară panoul. Butonul `Repair all` readuce scula și
articulațiile, iar `Repair arm` doar brațul ales; `DegradationIndex` coboară la eșantioanele
următoare, pe elementul atins, iar alarma lui se stinge la evaluarea următoare (fereastră de
5 minute). Traseul cu `AnomalyScore` rămîne cel din
sesiunea de colectare, fiindcă modelul RCF se antrenează doar pe eșantioane `CUTTING`, iar
viewer-ul publică în repaus.

### Fluxul continuu

Cu `--loop`, ciclul nu se mai oprește între piese. Imediat după verdict, cît brațul 2 își duce
piesa la raft, brațul 1 își încearcă următoarea încărcare pe o copie a stării și o redă în pas cu
mișcarea brațului 2 (ADR 0025). Jurnalul scrie `Arm 1 starts loading the next blank while arm 2
sorts`, iar ciclul următor sare peste banda 1 și începe direct la așchiere: primul ciclu ține
~104 s, cele din regim ~82 s. Pe panou și în overlay, `Machine:` arată starea publicată în acel
moment (`IDLE`, `LOADING`, `CUTTING`, `UNLOADING`, `INSPECTING`). La rebut nu se pregătește nimic:
piesa pleacă în ladă, iar ciclul următor readuce corpurile în poziția nominală.

Mișcarea brațelor este continuă: orientarea sculei se interpolează pe traseu, iar comenzile se
rampează la `JOINT_SPEED_RAD_S`, deci saltul maxim măsurat este 0,11°/pas la ambele brațe
(ADR 0024).

### Livrarea către IoT Core

`cncdt_mj/iot.py` numără confirmările brokerului, nu doar apelurile reușite: `sent` crește cînd
paho acceptă mesajul în coadă, iar `acked` doar cînd IoT Core trimite PUBACK. Jurnalul viewer-ului
scrie prima confirmare, prima eroare cu `last_error` și, periodic, `sent … acked … failed …
rejected …` împreună cu starea mașinii. La închidere, `close()` așteaptă pînă la 3 s ca
`sent <= acked + rejected`, apoi se deconectează. Un `sent` care nu crește în `acked` înseamnă
coadă blocată, nu livrare; `rejected` înseamnă că brokerul a refuzat mesajul, de obicei din cauza
certificatului sau a politicii topicului.

### Ce se urmărește

| Unde | Ce |
| --- | --- |
| `AnomalyScore` (namespace `CNCDT/Telemetry`, dimensiunile `DeviceId` și `CellId`) | scorul RCF pe fereastră; prag 1,58 |
| Alarma `cncdt-dev-process-anomaly` | trei ferestre marcate în 10 minute |
| `DegradationIndex` (alarmele `cncdt-dev-cnc-degradation`, `cncdt-dev-arm1-degradation` și `cncdt-dev-arm2-degradation`, prag 0,7) | uzura sculei și frecarea fiecărui braț, independent de model |
| `WindowsProcessed`, `EndpointFailures` | sănătatea pipeline-ului |

Modelul este antrenat pe ferestre sănătoase dintr-o celulă pornită curat; pragul de 1,58 și
metricele sunt în `ml/models/predictive-maintenance.md`. După o reparație, scorul revine la
baseline în câteva ferestre, iar alarma se stinge singură.

### Ruta de rebut

Piesa refuzată nu este ridicată de brațul 2: brațul coboară peste ea doar cît să ia cadrul de
sortare (`arm2_wrist`, rolul `sorting`), apoi se retrage, iar banda 2 își ține forța și după
linia de preluare. Piesa trece de linia brațului 2 și cade de la capătul de vest al benzii în
lada din `A4–B5`. Măsurat în scenă, pe toate cele șase familii: contactul cu banda se pierde la
`x ≈ 0,78`, iar piesa se oprește la `x = 0,68–0,74`, pe podeaua lăzii (marginea de est a lăzii
este la `x = 0,90`). Verificarea finală (`_check_scrap`) cere ca piesa să fie în amprenta lăzii și
la cota dintre podeaua lăzii și marginea de 0,62 m, deci o piesă rămasă pe bandă, căzută pe podea
ori trecută prin tablă cade testul.

Ruta vine din verdictul endpoint-ului de viziune, prin `cncdt_mj/inspection.py`:

```bash
# rebut forțat, fără AWS: se vede mersul pe bandă în viewer
simulation/mujoco/.venv/bin/python simulation/mujoco/run.py --cycles 1 --route scrap

# verdictul modelului: cadrul de sortare ajunge la endpoint
simulation/mujoco/.venv/bin/python simulation/mujoco/run.py --cycles 1 --route vision
```

`--route vision` cheamă `cncdt-dev-vision-qa` prin AWS CLI, cu credențialele rolului de operator.
Serverless pornește containerul la prima invocare, deci prima piesă a unei rulări poate aștepta
zeci de secunde; o invocare de încălzire înainte de sesiune scutește așteptarea. Un apel căzut nu
inventează un verdict: piesa merge la rebut (`fallback`, ADR 0009), iar ciclul raportează eroarea
în `session.json`.

Familia care alege compartimentul este cea citită de model, nu eticheta sesiunii: eticheta rămîne
în dataset (ADR 0013), iar `session.json` păstrează ambele nume la fiecare ciclu, ca o
clasificare greșită să se poată număra.

### Ce încă nu face

Mașina nu are axe în scenă (`CUTTING` este o fereastră de timp), degradarea camerei nu se aplică,
iar legătura de prindere nu slăbește cu uzura. Panoul de control și endpoint-ul HTTP local pentru
scenarii scriptate sînt încă pe listă.
