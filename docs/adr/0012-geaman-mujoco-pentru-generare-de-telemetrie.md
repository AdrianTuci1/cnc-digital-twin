# ADR 0012: Al doilea geamăn, în MuJoCo, pentru generarea de telemetrie

- **Status:** propus; se acceptă după faza 1 din `docs/11-geaman-mujoco.md`
- **Data:** 2026-10-13
- **Decis de:** echipa de proiect
- **Pornește de la:** ADR 0007 (ceas real), ADR 0010 (instanța pe spot)

## Context

Scena din Isaac Sim (descrisă în `docs/09-scena-celulei-cnc.md`) este geamănul operatorului: se
vede, se atinge din panou, se filmează. Prețul ei este că are nevoie de o instanță GPU pe piața
spot (ADR 0010), de streaming DCV sau WebRTC (ADR 0004) și se validează doar rulând în Kit, ceea
ce nu se poate face în CI.

Pentru detecția de anomalii ne trebuie altceva decât fidelitate vizuală: **volum de telemetrie
etichetată, reproductibilă, generată ieftin**. Trei cerințe nu se acoperă comod în Kit:

1. **Sesiuni lungi, pe CPU.** Ca să antrenăm un detector care vede „câteva minute înainte" avem
   nevoie de sute de ore de funcționare normală plus sute de episoade de eșec, nu de câteva
   demonstrații pe GPU.
2. **Rulare în CI.** Motorul de uzură și formatul telemetriei trebuie testate fără Kit, ca orice
   alt modul pur din proiect.
3. **Uzură non-liniară cu eșecuri discrete.** Modelul actual din
   `simulation/extensions/cncdt.cnc_cell/cncdt/cnc_cell/degradation/model.py` crește liniar
   (`rate_per_second` constant) și are un singur eveniment discret (injectorul de defecte). Ne
   trebuie accelerare către avarie, drift, zgomot dependent de uzură, eșecuri mecanice (piesa
   scăpată, blocaj de ax) și degradarea imaginii de cameră — semnalele din care un detector
   învață un preaviz.

Ce există deja și se reutilizează: contractul de eșantion brut
(`telemetry/schemas/cnc.telemetry.v1.json`, ~10 Hz), contractul de ferestre
(`cncdt_ml.schema.ENRICHED_COLUMNS`, 60 s), vocabularul de comenzi
(`telemetry/schemas/cnc.control.v1.json`) și forma modelului de degradare (indicatori în [0, 1],
severitate, reparație, injectare). Random Cut Forest **nu** există încă:
`ml/training/predictive_maintenance/` este gol.

Verificat pe 2026-10-13, pe macOS arm64, în venv-ul proiectului (Python 3.14.6):

| Verificare | Rezultat |
| --- | --- |
| Wheel `mujoco 3.15.0` pentru `cp314-macosx_11_0_arm64` | există pe PyPI |
| Instalare + import | OK |
| MJCF din string, 2000 de pași `mj_step`, control pe articulații | OK, `qpos` converge la țintă |
| Cinematică (`mj_jacSite`) și contacte (`data.ncon`) | OK |
| Randare offscreen fără fereastră (`mujoco.Renderer`, 240×320) | OK, cadru RGB valid |
| Modele de roboți: pachetul `mujoco-menagerie` 2026.10.1 (`py3-none-any`) | disponibil; modelul Franka Panda este sub Apache-2.0 |

## Decizie

1. **Al doilea geamăn, headless, în `simulation/mujoco/`.** Scopul lui este generarea de
   telemetrie și de seturi de date pentru ML. Nu îl înlocuiește pe cel din Isaac Sim și nu
   devine interfața operatorului: panoul, DCV, WebRTC și dashboard-urile rămân cum sunt.
2. **Fizica o face MuJoCo, pe MJCF.** Scena conține banda 1, un braț Franka Emika Panda care
   încarcă piesa în mașină și, după ce mașina termină, o descarcă pe banda 2, o mașină CNC cu
   3 axe (stare `IDLE`/`LOADING`/`CUTTING`/`UNLOADING`/`INSPECTING`, fără așchiere efectivă),
   banda 2 cu un singur sens de mers, al doilea braț la mijlocul ei, două cutii
   (Accepted / Trash) de partea cealaltă a celui de-al doilea braț, camera fixă de inspecție a
   calității deasupra benzii 2, înainte de brațul 2, și câte o cameră pe flanșa fiecărui braț,
   pentru prindere. Modelele de roboți vin din pachetul `mujoco-menagerie`, cu licența și
   atribuirea păstrate, nu copiate manual în repo.
3. **Motorul de uzură este cod pur Python (numpy), non-liniar**, cu patru mecanisme: rată de uzură
   care crește pe măsură ce sănătatea scade, drift de calibrare, zgomot gaussian dependent de
   uzură și eșecuri discrete cu hazard crescător (piesa scăpată, blocaj CNC, alunecare de bandă).
   Degradarea camerei este un al cincilea mecanism, aplicat peste cadrele randate. Motorul nu
   importă MuJoCo, deci se testează fără el.
4. **Telemetria respectă contractele existente.** Eșantion brut la ~10 Hz pe exact câmpurile din
   `cnc.telemetry.v1.json`, apoi ferestre de 60 s pe exact cele 26 de coloane din
   `cncdt_ml.schema.ENRICHED_COLUMNS`. Geamănul nu inventează un al doilea format de date.
