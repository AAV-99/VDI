# VDI 2206 — Framework Multi-Agente para Revisión de Diseño Mecatrónico

Framework basado en **CrewAI Flows** y modelos de lenguaje **Google Gemini** para automatizar el ciclo de diseño conceptual mecatrónico bajo la norma **VDI 2206**. El sistema ejecuta una tripulación de 6 agentes especializados por disciplina —Integración, Mecánica, Electrónica, Software, Mantenimiento y Validación del Cliente— para generar reportes individuales por disciplina y un dossier técnico consolidado.

---

## 📑 Tabla de Contenidos

- [Requisitos Previos](#requisitos-previos)
- [Guía Paso a Paso (Windows)](#-guía-paso-a-paso)
- [Archivos de Salida](#-archivos-de-salida-output)
- [Visualización de Entregables](#-visualización-de-entregables-markdown-en-vs-code)

---

## Requisitos Previos

- Sistema operativo Windows
- Cuenta de Google (para la API key de Gemini)
- Conexión a internet para descargar dependencias

---

## 📋 Guía Paso a Paso:

Sigue esta secuencia de pasos para configurar el entorno desde cero en un sistema Windows recién formateado o sin herramientas previas.

### 1. Instalar Software Base

- **Git for Windows:** necesario para clonar el proyecto. Descarga e instala desde la [Guía de Instalación Oficial de Git](https://git-scm.com/download/win).
- **Visual Studio Code:** editor de código recomendado. Descarga e instala desde la [Guía de Instalación Oficial de VS Code](https://code.visualstudio.com/docs/setup/windows).

### 2. Obtener API Key de Google Gemini

1. Accede a [Google AI Studio](https://aistudio.google.com/) e inicia sesión con tu cuenta de Google.
2. Genera una nueva clave en la sección **Get API Key**. Consulta la [Documentación Oficial de Google AI Studio](https://ai.google.dev/gemini-api/docs/api-key) para más detalles sobre cuotas y permisos.

### 3. Instalar `uv` (Administrador de Entornos y Python)

**uv** se encarga de descargar Python y gestionar dependencias automáticamente.

1. Abre **PowerShell** en tu equipo.
2. Ejecuta el comando de instalación oficial (consulta la [Guía de Instalación Oficial de uv](https://docs.astral.sh/uv/getting-started/installation/)):

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

> ⚠️ **Importante:** una vez terminada la instalación, **cierra y vuelve a abrir PowerShell** para que los comandos queden registrados en las variables de sistema.

### 4. Clonar el Repositorio y Abrir en VS Code

1. En PowerShell, navega a la carpeta donde guardarás tus proyectos (por ejemplo, el Escritorio):

   ```powershell
   cd ~\Desktop
   ```

2. Clona el repositorio:

   ```powershell
   git clone <URL_DE_TU_REPOSITORIO>
   ```

3. Abre VS Code y, desde el menú **Archivo > Abrir carpeta...** (`File > Open Folder...`), selecciona la carpeta del proyecto clonado (`vdi`).

### 5. Sincronizar el Entorno Local y Crear la API Key

1. Abre la terminal integrada de VS Code (`Ctrl + ~` o menú **Terminal > Nueva Terminal**).
2. Ejecuta el siguiente comando para crear el entorno virtual e instalar CrewAI y todas las librerías necesarias definidas en `pyproject.toml` (más información en la [Guía de Inicio Rápido de CrewAI](https://docs.crewai.com/installation)):

   ```powershell
   uv sync
   ```

3. En la raíz del proyecto (junto al archivo `pyproject.toml`), crea un nuevo archivo llamado `.env`.
4. Agrega tu clave de API de Gemini dentro del archivo `.env`:

   ```env
   GEMINI_API_KEY="tu_api_key_aqui"
   ```

### 6. Modificar Parámetros del Código (Caso de Estudio)

Abre el archivo `src/vdi/main.py` y ajusta los parámetros de tu proyecto en la clase `VdiState` según tus necesidades:

```python
class VdiState(BaseModel):
    system_type: str = "Efector Final (Gripper) para Manipulación de Láminas de Acero"
    project_scope: str = (
        "Diseño mecatrónico e integración de un efector final (gripper) liviano (< 1.5 kg) "
        "diseñado para acople directo a la brida ISO 9409-1-50-4-M6 del cobot Universal Robots UR5. "
        "El sistema tomará láminas planas de acero AISI/SAE 1020 de 250x250x2 mm (~0.98 kg) "
        "provenientes directamente de una estación de corte láser y las alimentará a una celda de doblado o soldadura."
    )
    primary_requirements: str = (
        "Masa total del gripper <= 1.5 kg. Tiempo de ciclo pick-and-place <= 4.0 s. "
        "Inclusión de sensado de verificación de agarre seguro ('pieza sujeta'). "
        "Alimentación y control compatible con la Tool I/O del UR5 (24V DC). "
        "Cumplimiento de seguridad colaborativa bajo la norma ISO/TS 15066."
    )
```

### 7. Ejecutar

En la terminal integrada de VS Code, inicia la ejecución del flujo de agentes:

```powershell
uvx crewai run
```

*(También puedes ejecutarlo con `uv run kickoff`).*

---

## 📁 Archivos de Salida (`/output`)

Toda la documentación generada por los agentes se guarda de forma independiente en la carpeta `output/` (excluida del control de versiones mediante `.gitignore`):

| Archivo | Contenido |
|---|---|
| `output/01_requirements_and_system.md` | Matriz de Requerimientos e Integración VDI 2206 |
| `output/02_mechanical_subsystem.md` | Concepto Mecánico/Mecanismos + **BOM Preliminar de Materiales** |
| `output/03_electronic_subsystem.md` | Hardware de Control/Potencia + **BOM Electrónico** |
| `output/04_software_architecture.md` | Arquitectura Conceptual de Software/Firmware **sin código** |
| `output/05_rams_maintenance.md` | Evaluación de Mantenibilidad RAMS y Diagnóstico PHM |
| `output/06_client_acceptance.md` | Dictamen de Calidad y Criterios de Aceptación del Cliente |
| `output/00_vdi2206_consolidated_dossier.md` | **Expediente Final Consolidado VDI 2206** |

---

## 📊 Visualización de Entregables Markdown en VS Code

Para leer y revisar los informes formateados dentro de VS Code:

1. Abre cualquier archivo dentro de la carpeta `output/`.
2. Presiona la combinación de teclas **`Ctrl + K`** y luego **`V`** (o haz clic en el botón de vista previa dividida en la esquina superior derecha).