#!/usr/bin/env python
from pathlib import Path
from pydantic import BaseModel
from crewai.flow import Flow, listen, start

from .crews.vdicrew.vdicrew import VdiCrew


class VdiState(BaseModel):
    system_type: str = ""
    final_design: str = ""


class VdiFlow(Flow[VdiState]):

    @start()
    def plan_design(self, crewai_trigger_payload: dict = None):
        print("Initializing VDI 2206 Design Cycle")

        if crewai_trigger_payload:
            self.state.system_type = crewai_trigger_payload.get("system_type", "Plastic Extrusion Assembly Cell")
            print(f"Using trigger payload: {crewai_trigger_payload}")
        else:
            self.state.system_type = "Plastic Extrusion Assembly Cell"

        print(f"Target System: {self.state.system_type}")

    @listen(plan_design)
    def generate_design(self):
        print(f"Running multi-agent design review for: {self.state.system_type}")
        result = (
            VdiCrew()
            .crew()
            .kickoff(inputs={"system_type": self.state.system_type})
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