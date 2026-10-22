CONTEXTO METODOLÓGICO COMÚN DEL EQUIPO (VDI/VDE 2206:2021 y diseño conceptual)

MARCO VDI/VDE 2206. La guía VDI/VDE 2206:2021 organiza el desarrollo de sistemas mecatrónicos y ciberfísicos en un modelo en V. A partir de los requisitos se diseña el sistema: su arquitectura y la partición de funciones entre los dominios mecánico, eléctrico/electrónico y de software. Luego cada dominio desarrolla su parte en paralelo sobre esa arquitectura, y al final las partes se integran en el sistema. Durante todo el proceso se aseguran las propiedades del sistema mediante verificación (el diseño cumple la especificación) y validación (el diseño satisface la necesidad del usuario), apoyadas en modelos y análisis. Este proyecto cubre la fase conceptual: Fase 1, Información (necesidades y requisitos), y Fase 2, Síntesis de diseño (concepto, arquitectura y dimensionamiento preliminar).

REGLAS COMUNES PARA TODOS LOS INFORMES
1. Trazabilidad. Usa estos identificadores y mantenlos en todos los informes: N-xx necesidades, RF-xx requerimientos funcionales, RNF-xx requerimientos no funcionales, R-xx restricciones, E-xx especificaciones, F-xx funciones y subfunciones, DP-xx variables o parámetros de diseño, C-xx conceptos. Toda decisión de diseño debe citar los identificadores de los requisitos que atiende.
2. Cuantificación. Cada requerimiento y especificación lleva valor objetivo, unidad del SI, límite o tolerancia y método de verificación (cálculo, simulación, inspección o ensayo).
3. Cálculos explícitos. Presenta la fórmula, los valores con unidades, el resultado y el factor de seguridad cuando aplique. Distingue los datos (del enunciado o de un informe anterior) de los supuestos (valores que asumes tú), y marca cada supuesto como Supuesto.
4. Honestidad técnica. No afirmes análisis, simulaciones, ensayos, cotizaciones ni consultas a catálogos que no se hayan hecho. Puedes proponer componentes comerciales típicos, pero sus datos son estimados hasta verificar la hoja de datos, y debes marcarlos así.
5. Sin fuentes externas. El equipo no tiene acceso a internet ni a catálogos en esta fase: no hagas benchmarking ni estado del arte con datos de productos concretos; si es necesario, decláralo como actividad pendiente. Las reglas de uso de herramientas pueden indicar excepciones a esta regla.
6. Coherencia de totales. Toda tabla con totales (BOM, masa, costo, tiempos, puntajes) debe sumar exactamente sus líneas.
7. Plausibilidad física. Verifica que masas y dimensiones sean físicamente posibles: la masa de una pieza maciza es su volumen por la densidad del material (por ejemplo, aluminio 2700 kg/m3, acero 7850 kg/m3). Si una pieza se aligera, indica cómo (vaciados, perfiles, espesores) y recalcula su masa.
8. Sin ajustes a conveniencia. No cambies precios, masas ni tiempos solo para que un total quepa en un límite. Todo cambio necesita una razón técnica (otro componente, otro material, otra secuencia). Si un total no cabe con valores justificados, decláralo como No cumple y propone qué cambio de diseño lo resolvería.

HERRAMIENTAS DE DISEÑO Y FORMATO ESPERADO (cada tarea indica cuáles debe aplicar)
- Caja negra: el sistema como una función global con sus entradas y salidas de materia, energía y señal. Diagrama Mermaid.
- Estructura funcional: descomposición de la función global en subfunciones F-xx conectadas por flujos de materia, energía y señal. Diagrama Mermaid y tabla de subfunciones.
- Descomposición funcional con atributos y portadores de función: tabla con subfunción F-xx, atributo medible y portador de función (módulo o componente que la realiza).
- Necesidades de stakeholders: identifica los stakeholders (usuario, operador, mantenimiento, cliente, seguridad, fabricación) y sus necesidades N-xx en lenguaje del usuario.
- Clasificación de necesidades: obligatorias (exigencias que el diseño debe cumplir) o deseables (deseos que suman valor), con importancia de 1 a 5.
- Matriz de necesidades: necesidades frente a stakeholders, indicando quién origina cada necesidad.
- QFD etapa 1 (necesidades frente a requerimientos): filas con las necesidades y su importancia; columnas con los requerimientos; relación fuerte 9, media 3, débil 1. Importancia técnica de cada requerimiento igual a la suma de importancia por relación. Presenta la tabla y el cálculo.
- Matriz morfológica: filas con las subfunciones; columnas con al menos tres principios de solución por subfunción, de distintos dominios físicos cuando sea posible.
- Ruta factible: combinaciones de principios compatibles entre sí. Genera al menos tres conceptos C-xx diferentes y explica por qué se descartan las combinaciones incompatibles.
- Evaluación de conceptos: criterios derivados de los requerimientos, con pesos tomados de la importancia técnica del QFD que sumen 100 %, escala de 1 a 5 y tabla con el puntaje ponderado de cada concepto y su cálculo. Selecciona el de mayor puntaje o justifica explícitamente otra elección.
- Arquitectura del producto: módulos del sistema, asignación de cada subfunción a un módulo y a un dominio (mecánico, eléctrico/electrónico o software), e interfaces entre módulos (mecánicas, de energía, de señal y de datos).
- Análisis de integración: para cada interfaz entre dominios, tipo de señal o protocolo, niveles (tensión, corriente, frecuencia o tasa), compatibilidad entre los lados y prueba de compatibilidad propuesta para verificarla.
- QFD etapa 2 (requerimientos frente a variables de diseño): relación de cada RF y RNF con los parámetros de diseño DP-xx que lo determinan.
- Diseño axiomático: matriz de diseño de requerimientos funcionales frente a parámetros de diseño. Diagonal significa diseño desacoplado; triangular, semiacoplado (indica el orden de ajuste); llena, acoplado (señálalo y propone cómo desacoplar).
- Dimensionamiento de componentes: cálculo de los componentes críticos de cada dominio con sus cargas, márgenes y factores de seguridad.
