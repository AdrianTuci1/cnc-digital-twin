# Ghid Isaac Sim: Kit, extensii, obținerea datelor

Ghidul răspunde la trei întrebări: cum se construiește interfața, cum se produce degradarea și
de unde vin datele. Codul aferent este în `simulation/extensions/cncdt.cnc_cell/`; acolo este
și [README-ul extensiei](../simulation/extensions/cncdt.cnc_cell/README.md), cu ordinea de
validare.

## Kit și extensii, în două minute

Isaac Sim este o aplicație construită pe **Kit**, un runtime de aplicații extensibil. Tot ce
vede utilizatorul este o extensie: viewport-ul, panoul de proprietăți, consola. O extensie are:

- un manifest `config/extension.toml` cu nume, versiune, dependențe și modulul Python;
- o clasă care moștenește `omni.ext.IExt`, cu `on_startup(ext_id)` și `on_shutdown()`;
- module Python care folosesc API-urile Kit (`omni.ui`, `omni.usd`, `omni.physx`).

De ce extensie și nu script: un script rulează o dată, iar starea lui dispare. Extensia are un
ciclu de viață gestionat de Kit, poate ține fire de execuție, poate primi evenimente de cadru
și poate desena interfață în același proces cu simularea.

## Interfața

O extensie poate desena cu `omni.ui`, direct în viewport sau într-o fereastră. Avantajul față
de o aplicație web separată: nu există două procese de sincronizat, iar stream-ul WebRTC
transmite și interfața, nu doar scena 3D.

Structura folosită în proiect (`ui/panel.py`):

```python
self._window = ui.Window("CNC Digital Twin — celula CNC", width=400, height=720)
self._window.frame.set_build_fn(self._build)          # desenează conținutul
stream = kit_app.get_app().get_update_event_stream()
self._subscription = stream.create_subscription_to_pop(self._on_update)
```

Reîmprospătarea se face citind starea controlerului de două ori pe secundă și scriind `.text`
pe etichete. Logica de business rămâne în `sim_control.py`, nu în panou.

## Degradarea

Degradarea este modelată ca **mărime fizică**, nu ca eveniment. Două procese continue, fiecare
între 0 și 1:

- **uzura sculei** — crește cât timp mașina taie; crește amplitudinea vibrațiilor
  (`1 + index^1.5`), sarcina axului (`1 + 0.7·index`), scade calitatea suprafeței
  (`1 - index^1.2`);
- **frecarea articulațiilor** — crește cât timp mașina se mișcă; crește efortul
  (`1 + 0.8·index`) și scade viteza (`1 - 0.4·index`).

`SceneSensors.apply_degradation_to_scene()` scrie aceste valori în scenă: atribute de proces
pe prim-ul axului (`cncdt:process:spindleLoad`, `:vibration`, `:temperature`, `:coolantFlow`,
`:axisFeed`) și `physxJoint:jointFriction` pe robot. Efectul apare deci în fizica simulării,
iar senzorii citesc cauza, nu doar un număr inventat.

Al treilea proces este injectorul de defecte: rupe calitatea suprafeței pe o piesă anume, ca
lanțul de inspecție să aibă ce respinge. Se declanșează din panou (`Force Defect`) sau din
cloud (`force_defect`).

## De unde vin datele

Trei surse, toate în `telemetry/sensors.py`:

1. **Articulații.** Clasa de articulație a versiunii instalate (`isaacsim.core` în 5.x,
   `omni.isaac.core` în 4.x) dă eforturile măsurate per articulație. Diferența de API este
   izolată în `load_articulation_class()`.
2. **Atribute de proces.** Valorile scrise de modelul de degradare pe prim-uri. Dacă scena nu
   le are, `SceneSensors` trece automat pe semnale sintetice și scrie un avertisment.
3. **Camera de inspecție.** Replicator (`BasicWriter`, RGB) sau, în lipsa lui, captura de
   viewport. Dacă niciuna nu funcționează, se scrie doar sidecar-ul JSON, iar pipeline-ul
   rămâne testabil.

Rezultatul este un eșantion la ~10 Hz, publicat pe MQTT mTLS. Contractul este
`telemetry/schemas/cnc.telemetry.v1.json`.

## Semnale sintetice: de ce există

Scena USD a proiectului nu este încă finalizată: nu are toate atributele de proces. Fără o
rezervă, pipeline-ul de date nu ar putea fi testat deloc. `telemetry/synthetic.py` produce
semnale coerente fizic (inerție termică, șocuri ocazionale la uzură mare, defecte de răcire)
și este folosit de:

- extensie, când scena nu are senzori;
- `telemetry/dev_publisher/`, care produce date fără Isaac Sim;
- teste, pentru paritatea caracteristicilor.

Semnalele sintetice nu sunt un înlocuitor pentru scenă: sunt o punte până când scena are
senzori. Cum se construiește scena și cum se autoră atributele de proces este în
[09-scena-celulei-cnc.md](09-scena-celulei-cnc.md).

## Ordinea de validare

1. `python3 -m pytest tests/unit/test_sim_modules.py` — modulele pure, fără GPU.
2. Extensia se încarcă în Isaac Sim și fereastra apare.
3. Butonul „Leagă senzorii" găsește atributele de proces; altfel apare avertismentul de trecere
   pe sintetic.
4. Telemetria ajunge în `/logs/telemetry.jsonl`; un eșantion se compară cu schema.
5. `tests/e2e/run_closed_loop.sh` verifică lanțul complet fără Isaac Sim.

## Capcane

- **Porturile se schimbă între versiuni.** Citiți-le din `.env`.
- **`omni.ui` nu există în afara Kit.** Importul este în `try/except`; modulele pure nu depind
  de el.
- **Căile prim-urilor diferă între scene.** Adăugați-le în `CANDIDATE_PATHS`, nu în altă parte.
- **Captura de cadre consumă timp.** O captură per piesă, la intrarea în `INSPECTING`.
- **API-ul articulațiilor s-a mutat** între 4.x și 5.x; diferența este izolată într-o singură
  funcție.
