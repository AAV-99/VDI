# INFORME DE INGENIERÍA DE SISTEMAS E INTEGRACIÓN MECATRÓNICA (VDI 2206)

**Proyecto:** Nodo IoT de Monitoreo de Condición para Motores Eléctricos en Línea de Transportadores  
**Documento Técnico:** Arquitectura de Sistema, Fronteras y Matriz de Requerimientos Inicial  
**Fecha:** 12-Oct-2026  
**Responsable:** Lead System & Mechatronics Integration Engineer  

---

## 1. INFORMACIÓN GENERAL Y ANÁLISIS DE LA MISIÓN

### 1.1 Contexto Operativo
El sistema consiste en un nodo inteligente autónomo adherido a motores trifásicos (5 kW a 30 kW) para predecir fallas mecánicas y térmicas sin recurrir a rutas manuales mensuales. La operación continua demanda procesamiento local (edge computing) de parámetros dinámicos para detectar anomalías según la norma ISO 10816-3 y reportar alertas críticas de manera determinista, mitigando las pérdidas de paquetes debidas a la cobertura Wi-Fi irregular de 2.4 GHz en planta.

### 1.2 Objetivo Técnico-Económico
- **Costo Objetivo de Materiales (BOM):** $\le 120\text{ USD}$ por nodo para lote piloto de 50 unidades.
- **Ciclo de Servicio:** Operación ininterrumpida $\ge 12\text{ meses}$ (8760 h) con batería primaria integrada, reemplazable exclusivamente en paradas de planta programadas.
- **Resiliencia Ambiental:** Aislamiento total frente a polvo metálico conductor y salpicaduras de emulsión refrigerante/aceite.

---

## 2. LÍMITES Y FRONTERAS DEL SISTEMA (METODOLOGÍA VDI 2206)

De acuerdo con la metodología VDI 2206, se definen las fronteras del sistema mecatrónico separando el producto a desarrollar del entorno físico, la infraestructura de planta y los sistemas superiores de gestión.

### 2.1 Diagrama de Fronteras e Interfaces del Sistema

```mermaid
flowchart TD
    subgraph ENTORNO_EXTERNO["Entorno Operativo de Planta"]
        Motor["Motor Eléctrico Trifásico (5-30 kW)<br/>- Vibración mecánica<br/>- Disipación térmica superficial"]
        Ambiente["Condiciones Ambientales<br/>- Polvo metálico<br/>- Salpicaduras refrigerante<br/>- Temp: -10 °C a 60 °C"]
        AP_WiFi["Punto de Acceso Wi-Fi (2.4 GHz)<br/>- Cobertura irregular"]
        ServidorPlanta["Servidor / Broker MQTT Planta<br/>- Base de datos / SCADA"]
        Tecnico["Personal de Mantenimiento<br/>- Reemplazo anual de batería"]
    end

    subgraph SISTEMA_NODO["Límite del Sistema: Nodo IoT VDI 2206"]
        subgraph MECANICA["Subsistema Mecánico y Térmico"]
            Fijacion["Acoplamiento Mecánico<br/>(Base magnética Neodimio N52 / Adhesivo)"]
            Carcasa["Carcasa IP66 / Blindaje<br/>(Aluminio fundido / Polímero técnico)"]
            AcopleTermico["Pad / Interfaz Térmica Conductora"]
        end

        subgraph ELECTRONICA["Subsistema Sensado y Potencia"]
            SensVib["Acelerómetro MEMS Triaxial<br/>(Digital, BW >= 1 kHz, +/-16 g)"]
            SensTemp["Sensor Temperatura Superficial<br/>(RTD / Sensor Digital Contacto)"]
            PowerSub["Fuente Autónoma de Energía<br/>(Pack Li-SOCl2 / PMIC Low-Iq)"]
        end

        subgraph PROCESAMIENTO["Subsistema Firmware y Edge Computing"]
            MCU["Microcontrolador Dual-Core / Ultra-Low Power<br/>(Cálculo FFT / RMS / ISO 10816-3)"]
            Transceiver["Radio Wi-Fi 802.11 b/g/n + Buffer Flash<br/>(Almacenamiento Store & Forward)"]
        end

        subgraph SOFTWARE_NIVEL_1["Dashboard de Alertas (Nivel 1)"]
            UI_Alertas["Interfaz Básica de Alertas Locales/Dashboard"]
        end
    end

    Motor -->|Vibración triaxial| Fijacion
    Fijacion -->|Transmisión dinámica| Carcasa
    Carcasa -->|Acople mecánico rígido| SensVib

    Motor -->|Conducción térmica| AcopleTermico
    AcopleTermico -->|Flujo de calor| SensTemp

    Ambiente -.->|Polvo / Humedad| Carcasa
    Tecnico -->|Mantenimiento anual batería| PowerSub

    PowerSub -->|Energía eléctrica regulada| SensVib
    PowerSub -->|Energía eléctrica regulada| SensTemp
    PowerSub -->|Energía eléctrica regulada| MCU
    PowerSub -->|Energía eléctrica regulada| Transceiver

    SensVib -->|I2C / SPI (Datos brutos aceleración)| MCU
    SensTemp -->|I2C / SPI / ADC (Temperatura)| MCU

    MCU -->|Métricas calculadas / Payloads MQTT| Transceiver
    Transceiver -->|RF 2.4 GHz (Telemetría / Alertas ISO)| AP_WiFi
    AP_WiFi -->|Red Ethernet / Wi-Fi Industrial| ServidorPlanta
    ServidorPlanta -->|Ingesta de tópicos MQTT| UI_Alertas
```

