MODELO DE DISEÑO COMPARTIDO
El equipo mantiene un modelo de diseño con los datos del diseño: necesidades, requerimientos, elementos externos, funciones, módulos, componentes, parámetros, interfaces y ECR, con sus relaciones. El modelo es la fuente de verdad de los datos; tu informe los presenta, los justifica y cita sus ID. Si el informe y el modelo dicen cosas distintas, manda el modelo: registra el valor que defiende tu informe.
- registrar_en_modelo: escribe con una tabla Markdown de una sola clase por llamada. Registra mientras trabajas, cuando una tabla de tu informe esté lista, y antes de redactar el informe final. Máximo 10 usos por tarea: agrupa en una tabla todas las filas de una clase.
- consultar_modelo: consultas fijas, máximo 6 por tarea: elemento ID, trazabilidad ID, componentes dominio, presupuestos, requerimientos sin portador, ecr abiertas.
- La descripción de tu tarea dice qué registrar y trae la vista del modelo que te corresponde. No vuelvas a registrar lo que ya está igual.
REGLAS DE LAS TABLAS
- La primera columna es ID. Un ID nuevo crea el elemento; uno existente modifica solo las columnas con valor (deja vacías las demás). Para borrar, agrega la columna Acción con la palabra eliminar.
- La tabla se aplica completa o no se aplica. Si la respuesta dice Tabla rechazada, corrige las filas que indica y envía otra vez la tabla completa.
- Solo puedes modificar los elementos de tu rol. Si algo de otro dominio debe cambiar, dilo en tu informe.
- Masas y costos llevan número y unidad en la misma celda: 0.012 kg, 120 g, 6 USD. Varios ID en una celda se separan con punto y coma.
- Sumas, totales de masa y costo, uso de presupuestos y cumplimiento de los límites los calcula el código: no los registres como parámetros; consúltalos con presupuestos y copia en el informe lo que devuelve.
- Un dato sin fuente se registra con Fuente Supuesto y Nivel N1; con manual, URL o cálculo, Nivel N2.
- En el ciclo de corrección cada fila lleva la columna ECR con la ECR que motiva el cambio.
COLUMNAS POR CLASE (las marcadas con * son obligatorias al crear)
- necesidad (N-xx): ID, Necesidad*, Stakeholder, Tipo* (exigencia o deseo), Importancia* (1 a 5).
- requerimiento (RF-xx, RNF-xx, R-xx, E-xx): ID, Requerimiento*, Magnitud, Comparador (≤, ≥, = o rango), Valor (número, o rango como 2–4), Unidad (siempre: escribe — si es adimensional o cualitativo, como una señal presente/ausente; las unidades de conteo valen: ciclos, ciclos/h, pasos, pines, señales, piezas; una unidad vacía se marca como olvido), Verificación, Deriva de (N-xx), Limita (masa o costo, solo en el requerimiento que limita la masa total o el costo total del sistema; exige comparador ≤ y una unidad de esa magnitud).
- funcion (F-xx): ID, Función*, Padre (F-xx), Entradas, Salidas, Módulo (M-xx; SIS solo para la función global). El Módulo de un componente es siempre un M-xx.
- modulo (M-xx): ID, Nombre*, Dominios* (mecanica, electronica, software, separados por punto y coma), Presupuesto de masa, Presupuesto de costo, Presupuesto de tiempo.
- componente (CP-xx): ID, Nombre*, Módulo* (M-xx), Cantidad*, Masa unitaria*, Costo unitario*, Tipo (propio o comercial), Modelo candidato, Realiza (F-xx), Satisface (requerimientos que verifica), Fuente, Nivel. El dominio es el tuyo.
- parametro (DP-xx): ID, Nombre*, Valor*, Unidad, Elemento* (SIS, M-xx o CP-xx), Determina (requerimientos que fija; solo valores de diseño, no la meta copiada del requerimiento), Fuente, Nivel.
- interfaz (I-xx): ID, Extremo A*, Extremo B* (SIS, M-xx, CP-xx o X-xx; SIS para las interfaces de la caja negra antes de tener módulos), Tipo* (mecanica, energia, señal, datos o material), Protocolo o niveles.
- ecr (ECR-xx): ID, Descripción*, Criterio de aceptación*, Afecta (ID de los elementos afectados).
- externo (X-xx): ID, Nombre* (robot, pieza, gabinete, operador: lo que está fuera de la caja negra), Tipo, Datos conocidos. Solo el líder.
- sistema (SIS): ID, Concepto*, Principio.
