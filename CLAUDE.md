# Tarea ConVRP — Optimización

## Contexto

Tarea grupal (3 integrantes) del curso de Optimización. Consiste en modelar una versión reducida del ConVRP de Campelo et al. (2019, EJOR 273(1), 131–145) como PLEM, implementarlo en gurobipy y comparar configuraciones del solver.

- Entrega: martes 20 de octubre de 2026, hasta las 23:59. Se sube a Canvas un zip con el pitch en PDF (máx. 5 min), el código y las instancias.
- Presentación: miércoles 21 de octubre de 2026.
- Joaquín está a cargo del código del modelo y quiere aprender gurobipy. Cuando introduzcas algo nuevo de gurobipy, explica brevemente qué hace.
- Idioma: español en código, comentarios y documentos.

## Archivos

- `formulacion.md`: formulación algebraica. Es la fuente de verdad del modelo.
- `contrato_instancias.md`: formato JSON de las instancias (v1.0). El resto del grupo lo sigue; no cambiarlo sin subir la versión.
- `modelo.py`: `cargar_instancia()`, `construir_modelo()`, `extraer_solucion()`, `validar_solucion()`.
- `instancias/`: archivos JSON. `ejemplo_mini.json` es solo para pruebas.

## Decisiones tomadas (no revertir sin consultar)

1. Alcance: restricciones (1)–(5), (7)–(12), (14) y (16). Quedan fuera (6), (13), (15) y los múltiples períodos por cliente. Un nodo por cliente.
2. (3)–(4) se indexan por pedido $(j,t) \in \mathcal{O}$ en vez de sumar sobre $t$. La versión literal del paper no garantiza la visita diaria ni el regreso al depósito; el texto del paper respalda la versión por pedido.
3. (10) se indexa sobre $(i, n+1, t)$, no sobre $(i, 0, t)$.
4. Big-M por arco, con cotas de implementación $a_W \le W \le b_W$, $dW \le b_W$ y $rW \ge a_W$. Si $M \le 0$, la restricción se omite. Los arcos con $a_i + st_{it} + tt_{ij} > b_j$ se eliminan.
5. Depósito como dos nodos: 0 (salida) y $n+1$ (regreso), con las mismas coordenadas.
6. Distancias euclidianas sin redondeo; velocidad 1 por defecto.

## Requisitos de la tarea

- Dos instancias propias con al menos 15 clientes, 3 días y 3 rutas por día. Deben ser no triviales: no pueden cerrar de inmediato con la configuración por defecto.
- Fuente: CVRPLIB, sección VRPTW (Solomon o Gehring & Homberger), de preferencia la serie 2 (R2, RC2). No usar los sets CVRP, como el Set F, porque no traen ventanas de tiempo.
- Todas las corridas con `Threads=1` y `TimeLimit=600`:
  - relajación continua con `m.relax()` y brecha $(z_{MILP} - z_{LP}) / z_{MILP}$;
  - baseline contra `Cuts=0`, `Presolve=0`, `MIPFocus=1`, `MIPFocus=3` y `Heuristics=0`, cambiando un parámetro a la vez;
  - `model.tune()` con `TuneTimeLimit`;
  - sensibilidad de $Q$ con `m.fixed()`: `Pi`, `SARHSLow` y `SARHSUp` de las restricciones `capacidad[r,t]`.
- Resultado esperado en sensibilidad: `Pi = 0` en capacidad, porque (14) solo involucra $Z$, que queda fija en el modelo fijado.
- Todas las corridas de una misma instancia deben hacerse en el mismo computador.

## Estado

- Hecho: formulación, contrato y `modelo.py`. Probado con `ejemplo_mini.json`: óptimo, validación OK, brecha LP ≈ 21 %, `Pi` de capacidad = 0.
- Pendiente: generador de instancias desde Solomon (`.txt`), instancias `inst_A` e `inst_B` calibradas, script de experimentos y slides.
- Licencia: el gurobipy de pip trae una licencia limitada (~2000 variables y restricciones). Las instancias reales requieren licencia académica.

## Cómo correr

```bash
python modelo.py instancias/ejemplo_mini.json
python modelo.py instancias/inst_A.json --timelimit 600 --threads 1
python modelo.py instancias/ejemplo_mini.json --lp   # escribe el modelo en .lp
```