### 2.2 Clasificación de Flujos de Interfaz (VDI 2206)

| Tipo de Flujo | Elemento de Entrada (Origen $\to$ Límite) | Transformación / Procesamiento Interno | Salida (Límite $\to$ Destino) |
| :--- | :--- | :--- | :--- |
| **Materia** | Salpicaduras de refrigerante, polvo ferroso en suspensión. | Retención física mediante sellado elastomérico y prensaestopas / membrana Gore. | Drenaje superficial externo, cero intrusión al volumen interno (IP66). |
| **Energía** | - Conducción térmica superficial ($0\text{ a }120\text{ }^\circ\text{C}$).<br/>- Energía cinética vibratoria ($\pm 16\text{ g}$).<br/>- Energía química interna (batería primaria). | - Medición de flujo térmico por contacto.<br/>- Conversión mecanoeléctrica MEMS capacitivo.<br/>- Conversión electroquímica DC a voltajes de riel ($3.3\text{ V}$, $1.8\text{ V}$). | Disipación convectiva residual al aire; radiación RF electromagnética ($2.4\text{ GHz}, \le 20\text{ dBm}$). |
| **Información** | Parámetros físicos analógicos: desplazamiento/frecuencia mecánica y gradiente térmico. | Muestreo a $f_s \ge 3.2\text{ kS/s}$, cálculo RMS de velocidad vibratoria ($10\text{ Hz - }1000\text{ Hz}$), evaluación de umbrales ISO 10816-3, serialización JSON/CBOR. | Tramas Wi-Fi/MQTT: paquetes periódicos (cada 10 min) y mensajes de alarma en ráfaga ($\le 5\text{ s}$). |

---

## 3. MATRIZ DE REQUERIMIENTOS FUNCIONALES (FRM)

| ID | Requerimiento Funcional | Métrica / Variable | Valor Objetivo | Tolerancia / Límite | Prioridad | Método de Verificación |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **FR-01** | Medición de vibración en 3 ejes ortogonales ($X, Y, Z$) | Rango dinámico de aceleración | $\pm 16\text{ g}$ | Mínimo $\pm 16\text{ g}$ | Must | Ensayo en shaker electrodinámico calibrado |
| **FR-02** | Ancho de banda de frecuencia de aceleración | Frecuencia de corte superior ($-3\text{ dB}$) | $\ge 1.0\text{ kHz}$ por eje | Plano $\pm 10\%$ hasta $1\text{ kHz}$ | Must | Barrido senoidal en banco de vibración ($10\text{ Hz - }2\text{ kHz}$) |
| **FR-03** | Medición de temperatura superficial del motor | Rango y exactitud | $0\text{ a }120\text{ }^\circ\text{C}$; $\pm 1.0\text{ }^\circ\text{C}$ | Exactitud $\le \pm 1.0\text{ }^\circ\text{C}$ en todo el rango | Must | Baño termostático / placa calibrada vs. sonda RTD PT100 Clase A |
| **FR-04** | Evaluación en el borde de severidad vibratoria | Cumplimiento algorítmico ISO 10816-3 | Velocidad RMS ($v_{\text{RMS}}$ en $\text{mm/s}$) | Integración digital en banda $10\text{ Hz - }1\text{ kHz}$ | Must | Inyección de señales sintéticas y cotejo contra software de análisis de referencia |
| **FR-05** | Transmisión periódica de telemetría | Intervalo de reporte normal | $10\text{ min}$ | $\pm 10\text{ s}$ | Must | Captura de paquetes Wireshark/Broker MQTT por 72 h consecutivas |
| **FR-06** | Detección y notificación de estado de alarma | Latencia de reporte ante excedencia de umbral | $< 5.0\text{ s}$ | Máximo $5.0\text{ s}$ desde detección | Must | Disparo de escalón de vibración y timestamp en broker MQTT |
| **FR-07** | Tolerancia a desconexión Wi-Fi (Store & Forward) | Almacenamiento no volátil en cola local | $\ge 24\text{ h}$ de métricas históricas | 144 paquetes completos sin pérdida | Should | Simulación de caída de AP Wi-Fi durante 24 h y verificación de descarga post-reconexión |
| **FR-08** | Visualización básica y gestión de alertas | Interfaz gráfica de planta | Despliegue de estado, tendencias y alarmas | Refresco $\le 2\text{ s}$ post-recepción | Should | Prueba funcional de usuario en dashboard integrado |

