# Expediente Consolidado VDI 2206

> **Ejemplo de prueba** — contenido ilustrativo, no generado por los agentes reales.

**Sistema:** Efector final (gripper) de vacío para manipulación de láminas de acero estampadas

**Dictamen:** Aprobado con observaciones — masa 5,2 kg, factor de seguridad 5,7, ciclo 10,5 s.

---

## Matriz de Requerimientos e Integración VDI 2206


### Requerimientos

| ID | Requerimiento | Valor | Disciplina | Verificación |
|---|---|---|---|---|
| R-01 | Masa de la pieza | <= 8 kg | Mecánica | Pesaje |
| R-02 | Masa del gripper | <= 6 kg | Mecánica | Pesaje |
| R-03 | Tiempo de ciclo | <= 12 s | Software | Cronometraje |
| R-04 | Factor de seguridad de sujeción | >= 2 a 5 m/s² | Mecánica | Cálculo y prueba |
| R-05 | Retención sin energía | >= 60 s | Electrónica | Prueba funcional |
| R-06 | Detección de pérdida de vacío | < 100 ms | Electrónica / Software | Osciloscopio |
| R-07 | Sin marcas en la lámina | Visual | Mecánica | Inspección |

### Interfaces del sistema

- **Mecánica:** brida ISO 9409-1-100-6-M8 hacia el robot.
- **Neumática:** una manguera de 8 mm a 6 bar.
- **Eléctrica:** 24 V DC, 2 A máximo en muñeca; señales digitales hacia el PLC.

### Presupuesto de carga

Pieza (8 kg) + gripper (<= 6 kg) = 14 kg, frente a 20 kg de carga útil del robot: margen de 6 kg (30 %).

---

## Concepto Mecánico y BOM Preliminar


### Concepto

Marco en perfil de aluminio con cuatro ventosas de fuelle de Ø60 mm en nitrilo (resistente al aceite de estampado), montadas sobre compensadores de resorte.

### Cálculo de sujeción

- Área por ventosa: π · (0,03 m)² = 0,00283 m²
- Fuerza por ventosa a −0,6 bar: 60 000 Pa · 0,00283 m² ≈ 170 N
- Fuerza total (4 ventosas): ≈ 679 N
- Carga con aceleración: 8 kg · (9,81 + 5) m/s² ≈ 118 N
- **Factor de seguridad: 679 / 118 ≈ 5,7** (requerido: >= 2)

Si falla una ventosa, las tres restantes dan ≈ 509 N, factor ≈ 4,3.

### BOM preliminar

| Ítem | Descripción | Cant. | Masa (kg) |
|---|---|---|---|
| M-01 | Marco de perfil de aluminio 30×30 | 1 | 2,1 |
| M-02 | Placa adaptadora ISO 9409-1-100 | 1 | 0,9 |
| M-03 | Ventosa de fuelle Ø60 mm NBR | 4 | 0,3 |
| M-04 | Compensador de resorte | 4 | 0,6 |
| M-05 | Tornillería y soportes | — | 0,4 |
| | **Total mecánico** | | **4,3** |

---

## Hardware de Control/Potencia y BOM Electrónico


### Arquitectura

Eyector de vacío con válvula **normalmente abierta al vacío** y válvula antirretorno: ante corte eléctrico el vacío se conserva (R-05). Un vacuostato digital por circuito reporta al PLC.

### Señales

| Señal | Tipo | Dirección | Función |
|---|---|---|---|
| DO-1 | 24 V | PLC → gripper | Cortar vacío |
| DO-2 | 24 V | PLC → gripper | Soplado de liberación |
| DI-1 | 24 V PNP | gripper → PLC | Vacío OK circuito A |
| DI-2 | 24 V PNP | gripper → PLC | Vacío OK circuito B |
| DI-3 | 24 V PNP | gripper → PLC | Pieza presente (inductivo) |

### Consumo

Dos válvulas (0,1 A c/u) + dos vacuostatos (0,04 A c/u) + sensor inductivo (0,02 A) = **0,30 A**, frente a 2 A disponibles.

### BOM electrónico

