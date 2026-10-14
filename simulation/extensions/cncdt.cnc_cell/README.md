# Extensia `cncdt.cnc_cell`

Extensia Kit care transformă scena Isaac Sim într-o celulă CNC instrumentată: citește senzori,
aplică degradare controlată, publică telemetrie și oferă operatorului un panou de control.
Panoul este desenat cu `omni.ui`, deci apare în browser prin stream-ul WebRTC, fără aplicație
web separată.

Extensia nu construiește geometrie: citește o scenă care respectă contractul de căi din
`CANDIDATE_PATHS`. De unde vin props-urile, cum se asamblează scena și cum se leagă de acest
contract sunt descrise în
[docs/09-scena-celulei-cnc.md](../../../docs/09-scena-celulei-cnc.md); scheletul se poate
genera cu `simulation/scripts/build-scene.py`.

## De ce o extensie Kit

Isaac Sim este construit pe Kit. Orice interfață care trebuie să vadă scena în același timp cu
simularea are două variante: o extensie în proces, sau un client extern care vorbește cu
simularea printr-un socket. A doua variantă cere sincronizare de stare și un protocol propriu.
Extensia folosește direct `omni.ui`, `omni.usd` și `omni.physx`, iar rezultatul se vede în
clientul WebRTC existent.

## Structura

| Fișier | Rol | Depinde de Isaac Sim |
| --- | --- | --- |
| `config/extension.toml` | Manifestul Kit: nume, versiune, dependențe, modul Python | da (citit de Kit) |
| `cncdt/cnc_cell/config.py` | Configurația din variabile de mediu | nu |
| `cncdt/cnc_cell/degradation/model.py` | Uzură de sculă, frecare de articulații, injector de defecte | nu |
| `cncdt/cnc_cell/telemetry/features.py` | Caracteristici pe fereastră (medie, RMS, percentile) | nu |
| `cncdt/cnc_cell/telemetry/synthetic.py` | Semnale sintetice de rezervă, când scena nu are senzori | nu |
| `cncdt/cnc_cell/telemetry/publisher.py` | Publicare MQTT (mTLS, simplu) sau fișier JSON Lines | nu |
| `cncdt/cnc_cell/telemetry/sensors.py` | Citirea articulațiilor și atributelor de proces | da |
| `cncdt/cnc_cell/telemetry/qa_camera.py` | Captura cadrelor de inspecție (Replicator / viewport) | da |
| `cncdt/cnc_cell/sim_control.py` | Ciclul de producție și reacția la comenzile din cloud | nu |
| `cncdt/cnc_cell/ui/panel.py` | Panoul de operator | da |
| `cncdt/cnc_cell/extension.py` | `omni.ext.IExt`: leagă config → controler → panou | da |

Modulele marcate „nu" sunt bibliotecă standard Python. Sunt testate local, fără GPU și fără
Isaac Sim, și sunt refolosite de publicatorul de dezvoltare din `telemetry/dev_publisher/`.

## Cele trei lucruri pe care le rezolvă extensia

**Interfața.** Panoul din `ui/panel.py` are secțiuni pentru ciclu și degradare, producție și
inspecție, control, telemetrie și un jurnal de evenimente. Butoanele cheamă direct metodele
controlerului. Interfața se reîmprospătează de două ori pe secundă, citind starea controlerului.

**Degradarea.** Uzura de sculă și frecarea articulațiilor sunt mărimi fizice continue, între 0
și 1. Fiecare crește în timp și are un efect măsurabil în scenă:

- uzura crește amplitudinea vibrațiilor (`1 + index^1.5`), sarcina axului (`1 + 0.7·index`) și
  scade calitatea suprafeței (`1 - index^1.2`);
- frecarea crește efortul articulațiilor (`1 + 0.8·index`) și scade viteza (`1 - 0.4·index`);
- `sensors.apply_degradation_to_scene()` scrie aceste valori fie ca atribute de proces pe
  prim-ul axului, fie ca `physxJoint:jointFriction` pe robot, deci efectul apare în fizica
  simulării, nu doar în date.

Injectorul de defecte acoperă cazul în care nu există model de viziune: rupe calitatea
suprafeței pe o piesă, ca lanțul de inspecție să aibă ce respinge.

**Datele.** Trei surse, toate citite în `telemetry/sensors.py`:

1. articulații, prin clasa de articulație a versiunii de Isaac Sim instalate;
2. atribute de proces pe prim-uri (`cncdt:process:spindleLoad`, `:vibration`, `:temperature`,
   `:coolantFlow`, `:axisFeed`), pe care le creează `apply_degradation_to_scene()`;
3. camera de inspecție, prin Replicator; dacă Replicator lipsește, se încearcă captura de
   viewport, iar dacă și aceea lipsește se scrie doar metadatal în sidecar-ul JSON.

Dacă scena nu are niciun atribut de proces, `SceneSensors` trece automat pe semnale sintetice
și scrie un avertisment în log. Așa pipeline-ul de date poate fi testat înainte ca scena USD
să fie completă.

## Activare

```bash
export CNCDT_EXTENSION_PATH="$PWD/simulation/extensions"
./isaac-sim.sh --ext-folder "$CNCDT_EXTENSION_PATH" --enable cncdt.cnc_cell
```

Pentru streaming, `simulation/scripts/start-isaac.sh` montează deja directorul extensiilor și
transmite variabilele de mediu din `.env`.

## Ordinea de validare

Codul a fost scris fără acces la Isaac Sim, deci trebuie validat în această ordine. Fiecare pas
are un rezultat așteptat clar; dacă un pas eșuează, pașii următori nu au sens.

1. **Modulele pure, local:** `python3 -m pytest tests/unit/test_sim_modules.py`. Nu cere GPU.
2. **Extensia se încarcă:** în Isaac Sim, fereastra de control apare în UI. Dacă nu apare,
   verificați consola Kit pentru erori la `config/extension.toml`.
3. **Senzorii se leagă:** butonul „Leagă senzorii" scrie în jurnal căile găsite. Dacă nu
   găsește niciun atribut de proces, apare avertismentul de trecere pe semnale sintetice. În
   acest caz, rulați `apply_degradation_to_scene()` (butonul „Uzură +0.25") și reîncercați.
4. **Telemetria ajunge în cloud:** publicatorul scrie în `/logs/telemetry.jsonl`. Comparați
   un eșantion cu schema `telemetry/schemas/cnc.telemetry.v1.json`.
5. **Bucla completă:** `tests/e2e/run_closed_loop.sh` verifică lanțul fără Isaac Sim.

## Capcane

- **Căile prim-urilor diferă între scene.** `CANDIDATE_PATHS` listează mai multe variante
  uzuale; adăugați acolo căile scenei voastre în loc să le hardcodați în altă parte.
- **API-ul articulațiilor s-a mutat** între Isaac Sim 4.x (`omni.isaac.core`) și 5.x
  (`isaacsim.core`). Diferența este izolată în `load_articulation_class()`.
- **`omni.ui` nu există în afara Kit.** Din acest motiv `ui/panel.py` importă modulul în
  `try/except`, iar `__init__.py` importă punctul de intrare tolerant.
- **Captura de cadre consumă timp.** Se face o dată pe piesă, la intrarea în starea
  `INSPECTING`, nu la fiecare cadru.
