"""Motorul de uzură al celulei: sănătatea componentelor, efectele ei și eșecurile discrete.

Cod **pur** (numpy), fără MuJoCo: se importă din `.venv`-ul proiectului (ADR 0012) și se
testează fără motor. Aici stă răspunsul la „de unde vin orele de date degradate": un proces lent
per componentă, efectele lui în mărimile din contract și evenimentele discrete care etichetează
episoadele.

Fiecare componentă are o sănătate ``h ∈ (0, 1]`` care scade cu

```
dh/dt = -k · (1 + α · u) · h^p,   u = sarcina normalizată, p < 1
```

Exponentul ``p < 1`` face uzura lentă cât timp mașina e bună și accelerată spre avarie
(`docs/11-geaman-mujoco.md` §3a). Constanta ``k`` nu se ia din catalog, ci din timpul de lucru
până la „degradat" (``h = 0.3``) la sarcină nominală, cu soluția ecuației:

```
k = (1 - h_degradat^(1-p)) / ((1 - p) · t_degradat)
```

Timpii nominali sunt cei din document: scula CNC ajunge la degradat în ~30 de minute de așchiere,
brațele în ~90 de minute de mișcare, benzile în câteva ore. ``rate`` comprimă viața mașinii fără
să atingă ceasul (ADR 0007), ca o sesiune scurtă să prindă tot lanțul de uzură.

Componentele și efectul lor în contract:

===================  ===========================================================
Componentă           Ce se vede în date
===================  ===========================================================
``belt_in``          viteza de suprafață scade până la 8% (driftul din §3b), deci
``belt_out``         timpul de ciclu crește; ``belt_speed_mps`` în diagnostic
``arm1``             ``joint_effort`` măsurat în scenă, scalat de frecare; timpul
``arm2``             ciclului crește; ``joint_friction_index`` = brațul mai rău,
                     ``joint_friction_index_arm1/2`` = fiecare braț
``cnc_tool``         ``tool_wear_index``, ``surface_quality``, ``spindle_load``,
                     ``axis_feed``
``cnc_spindle``      ``vibration``, ``temp_c`` (rulmenții încălzesc și tremură)
``cnc_coolant``      ``coolant_flow_lpm``, ``coolant_fault``
===================  ===========================================================

Mașina **nu se animează**: nu are axe în scenă, iar prelucrarea intră în dataset ca fereastră de
timp cu starea `CUTTING` și cifrele de mai sus. Când scenei îi vor crește axele, aceleași
mărimi se citesc din motor și nimic din contract nu se schimbă.

Eșecurile discrete (§3d) au hazard crescător ``λ = λ0 · exp(γ · (1 - h))``, evaluat per pas cu un
generator cu sămânță: banda alunecă, griperul scapă piesa, scula se rupe, rulmentul cedează,
răcirea se pierde. Fiecare eveniment este o linie în `events.jsonl` — **eticheta** pentru
detector.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from . import telemetry

#: Sănătatea la care componenta se socotește „degradată": de aici încolo se accelerează uzura, iar
#: pragurile de severitate o iau înaintea avariei.
DEGRADED_HEALTH = 0.3

#: Sănătatea minimă. Componenta nu ajunge la zero: la zero, indicele de uzură ar fi 1, iar
#: contractul cere ca indicii să rămână în interval închis, iar la sănătate foarte mică hazardul
#: este oricum mare.
MIN_HEALTH = 0.02

#: Pragurile de severitate, pe sănătate. Se scriu o dată, aici, ca raportul și testele să spună
#: același lucru.
SEVERITY_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (0.7, "worn"),
    (0.5, "degraded"),
    (0.3, "critical"),
)

#: Cât scade viteza benzii la sănătate minimă (§3b): 8%.
BELT_SPEED_DROP = 0.08

#: Cât crește efortul articulațiilor la frecare maximă (§3b): 80%.
ARM_EFFORT_GAIN = 0.8

#: Cu cât crește rata de uzură cât ține o accelerare cerută cu `inject_fault_burst`.
BURST_FACTOR = 25.0

#: Fazele care nu vin din rutele brațelor: mașina care taie și benzile care cară. Sunt chiar
#: stările `CUTTING` și `IDLE` din `cnc.telemetry.v1.json`.
PHASE_CUTTING = "CUTTING"
PHASE_TRANSPORT = "IDLE"


@dataclass(frozen=True)
class Work:
    """Ce lucrează într-un interval: cine se uzează.

    Uzura nu curge cu ceasul, ci cu timpul de lucru al fiecărei componente: scula se uzează cât
    taie, brațul cât se mișcă, banda cât cară. Un interval în care nu lucrează nimeni consumă
    ceasul, nu sănătatea.
    """

    cutting: bool = False
    belts: tuple[str, ...] = ()
    arms: tuple[str, ...] = ()

    def components(self) -> tuple[str, ...]:
        """Componentele care lucrează în intervalul acesta, în ordine stabilă."""
        names: list[str] = []
        if self.cutting:
            names.extend(("cnc_tool", "cnc_spindle", "cnc_coolant"))
        names.extend(self.belts)
        names.extend(self.arms)
        return tuple(dict.fromkeys(names))


@dataclass(frozen=True)
class Component:
    """O componentă care se uzează: cât rezistă, ce o încarcă și cum cade."""

    name: str
    label: str
    kind: str

    #: Timpul de lucru, în secunde, până la `h = DEGRADED_HEALTH` la sarcină nominală.
    degraded_after_s: float

    #: Sarcina normalizată de lucru, `u` din ecuație.
    load: float

    exponent: float = 0.5
    load_sensitivity: float = 0.6

    #: Hazardul la sănătate deplină, în evenimente pe oră de lucru.
    hazard_per_hour: float = 0.1

    #: Cât de tare crește hazardul cu uzura: `γ` din `λ = λ0 · exp(γ (1 - h))`.
    hazard_gamma: float = 6.0

    #: Ce fel de eșec dă, și cât ține. ``None`` înseamnă că rămâne până la reparație.
    failure_kind: str = ""
    failure_duration_s: float | None = None

    def rate(self) -> float:
        """Constanta `k` din ecuația uzurii, la sarcina nominală a componentei.

        Ecuația completă are și factorul de sarcină, ``dh/dt = -k (1 + α u) h^p``, iar timpul până
        la degradat este cel de la sarcină nominală: de aceea ``k`` se împarte la ``1 + α u``, ca
        cele două să se taie și componenta să ajungă la ``h = DEGRADED_HEALTH`` exact în
        ``degraded_after_s`` secunde de lucru.
        """
        power = 1.0 - self.exponent
        load_factor = 1.0 + self.load_sensitivity * self.load
        return (1.0 - DEGRADED_HEALTH ** power) / (
            power * self.degraded_after_s * load_factor)

    def hazard_per_s(self, health: float) -> float:
        """Hazardul de eșec la sănătatea dată, pe secundă de lucru."""
        return (self.hazard_per_hour / 3600.0) * math.exp(
            self.hazard_gamma * (1.0 - min(1.0, health)))


#: Componentele celulei, cu timpii lor nominali. Ordinea este ordinea din documentație.
COMPONENTS: tuple[Component, ...] = (
    Component("belt_in", "banda 1, alimentare", "belt", degraded_after_s=4 * 3600, load=0.6,
              hazard_per_hour=0.12, hazard_gamma=6.0, failure_kind="belt_slip",
              failure_duration_s=2.5),
    Component("belt_out", "banda 2, evacuare", "belt", degraded_after_s=4 * 3600, load=0.6,
              hazard_per_hour=0.12, hazard_gamma=6.0, failure_kind="belt_slip",
              failure_duration_s=2.5),
    Component("arm1", "brațul 1, deservirea mașinii", "arm", degraded_after_s=1.5 * 3600,
              load=0.5, hazard_per_hour=0.08, hazard_gamma=6.0, failure_kind="gripper_slip",
              failure_duration_s=1.5),
    Component("arm2", "brațul 2, sortare", "arm", degraded_after_s=1.5 * 3600, load=0.5,
              hazard_per_hour=0.08, hazard_gamma=6.0, failure_kind="gripper_slip",
              failure_duration_s=1.5),
    Component("cnc_tool", "scula mașinii", "cnc", degraded_after_s=30 * 60, load=0.8,
              exponent=0.6, hazard_per_hour=0.05, hazard_gamma=8.0, failure_kind="tool_break",
              failure_duration_s=None),
    Component("cnc_spindle", "axul mașinii", "cnc", degraded_after_s=90 * 60, load=0.7,
              hazard_per_hour=0.05, hazard_gamma=8.0, failure_kind="spindle_bearing",
              failure_duration_s=60.0),
    Component("cnc_coolant", "instalația de răcire", "cnc", degraded_after_s=6 * 3600, load=0.4,
              hazard_per_hour=0.06, hazard_gamma=8.0, failure_kind="coolant_loss",
              failure_duration_s=120.0),
)

#: Componentele pe categorii, pentru raportare și pentru acțiunile de operator.
BELT_COMPONENTS = tuple(item.name for item in COMPONENTS if item.kind == "belt")
ARM_COMPONENTS = tuple(item.name for item in COMPONENTS if item.kind == "arm")
CNC_COMPONENTS = tuple(item.name for item in COMPONENTS if item.kind == "cnc")


def demo_work(cutting: bool) -> Work:
    """Ce lucrează într-un pas al demonstrației: benzile și brațele, plus scula cât taie.

    În viewer, uzura se avansează din bucla de desenare, nu din ciclu, deci munca trebuie
    reconstruită aici. Fără `cutting`, scula, axul și răcirea nu se uzează niciodată, iar
    gauge-ul CNC rămîne la zero deși mașina taie.
    """
    return Work(belts=BELT_COMPONENTS, arms=ARM_COMPONENTS, cutting=bool(cutting))


@dataclass(frozen=True)
class CuttingProfile:
    """Profilul nominal al așchierii, la o sculă nouă: ce citesc senzorii când mașina taie.

    Valorile sunt de simulare, nu de catalog. Sunt singurele cifre de proces ale mașinii, fiindcă
    scena nu are axe: ele intră în coloanele `spindle_load`, `vibration`, `temp_c`,
    `coolant_flow_lpm` și `axis_feed` din contract.
    """

    spindle_load: float = 0.55
    vibration: float = 0.35
    idle_vibration: float = 0.03
    temp_rise_c: float = 16.0
    thermal_tau_s: float = 90.0
    coolant_lpm: float = 6.0
    axis_feed: float = 0.075

    #: Cât de mare este zgomotul de măsurare la sănătate deplină, pe fiecare mărime.
    spindle_noise: float = 0.02
    vibration_noise: float = 0.01
    temp_noise_c: float = 0.3
    coolant_noise_lpm: float = 0.1


@dataclass(frozen=True)
class Failure:
    """Un eșec discret: cine, ce fel, la ce sănătate și cât ține."""

    sim_time_s: float
    component: str
    kind: str
    health: float
    duration_s: float | None

    @property
    def until_s(self) -> float | None:
        """Momentul în care se stinge; ``None`` pentru cele care țin până la reparație."""
        if self.duration_s is None:
            return None
        return self.sim_time_s + self.duration_s

    def active_at(self, sim_time_s: float) -> bool:
        """Mai este în curs la momentul dat?"""
        return self.until_s is None or sim_time_s < self.until_s


@dataclass(frozen=True)
class Action:
    """O acțiune de operator (ori de cloud), ca etichetă: ce s-a cerut și când."""

    sim_time_s: float
    command: str
    component: str
    amount: float
    detail: str = ""


class WearModel:
    """Sănătatea componentelor unei sesiuni, plus mărimile de proces care ies din ea.

    Un singur model ține toată sesiunea: ciclurile se înlănțuie, iar uzura se acumulează peste ele
    — de aceea scena se poate recompune la fiecare ciclu fără să piardă uzura.
    """

    def __init__(self, *, seed: int = 0, rate: float = 1.0, ambient_c: float = 22.0,
                 profile: CuttingProfile | None = None) -> None:
        self.components: dict[str, Component] = {item.name: item for item in COMPONENTS}
        self.health: dict[str, float] = {item.name: 1.0 for item in COMPONENTS}
        self.rate = float(rate)
        self.ambient_c = float(ambient_c)
        self.profile = profile or CuttingProfile()
        self.now_s = 0.0
        self.temp_c = float(ambient_c)
        self.rng = np.random.default_rng(seed)
        self.failures: list[Failure] = []
        self.actions: list[Action] = []
        #: Cât timp mai ține o accelerare cerută cu `inject_fault_burst`, pe componentă.
        self.bursts: dict[str, float] = {}
        #: Eșecurile în curs, pe componentă: cel mai recent care încă nu s-a stins.
        self.active: dict[str, Failure] = {}

    # ------------------------------------------------------------ uzura
    def advance(self, seconds: float, work: Work) -> list[Failure]:
        """Trece timpul de lucru dat: scade sănătatea, trage eșecuri, mișcă temperatura.

        Uzura se integrează **exact**, nu în pași: ecuația ``dh/dt = -c h^p`` este o ecuație
        Bernoulli, iar cu ``q = 1 - p`` ea devine ``d(h^q)/dt = -q c``. De aici
        ``h^q(t) = h^q(0) - q c t``, deci timpul până la degradat nu depinde de pasul de integrare:
        același lucru se vede și la 2 ms, și la o secundă.

        Întoarce eșecurile **noi** apărute în intervalul acesta, ca sesiunea să le scrie în
        `events.jsonl` la momentul lor (și nu la sfârșitul ciclului).
        """
        if seconds <= 0:
            return []
        # Ceasul se mută după ce se socotește uzura: factorul de accelerare se citește la
        # începutul intervalului, altfel o accelerare care expiră exact la capătul lui ar sări
        # ultimul interval, iar una de 60 de secunde ar ține 59,998 dintr-un pas de 2 ms.
        for name in work.components():
            component = self.components[name]
            decay = (component.rate() * self.rate * self._burst_factor(name)
                     * (1.0 + component.load_sensitivity * component.load))
            power = 1.0 - component.exponent
            remaining = self.health[name] ** power - power * decay * seconds
            if remaining <= 0.0:
                self.health[name] = MIN_HEALTH
            else:
                self.health[name] = max(MIN_HEALTH, remaining ** (1.0 / power))
        self.now_s += seconds
        self._expire_failures()
        failures = self._draw_failures(seconds, work)
        self._thermal_step(seconds, cutting=work.cutting)
        return failures

    def _burst_factor(self, name: str) -> float:
        """Accelerarea cerută explicit cu `inject_fault_burst`, cât timp mai ține."""
        until = self.bursts.get(name)
        if until is None:
            return 1.0
        if self.now_s >= until:
            del self.bursts[name]
            return 1.0
        return BURST_FACTOR

    def _expire_failures(self) -> None:
        """Scoate din lista de eșecuri active pe cele care s-au stins."""
        for name, failure in list(self.active.items()):
            if not failure.active_at(self.now_s):
                del self.active[name]

    def _draw_failures(self, seconds: float, work: Work) -> list[Failure]:
        """Trage eșecurile intervalului, cu hazard crescător, din generatorul cu sămânță."""
        drawn: list[Failure] = []
        for name in work.components():
            if name in self.active:
                continue
            component = self.components[name]
            if not component.failure_kind:
                continue
            probability = 1.0 - math.exp(-component.hazard_per_s(self.health[name]) * seconds)
            if float(self.rng.random()) >= probability:
                continue
            failure = Failure(sim_time_s=self.now_s, component=name, kind=component.failure_kind,
                              health=self.health[name], duration_s=component.failure_duration_s)
            self.active[name] = failure
            self.failures.append(failure)
            drawn.append(failure)
        return drawn

    def _thermal_step(self, seconds: float, *, cutting: bool) -> None:
        """Temperatura zonei de prelucrare: urcă la așchiere, scade la repaus.

        Este un răspuns de ordinul întâi, cu constanta de timp din profil: așa arată și pe o
        mașină reală, unde termometrul urcă și coboară cu minutele, nu cu secundele.
        """
        target = self.ambient_c
        if cutting:
            target += self.profile.temp_rise_c * (1.0 + 0.5 * self.wear_index("cnc_spindle"))
        self.temp_c += (target - self.temp_c) * (1.0 - math.exp(-seconds / self.profile.thermal_tau_s))

    # ------------------------------------------------------------ citirile
    def health_of(self, name: str) -> float:
        """Sănătatea unei componente; eroarea listează numele cunoscute."""
        try:
            return self.health[name]
        except KeyError:
            raise KeyError(f"componentă inexistentă: {name}; există {sorted(self.health)}") from None

    def wear_index(self, name: str) -> float:
        """Indicele de uzură al unei componente, `1 - h`, exact intervalul din contract."""
        return round(1.0 - self.health_of(name), 6)

    def severity(self, name: str) -> str:
        """Cât de rău stă componenta: ``ok``, apoi pragurile din `SEVERITY_THRESHOLDS`.

        Pragurile sunt în ordine descrescătoare, deci se ia **ultimul** pe care sănătatea îl
        trece: la ``h = 0.1`` componenta este critică, nu doar uzată, fiindcă a trecut și de 0,5 și
        de 0,3.
        """
        health = self.health_of(name)
        label = "ok"
        for threshold, severity in SEVERITY_THRESHOLDS:
            if health < threshold:
                label = severity
        return label

    def joint_friction_index(self) -> float:
        """Frecarea articulațiilor, `1 - h`: o singură coloană în contract, deci brațul mai rău."""
        return round(1.0 - min(self.health[name] for name in ARM_COMPONENTS), 6)

    def tool_wear_index(self) -> float:
        """Uzura sculei, `1 - h`."""
        return self.wear_index("cnc_tool")

    def surface_quality(self) -> float:
        """Calitatea suprafeței, dedusă din uzura sculei; o sculă ruptă o înjumătățește.

        Relația este cea din modelul de degradare al celulei Isaac Sim
        (`cnc_cell/degradation/model.py`), ca cele două simulări să spună același lucru despre
        aceeași sculă.
        """
        quality = max(0.0, min(1.0, 1.0 - self.tool_wear_index() ** 1.2))
        if "cnc_tool" in self.active:
            quality *= 0.5
        return round(quality, 6)

    def belt_speed_scales(self) -> dict[str, float]:
        """Scala vitezei de suprafață a fiecărei benzi, pentru `cell.transport`.

        Driftul din §3b este determinist, iar banda care alunecă (eșecul `belt_slip`) stă pe loc
        cât ține evenimentul: de acolo vine „piesa nu mai ajunge la linia de preluare".
        """
        scales: dict[str, float] = {}
        for name in BELT_COMPONENTS:
            if name in self.active:
                scales[name] = 0.0
                continue
            scales[name] = round(1.0 - BELT_SPEED_DROP * (1.0 - self.health_of(name)), 6)
        return scales

    def belt_speed_scale(self) -> float:
        """Cea mai mică scală dintre benzi, pentru raportare: cât de tare merge transportul azi."""
        return min(self.belt_speed_scales().values())

    def arm_effort_scales(self) -> dict[str, float]:
        """Cât de tare crește efortul fiecărui braț față de cel măsurat în scenă.

        Scena nu modelează frecarea articulațiilor (modelul Panda vine cu `frictionloss` fix), deci
        efectul uzurii se aplică peste efortul măsurat: același factor ajunge în `joint_effort` din
        contract și în diagnostic, ca cele două fișiere să nu se contrazică.
        """
        return {name: round(1.0 + ARM_EFFORT_GAIN * (1.0 - self.health_of(name)), 6)
                for name in ARM_COMPONENTS}

    def reading(self, *, cutting: bool, noise: bool = True) -> telemetry.MachineReading:
        """Mărimile mașinii de acum, în forma din contract.

        Așchierii i se adaugă efectul uzurii: scula uzată încarcă axul și scade calitatea
        suprafeței, rulmentul obosit tremură și încălzește, pompa uzată dă mai puțin debit. La
        repaus rămân doar indicii, care sunt o stare, nu o măsurătoare de moment.
        """
        profile = self.profile
        tool = self.tool_wear_index()
        spindle = self.wear_index("cnc_spindle")
        coolant = self.wear_index("cnc_coolant")

        vibration = profile.vibration * (1.0 + spindle ** 1.5) if cutting \
            else profile.idle_vibration
        if "cnc_spindle" in self.active:
            vibration *= 3.0
        spindle_load = min(1.0, profile.spindle_load * (1.0 + 0.7 * tool)) if cutting else 0.0
        axis_feed = profile.axis_feed * (1.0 - 0.15 * tool) if cutting else 0.0
        flow = profile.coolant_lpm * (1.0 - 0.7 * coolant) if cutting else 0.0
        fault = "cnc_coolant" in self.active
        if fault:
            flow = 0.0

        if noise:
            vibration += float(self.rng.normal(0.0, profile.vibration_noise * (1.0 + 4 * spindle)))
            spindle_load = min(1.0, max(0.0, spindle_load + float(
                self.rng.normal(0.0, profile.spindle_noise * (1.0 + 2 * tool)))))
            axis_feed = max(0.0, axis_feed + float(
                self.rng.normal(0.0, profile.spindle_noise * 0.05)))
            flow = max(0.0, flow + float(self.rng.normal(0.0, profile.coolant_noise_lpm)))
            temp = self.temp_c + float(self.rng.normal(0.0, profile.temp_noise_c))
        else:
            temp = self.temp_c

        return telemetry.MachineReading(
            spindle_load=round(max(0.0, spindle_load), 6),
            vibration=round(max(0.0, vibration), 6),
            temp_c=round(temp, 6),
            coolant_flow_lpm=round(flow, 6),
            axis_feed=round(axis_feed, 6),
            tool_wear_index=self.tool_wear_index(),
            joint_friction_index=self.joint_friction_index(),
            joint_friction_index_arm1=self.wear_index("arm1"),
            joint_friction_index_arm2=self.wear_index("arm2"),
            surface_quality=self.surface_quality(),
            coolant_fault=bool(fault),
        )

    def snapshot(self) -> dict[str, Any]:
        """Sănătatea tuturor componentelor și indicii derivați, pentru `wear.jsonl`."""
        return {
            **{f"{name}_health": round(self.health_of(name), 6) for name in self.health},
            "tool_wear_index": self.tool_wear_index(),
            "joint_friction_index": self.joint_friction_index(),
            "surface_quality": self.surface_quality(),
            "belt_speed_scale": self.belt_speed_scale(),
            "temp_c": round(self.temp_c, 6),
        }

    def component_table(self) -> list[dict[str, Any]]:
        """Tabelul componentelor, pentru `session.json`: cu ce parametri s-a generat uzura."""
        return [{
            "name": item.name,
            "label": item.label,
            "kind": item.kind,
            "degraded_after_s": item.degraded_after_s,
            "exponent": item.exponent,
            "load": item.load,
            "load_sensitivity": item.load_sensitivity,
            "rate_per_s": round(item.rate() * self.rate, 12),
            "hazard_per_hour": item.hazard_per_hour,
            "hazard_gamma": item.hazard_gamma,
            "failure_kind": item.failure_kind,
            "failure_duration_s": item.failure_duration_s,
        } for item in COMPONENTS]

    # ------------------------------------------------------------ acțiuni de operator
    def inject(self, name: str, amount: float) -> float:
        """Adaugă la indicele de uzură al componentei; întoarce sănătatea de după."""
        self.health_of(name)
        self.health[name] = max(MIN_HEALTH, min(1.0, self.health[name] - float(amount)))
        if self.health[name] > DEGRADED_HEALTH:
            self.active.pop(name, None)
        return self.health[name]

    def repair(self, name: str) -> float:
        """Readuce componenta la sănătate deplină și stinge eșecul ei, dacă era în curs."""
        self.health_of(name)
        self.health[name] = 1.0
        self.active.pop(name, None)
        self.bursts.pop(name, None)
        return self.health[name]

    def set_rate(self, multiplier: float) -> float:
        """Compresia vieții mașinii: `1.0` este uzura nominală, `20` o termină de 20 de ori mai iute."""
        if multiplier <= 0:
            raise ValueError(f"multiplicatorul de uzură trebuie pozitiv: {multiplier}")
        self.rate = float(multiplier)
        return self.rate

    def burst(self, name: str, amount: float, duration_s: float) -> None:
        """Scade brusc sănătatea și ține rata ridicată cât timpul dat — scenariul de prins."""
        self.inject(name, amount)
        self.bursts[name] = self.now_s + float(duration_s)

    def _record(self, command: str, component: str, amount: float, detail: str = "") -> Action:
        action = Action(sim_time_s=self.now_s, command=command, component=component,
                        amount=float(amount), detail=detail)
        self.actions.append(action)
        return action

    def apply_action(self, command: str, name: str | None = None, amount: float | None = None,
                     value: float | None = None, duration_s: float | None = None) -> Action:
        """Aplică o comandă din vocabularul de control și o înregistrează ca etichetă.

        Numele din `cnc.control.v1.json` sunt cele care există deja acolo (`inject_tool_wear`,
        `inject_joint_friction`, `replace_tool`, `repair_joints`); restul sunt locale geamănului
        (`inject_wear`, `repair`, `inject_fault_burst`, `set_wear_rate`, `reset_wear`), fiindcă
        schema de control este închisă și un nume nou acolo cere bump de versiune.
        """
        if command == "inject_tool_wear":
            return self._record(command, "cnc_tool", amount or 0.25,
                                f"health {self.inject('cnc_tool', amount or 0.25):.3f}")
        if command == "inject_joint_friction":
            share = amount or 0.25
            for arm in ARM_COMPONENTS:
                self.inject(arm, share)
            return self._record(command, ",".join(ARM_COMPONENTS), share,
                                f"health {min(self.health[a] for a in ARM_COMPONENTS):.3f}")
        if command == "replace_tool":
            self.repair("cnc_tool")
            return self._record(command, "cnc_tool", 0.0, "new tool")
        if command == "repair_joints":
            for arm in ARM_COMPONENTS:
                self.repair(arm)
            return self._record(command, ",".join(ARM_COMPONENTS), 0.0, "joints repaired")
        if command == "inject_wear":
            if name is None:
                raise ValueError("inject_wear cere o componentă")
            health = self.inject(name, amount or 0.25)
            return self._record(command, name, amount or 0.25, f"health {health:.3f}")
        if command == "repair":
            if name is None:
                raise ValueError("repair cere o componentă")
            self.repair(name)
            return self._record(command, name, 0.0, "repaired")
        if command == "inject_fault_burst":
            if name is None:
                raise ValueError("inject_fault_burst cere o componentă")
            self.burst(name, amount or 0.25, duration_s if duration_s is not None else 300.0)
            return self._record(command, name, amount or 0.25,
                                f"burst {BURST_FACTOR:g}× for "
                                f"{duration_s if duration_s is not None else 300.0:g} s")
        if command == "set_wear_rate":
            multiplier = self.set_rate(value if value is not None else 1.0)
            return self._record(command, "*", multiplier, f"wear rate {multiplier:g}×")
        if command == "reset_wear":
            for component in COMPONENTS:
                self.repair(component.name)
            self.set_rate(1.0)
            return self._record(command, "*", 0.0, "wear and rate reset")
        raise KeyError(f"comandă necunoscută: {command}")

    def apply_actions(self, actions: Sequence[Mapping[str, Any]]) -> list[Action]:
        """Aplică o listă de comenzi, în ordine, ca la pornirea unei sesiuni."""
        applied: list[Action] = []
        for item in actions:
            applied.append(self.apply_action(
                str(item["command"]), name=item.get("component"),
                amount=item.get("amount"), value=item.get("value"),
                duration_s=item.get("duration_s")))
        return applied


def parse_injections(items: Sequence[str]) -> tuple[dict[str, Any], ...]:
    """Citește `--inject componentă:cât`, cum vine de pe linia de comandă.

    Forma este aceeași ca a acțiunilor de operator, doar scrisă scurt: fiecare intrare devine o
    comandă `inject_wear`, iar o componentă necunoscută se prinde la aplicare, cu lista numelor.
    """
    parsed: list[dict[str, Any]] = []
    for item in items:
        name, _, raw = item.partition(":")
        if not name:
            raise ValueError(f"injecție fără componentă: {item!r}")
        amount = float(raw) if raw else 0.25
        if not 0.0 < amount <= 1.0:
            raise ValueError(f"cantitatea injectată iese din (0, 1]: {item!r}")
        parsed.append({"command": "inject_wear", "component": name, "amount": amount})
    return tuple(parsed)


def component_names() -> tuple[str, ...]:
    """Numele componentelor, în ordinea din registru: pentru validări și pentru rapoarte."""
    return tuple(item.name for item in COMPONENTS)
