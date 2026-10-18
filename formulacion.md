# Formulación reducida — ConVRP

Basada en: Campelo, P., Neves-Moreira, F., Amorim, P., & Almada-Lobo, B. (2019). *Consistent vehicle routing problem with service level agreements: A case study in the pharmaceutical distribution sector*. EJOR, 273(1), 131–145.

## Alcance

| Incluido | Excluido |
|---|---|
| (1) Función objetivo | (6) Separación de nodos del mismo cliente (un nodo por cliente) |
| (2)–(4) Asignación y flujo | (13) Fechas de liberación $rd_i$ |
| (5) Salida diaria del depósito | (15) Duración máxima de ruta $MD$ |
| (7)–(8) Tiempos y ventanas | Múltiples períodos de despacho por cliente |
| (9)–(12) Horarios del depósito | |
| (14) Capacidad | |
| (16) Dominio de variables | |

---

## Conjuntos

| Símbolo | Descripción |
|---|---|
| $\mathcal{D}$ | Días del horizonte de planificación |
| $\mathcal{R}$ | Rutas (vehículos/conductores) |
| $\mathcal{N}_C$ | Clientes (un nodo por cliente), $n = \lvert \mathcal{N}_C \rvert$ |
| $\mathcal{N} = \{0\} \cup \mathcal{N}_C \cup \{n+1\}$ | Todos los nodos; $0$ = salida del depósito, $n+1$ = regreso al depósito (mismo lugar físico) |
| $\mathcal{N}_{TW} \subseteq \mathcal{N}_C$ | Clientes con ventana de tiempo |
| $\mathcal{O} \subseteq \mathcal{N}_C \times \mathcal{D}$ | Pedidos: $(i,t) \in \mathcal{O}$ si el cliente $i$ pide el día $t$ |
| $\mathcal{A}$ | Arcos $(i,j,t)$ (ver abajo) |

**Construcción de $\mathcal{A}$** — para cada día $t \in \mathcal{D}$:

- $(0, j, t)$ para todo $(j,t) \in \mathcal{O}$
- $(i, j, t)$ para todo $i \neq j$ con $(i,t), (j,t) \in \mathcal{O}$
- $(i, n+1, t)$ para todo $(i,t) \in \mathcal{O}$
- $(0, n+1, t)$: ruta vacía, con $td_{0,n+1} = tt_{0,n+1} = 0$

No existen arcos que entren a $0$ ni que salgan de $n+1$.

## Parámetros

| Símbolo | Descripción |
|---|---|
| $td_{ij}$ | Distancia de viaje entre $i$ y $j$ |
| $tt_{ij}$ | Tiempo de viaje entre $i$ y $j$ |
| $st_{it}$ | Tiempo de servicio del cliente $i$ el día $t$ |
| $q_{it}$ | Cantidad pedida por el cliente $i$ el día $t$ |
| $[a_i, b_i]$ | Ventana de tiempo del cliente $i \in \mathcal{N}_{TW}$ |
| $[a_W, b_W]$ | Horario de operación del depósito |
| $Q$ | Capacidad de cada vehículo |
| $M$ | Constante grande (calculada por arco, ver notas) |

## Variables de decisión

| Variable | Dominio | Descripción |
|---|---|---|
| $X^r_{ijt}$ | $\{0,1\}$ | 1 si la ruta $r$ recorre el arco $(i,j,t)$ |
| $Z^r_i$ | $\{0,1\}$ | 1 si el cliente $i$ está asignado a la ruta $r$ (sin índice $t$: consistencia) |
| $W_{it}$ | $\geq 0$ | Hora de llegada al cliente $i$ el día $t$ |
| $dW^r$ | $\geq 0$ | Hora de salida de la ruta $r$ (igual todos los días) |
| $rW^r_t$ | $\geq 0$ | Hora de regreso de la ruta $r$ al depósito el día $t$ |

---

## Modelo

**Función objetivo**: minimizar la distancia total recorrida.

$$
\min \sum_{(i,j,t)\in\mathcal{A}} \sum_{r\in\mathcal{R}} td_{ij}\, X^r_{ijt} \tag{1}
$$

**Asignación cliente–ruta**: cada cliente se asigna a exactamente una ruta.

$$
\sum_{r\in\mathcal{R}} Z^r_i = 1 \qquad \forall i\in\mathcal{N}_C \tag{2}
$$

**Flujo de entrada**: cada pedido se atiende una vez, por la ruta asignada.

$$
\sum_{i\in\mathcal{N} \,:\, (i,j,t)\in\mathcal{A}} X^r_{ijt} = Z^r_j \qquad \forall (j,t)\in\mathcal{O},\ \forall r\in\mathcal{R} \tag{3}
$$

**Flujo de salida**: mismo razonamiento para las salidas de cada nodo.

$$
\sum_{j\in\mathcal{N} \,:\, (i,j,t)\in\mathcal{A}} X^r_{ijt} = Z^r_i \qquad \forall (i,t)\in\mathcal{O},\ \forall r\in\mathcal{R} \tag{4}
$$

**Salida diaria del depósito**: cada ruta sale exactamente una vez por día.

$$
\sum_{j\in\mathcal{N} \,:\, (0,j,t)\in\mathcal{A}} X^r_{0jt} = 1 \qquad \forall t\in\mathcal{D},\ \forall r\in\mathcal{R} \tag{5}
$$

**Seguimiento de tiempo (Big-M)**: también elimina subtours.

