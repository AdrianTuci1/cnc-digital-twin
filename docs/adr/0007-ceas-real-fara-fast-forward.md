# ADR 0007: Ceas de perete în telemetrie, fără avansarea timpului

- **Status:** acceptat
- **Data:** 2026-10-05
- **Decis de:** echipa de proiect

## Context

Degradarea se vede greu în timp real: uzura sculei crește cu `CNCDT_TOOL_WEAR_RATE`
(0,0015 pe secundă), deci un index de avarie de 1,0 cere peste unsprezece minute de tăiere
continuă, iar frecarea articulațiilor (0,0008 pe secundă) cere peste douăzeci de minute de
mișcare. Pentru a colecta date de antrenare pe serii de timp într-o demonstrație, cineva
trebuie să poată parcurge un ciclu de viață într-un timp rezonabil.

Am luat în calcul două variante:

1. **Avansarea ceasului simulării** — un buton care mută timpul înainte (10 secunde reale să
   însemne 24 de ore), ca degradarea să avanseze rapid.
2. **Accelerarea procesului** — ceasul rămâne cel de perete, iar parametrul care se schimbă
   este rata de uzură.

## Decizie

1. **Telemetria folosește ceasul de perete.** `ts_ms` este `int(time.time() * 1000)` în
   `simulation/extensions/cncdt.cnc_cell`. Nu există niciun decalaj între timpul scris în
   date și timpul real.
2. **Nu se avansează timpul.** Nu se implementează un buton de fast-forward și nicio
   multiplicare a ceasului, nici în simulare, nici în pipeline.
3. **Accelerarea vieții se face prin rata de uzură**, parametru documentat al experimentului:
   `CNCDT_TOOL_WEAR_RATE` și `CNCDT_JOINT_FRICTION_RATE` din `.env`, plus săriturile
   instantanee din panou (**Uzură +0.1 / +0.25**, slider-ele **Uzură sculă** și **Frecare
   articulații**). Fiecare rulare cu rată modificată este un **test de viață accelerat**
   (accelerated life test) și se notează ca atare.
4. **Rata este publicată ca metrică și interogabilă.** Lambda-ul de feature engineering
   primește ratele prin `TOOL_WEAR_RATE` și `JOINT_FRICTION_RATE` și derivă `RULMinutes`, deci
   dashboard-ul arată minutele de operare până la prag, nu un număr de secunde de perete.

## Consecințe

- Timestampurile rămân coerente în toate locurile care le consumă: alarmele CloudWatch,
  partițiile `dt_year/dt_month/dt_day/dt_hour` ale Firehose, interogările Athena și
  caracteristicile calendaristice din antrenare văd același ceas.
- `PutMetricData` are o limită dură de două ore în viitor și două săptămâni în trecut. Un
  ceas avansat ar produce date respinse de CloudWatch sau, mai rău, arată corect și mincinos.
- Numerele din dashboard rămân conforme cu realitatea. Nu apare situația „500 de piese în 10
  minute" care ar rupe comparația între rulări.
- Compresia vieții costă doar răbdare: cu ratele implicite, o degradare completă se vede în
  câteva zeci de minute, iar cu rate ridicate în câteva minute. Rapiditatea se plătește prin
  faptul că lotul nu mai seamănă cu uzura naturală, de aceea rata se notează per rulare.
- Pragul de afișare „câte minute până la degradare" depinde de rată, nu de ceas; la rate
  modificate, minutele afișate sunt cele ale testului accelerat.

## Alternative respinse

| Alternativă | Motivul respingerii |
| --- | --- |
| Fast-forward la ceasul simulării | CloudWatch respinge metricele cu marcaj de timp peste două ore în viitor; partițiile orare din S3 și caracteristicile calendaristice ar deveni inconsistente, iar dashboard-ul ar arăta cifre neconforme. |
| Contor de „timp virtual" paralel cu cel real | Al doilea ceas trebuie ținut sincronizat în simulare, în Lambda, în Firehose și în Athena; orice loc uitat produce date imposibil de comparat între ele. |
| Multiplicarea ratei de eșantionare (10 Hz → 100 Hz) | Nu accelerează fizica, doar produce mai multe eșantioane identice; costul Firehose și Lambda crește fără să avanseze degradarea. |