---

## 4. MATRIZ DE RESTRICCIONES Y CONDICIONES DE BORDE (CONSTRAINTS)

| ID | Parámetro de Restricción | Límite Impuesto | Justificación Técnica / Origen del Requerimiento |
| :--- | :--- | :--- | :--- |
| **CR-01** | Tipo de fijación al motor | Base magnética de alta adherencia o adhesivo estructural bicomponente. Cero perforaciones o roscados. | Restricción de planta: preservación de garantía, integridad mecánica y certificación de los 50 motores existentes. |
| **CR-02** | Frecuencia de resonancia del montaje | $f_{\text{res, montaje}} \ge 2.5\text{ kHz}$ | Evitar artefactos o distorsión por resonancia estructural dentro del ancho de banda útil de medición ($1\text{ kHz}$). |
| **CR-03** | Factor de forma y dimensiones | Envolvente exterior máxima: $80 \times 60 \times 40\text{ mm}$ | Espacio restringido en aletas de refrigeración y escudos de cojinetes de motores de 5 a 30 kW. |
| **CR-04** | Masa total del nodo | Masa máxima: $\le 250\text{ g}$ | Limitar el efecto de carga de masa sobre la carcasa del motor y evitar desprendimiento magnético por cizalladura. |
| **CR-05** | Grado de protección IP | **IP66** (IEC 60529) | Exposición a chorros directos de lubricante refrigerante y presencia constante de viruta/polvo metálico fino conductivo. |
| **CR-06** | Temperatura ambiente de operación | $-10\text{ }^\circ\text{C a }+60\text{ }^\circ\text{C}$ | Perfil térmico severo en el entorno adyacente a los transportadores y carcasa caliente del estator. |
| **CR-07** | Autonomía energética | $\ge 12\text{ meses}$ (8760 h) continuos | Mantenimiento condicionado: reemplazo de baterías estrictamente limitado a la parada anual de planta. |
| **CR-08** | Protocolo y espectro RF | Wi-Fi $802.11\text{ b/g/n}$ en $2.4\text{ GHz}$; MQTT v3.1.1 / v5.0 | Compatibilidad con la infraestructura de red preexistente en planta. |
| **CR-09** | Presupuesto unitario BOM | $\le 120.00\text{ USD}$ en volumen piloto de 50 unidades | Viabilidad financiera del proyecto y techo de retorno de inversión (ROI). |

---

## 5. ARQUITECTURA FUNCIONAL Y ASIGNACIÓN DE SUBSISTEMAS

### 5.1 Subsistema Mecánico y Térmico
- **Estructura del Envolvente:** Carcasa en aleación de aluminio fundido (ej. ADC12 / A380) o policarbonato técnico reforzado con fibra de vidrio (grado UL94-V0, resistente a aceites).
- **Sellado:** O-ring continuo de caucho fluorado (FKM / Viton) contra degradación por fluidos de corte; membrana de ventilación hidrófoba/oleófoba IP66 para ecualización de presión sin condensación.
- **Acoplamiento de Vibración y Temperatura:** Base magnética con imán de neodimio de grado N52H (resistente hasta $120\text{ }^\circ\text{C}$) encapsulado en polo de acero dulce para maximizar fuerza de atracción ($\ge 120\text{ N}$ en tracción), incorporando un tetón térmico de cobre/aluminio con resorte de contacto directo sobre la carcasa del motor.

