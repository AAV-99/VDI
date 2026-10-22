# VDI 2206 — Sistema multiagente para diseño conceptual mecatrónico

Flujo de **CrewAI** que desarrolla el diseño conceptual de un sistema mecatrónico según la guía **VDI/VDE 2206**, con un modelo de lenguaje local servido por **Ollama**. Seis agentes (integración de sistemas, mecánica, electrónica, software, RAMS y cliente) desarrollan el diseño en tres partes: una pasada inicial (requerimientos o Fase 1, síntesis del sistema o Fase 2, diseño por dominio, evaluación RAMS y revisión del cliente), un ciclo de corrección y el dossier consolidado. En el ciclo, el líder de integración asigna cada solicitud de cambio (ECR) del cliente a la disciplina responsable, solo esas disciplinas corrigen su informe y el cliente verifica las correcciones; se repite hasta 3 veces o hasta que no queden ECR corregibles. Los agentes disponen de consulta a los manuales técnicos del robot (RAG), una calculadora y una matriz ponderada para el QFD y la evaluación de conceptos; el líder de integración y los ingenieros mecánico, electrónico y RAMS pueden además buscar en internet (benchmarking, selección de componentes con su hoja de datos, datos de confiabilidad y consultas técnicas; los precios son estimaciones).

## Requisitos del sistema

| Componente | Versión o capacidad |
|---|---|
| Python | 3.10 a 3.13, gestionado con `uv` |
| CrewAI | 1.15.17 (fijado en `uv.lock`) |
| Ollama | 0.32.12 o superior |
| Modelo | `qwen3.8:27b-q4_K_M` y su derivado `qwen3.8-vdi` (contexto de 131 072 tokens, definido en `Modelfile`) |
| GPU | Unos 26 GB de memoria para el modelo con el contexto completo (probado en una NVIDIA H100) |

El modelo derivado se crea una vez en el servidor de Ollama:

```bash
ollama create qwen3.8-vdi -f Modelfile
```

**Documentos del RAG.** Los manuales van en la carpeta `knowledge/` (PDF, TXT o MD) y no se publican en el repositorio por derechos de autor; se copian a mano. La búsqueda es híbrida: embeddings de `qwen3-embedding:0.6b`, servido por el mismo Ollama, combinados con BM25. El modelo de embeddings se descarga una vez:

```bash
ollama pull qwen3-embedding:0.6b
```

El índice se guarda en `.rag_db/` y se construye al empezar la corrida, solo cuando cambian los documentos, el modelo o la partición. Para probarlo sin lanzar la corrida, con Ollama en marcha:

```bash
uv run python -m vdi.crews.vdicrew.herramientas "allowable torque axis 6"
```

El comando lista los documentos, construye el índice si hace falta y muestra el resultado de la consulta de prueba.

## Cómo ejecutar

1. **Definir el caso de estudio** en `src/vdi/contexto.txt`. El archivo tiene cuatro secciones obligatorias (`## TIPO DE SISTEMA`, `## ALCANCE DEL PROYECTO`, `## REQUERIMIENTOS PRIMARIOS` y `## COSTO OBJETIVO`); las líneas de cada sección se unen en un párrafo y las viñetas `- ` se quitan. No debe contener llaves.

2. **Lanzar la corrida** con Ollama en marcha:

   ```bash
   bash correr.sh
   ```

   Equivale a `uv run kickoff`, pero guarda también el log completo en la carpeta de la corrida.

3. **Variables de entorno opcionales:**

   | Variable | Uso | Valor por defecto |
   |---|---|---|
   | `OLLAMA_HOST` | Dirección del servidor de Ollama | `localhost:11434` |
   | `VDI_REASONING_EFFORT` | Esfuerzo de razonamiento del modelo: `low`, `medium`, `high` o `xhigh` | El del modelo |
   | `VDI_CONTEXT_FILE` | Otro archivo de contexto (otro caso de estudio) | `src/vdi/contexto.txt` |
   | `VDI_RUN_DIR` | Carpeta de salida de la corrida | `output/<fecha_hora>` |
   | `VDI_CICLOS` | Máximo de iteraciones del ciclo de corrección; `0` lo apaga (flujo de las etapas anteriores) | `3` |
   | `VDI_HERRAMIENTAS` | `0` apaga el RAG y la calculadora (configuración de las etapas anteriores) | Activas |
   | `VDI_KNOWLEDGE_DIR` | Otra carpeta de documentos para el RAG | `knowledge/` |
   | `VDI_EMBEDDINGS` | Modelo de embeddings en Ollama para el RAG | `qwen3-embedding:0.6b` |
   | `TAVILY_API_KEY` | Clave de la API de búsqueda Tavily; también puede ir en un archivo `.env` en la raíz (ignorado por Git) | Sin clave no hay búsqueda web |
   | `VDI_BUSQUEDA_WEB` | `0` apaga la búsqueda web aunque haya clave | Activa si hay clave |

   Ejemplo: `VDI_REASONING_EFFORT=high VDI_CONTEXT_FILE=casos/banda.txt bash correr.sh`

