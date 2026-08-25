import os
from crewai import Agent, Crew, Process, Task, LLM
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

@CrewBase
class VdiCrew:
    """VDI 2206 Mechatronic Design Review Crew"""

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    llm = "gemini/gemini-3.6-flash"

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

    # --- TAREAS VDI 2206 ---
    @task
    def requirements_ingestion_task(self) -> Task:
        return Task(
            config=self.tasks_config["requirements_ingestion_task"],  # type: ignore[index]
        )

    @task
    def mechanical_subsystem_design_task(self) -> Task:
        return Task(
            config=self.tasks_config["mechanical_subsystem_design_task"],  # type: ignore[index]
        )

    @task
    def electronic_subsystem_design_task(self) -> Task:
        return Task(
            config=self.tasks_config["electronic_subsystem_design_task"],  # type: ignore[index]
        )

    @task
    def software_subsystem_design_task(self) -> Task:
        return Task(
            config=self.tasks_config["software_subsystem_design_task"],  # type: ignore[index]
        )

    @task
    def maintenance_rams_eval_task(self) -> Task:
        return Task(
            config=self.tasks_config["maintenance_rams_eval_task"],  # type: ignore[index]
        )

    @task
    def client_acceptance_review_task(self) -> Task:
        return Task(
            config=self.tasks_config["client_acceptance_review_task"],  # type: ignore[index]
        )

    @task
    def design_board_integration_task(self) -> Task:
        return Task(
            config=self.tasks_config["design_board_integration_task"],  # type: ignore[index]
        )

    # --- TRIPULACIÓN / PROCESO ---
    @crew
    def crew(self) -> Crew:
        """Creates the VDI 2206 Design Crew"""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )