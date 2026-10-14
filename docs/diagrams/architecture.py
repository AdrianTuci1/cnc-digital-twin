"""Diagramele de arhitectură, generate din cod cu biblioteca `diagrams`.

Proiectul are două suprafețe de citit, ca și dashboard-urile CloudWatch (ADR 0008), așa că
scriptul randează **două** diagrame:

1. `architecture-loop` — bucla de date și control: simularea produce telemetrie și cadre QA,
   AWS le transformă în decizii, verdictul se întoarce ca acțiune fizică în simulare.
2. `architecture-platform` — platforma: ciclul de viață al modelelor, operarea și guvernanța
   (Terraform, rol de operator, backup).

Ambele se citesc stânga→dreapta, pe coloane: fiecare etapă își are coloana. Bucla are cinci
coloane — clientul, VPN-ul, pipeline-ul, coloana din mijloc (ML route deasupra, Live route la
mijloc, Observabilitate dedesubt, toate trei una sub alta) și comanda, cu utilizatorul la
capătul notificării. O diagramă produce o singură imagine.

Rulare (cere `graphviz` instalat și pachetul `diagrams`):

    pip install diagrams
    python docs/diagrams/architecture.py            # ambele diagrame
    python docs/diagrams/architecture.py loop       # doar bucla

Rezultatul este scris lângă acest fișier: câte un `.png` și câte o sursă Graphviz `.gv`.
Identificatorii din cod păstrează engleza; etichele din diagramă sunt în română, ca restul
documentației. Detalii despre componente: `docs/01-architecture.md`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from diagrams import Cluster, Diagram, Edge, Node, getcluster, getdiagram
from diagrams.aws.analytics import Athena, KinesisDataFirehose
from diagrams.aws.compute import EC2, Lambda
from diagrams.aws.integration import SNS
from diagrams.aws.iot import IotCore
from diagrams.aws.management import Cloudwatch, CloudwatchAlarm
from diagrams.aws.ml import (
    Sagemaker,
    SagemakerModel,
    SagemakerNotebook,
    SagemakerTrainingJob,
)
from diagrams.aws.network import ClientVpn
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

    Coloanele sunt etapele: clientul WebRTC, nodul AWS VPN, pipeline-ul (EC2-ul de simulare →
    IoT Core → Firehose), coloana din mijloc — ML route deasupra, Live route la mijloc,
    Observabilitate dedesubt — și comanda, cu utilizatorul în afara grupurilor, la capătul
    notificării SNS.

    Ruta ML stă pe două rânduri: pregătirea offline (S3 brut → Data Wrangler) și cele două
    modele ale endpoint-ului. Ruta live, observabilitatea și comanda stau fiecare pe câte un
    singur rând.

    Fluxul: Firehose scrie brutul în S3 și trimite fereastra Lambda-ului de transformare.
    Lambda-ul trimite datele modelului și primește verdictul înapoi (o singură muchie, cu vârf
    la ambele capete), iar tot Lambda-ul scrie metricele în CloudWatch. Nodurile au nume
    scurte, iar muchiile nu poartă etichete. Întoarce sursa Graphviz.
    """
    with Diagram(
        "CNC Digital Twin — bucla de date și control",
        filename=str(HERE / stem_for(name)),
        outformat="png",
        show=False,
        direction="LR",
        graph_attr=graph_attr(),
    ) as diagram:
        browser = Client("WebRTC")
        # Utilizatorul ține legătura cu instanța prin Client VPN.
        vpn = ClientVpn("AWS VPN")

        # Containerul de simulare poartă pictograma EC2, fiindcă acolo rulează instanța GPU.
        with group("Pipeline"):
            cell = EC2("Isaac Sim")
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
            model_anomalies = Sagemaker("ML1 — anomalii")
            model_vision = Sagemaker("ML2 — viziune")

        with group("Live route"):
            features = Lambda("Transformare")
            vision = Lambda("Viziune")

        # Doar Lambda-urile scriu metrice, iar SNS notifică mai departe.
        with group("Observabilitate"):
            metrics = Cloudwatch("CloudWatch")
            notify = SNS("SNS")

        # Comanda pe un singur rând, cu utilizatorul în afara grupului, la capătul notificării
        # SNS.
        with group("Comandă"):
            dispatcher = Lambda("Dispatcher")
            cell_end = EC2("Isaac Sim")
        user = User("Utilizator")

        # ------------------------------------------------ fluxul, într-un singur sens
        browser >> vpn >> cell

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
        dispatcher >> Edge(color="firebrick", style="bold") >> cell_end

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
        "CNC Digital Twin — platforma: modele, operare, guvernanță",
        filename=str(HERE / stem_for(name)),
        outformat="png",
        show=False,
        direction="LR",
        graph_attr=graph_attr(),
    ) as diagram:
        lake = S3("S3 data lake\nParquet partiționat pe oră")
        athena = Athena("Athena\ninterogări ad-hoc")
        studio = SagemakerNotebook("Studio + Data Wrangler\nrețetă generată din cod")
        studio_home = EFS("EFS home Studio\npersistă la destroy")
        settings_backup = Backup("AWS Backup\nplan zilnic EFS")
        training = SagemakerTrainingJob(
            "Antrenare\nmetrice în Experiments\nML1 anomalii · ML2 viziune"
        )
        model_store = S3("S3 modele\nmodels/<nume>/<versiune>/")
        # Un singur endpoint deservesc ambele modele, deci și promovarea e o singură muchie.
        endpoint = Sagemaker("SageMaker endpoint\nML1 anomalii + ML2 viziune\ncu data capture")

        with group("Operare (`observability`, ADR 0005 și ADR 0008)"):
            metrics = Cloudwatch("CloudWatch\ndashboard infra + fabrică")
            alarms = CloudwatchAlarm("Alarme\ndegradare · RUL · pipeline")
            notify = SNS("SNS\nemail + SMS")

        with group("Guvernanță și identitate (ADR 0001 și ADR 0006)"):
            operator = IAM("Rol de operator\nasumat din admin, fără chei statice")
            terraform = Terraform("Terraform\nstacks: access · sim · platform")

        # ------------------------------------------------ ciclul de viață al modelului
        lake >> Edge(label="caracteristici offline") >> studio
        lake >> Edge(label="interogări exploratorii") >> athena
        studio >> Edge(label="ferestre glisante\n+ etichete") >> training
        training >> Edge(label="model.tar.gz") >> model_store
        model_store >> Edge(label="promovare\n(ambele modele)", style="dashed") >> endpoint
        studio >> studio_home
        studio_home >> Edge(label="plan de backup") >> settings_backup

        # ------------------------------------------------ operare
        endpoint >> Edge(label="metrice de invocare") >> metrics
        metrics >> alarms
        alarms >> Edge(label="notificare") >> notify

        # ------------------------------------------------ guvernanță și securitate
        operator >> Edge(label="rulează") >> terraform
        terraform >> Edge(style="dashed", label="provisionează") >> studio
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