Si una tarea falla (por ejemplo, el modelo responde vacío varias veces seguidas), la corrida se reanuda hasta 2 veces desde el primer paso sin informe, con los informes ya escritos como contexto.

En un clúster con Slurm, el script de trabajo debe levantar Ollama dentro del trabajo, exportar `OLLAMA_HOST` y `VDI_RUN_DIR`, y ejecutar `uv run kickoff`.

## Configuración del flujo

| Archivo | Contenido |
|---|---|
| `src/vdi/contexto.txt` | Caso de estudio: sistema, alcance, requerimientos y costo |
| `src/vdi/crews/vdicrew/config/agents.yaml` | Rol, objetivo y trasfondo de cada agente |
| `src/vdi/crews/vdicrew/config/tasks.yaml` | Instrucciones y entregable de cada tarea |
| `src/vdi/crews/vdicrew/config/metodologia.md` | Contexto metodológico común (VDI 2206 y herramientas de diseño conceptual) que reciben todos los agentes |
| `src/vdi/crews/vdicrew/config/herramientas.md` | Reglas de uso de las herramientas que reciben los agentes cuando están activas |
| `src/vdi/crews/vdicrew/config/busqueda_web.md` | Reglas de la búsqueda web (para qué se usa, citas con URL, qué vale como dato) |
| `src/vdi/crews/vdicrew/herramientas.py` | RAG de los manuales, calculadora, matriz ponderada y topes de uso por tarea |
| `src/vdi/ciclo.py` | Orquestación de la corrida: pasada inicial, ciclo de corrección e integración final |
| `src/vdi/crews/vdicrew/vdicrew.py` | Modelo y muestreo, límites de tokens y palabras, y guardrail de informes completos |

## Salidas

Cada corrida queda en su propia carpeta dentro de `output/` (ignorada por Git):

| Archivo | Contenido |
|---|---|
| `01_requirements_and_system.md` | Fase 1: necesidades, requerimientos, especificaciones y QFD etapa 1 |
| `01b_system_synthesis.md` | Fase 2 a nivel de sistema: matriz morfológica, conceptos, evaluación, arquitectura, QFD etapa 2 y diseño axiomático |
| `02_mechanical_subsystem.md` | Diseño del dominio mecánico y BOM mecánico |
| `03_electronic_subsystem.md` | Diseño del dominio eléctrico y electrónico, análisis de integración y BOM electrónico |
| `04_software_architecture.md` | Arquitectura de software o firmware, sin código |
| `05_rams_maintenance.md` | Evaluación RAMS y estrategia PHM |
| `06_client_acceptance.md` | Matriz de conformidad, solicitudes de cambio (ECR) y dictamen del cliente |
| `07_triaje_itN.md` | Triaje de la iteración N: disciplina y tipo de cada ECR abierta |
| `02_..._vN.md`, `03_..._vN.md`, … | Versión N de un informe de dominio corregido en el ciclo, con su tabla de cambios al inicio |
| `06_client_acceptance_vN.md` | Verificación del cliente al final de la iteración N−1: estado de cada ECR y ECR nuevas |
| `_iteraciones.json` | Historial del ciclo: ECR asignadas, disciplinas que corrigieron, ECR cerradas, fallidas, nuevas y escaladas, y tokens por iteración |
| `00_vdi2206_consolidated_dossier.md` | Dossier consolidado con decisiones de integración y cierre de ECR |
| `corrida.json` | Commit, configuración del modelo, entradas, parámetros y tiempos de la corrida; si una tarea falló, las reanudaciones (tarea y motivo) |
| `_guardrail.log` | Resultado de la verificación de completitud de cada informe |
| `_llm.log` | Una línea por respuesta del modelo: tokens, tiempo, motivo de fin y herramientas pedidas |
| `_herramientas.log` | Cada consulta al RAG y cada cálculo, con la tarea que lo pidió y el inicio del resultado |
| `corrida.log` | Log completo de CrewAI |
