# Arquitectura Conceptual de Software/Firmware

> **Ejemplo de prueba** — contenido ilustrativo, no generado por los agentes reales.

## Máquina de estados

1. **REPOSO** — vacío cortado, esperando orden.
2. **APROXIMACIÓN** — robot sobre la pieza, DI-3 confirma presencia.
3. **SUJECIÓN** — se activa el vacío; se espera DI-1 y DI-2 (tiempo límite 1,5 s).
4. **TRANSPORTE** — vigilancia continua de vacío.
5. **DEPÓSITO** — posición confirmada, corte de vacío y soplado de 0,3 s.
6. **FALLA** — robot detenido, vacío mantenido, alarma al PLC.

## Reglas de seguridad

- Pérdida de DI-1 o DI-2 en TRANSPORTE → FALLA en el mismo ciclo de PLC (10 ms, cumple R-06).
- La liberación solo se habilita con la posición de depósito confirmada.
- Tres fallos de sujeción consecutivos → paro y llamado a mantenimiento.

## Presupuesto de tiempo de ciclo

| Fase | Tiempo (s) |
|---|---|
| Aproximación | 2,5 |
| Sujeción | 1,0 |
| Transporte | 5,0 |
| Depósito y retirada | 2,0 |
| **Total** | **10,5** (requerido <= 12) |
