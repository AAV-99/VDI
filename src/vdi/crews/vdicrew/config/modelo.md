MODELO DE DISEÑO COMPARTIDO
El equipo mantiene un modelo de diseño con los datos del diseño: necesidades, requerimientos, elementos externos, funciones, módulos, componentes, parámetros, interfaces, decisiones, modos de falla y ECR, con sus relaciones, y la evidencia que los respalda. El modelo es la fuente de verdad de los datos; tu informe los presenta, los justifica y cita sus ID. Si el informe y el modelo dicen cosas distintas, manda el modelo: registra el valor que defiende tu informe.
- registrar_en_modelo: escribe con una tabla Markdown de una sola clase por llamada. Registra mientras trabajas, cuando una tabla de tu informe esté lista, y antes de redactar el informe final. Máximo 10 usos por tarea: agrupa en una tabla todas las filas de una clase.
- consultar_modelo: consultas fijas, máximo 6 por tarea: elemento ID, trazabilidad ID, componentes dominio, presupuestos, requerimientos sin portador, ecr abiertas, decisiones, modos de falla.
- La descripción de tu tarea dice qué registrar y trae la vista del modelo que te corresponde. No vuelvas a registrar lo que ya está igual.
REGLAS DE LAS TABLAS
- La primera columna es ID. Un ID nuevo crea el elemento; uno existente modifica solo las columnas con valor (deja vacías las demás). Para borrar, agrega la columna Acción con la palabra eliminar.
- La tabla se aplica completa o no se aplica. Si la respuesta dice Tabla rechazada, corrige las filas que indica y envía otra vez la tabla completa.
- Solo puedes modificar los elementos de tu rol. Si algo de otro dominio debe cambiar, dilo en tu informe.
- Masas y costos llevan número y unidad en la misma celda: 0.012 kg, 120 g, 6 USD. Varios ID en una celda se separan con punto y coma.
- Sumas, totales de masa y costo, uso de presupuestos, cumplimiento de los límites y RPN los calcula el código: no los registres como parámetros ni los sumes a mano; consúltalos con presupuestos.
- Un dato sin fuente se registra con Fuente Supuesto y Nivel N1; con manual, URL o cálculo, Nivel N2.
- En el ciclo de corrección cada fila lleva la columna ECR con la ECR que motiva el cambio.
COLUMNAS POR CLASE (las marcadas con * son obligatorias al crear)
- necesidad (N-xx): ID, Necesidad*, Stakeholder, Tipo* (exigencia o deseo), Importancia* (1 a 5).
- requerimiento (RF-xx, RNF-xx, R-xx, E-xx): ID, Requerimiento*, Magnitud, Comparador (≤, ≥, = o rango), Valor (número, o rango como 2–4), Unidad (siempre: escribe — si es adimensional o cualitativo, como una señal presente/ausente; las unidades de conteo valen: ciclos, ciclos/h, pasos, pines, señales, piezas; una unidad vacía se marca como olvido), Verificación, Deriva de (N-xx), Limita (masa o costo, solo en el requerimiento que limita la masa total o el costo total del sistema; exige comparador ≤ y una unidad de esa magnitud).
- funcion (F-xx): ID, Función*, Padre (F-xx), Entradas, Salidas, Módulo (M-xx; SIS solo para la función global). El Módulo de un componente es siempre un M-xx.
- modulo (M-xx): ID, Nombre*, Dominios* (mecanica, electronica, software, separados por punto y coma), Presupuesto de masa, Presupuesto de costo, Presupuesto de tiempo.
- componente (CP-xx): ID, Nombre*, Módulo* (M-xx), Cantidad*, Masa unitaria*, Costo unitario*, Tipo (propio o comercial), Modelo candidato, Realiza (F-xx), Satisface (requerimientos que verifica), Fuente, Nivel. El dominio es el tuyo.
- parametro (DP-xx): ID, Nombre*, Valor*, Unidad, Elemento* (SIS, M-xx o CP-xx), Mide (el requerimiento cuyo valor alcanzado ES este parámetro, por ejemplo la fuerza de sujeción calculada frente a la fuerza exigida: el código lo compara con la meta; un parámetro por requerimiento), Contribuye a (requerimientos a los que aporta sin ser su medida), Fuente, Nivel. Solo valores de diseño: no registres la meta copiada del requerimiento.
- interfaz (I-xx): ID, Extremo A*, Extremo B* (SIS, M-xx, CP-xx o X-xx; SIS para las interfaces de la caja negra antes de tener módulos), Tipo* (mecanica, energia, señal, datos o material), Protocolo o niveles.
- ecr (ECR-xx): ID, Descripción*, Criterio de aceptación*, Afecta (ID de los elementos afectados).
- externo (X-xx): ID, Nombre* (robot, pieza, gabinete, operador: lo que está fuera de la caja negra), Tipo, Datos conocidos. Solo el líder.
- sistema (SIS): ID, Concepto*, Principio.
- decision (D-xx): ID, Asunto* (la pregunta de diseño), Alternativas* (al menos dos, incluida la elegida, separadas por punto y coma), Criterios* (requerimientos que pesaron), Elegida*, Justificación* (por qué, en una o dos frases), Evidencia (CAL-xx, FT-xx, MAT-xx o DP-xx que la respaldan), Justifica* (SIS, M-xx, F-xx, CP-xx, DP-xx o I-xx que fija), Motivada por (ECR o requerimiento), Reemplaza (la D-xx que deja sin efecto). La registra el líder, o la disciplina dueña de todo lo que justifica. Para cambiar una elección no modifiques la decisión: registra una nueva con Reemplaza; la anterior queda como reemplazada. El sistema, cada módulo y cada componente propio deben quedar justificados por una decisión vigente.
- modo_falla (MF-xx): ID, Elemento* (SIS, M-xx o CP-xx), Modo*, Efecto*, Causa, S*, O*, D* (severidad, ocurrencia y detección, enteros de 1 a 10), Acción recomendada. Solo RAMS.
EVIDENCIA
- La calculadora devuelve un CAL-xx por línea, buscar_manuales_crp y buscar_web un FT-xx por fragmento o resultado, y matriz_ponderada un MAT-xx. El código los guarda con su contenido.
- Para usar un resultado, cita su ID en la columna Fuente del parámetro o del componente, o en Evidencia de la decisión. Un CAL o una FT que nadie cita no entra al modelo.
- Cita una FT solo si su extracto contiene el dato: el código comprueba que el valor del parámetro aparezca en el extracto de la fuente citada.
