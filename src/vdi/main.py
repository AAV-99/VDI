#!/usr/bin/env python
from pathlib import Path
from pydantic import BaseModel
from crewai.flow import Flow, listen, start

from .crews.vdicrew.vdicrew import VdiCrew


class VdiState(BaseModel):
    system_type: str = "Efector Final (Gripper) para Manipulación de Láminas de Acero"
    project_scope: str = (
        "Diseño mecatrónico e integración de un efector final (gripper) liviano (< 1.5 kg) "
        "diseñado para acople directo a la brida ISO 9409-1-50-4-M6 del cobot Universal Robots UR5. "
        "El sistema tomará láminas planas de acero AISI/SAE 1020 de 250x250x2 mm (~0.98 kg) "
        "provenientes directamente de una estación de corte láser (superficie seca, con posible presencia "
        "de rebaba o temperatura leve) y las alimentará a una celda de doblado o soldadura. "
        "Se debe proponer y justificar el principio de sujeción óptimo (mecánico, magnético, vacío o híbrido)."
    )
    primary_requirements: str = (
        "Masa total del gripper <= 1.5 kg (para garantizar carga combinada <= 2.5 kg en el UR5). "
        "Tiempo de ciclo pick-and-place <= 4.0 s. "
        "Garantizar sujeción segura y repetible de las láminas considerando rebabas o imperfecciones de borde. "
        "Inclusión de sensado de verificación de agarre seguro ('pieza sujeta') antes de autorizar trayectoria del robot. "
        "Alimentación y control compatible con la interfaz del UR5 (Tool I/O 24V DC / señales digitales o armario). "
        "Cumplimiento de criterios de seguridad para robótica colaborativa bajo la norma ISO/TS 15066 "
        "(geometría libre de bordes cortantes o puntos de atrapamiento)."
    )
    final_design: str = ""


class VdiFlow(Flow[VdiState]):

    @start()
    def plan_design(self, crewai_trigger_payload: dict = None):
        print("Initializing VDI 2206 Design Cycle")

        if crewai_trigger_payload:
            self.state.system_type = crewai_trigger_payload.get("system_type", self.state.system_type)
            self.state.project_scope = crewai_trigger_payload.get("project_scope", self.state.project_scope)
            self.state.primary_requirements = crewai_trigger_payload.get("primary_requirements", self.state.primary_requirements)
            print(f"Using trigger payload: {crewai_trigger_payload}")

        print(f"Target System: {self.state.system_type}")

    @listen(plan_design)
    def generate_design(self):
        print(f"Running multi-agent design review for: {self.state.system_type}")
        
        # Diccionario con los 3 placeholders genéricos
        inputs = {
            "system_type": self.state.system_type,
            "project_scope": self.state.project_scope,
            "primary_requirements": self.state.primary_requirements,
        }
        
        result = (
            VdiCrew()
            .crew()
            .kickoff(inputs=inputs)
        )

        print("VDI 2206 Design review completed")
        self.state.final_design = result.raw

    @listen(generate_design)
    def save_design(self):
        print("Saving conceptual design dossier")
        output_dir = Path("output")
        output_dir.mkdir(exist_ok=True)
        with open(output_dir / "vdi2206_conceptual_design.md", "w", encoding="utf-8") as f:
            f.write(self.state.final_design)
        print("Dossier saved to output/vdi2206_conceptual_design.md")


def kickoff():
    vdi_flow = VdiFlow()
    vdi_flow.kickoff()


def plot():
    vdi_flow = VdiFlow()
    vdi_flow.plot()


def run_with_trigger():
    """
    Run the flow with trigger payload.
    """
    import json
    import sys

    if len(sys.argv) < 2:
        raise Exception("No trigger payload provided. Please provide JSON payload as argument.")

    try:
        trigger_payload = json.loads(sys.argv[1])
    except json.JSONDecodeError:
        raise Exception("Invalid JSON payload provided as argument")

    vdi_flow = VdiFlow()

    try:
        result = vdi_flow.kickoff({"crewai_trigger_payload": trigger_payload})
        return result
    except Exception as e:
        raise Exception(f"An error occurred while running the flow with trigger: {e}")


if __name__ == "__main__":
    kickoff()