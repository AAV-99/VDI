# Matriz de Requerimientos e Integración VDI 2206

> **Ejemplo de prueba** — contenido ilustrativo, no generado por los agentes reales.

## Requerimientos

| ID | Requerimiento | Valor | Disciplina | Verificación |
|---|---|---|---|---|
| R-01 | Masa de la pieza | <= 8 kg | Mecánica | Pesaje |
| R-02 | Masa del gripper | <= 6 kg | Mecánica | Pesaje |
| R-03 | Tiempo de ciclo | <= 12 s | Software | Cronometraje |
| R-04 | Factor de seguridad de sujeción | >= 2 a 5 m/s² | Mecánica | Cálculo y prueba |
| R-05 | Retención sin energía | >= 60 s | Electrónica | Prueba funcional |
| R-06 | Detección de pérdida de vacío | < 100 ms | Electrónica / Software | Osciloscopio |
| R-07 | Sin marcas en la lámina | Visual | Mecánica | Inspección |

## Interfaces del sistema

- **Mecánica:** brida ISO 9409-1-100-6-M8 hacia el robot.
- **Neumática:** una manguera de 8 mm a 6 bar.
- **Eléctrica:** 24 V DC, 2 A máximo en muñeca; señales digitales hacia el PLC.

## Presupuesto de carga

Pieza (8 kg) + gripper (<= 6 kg) = 14 kg, frente a 20 kg de carga útil del robot: margen de 6 kg (30 %).
