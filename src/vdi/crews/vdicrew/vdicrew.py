import os
from crewai import Agent, Crew, Process, Task, LLM
from crewai.project import CrewBase, agent, crew, task

@CrewBase
class VdiCrew:
    """VDI 2206 Mechatronic Design Review Crew"""

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    # Configuración del LLM con control de tokens
    llm = LLM(
        model="gemini/gemini-3.6-flash", # O la versión habilitada en tu API
        temperature=0.2,
        max_tokens=4000,                  # Límite máximo de tokens por respuesta del agente
    )

    # --- AGENTES VDI 2206 ---
    @agent
    def team_lead(self) -> Agent:
        return Agent(config=self.agents_config["team_lead"], llm=self.llm, verbose=True)

    @agent
    def mecanico(self) -> Agent:
        return Agent(config=self.agents_config["mecanico"], llm=self.llm, verbose=True)

    @agent
    def electronico(self) -> Agent:
        return Agent(config=self.agents_config["electronico"], llm=self.llm, verbose=True)

    @agent
    def software(self) -> Agent:
        return Agent(config=self.agents_config["software"], llm=self.llm, verbose=True)

    @agent
    def mantenimiento(self) -> Agent:
        return Agent(config=self.agents_config["mantenimiento"], llm=self.llm, verbose=True)

    @agent
    def cliente(self) -> Agent:
        return Agent(config=self.agents_config["cliente"], llm=self.llm, verbose=True)

    # --- TAREAS CON SALIDA A ARCHIVOS INDIVIDUALES ---
    @task
    def requirements_ingestion_task(self) -> Task:
        return Task(
            config=self.tasks_config["requirements_ingestion_task"],
            output_file="output/01_requirements_and_system.md"
        )

    @task
    def mechanical_subsystem_design_task(self) -> Task:
        return Task(
            config=self.tasks_config["mechanical_subsystem_design_task"],
            output_file="output/02_mechanical_subsystem.md"
        )

    @task
    def electronic_subsystem_design_task(self) -> Task:
        return Task(
            config=self.tasks_config["electronic_subsystem_design_task"],
            output_file="output/03_electronic_subsystem.md"
        )

    @task
    def software_subsystem_design_task(self) -> Task:
        return Task(
            config=self.tasks_config["software_subsystem_design_task"],
            output_file="output/04_software_architecture.md"
        )

    @task
    def maintenance_rams_eval_task(self) -> Task:
        return Task(
            config=self.tasks_config["maintenance_rams_eval_task"],
            output_file="output/05_rams_maintenance.md"
        )

    @task
    def client_acceptance_review_task(self) -> Task:
        return Task(
            config=self.tasks_config["client_acceptance_review_task"],
            output_file="output/06_client_acceptance.md"
        )

    @task
    def design_board_integration_task(self) -> Task:
        return Task(
            config=self.tasks_config["design_board_integration_task"],
            output_file="output/00_vdi2206_consolidated_dossier.md"
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )