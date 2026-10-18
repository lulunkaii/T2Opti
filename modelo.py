"""
modelo.py — ConVRP reducido (Campelo et al., 2019) implementado en gurobipy.

Formulación: ver formulacion_reducida.md
Formato de instancias: ver contrato_instancias.md

Uso:
    python modelo.py instancias/ejemplo_mini.json
    python modelo.py instancias/inst_A.json --timelimit 600 --threads 1
    python modelo.py instancias/ejemplo_mini.json --lp     # escribe el modelo en .lp para revisarlo

Uso desde otro script (por ejemplo, el de experimentos):
    from modelo import cargar_instancia, construir_modelo
    inst = cargar_instancia("instancias/inst_A.json")
    m = construir_modelo(inst)
    m.Params.Threads = 1
    m.optimize()
"""

import argparse
import json
import math
from dataclasses import dataclass, field

import gurobipy as gp
from gurobipy import GRB


# =============================================================================
# 1. Datos de la instancia
# =============================================================================

@dataclass
class Instancia:
    """Conjuntos y parámetros del modelo, con la notación de formulacion_reducida.md."""
    nombre: str
    D: list          # días: [0, ..., |D|-1]
    R: list          # rutas: [0, ..., |R|-1]
    C: list          # clientes N_C: [1, ..., n]
    salida: int      # nodo 0 (depósito como salida)
    regreso: int     # nodo n+1 (depósito como regreso)
    O: list          # pedidos (i, t)
    ventana: dict    # i -> (a_i, b_i), solo para clientes en N_TW
    aW: float        # apertura del depósito
    bW: float        # cierre del depósito
    Q: float         # capacidad de cada vehículo
    q: dict          # (i, t) -> demanda
    st: dict         # (i, t) -> tiempo de servicio
    td: dict         # (i, j) -> distancia
    tt: dict         # (i, j) -> tiempo de viaje
    A: list = field(default_factory=list)  # arcos (i, j, t)

    # Ventana "efectiva": la del cliente si tiene; si no, el horario del depósito.
    # Se usa para calcular los Big-M y para el preprocesamiento de arcos.
    def a(self, i):
        return self.ventana[i][0] if i in self.ventana else self.aW

    def b(self, i):
        return self.ventana[i][1] if i in self.ventana else self.bW


def cargar_instancia(ruta: str) -> Instancia:
    """Lee un JSON con el formato de contrato_instancias.md y calcula lo derivado."""
    with open(ruta, encoding="utf-8") as f:
        data = json.load(f)

    n = len(data["clientes"])
    C = [c["id"] for c in data["clientes"]]
    if C != list(range(1, n + 1)):
        raise ValueError("Los ids de clientes deben ser 1..n, consecutivos y en orden.")

    D = list(range(data["dias"]))
    R = list(range(data["rutas"]))
    salida, regreso = 0, n + 1
    dep = data["deposito"]
    aW, bW = dep["a"], dep["b"]
    Q = data["capacidad"]
    velocidad = data.get("velocidad", 1.0)

    # Coordenadas: los nodos 0 y n+1 son el mismo depósito físico.
    coord = {salida: (dep["x"], dep["y"]), regreso: (dep["x"], dep["y"])}
    ventana = {}
    for c in data["clientes"]:
        coord[c["id"]] = (c["x"], c["y"])
        if c.get("ventana") is not None:
            a_i, b_i = c["ventana"]
            if a_i > b_i:
                raise ValueError(f"Cliente {c['id']}: ventana con a > b.")
            ventana[c["id"]] = (a_i, b_i)

    q, st = {}, {}
    for p in data["pedidos"]:
        i, t = p["cliente"], p["dia"]
        if i not in C:
            raise ValueError(f"Pedido de un cliente inexistente: {i}.")
        if t not in D:
            raise ValueError(f"Pedido en un día inexistente: {t}.")
        if (i, t) in q:
            raise ValueError(f"Pedido duplicado para cliente {i}, día {t}.")
        if p["q"] > Q:
            raise ValueError(f"Pedido ({i}, {t}) con demanda {p['q']} > capacidad {Q}.")
        q[i, t] = p["q"]
        st[i, t] = p.get("st", 0)

    O = sorted(q)
    sin_pedidos = set(C) - {i for i, _ in O}
    if sin_pedidos:
        raise ValueError(f"Clientes sin ningún pedido: {sorted(sin_pedidos)}.")

    # Distancias euclidianas y tiempos de viaje (no se guardan en el JSON).
    nodos = [salida] + C + [regreso]
    td = {(i, j): math.dist(coord[i], coord[j]) for i in nodos for j in nodos if i != j}
    tt = {k: v / velocidad for k, v in td.items()}

    inst = Instancia(data["nombre"], D, R, C, salida, regreso, O, ventana,
                     aW, bW, Q, q, st, td, tt)

    # Validación 7: cada pedido debe poder atenderse al menos en una ruta exclusiva.
    for (i, t) in O:
        llegada_min = max(inst.a(i), aW + tt[salida, i])
        if llegada_min > inst.b(i):
            raise ValueError(f"Pedido ({i}, {t}): no se alcanza la ventana saliendo a las {aW}.")
        if llegada_min + st[i, t] + tt[i, regreso] > bW:
            raise ValueError(f"Pedido ({i}, {t}): no se puede volver antes del cierre del depósito.")

    # Avisos: requisitos de tamaño de la tarea.
    if n < 15 or len(D) < 3 or len(R) < 3:
        print(f"Aviso: '{inst.nombre}' no cumple el tamaño mínimo de la tarea "
              f"(n={n}, días={len(D)}, rutas={len(R)}). Úsala solo para pruebas.")

    inst.A = construir_arcos(inst)
    return inst


