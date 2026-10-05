# Hardware de Control/Potencia y BOM Electrónico

> **Ejemplo de prueba** — contenido ilustrativo, no generado por los agentes reales.

## Arquitectura

Eyector de vacío con válvula **normalmente abierta al vacío** y válvula antirretorno: ante corte eléctrico el vacío se conserva (R-05). Un vacuostato digital por circuito reporta al PLC.

## Señales

| Señal | Tipo | Dirección | Función |
|---|---|---|---|
| DO-1 | 24 V | PLC → gripper | Cortar vacío |
| DO-2 | 24 V | PLC → gripper | Soplado de liberación |
| DI-1 | 24 V PNP | gripper → PLC | Vacío OK circuito A |
| DI-2 | 24 V PNP | gripper → PLC | Vacío OK circuito B |
| DI-3 | 24 V PNP | gripper → PLC | Pieza presente (inductivo) |

## Consumo

Dos válvulas (0,1 A c/u) + dos vacuostatos (0,04 A c/u) + sensor inductivo (0,02 A) = **0,30 A**, frente a 2 A disponibles.

## BOM electrónico

| Ítem | Descripción | Cant. | Masa (kg) |
|---|---|---|---|
| E-01 | Eyector de vacío con válvulas integradas | 2 | 0,5 |
| E-02 | Vacuostato digital PNP | 2 | 0,1 |
| E-03 | Sensor inductivo M12 | 1 | 0,05 |
| E-04 | Caja de conexiones M12 de 8 puertos | 1 | 0,25 |
| | **Total electrónico** | | **0,9** |