$$
W_{it} + st_{it} + tt_{ij} \leq W_{jt} + M_{ij}\,(1 - X^r_{ijt}) \qquad \forall (i,j,t)\in\mathcal{A},\ i,j\in\mathcal{N}_C,\ \forall r\in\mathcal{R} \tag{7}
$$

**Ventanas de tiempo**: solo para clientes con ventana definida.

$$
a_i \leq W_{it} \leq b_i \qquad \forall (i,t)\in\mathcal{O},\ i\in\mathcal{N}_{TW} \tag{8}
$$

**Coordinación con la salida del depósito**:

$$
dW^r + tt_{0j} \leq W_{jt} + M_{0j}\,(1 - X^r_{0jt}) \qquad \forall (0,j,t)\in\mathcal{A},\ j\in\mathcal{N}_C,\ \forall r\in\mathcal{R} \tag{9}
$$

**Coordinación con el regreso al depósito**:

$$
W_{it} + st_{it} + tt_{i,n+1} \leq rW^r_t + M_{i,n+1}\,(1 - X^r_{i,n+1,t}) \qquad \forall (i,n+1,t)\in\mathcal{A},\ i\in\mathcal{N}_C,\ \forall r\in\mathcal{R} \tag{10}
$$

**Horario de operación del depósito**:

$$
dW^r \geq a_W \qquad \forall r\in\mathcal{R} \tag{11}
$$

$$
rW^r_t \leq b_W \qquad \forall t\in\mathcal{D},\ \forall r\in\mathcal{R} \tag{12}
$$

**Capacidad**: carga diaria de cada ruta según los clientes asignados.

$$
\sum_{i \,:\, (i,t)\in\mathcal{O}} q_{it}\, Z^r_i \leq Q \qquad \forall r\in\mathcal{R},\ \forall t\in\mathcal{D} \tag{14}
$$

**Dominio**:

$$
X^r_{ijt},\ Z^r_i \in \{0,1\}; \qquad W_{it},\ dW^r,\ rW^r_t \geq 0 \tag{16}
$$

---

## Notas de adaptación

### 1. Depósito como dos nodos
Los nodos $0$ y $n+1$ representan el mismo depósito físico (mismas coordenadas). Separarlos convierte cada ruta en un camino $0 \to \dots \to n+1$ en vez de un ciclo, lo que permite que (7) imponga tiempos crecientes a lo largo de la ruta. La salida se registra en $dW^r$ y el regreso en $rW^r_t$.

### 2. Restricciones (3)–(4) indexadas por pedido $(j,t)$
En el paper, (3)–(4) suman sobre $t$ y se indexan solo por $j, r$. Se indexan por pedido $(j,t)\in\mathcal{O}$ porque:

- El texto del paper describe (3) como una visita por nodo **en cada día con pedido**.
- El paper afirma, junto a (5), que el regreso al depósito queda asegurado por (3)–(4). Eso solo es cierto si el balance de flujo se cumple **cada día**. Con la suma sobre $t$, un cliente podría tener su entrada el día 1 y su salida el día 2, y la ruta del día 1 no volvería al depósito.
- Con la suma sobre $t$, el modelo no resulta infactible: subestima la distancia porque deja pedidos sin atender.

### 3. Índice de (10)
El paper indexa (10) sobre $(i,0,t)\in\mathcal{A}$, pero el cuerpo de la restricción usa $X^r_{i(n+1)t}$ y no existen arcos que entren a $0$. Se indexa sobre $(i,n+1,t)\in\mathcal{A}$.

### 4. Big-M por arco
Un $M$ global grande debilita la relajación continua y aumenta la brecha de integralidad. Se usa el menor valor válido por arco. Para clientes sin ventana, se toma $[a_i,b_i] = [a_W,b_W]$:

| Restricción | $M$ |
|---|---|
| (7) | $M_{ij} = b_i + st_{it} + tt_{ij} - a_j$ |
| (9) | $M_{0j} = b_W + tt_{0j} - a_j$ |
| (10) | $M_{i,n+1} = b_i + st_{it} + tt_{i,n+1} - a_W$ |

Estos valores son válidos si en la implementación se acotan $a_W \le W_{it} \le b_W$ y $dW^r \le b_W$. Ambas cotas se cumplen en toda solución factible con visitas, así que no cambian el óptimo. Si algún $M$ resulta negativo, el arco es infactible por tiempo y se puede eliminar de $\mathcal{A}$.

### 5. Consistencia
Hay dos formas de consistencia en el modelo:

- **De conductor**: $Z^r_i$ no depende de $t$, así que el cliente es atendido siempre por la misma ruta.
- **De horario de salida**: $dW^r$ no depende de $t$, así que cada vehículo sale a la misma hora todos los días.

### 6. Simetría
Las rutas son idénticas (misma $Q$, mismo depósito), por lo que cualquier permutación de rutas da una solución equivalente. Esto hace más lento el Branch-and-Bound. No se agregan restricciones de ruptura de simetría porque no forman parte del modelo pedido, pero es un punto a discutir en los resultados.

### 7. Tamaño estimado
Con 15 clientes, 3 días y 3 rutas, si todos piden todos los días:

- Arcos por día: $15 \cdot 14 + 15 + 15 + 1 = 241$
- Variables $X$: $241 \cdot 3 \cdot 3 \approx 2{,}170$ binarias
- Variables $Z$: $15 \cdot 3 = 45$ binarias