### 5.2 Subsistema Eléctrico, Sensado y Potencia
- **Acelerómetro Digital:** Sensor MEMS triaxial de ultra bajo consumo con FIFO integrada (ej. ST IIS3DWB o ADI ADXL355/357), conexión SPI, rango $\pm 16\text{ g}$, $f_s \ge 4\text{ kS/s}$.
- **Sensor Térmico:** Sensor digital de alta precisión de contacto directo (ej. TI TMP117 o Sensirion STS35), exactitud $\pm 0.1\text{ }^\circ\text{C}$ a $\pm 0.5\text{ }^\circ\text{C}$, interconectado térmicamente al tetón mecánico inferior.
- **Topología de Potencia:** Pack primario de Cloruro de Tionilo de Litio ($\text{Li-SOCl}_2$, celda tipo D o $2 \times \text{C}$, $3.6\text{ V}$, $8.5\text{ Ah}$ a $19\text{ Ah}$) con capacitor de pulso híbrido (HPC) para absorber los picos de corriente Wi-Fi ($\approx 250\text{ mA}$) sin experimentar retardo de pasivación.
- **Administración Energética:** Convertidor DC-DC Buck-Boost sincrónico de ultra bajo reposo ($I_q \le 500\text{ nA}$) con control de corte total de periféricos vía interruptores de carga (load switches).

### 5.3 Subsistema de Procesamiento y Comunicaciones (Firmware Edge)
- **Unidad de Cómputo:** SoC Dual-Core (ej. ESP32-S3 o módulo similar ultra-low-power con coprocesador ULP / RTC):
  - *Core 0 / ULP:* Monitoreo periódico de FIFO del acelerómetro, adquisición rápida y detección de disparo de umbral ISO.
  - *Core 1:* Ejecución de algoritmo FFT (1024 puntos), cálculo de velocidad RMS integrada ($10\text{ Hz - }1\text{ kHz}$) y empaquetado MQTT.
- **Estrategia de Red (Wi-Fi / MQTT):**
  - Conexión rápida (IP estática / DHCP caching, BSSID locked) para reducir el tiempo activo de radio a $< 2.5\text{ s}$ por ciclo ordinario.
  - Modo *Deep Sleep* el 99.5% del tiempo ($I_{\text{sleep}} \le 15\text{ }\mu\text{A}$).
  - Encolamiento circular en memoria Flash SPI externa de eventos de telemetría cuando el broker no responde (QoS 1).

---

## 6. ANÁLISIS DE FACTIBILIDAD TÉCNICO-ECONÓMICA (PRESUPUESTO OBJETIVO BOM)

Estimación de costos unitarios de materiales e insumos (BOM) para un lote de fabricación de 50 unidades:

| Ítem | Componente / Subsistema | Modelo / Especificación Referencial | Cant. | Costo Unitario (USD) | Subtotal (USD) |
| :---: | :--- | :--- | :---: | :---: | :---: |
| 1 | **Procesador / RF** | Módulo SoC ESP32-S3-WROOM-1 (Flash 8MB) | 1 | 4.80 | 4.80 |
| 2 | **Sensor de Vibración** | Acelerómetro MEMS triaxial industrial SPI, BW > 1 kHz | 1 | 24.50 | 24.50 |
| 3 | **Sensor de Temperatura** | Sensor digital de precisión $\pm 0.2\text{ }^\circ\text{C}$ (I2C) | 1 | 2.20 | 2.20 |
| 4 | **Gestión de Potencia** | Buck de ultra-bajo $I_q$ + Load switches + Supercap/HPC | 1 set | 6.50 | 6.50 |
| 5 | **Alimentación Primaria** | Celda $\text{Li-SOCl}_2$ C o D ($3.6\text{ V}, \ge 8500\text{ mAh}$) alta tasa | 1 | 18.00 | 18.00 |
| 6 | **PCB Principal** | PCB 4 capas FR4, acabado ENIG, pruebas eléctricas (lote 50) | 1 | 5.50 | 5.50 |
| 7 | **Componentes Pasivos/Otros** | Conectores internos, cristales, ESD, protección TVS | 1 set | 3.50 | 3.50 |
| 8 | **Carcasa Mecánica IP66** | Envolvente aleación aluminio maquinada/inyectada c/ O-ring | 1 | 26.00 | 26.00 |
| 9 | **Sistema de Fijación** | Base magnética Neodimio N52H + interfaz térmica cobre | 1 | 14.50 | 14.50 |
| 10 | **Membrana / Sellos** | Membrana de ventilación hidrófoba IP66 + prensaestopas/tapas | 1 set | 3.50 | 3.50 |
| 11 | **Empaque y Misceláneos** | Tornillería inox A4, pads térmicos, adhesivos de ensamble | 1 set | 3.00 | 3.00 |
| **TOTAL**| **Costo BOM por Nodo (Lote 50 uds.)** | — | — | — | **112.00 USD** |

### Conclusión de Factibilidad Económica
El costo calculado por nodo es de **112.00 USD**, situándose **8.00 USD por debajo del límite presupuestario de 120.00 USD** (margen de contingencia de 6.7%), cumpliendo estrictamente con la restricción financiera fijada.

<!-- FIN DEL INFORME -->