5. **Detecția anomaliilor: RCF local pentru dezvoltare, cu vector de caracteristici identic
   celui care va merge la SageMaker RCF.** Regula de alarmă este parte din livrabil (scor peste
   prag pe ferestre consecutive), iar timpul de anticipare se **măsoară** pe episoade de eșec
   etichetate, nu se declară.
6. **Ceasul rămâne real (ADR 0007).** `ts_ms` este ceas de perete, ferestrele au 60 s reali.
   Sesiunile lungi se obțin crescând rata de uzură, niciodată accelerând ceasul.
7. **Mediul Python al geamănului este separat de cel al lui Isaac Sim.** Kit aduce Python 3.11,
   proiectul rulează pe 3.14; cele două nu se importă reciproc. Geamănul MuJoCo este un proces
   de sine stătător, cu `requirements.txt` propriu și versiuni fixate.
8. **Fără resurse AWS în prima fază.** Niciun Terraform nou, niciun endpoint. SageMaker RCF intră
   abia în ultima fază, cu card de model în `ml/models/`, conform regulii 4 din `AGENTS.md`.

## Consecințe

- **Cost.** Generarea de date se mută pe CPU: zero GPU, zero instanță spot pornită pentru
  antrenare. Cifra de cost din `docs/07-cost.md` nu se schimbă, pentru că nu se adaugă resurse
  AWS.
- **Două geamănuri, un singur contract.** Riscul real este divergența: aceeași celulă descrisă în
  două locuri, cu doi algoritmi de uzură. Mitigarea este un test de paritate pe scenariu nominal,
  în spiritul `tests/unit/test_feature_parity.py`, care compară vectorii de caracteristici
  produși de cele două simulări.
- **Motorul de uzură se dublează la început.** Scena Isaac Sim păstrează modelul liniar în faza
  1. Unificarea (extragerea unui modul comun, cu Isaac Sim delegând) este o decizie ulterioară,
  luată când contractul de caracteristici e stabil, pentru că atinge testul de paritate.
- **Două mărimi de uzură nu au coloane în contract** (banda de alimentare, camera). Faza 1 le
  publică local, sub `device_id` distinct, pe coloanele existente. Dacă modelul are nevoie de
  ele în lake, urmează bump de versiune `1.0.0 → 1.1.0` **simultan** în cele trei locuri
  (tabelul din Terraform, schema JSON, `cncdt_ml/schema.py`), altfel Firehose cade la conversia
  Parquet.
- **Comenzile noi de panou cer bump de schemă.** `cnc.control.v1.json` are
  `additionalProperties: false`, deci `inject_fault_burst` și `set_wear_rate` nu pot intra pe
  topic fără `1.1.0`. Până atunci trăiesc doar în consola geamănului.
- **Dependență nouă, de dezvoltare**: `mujoco` și `mujoco-menagerie`, cu versiune fixată. Nu
  intră în straturile Lambda și nu ajunge în runtime-ul de producție.
- **Testele se împart în două.** Cele pure (uzură, telemetrie, detector, comenzi) rulează în
  `tests/unit/` fără MuJoCo. Cele care ating fizica sau randarea folosesc
  `pytest.importorskip("mujoco")` și se sar când pachetul lipsește.
- **Vizualizarea rămâne în CloudWatch (ADR 0005).** Viewerul MuJoCo este unealtă de dezvoltare
  locală, pornită manual, nu dashboard de operare; nu se adaugă niciun instrument BI.

## Alternative respinse

- **Mod headless în extensia Isaac Sim.** Kit pornește oricum un proces greu, cu GPU și cu
  oglinda de conținut (~10 GB); iterația pe motorul de uzură ar cere fiecare dată o instanță
  spot. Rămâne geamănul operatorului, exact ce știe să facă bine.
- **PyBullet sau Gazebo.** Al doilea motor de fizică în proiect, fără câștig pentru acest scop:
  MuJoCo are integrator implicit stabil la pași mari (important pentru sesiuni lungi), MJCF
  compact și o bibliotecă de modele curată (Menagerie, inclusiv Panda), plus cale de paralelizare
  pe GPU (MJX, MuJoCo Warp) dacă volumul de date o va cere.
- **RCF în SageMaker din prima fază.** Ar cere sesiune de operator, un endpoint care costă când
  stă degeaba și un ciclu de iterație de minute, exact în faza în care căutăm forma
  caracteristicilor. Contractul identic de caracteristici permite mutarea mai târziu fără
  rescriere.
- **Simularea efectivă a așchierii** (îndepărtare de material, FEM, model de forță pe sculă).
  Costă enorm și nu schimbă forma semnalului care contează pentru detector: un profil de sarcină,
  vibrație și temperatură modulat de uzură. Aceeași alegere ca în scena Isaac Sim.
- **Copierea modelelor MJCF în repo.** Duplică mentenanța și aduce întrebarea de licență în
  codul propriu. Pachetul `mujoco-menagerie` rezolvă amândouă.
- **MJX/Warp de la început.** Aduce JAX sau NVIDIA Warp ca dependență grea și mută problema pe
  GPU, înainte să știm că avem nevoie de atâta volum. Se reevaluează când timpul de generare
  devine limita, nu înainte.

## Completări ulterioare

- 2026-10-13: ADR propus, însoțit de planul din `docs/11-geaman-mujoco.md`. Niciun cod scris
  încă; faza 0 este doar documentația de față.
