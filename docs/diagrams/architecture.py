"""Diagramele de arhitectură, generate din cod cu biblioteca `diagrams`.

Proiectul are două suprafețe de citit, ca și dashboard-urile CloudWatch (ADR 0008), așa că
scriptul randează **două** diagrame:

1. `architecture-loop` — bucla de date și control: geamănul MuJoCo produce telemetrie și cadre
   QA, AWS le transformă în decizii, verdictul se întoarce ca acțiune fizică în simulare.
2. `architecture-platform` — platforma: ciclul de viață al modelelor, operarea și guvernanța
   (Terraform, rol de operator, backup).

Ambele se citesc stânga→dreapta, pe coloane: fiecare etapă își are coloana. Bucla are patru
coloane — simularea și ingestia, coloana din mijloc (ML route deasupra, Live route la mijloc,
Observabilitate dedesubt, toate trei una sub alta) și comanda, cu utilizatorul la capătul
notificării. O diagramă produce o singură imagine.

Rulare (cere `graphviz` instalat și pachetul `diagrams`):

    pip install diagrams
    python docs/diagrams/architecture.py            # ambele diagrame
    python docs/diagrams/architecture.py loop       # doar bucla

Rezultatul este scris lângă acest fișier: câte un `.png` și câte o sursă Graphviz `.gv`.
Identificatorii din cod și etichetele din diagramă păstrează engleza, fiindcă imaginile se
citesc din `README.md`; comentariile rămân în română, ca restul codului. Detalii despre
componente: `docs/01-architecture.md`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from diagrams import Cluster, Diagram, Edge, Node, getcluster, getdiagram
from diagrams.aws.analytics import Athena, KinesisDataFirehose
from diagrams.aws.compute import Lambda
from diagrams.aws.integration import SNS
from diagrams.aws.iot import IotCore
from diagrams.aws.management import Cloudwatch, CloudwatchAlarm
from diagrams.aws.ml import (
    Sagemaker,
    SagemakerModel,
    SagemakerNotebook,
    SagemakerTrainingJob,
)
from diagrams.aws.security import IAM
from diagrams.aws.storage import EFS, S3, Backup
from diagrams.onprem.client import Client, User
from diagrams.onprem.iac import Terraform

HERE = Path(__file__).parent


def graph_attr() -> dict[str, str]:
    """Aerisirea nodurilor: spațiu între coloane și între rândurile paralele.

    `pad` ține titlul grafului departe de eticheta ultimului nod, iar `ranksep` desparte
    coloanele atât de mult cât să încapă etichetele late. `newrank` lasă un `rank=same` să
    treacă dincolo de marginile grupurilor, fără să dizolve chenarele: așa stau ruta ML, ruta
    live și observabilitatea una sub alta, pe aceleași două coloane.
    """
    return {
        "splines": "spline",
        "fontsize": "13",
        "pad": "0.5",
        "nodesep": "0.9",
        "ranksep": "1.4",
        "newrank": "true",
    }


def group(name: str) -> Cluster:
    """Cluster cu margine interioară, ca chenarul să nu taie etichetele nodurilor."""
    return Cluster(name, graph_attr={"margin": "20"})


def same_rank(*nodes: Node) -> None:
    """Ține nodurile pe același rang, adică pe aceeași coloană, fiindcă fluxul curge la
    dreapta.

    La rădăcină instrucțiunea are nevoie de `newrank` ca să nu dizolve chenarele grupurilor,
    de aceea perechile din `build_data_loop` se scriu după ce toate grupurile s-au închis.
    Id-urile sunt hexazecimale, deci unele încep cu cifră și au nevoie de ghilimele.
    """
    target = getcluster() or getdiagram()
    ids = "; ".join(f'"{node.nodeid}"' for node in nodes)
    # Elementele corpului grafului se lipesc fără separator, deci linia are nevoie de `\n`.
    target.dot.body.append(f"{{rank=same; {ids}}}\n")


def stem_for(name: str) -> str:
    """Numele fișierelor: o diagramă, o imagine."""
    return f"architecture-{name}"


def build_data_loop(name: str) -> str:
    """Bucla de date și control, pe coloane.

    Coloanele sunt etapele: simularea și ingestia (geamănul MuJoCo → IoT Core → Firehose),
    coloana din mijloc — ML route deasupra, Live route la mijloc, Observabilitate dedesubt —
    și comanda, cu utilizatorul în afara grupurilor, la capătul notificării SNS.

    Ruta ML stă pe două rânduri: pregătirea offline (S3 brut → Data Wrangler) și cele două
    modele care rulează pe endpoint-uri. Ruta live, observabilitatea și comanda stau fiecare
    pe câte un singur rând.

    Fluxul: Firehose scrie brutul în S3 și trimite fereastra Lambda-ului de transformare.
    Lambda-ul trimite datele modelului și primește verdictul înapoi (o singură muchie, cu vârf
    la ambele capete), iar tot Lambda-ul scrie metricele în CloudWatch. Nodurile au nume
    scurte, iar muchiile nu poartă etichete. Întoarce sursa Graphviz.
    """
    with Diagram(
        "CNC Digital Twin — data and control loop",
        filename=str(HERE / stem_for(name)),
        outformat="png",
        show=False,
        direction="LR",
        graph_attr=graph_attr(),
    ) as diagram:
        # Geamănul rulează local, pe mașina operatorului: viewer-ul MuJoCo și panoul de uzură.
        with group("Simulation and ingestion"):
            cell = Client("MuJoCo twin\nlocal viewer + panel")
            iot = IotCore("IoT Core")
            firehose = KinesisDataFirehose("Firehose")
            cell >> iot >> firehose

        # Sunt două pictograme de model, deși endpoint-ul este unul singur, ca să se vadă ce
        # modele deservește. Data Wrangler nu are pictogramă proprie nici în bibliotecă, nici
        # în pachetul oficial AWS, deci folosește pictograma de bază de date (Athena), doar cu
        # numele schimbat. Rândurile acestui grup se așază la final, împreună cu cele ale
        # rutei live și ale observabilității.
        with group("ML route"):
            lake = S3("S3 raw")
            wrangler = Athena("Data Wrangler")
            model_anomalies = Sagemaker("ML1 — anomalies")
            model_vision = Sagemaker("ML2 — vision")

        with group("Live route"):
            features = Lambda("Feature engineering")
            vision = Lambda("Vision QA")

        # Doar Lambda-urile scriu metrice, iar SNS notifică mai departe.
        with group("Observability"):
            metrics = Cloudwatch("CloudWatch")
            notify = SNS("SNS")

        # Comanda pe un singur rând, cu utilizatorul în afara grupului, la capătul notificării
        # SNS.
        with group("Command"):
            dispatcher = Lambda("Dispatcher")
        user = User("Operator")

        # ------------------------------------------------ fluxul, într-un singur sens
        # Firehose scrie brutul în S3 și invocă Lambda-ul de transformare.
        firehose >> lake
        firehose >> features

        # Ruta ML se citește fără săgeți între S3 raw, Data Wrangler și cele două modele:
        # ce se întâmplă pe fiecare pas stă în `docs/01-architecture.md`. Muchiile de mai jos
        # sunt invizibile, dar țin nodurile în aceeași grilă și pe aceleași coloane.
        lake >> Edge(style="invis") >> wrangler
        wrangler >> Edge(style="invis") >> model_anomalies
        wrangler >> Edge(style="invis") >> model_vision

        # Lambda-ul trimite datele modelului și primește verdictul înapoi: o singură muchie,
        # cu vârf la ambele capete (`reverse=True` peste `>>`, care pune `forward`).
        model_anomalies >> Edge(reverse=True) >> features
        model_vision >> Edge(reverse=True) >> vision

        # ------------------------------------------------ observabilitate și comandă
        features >> metrics
        vision >> metrics
        metrics >> notify
        # SNS notifică utilizatorul, în afara grupurilor, și trimite comanda mai departe.
        notify >> user
        notify >> dispatcher
        dispatcher >> Edge(color="firebrick", style="bold") >> cell

        # ------------------------------------------------ rândurile coloanei din mijloc
        # Ruta ML, ruta live și observabilitatea stau fiecare pe câte un rând, una sub alta,
        # pe aceleași două coloane: perechile de mai jos ajung pe aceeași coloană, iar
        # Graphviz aliniază restul. Comanda și utilizatorul ies în coloana următoare.
        same_rank(lake, model_anomalies, features, metrics)
        same_rank(wrangler, model_vision, vision, notify)
        source = diagram.dot.source
    return source


def build_platform(name: str) -> str:
    """Platforma: ciclul de viață al modelelor, operarea și guvernanța.

    Fluxul principal stă pe un rând de etape (date → pregătire → antrenare → model →
    endpoint), iar operarea și guvernanța sunt ramuri laterale: singurele grupate, fiindcă
    altfel ar atârna fără legătură lângă flux. Întoarce sursa Graphviz.
    """
    with Diagram(
        "CNC Digital Twin — platform: models, operations, governance",
        filename=str(HERE / stem_for(name)),
        outformat="png",
        show=False,
        direction="LR",
        graph_attr=graph_attr(),
    ) as diagram:
        lake = S3("S3 data lake\nParquet partitioned by hour")
        athena = Athena("Athena\nad-hoc queries")
        studio = SagemakerNotebook("Studio + Data Wrangler\nrecipe generated from code")
        studio_home = EFS("EFS Studio home\nsurvives destroy")
        settings_backup = Backup("AWS Backup\ndaily EFS plan")
        training = SagemakerTrainingJob(
            "Training\nmetrics in Experiments\nML1 anomalies · ML2 vision"
        )
        model_store = S3("S3 models\nmodels/<name>/<version>/")
        # Un singur nod ține ambele endpoint-uri, ca promovarea să rămână o singură muchie.
        endpoint = Sagemaker("SageMaker endpoints\nML1 anomalies + ML2 vision\nserverless")

        with group("Operations (`observability`, ADR 0005 and ADR 0008)"):
            metrics = Cloudwatch("CloudWatch\ninfra + factory dashboards")
            alarms = CloudwatchAlarm("Alarms\ndegradation · RUL · pipeline")
            notify = SNS("SNS\nemail + SMS")

        with group("Governance and identity (ADR 0001 and ADR 0006)"):
            operator = IAM("Operator role\nassumed from admin, no static keys")
            terraform = Terraform("Terraform\nstacks: access · platform · training-data")

        # ------------------------------------------------ ciclul de viață al modelului
        lake >> Edge(label="offline features") >> studio
        lake >> Edge(label="exploratory queries") >> athena
        studio >> Edge(label="sliding windows\n+ labels") >> training
        training >> Edge(label="model.tar.gz") >> model_store
        model_store >> Edge(label="promotion\n(both models)", style="dashed") >> endpoint
        studio >> studio_home
        studio_home >> Edge(label="backup plan") >> settings_backup

        # ------------------------------------------------ operare
        endpoint >> Edge(label="invocation metrics") >> metrics
        metrics >> alarms
        alarms >> Edge(label="notification") >> notify

        # ------------------------------------------------ guvernanță și securitate
        operator >> Edge(label="runs") >> terraform
        terraform >> Edge(style="dashed", label="provisions") >> studio
        source = diagram.dot.source
    return source


BUILDERS = {"loop": build_data_loop, "platform": build_platform}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="architecture.py",
        description="Generează diagramele de arhitectură din `docs/diagrams/`.",
    )
    parser.add_argument(
        "diagram",
        nargs="*",
        choices=[*BUILDERS],
        default=[],
        help="ce diagramă se randează (implicit: toate)",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv[1:])
    names = args.diagram or list(BUILDERS)
    for name in names:
        stem = stem_for(name)
        source = BUILDERS[name](name)
        # Biblioteca șterge sursa `.gv` la ieșire; o păstrăm, ca să se poată depana layout-ul.
        (HERE / f"{stem}.gv").write_text(source, encoding="utf-8")
        print(f"Diagramă scrisă: {HERE / stem}.png (+ .gv)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