| Ítem | Descripción | Cant. | Masa (kg) |
|---|---|---|---|
| E-01 | Eyector de vacío con válvulas integradas | 2 | 0,5 |
| E-02 | Vacuostato digital PNP | 2 | 0,1 |
| E-03 | Sensor inductivo M12 | 1 | 0,05 |
| E-04 | Caja de conexiones M12 de 8 puertos | 1 | 0,25 |
| | **Total electrónico** | | **0,9** |

---

## Arquitectura Conceptual de Software/Firmware


### Máquina de estados

1. **REPOSO** — vacío cortado, esperando orden.
2. **APROXIMACIÓN** — robot sobre la pieza, DI-3 confirma presencia.
3. **SUJECIÓN** — se activa el vacío; se espera DI-1 y DI-2 (tiempo límite 1,5 s).
4. **TRANSPORTE** — vigilancia continua de vacío.
5. **DEPÓSITO** — posición confirmada, corte de vacío y soplado de 0,3 s.
6. **FALLA** — robot detenido, vacío mantenido, alarma al PLC.

### Reglas de seguridad

- Pérdida de DI-1 o DI-2 en TRANSPORTE → FALLA en el mismo ciclo de PLC (10 ms, cumple R-06).
- La liberación solo se habilita con la posición de depósito confirmada.
- Tres fallos de sujeción consecutivos → paro y llamado a mantenimiento.

### Presupuesto de tiempo de ciclo

| Fase | Tiempo (s) |
|---|---|
| Aproximación | 2,5 |
| Sujeción | 1,0 |
| Transporte | 5,0 |
| Depósito y retirada | 2,0 |
| **Total** | **10,5** (requerido <= 12) |

---

## Evaluación RAMS y Diagnóstico PHM


### Modos de falla principales

| Componente | Modo de falla | Efecto | Detección | Acción |
|---|---|---|---|---|
| Ventosa | Desgaste o grieta del labio | Fuga, vacío lento | Tiempo de sujeción creciente | Reemplazo por condición |
| Eyector | Obstrucción por aceite | Vacío insuficiente | Vacuostato no conmuta | Limpieza semanal del filtro |
| Manguera | Rozamiento o enganche | Pérdida de vacío | DI-1 / DI-2 | Guiado y revisión visual |
| Vacuostato | Deriva del umbral | Falsas alarmas | Comparación entre circuitos | Calibración trimestral |

### Indicador de salud (PHM)

Registrar el **tiempo de sujeción** de cada ciclo. Un aumento sostenido sobre la línea base indica fuga y permite programar el cambio de ventosas antes de la falla.

### Mantenimiento

- **Semanal (ventana de 30 min):** limpieza de filtros, inspección de ventosas y mangueras.
- **Trimestral:** calibración de vacuostatos y prueba de retención sin energía.
- Cambio de ventosa con acople rápido: objetivo < 5 min (CA-04).

---

## Dictamen de Calidad y Criterios de Aceptación


### Evaluación frente a criterios del cliente

| ID | Criterio | Estado | Observación |
|---|---|---|---|
| CA-01 | 300 ciclos sin caída | Pendiente de prueba | Factor de seguridad calculado 5,7 |
| CA-02 | Retención >= 60 s sin energía | Cumple por diseño | Válvula normalmente abierta + antirretorno |
| CA-03 | Sin marcas en la lámina | Riesgo | Validar ventosa NBR sobre lámina aceitada |
| CA-04 | Cambio de ventosa < 5 min | Cumple por diseño | Acople rápido |

### Verificación de masa

Mecánico 4,3 kg + electrónico 0,9 kg = **5,2 kg** (requerido <= 6 kg).

### Dictamen

**Aprobado con observaciones.** El concepto cumple los requerimientos primarios en cálculo. Antes de liberar a fabricación se debe:

1. Ejecutar la prueba de 300 ciclos (CA-01).
2. Validar la ausencia de marcas con lámina real aceitada (CA-03).
3. Confirmar el costo de materiales frente al presupuesto de 4.500 USD (no evaluado en esta revisión).

---