def construir_arcos(inst: Instancia) -> list:
    """Conjunto A: arcos (i, j, t) que pueden usarse el día t."""
    A = []
    for t in inst.D:
        clientes_t = [i for i in inst.C if (i, t) in inst.q]
        A.append((inst.salida, inst.regreso, t))   # ruta vacía
        for j in clientes_t:
            A.append((inst.salida, j, t))           # salida del depósito
            A.append((j, inst.regreso, t))          # regreso al depósito
        for i in clientes_t:
            for j in clientes_t:
                if i == j:
                    continue
                # Preprocesamiento (el paper lo permite en la sección 3): si aun
                # llegando a i lo antes posible no se alcanza la ventana de j,
                # el arco nunca se usará y se elimina.
                if inst.a(i) + inst.st[i, t] + inst.tt[i, j] > inst.b(j):
                    continue
                A.append((i, j, t))
    return A


# =============================================================================
# 2. Modelo en gurobipy
# =============================================================================

def construir_modelo(inst: Instancia) -> gp.Model:
    """Construye el PLEM. Para la relajación continua: m.relax() sobre este modelo."""
    m = gp.Model(inst.nombre)
    clientes = set(inst.C)
    s, e = inst.salida, inst.regreso

    # --- Variables -----------------------------------------------------------
    # addVars crea un tupledict: un diccionario {índice: variable} que además
    # permite sumar con comodines, por ejemplo X.sum(r, "*", j, t).
    idx_X = [(r, i, j, t) for r in inst.R for (i, j, t) in inst.A]
    X = m.addVars(idx_X, vtype=GRB.BINARY, name="X")
    Z = m.addVars(inst.R, inst.C, vtype=GRB.BINARY, name="Z")

    # Cotas de implementación (ver nota 4 de formulacion_reducida.md): son válidas
    # en toda solución factible y hacen correctos los Big-M.
    W = m.addVars(inst.O, lb=inst.aW, ub=inst.bW, name="W")
    dW = m.addVars(inst.R, lb=0, ub=inst.bW, name="dW")
    rW = m.addVars(inst.R, inst.D, lb=inst.aW, name="rW")

    # --- (1) Función objetivo -------------------------------------------------
    m.setObjective(
        gp.quicksum(inst.td[i, j] * X[r, i, j, t] for (r, i, j, t) in idx_X),
        GRB.MINIMIZE,
    )

    # --- Big-M por arco (nota 4) ---------------------------------------------
    M7 = {(i, j, t): inst.b(i) + inst.st[i, t] + inst.tt[i, j] - inst.a(j)
          for (i, j, t) in inst.A if i in clientes and j in clientes}
    M9 = {j: inst.bW + inst.tt[s, j] - inst.a(j) for j in inst.C}
    M10 = {(i, t): inst.b(i) + inst.st[i, t] + inst.tt[i, e] - inst.aW for (i, t) in inst.O}

    # --- Restricciones --------------------------------------------------------
    # addConstrs recibe un generador; los índices del "for" forman el nombre de
    # cada restricción, por ejemplo entrada[3,1,0].

    # (2) Cada cliente se asigna a exactamente una ruta.
    m.addConstrs((Z.sum("*", i) == 1 for i in inst.C), name="asignacion")

    # (3) Entrada: cada pedido (j, t) se visita una vez, por su ruta asignada.
    m.addConstrs((X.sum(r, "*", j, t) == Z[r, j]
                  for (j, t) in inst.O for r in inst.R), name="entrada")

    # (4) Salida: mismo razonamiento para las salidas de cada nodo.
    m.addConstrs((X.sum(r, i, "*", t) == Z[r, i]
                  for (i, t) in inst.O for r in inst.R), name="salida")

    # (5) Cada ruta sale del depósito exactamente una vez por día.
    m.addConstrs((X.sum(r, s, "*", t) == 1
                  for t in inst.D for r in inst.R), name="salida_deposito")

    # (7) Seguimiento de tiempo (Big-M); también elimina subtours.
    # Si M <= 0 la restricción se cumple siempre, así que se omite.
    m.addConstrs(
        (W[i, t] + inst.st[i, t] + inst.tt[i, j]
         <= W[j, t] + M7[i, j, t] * (1 - X[r, i, j, t])
         for (i, j, t) in M7 if M7[i, j, t] > 0
         for r in inst.R),
        name="tiempo",
    )

    # (8) Ventanas de tiempo, solo para clientes en N_TW.
    m.addConstrs((W[i, t] >= inst.ventana[i][0]
                  for (i, t) in inst.O if i in inst.ventana), name="ventana_ini")
    m.addConstrs((W[i, t] <= inst.ventana[i][1]
                  for (i, t) in inst.O if i in inst.ventana), name="ventana_fin")

    # (9) Coordinación con la hora de salida del depósito.
    m.addConstrs(
        (dW[r] + inst.tt[s, j] <= W[j, t] + M9[j] * (1 - X[r, s, j, t])
         for (i0, j, t) in inst.A if i0 == s and j in clientes and M9[j] > 0
         for r in inst.R),
        name="coord_salida",
    )

    # (10) Coordinación con la hora de regreso (índice corregido a n+1, nota 3).
    m.addConstrs(
        (W[i, t] + inst.st[i, t] + inst.tt[i, e]
         <= rW[r, t] + M10[i, t] * (1 - X[r, i, e, t])
         for (i, j0, t) in inst.A if j0 == e and i in clientes and M10[i, t] > 0
         for r in inst.R),
        name="coord_regreso",
    )

    # (11) y (12) Horario de operación del depósito.
    m.addConstrs((dW[r] >= inst.aW for r in inst.R), name="hora_salida")
    m.addConstrs((rW[r, t] <= inst.bW for r in inst.R for t in inst.D), name="hora_regreso")

    # (14) Capacidad: carga diaria de cada ruta según los clientes asignados.
    # Nombres capacidad[r,t]: útiles para buscarlas en m.fixed() con getConstrByName.
    m.addConstrs(
        (gp.quicksum(inst.q[i, t] * Z[r, i] for i in inst.C if (i, t) in inst.q) <= inst.Q
         for r in inst.R for t in inst.D),
        name="capacidad",
    )

    # Se guardan referencias en el modelo (atributos con "_" son libres en gurobipy).
    m._inst, m._X, m._Z, m._W, m._dW, m._rW = inst, X, Z, W, dW, rW
    m.update()
    return m


