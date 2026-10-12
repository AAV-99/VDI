# Concepto Mecánico y BOM Preliminar

> **Ejemplo de prueba** — contenido ilustrativo, no generado por los agentes reales.

## Concepto

Marco en perfil de aluminio con cuatro ventosas de fuelle de Ø60 mm en nitrilo (resistente al aceite de estampado), montadas sobre compensadores de resorte.

## Cálculo de sujeción

- Área por ventosa: π · (0,03 m)² = 0,00283 m²
- Fuerza por ventosa a −0,6 bar: 60 000 Pa · 0,00283 m² ≈ 170 N
- Fuerza total (4 ventosas): ≈ 679 N
- Carga con aceleración: 8 kg · (9,81 + 5) m/s² ≈ 118 N
- **Factor de seguridad: 679 / 118 ≈ 5,7** (requerido: >= 2)

Si falla una ventosa, las tres restantes dan ≈ 509 N, factor ≈ 4,3.

## BOM preliminar

| Ítem | Descripción | Cant. | Masa (kg) |
|---|---|---|---|
| M-01 | Marco de perfil de aluminio 30×30 | 1 | 2,1 |
| M-02 | Placa adaptadora ISO 9409-1-100 | 1 | 0,9 |
| M-03 | Ventosa de fuelle Ø60 mm NBR | 4 | 0,3 |
| M-04 | Compensador de resorte | 4 | 0,6 |
| M-05 | Tornillería y soportes | — | 0,4 |
| | **Total mecánico** | | **4,3** |