# =============================================================================
# 3. Lectura y validación de la solución
# =============================================================================

ESTADOS = {
    GRB.OPTIMAL: "óptimo",
    GRB.TIME_LIMIT: "límite de tiempo",
    GRB.INFEASIBLE: "infactible",
    GRB.INF_OR_UNBD: "infactible o no acotado",
    GRB.INTERRUPTED: "interrumpido",
}


def extraer_solucion(m: gp.Model):
    """Reconstruye las rutas de cada día recorriendo los arcos con X = 1."""
    if m.SolCount == 0:
        return None
    inst = m._inst
    siguiente = {(r, i, t): j for (r, i, j, t), var in m._X.items() if var.X > 0.5}

    rutas = {}
    for r in inst.R:
        for t in inst.D:
            camino, actual = [inst.salida], inst.salida
            for _ in range(len(inst.C) + 1):
                actual = siguiente.get((r, actual, t))
                if actual is None:
                    break
                camino.append(actual)
                if actual == inst.regreso:
                    break
            rutas[r, t] = camino

    return {
        "objetivo": m.ObjVal,
        "rutas": rutas,
        "asignacion": {i: r for r in inst.R for i in inst.C if m._Z[r, i].X > 0.5},
        "llegada": {k: v.X for k, v in m._W.items()},
        "salida": {r: m._dW[r].X for r in inst.R},
        "regreso": {k: v.X for k, v in m._rW.items()},
    }


def validar_solucion(inst: Instancia, sol: dict, tol: float = 1e-6) -> list:
    """Revisa la solución con independencia del modelo. Devuelve una lista de errores."""
    errores = []
    clientes = set(inst.C)
    visitas = {}

    for (r, t), camino in sol["rutas"].items():
        if camino[-1] != inst.regreso:
            errores.append(f"Ruta {r}, día {t}: no vuelve al depósito ({camino}).")
        en_ruta = [i for i in camino if i in clientes]
        carga = sum(inst.q[i, t] for i in en_ruta)
        if carga > inst.Q + tol:
            errores.append(f"Ruta {r}, día {t}: carga {carga} > Q = {inst.Q}.")
        for i in en_ruta:
            visitas.setdefault((i, t), []).append(r)
            if sol["asignacion"].get(i) != r:
                errores.append(f"Cliente {i}: atendido por la ruta {r} el día {t}, "
                               f"pero está asignado a la ruta {sol['asignacion'].get(i)}.")

    for (i, t) in inst.O:
        n_visitas = len(visitas.get((i, t), []))
        if n_visitas != 1:
            errores.append(f"Pedido ({i}, {t}): atendido {n_visitas} veces.")
        if i in inst.ventana:
            a_i, b_i = inst.ventana[i]
            w = sol["llegada"][i, t]
            if not (a_i - tol <= w <= b_i + tol):
                errores.append(f"Pedido ({i}, {t}): llegada {w:.1f} fuera de [{a_i}, {b_i}].")
    return errores


def imprimir_resumen(m: gp.Model, sol) -> None:
    inst = m._inst
    print(f"\nEstado: {ESTADOS.get(m.Status, m.Status)}")
    if sol is None:
        print("No se encontró solución factible.")
        return
    print(f"Distancia total: {m.ObjVal:.2f}")
    if m.IsMIP:
        print(f"Cota inferior:   {m.ObjBound:.2f}   Gap: {100 * m.MIPGap:.2f}%")
    print(f"Tiempo:          {m.Runtime:.2f} s")
    for t in inst.D:
        print(f"\nDía {t}")
        for r in inst.R:
            camino = sol["rutas"][r, t]
            if len(camino) <= 2:
                print(f"  Ruta {r}: sin pedidos")
                continue
            paradas = " -> ".join(f"{i} ({sol['llegada'][i, t]:.0f})" for i in camino[1:-1])
            print(f"  Ruta {r}: Dep ({sol['salida'][r]:.0f}) -> {paradas} "
                  f"-> Dep ({sol['regreso'][r, t]:.0f})")


def resumen_instancia(inst: Instancia) -> str:
    return (f"Instancia '{inst.nombre}': {len(inst.C)} clientes, {len(inst.D)} días, "
            f"{len(inst.R)} rutas, {len(inst.O)} pedidos, {len(inst.A)} arcos, "
            f"{len(inst.ventana)} clientes con ventana, Q = {inst.Q}")


# =============================================================================
# 4. Ejecución desde la terminal
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="ConVRP reducido en gurobipy")
    parser.add_argument("instancia", help="ruta al JSON de la instancia")
    parser.add_argument("--timelimit", type=float, default=600)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--lp", action="store_true",
                        help="escribe el modelo en formato .lp para revisarlo")
    args = parser.parse_args()

    inst = cargar_instancia(args.instancia)
    print(resumen_instancia(inst))

    m = construir_modelo(inst)
    if args.lp:
        m.write(f"{inst.nombre}.lp")
        print(f"Modelo escrito en {inst.nombre}.lp")

    m.Params.TimeLimit = args.timelimit
    m.Params.Threads = args.threads
    m.optimize()

    if m.Status == GRB.INFEASIBLE:
        # El IIS es un subconjunto mínimo de restricciones que ya es infactible.
        m.computeIIS()
        m.write(f"{inst.nombre}.ilp")
        print(f"Modelo infactible. Restricciones en conflicto: {inst.nombre}.ilp")
        return

    sol = extraer_solucion(m)
    imprimir_resumen(m, sol)
    if sol is not None:
        errores = validar_solucion(inst, sol)
        print("\nValidación: OK" if not errores else "\nValidación con errores:")
        for err in errores:
            print("  -", err)


if __name__ == "__main__":
    main()