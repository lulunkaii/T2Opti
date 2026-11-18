"""
convrp.py: ConVRP reducido (Campelo et al., 2019) en Gurobi y CPLEX.

Tarea 3: el mismo modelo de la Tarea 2 (formulacion.md), implementado en gurobipy y
en docplex, para comparar ambos solvers en la relajación continua (LP) y en el
modelo entero (MIP), con la configuración por defecto, sobre las mismas instancias.

Uso:
    python convrp.py # inst_A e inst_B, ambos solvers, LP y MIP
    python convrp.py instancias/inst_A.json --solver cplex --timelimit 60
    python convrp.py --lp-file # además escribe cada modelo en .lp

Salidas: logs/ (un log por solver, instancia y tipo), resultados.csv y figuras/.
"""

import argparse
import csv
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import gurobipy as gp
from gurobipy import GRB
from docplex.mp.model import Model as ModeloCplex
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

CARPETA = Path(__file__).parent
TIMELIMIT = 600
THREADS = 1


# Instancias --------------------------------------------

@dataclass
class Instancia:
    nombre: str
    D: list
    R: list
    C: list
    salida: int
    regreso: int
    O: list
    ventana: dict
    aW: float
    bW: float
    Q: float
    q: dict
    st: dict
    td: dict
    tt: dict
    A: list = field(default_factory=list)
    coord: dict = field(default_factory=dict)

    # Ventana efectiva: la del cliente o, si no tiene, el horario del depósito.
    def a(self, i):
        return self.ventana[i][0] if i in self.ventana else self.aW

    def b(self, i):
        return self.ventana[i][1] if i in self.ventana else self.bW


def cargar_instancia(ruta) -> Instancia:
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

    # Distancias euclidianas sin redondeo; con velocidad 1 el tiempo es igual a la distancia.
    nodos = [salida] + C + [regreso]
    td = {(i, j): math.dist(coord[i], coord[j]) for i in nodos for j in nodos if i != j}
    tt = {k: v / velocidad for k, v in td.items()}

    inst = Instancia(data["nombre"], D, R, C, salida, regreso, O, ventana,
                     aW, bW, Q, q, st, td, tt)

    for (i, t) in O:
        llegada_min = max(inst.a(i), aW + tt[salida, i])
        if llegada_min > inst.b(i):
            raise ValueError(f"Pedido ({i}, {t}): no se alcanza la ventana saliendo a las {aW}.")
        if llegada_min + st[i, t] + tt[i, regreso] > bW:
            raise ValueError(f"Pedido ({i}, {t}): no se puede volver antes del cierre del depósito.")

    if n < 15 or len(D) < 3 or len(R) < 3:
        print(f"Aviso: '{inst.nombre}' no cumple el tamaño mínimo de la tarea "
              f"(n={n}, días={len(D)}, rutas={len(R)}). Úsala solo para pruebas.")

    inst.coord = coord
    inst.A = construir_arcos(inst)
    return inst


def construir_arcos(inst: Instancia) -> list:
    # Solo clientes con pedido el día t: un cliente sin pedido ese día no tiene arcos
    A = []
    for t in inst.D:
        clientes_t = [i for i in inst.C if (i, t) in inst.q]
        A.append((inst.salida, inst.regreso, t))
        for j in clientes_t:
            A.append((inst.salida, j, t))
            A.append((j, inst.regreso, t))
        for i in clientes_t:
            for j in clientes_t:
                # Preprocesamiento: se elimina el arco si ni llegando a i lo antes posible se alcanza j
                if i != j and inst.a(i) + inst.st[i, t] + inst.tt[i, j] <= inst.b(j):
                    A.append((i, j, t))
    return A


def resumen_instancia(inst: Instancia) -> str:
    return (f"Instancia '{inst.nombre}': {len(inst.C)} clientes, {len(inst.D)} días, "
            f"{len(inst.R)} rutas, {len(inst.O)} pedidos, {len(inst.A)} arcos, "
            f"{len(inst.ventana)} clientes con ventana, Q = {inst.Q}")


def big_m(inst: Instancia):
    """Big-M por arco de (7), (9) y (10). Con M <= 0 la restricción no hace falta."""
    clientes = set(inst.C)
    s, e = inst.salida, inst.regreso
    M7 = {(i, j, t): inst.b(i) + inst.st[i, t] + inst.tt[i, j] - inst.a(j)
          for (i, j, t) in inst.A if i in clientes and j in clientes}
    M9 = {j: inst.bW + inst.tt[s, j] - inst.a(j) for j in inst.C}
    M10 = {(i, t): inst.b(i) + inst.st[i, t] + inst.tt[i, e] - inst.aW for (i, t) in inst.O}
    return M7, M9, M10


# Modelo en Gurobi --------------------------------------------

def construir_modelo_gurobi(inst: Instancia) -> gp.Model:
    m = gp.Model(inst.nombre)
    clientes = set(inst.C)
    s, e = inst.salida, inst.regreso

    idx_X = [(r, i, j, t) for r in inst.R for (i, j, t) in inst.A]
    X = m.addVars(idx_X, vtype=GRB.BINARY, name="X")
    Z = m.addVars(inst.R, inst.C, vtype=GRB.BINARY, name="Z")
    W = m.addVars(inst.O, lb=inst.aW, ub=inst.bW, name="W")
    dW = m.addVars(inst.R, lb=0, ub=inst.bW, name="dW")
    rW = m.addVars(inst.R, inst.D, lb=inst.aW, name="rW")

    # (1)
    m.setObjective(gp.quicksum(inst.td[i, j] * X[r, i, j, t] for (r, i, j, t) in idx_X), GRB.MINIMIZE)

    M7, M9, M10 = big_m(inst)

    # (2)-(5)
    m.addConstrs((Z.sum("*", i) == 1 for i in inst.C), name="asignacion")
    m.addConstrs((X.sum(r, "*", j, t) == Z[r, j] for (j, t) in inst.O for r in inst.R), name="entrada")
    m.addConstrs((X.sum(r, i, "*", t) == Z[r, i] for (i, t) in inst.O for r in inst.R), name="salida")
    m.addConstrs((X.sum(r, s, "*", t) == 1 for t in inst.D for r in inst.R), name="salida_deposito")

    # (7)
    m.addConstrs((W[i, t] + inst.st[i, t] + inst.tt[i, j] <= W[j, t] + M7[i, j, t] * (1 - X[r, i, j, t])
                  for (i, j, t) in M7 if M7[i, j, t] > 0 for r in inst.R), name="tiempo")

    # (8)
    m.addConstrs((W[i, t] >= inst.ventana[i][0] for (i, t) in inst.O if i in inst.ventana), name="ventana_ini")
    m.addConstrs((W[i, t] <= inst.ventana[i][1] for (i, t) in inst.O if i in inst.ventana), name="ventana_fin")

    # (9)-(10)
    m.addConstrs((dW[r] + inst.tt[s, j] <= W[j, t] + M9[j] * (1 - X[r, s, j, t])
                  for (i0, j, t) in inst.A if i0 == s and j in clientes and M9[j] > 0
                  for r in inst.R), name="coord_salida")
    m.addConstrs((W[i, t] + inst.st[i, t] + inst.tt[i, e]
                  <= rW[r, t] + M10[i, t] * (1 - X[r, i, e, t])
                  for (i, j0, t) in inst.A if j0 == e and i in clientes and M10[i, t] > 0
                  for r in inst.R), name="coord_regreso")

    # (11)-(12)
    m.addConstrs((dW[r] >= inst.aW for r in inst.R), name="hora_salida")
    m.addConstrs((rW[r, t] <= inst.bW for r in inst.R for t in inst.D), name="hora_regreso")

    # (14)
    m.addConstrs((gp.quicksum(inst.q[i, t] * Z[r, i] for i in inst.C if (i, t) in inst.q) <= inst.Q
                  for r in inst.R for t in inst.D), name="capacidad")

    m._vars = (X, Z, W, dW, rW)
    m.update()
    return m


ESTADOS_GUROBI = {
    GRB.OPTIMAL: "óptimo",
    GRB.TIME_LIMIT: "límite de tiempo",
    GRB.INFEASIBLE: "infactible",
    GRB.INF_OR_UNBD: "infactible o no acotado",
    GRB.INTERRUPTED: "interrumpido",
}


def resolver_gurobi(inst: Instancia, relajado: bool, timelimit: float, threads: int, log: Path,
                    lp_file: Path = None):
    m = construir_modelo_gurobi(inst)
    if relajado:
        m = m.relax()  # copia del modelo con todas las binarias continuas en [0, 1]
    if lp_file:
        m.write(str(lp_file))
    log.unlink(missing_ok=True)  # Gurobi agrega al final del LogFile si ya existe
    m.Params.LogFile = str(log)
    m.Params.LogToConsole = 0
    m.Params.TimeLimit = timelimit
    m.Params.Threads = threads
    m.optimize()

    hay_sol = m.SolCount > 0
    fila = {
        "solver": "Gurobi", "instancia": inst.nombre, "tipo": "LP" if relajado else "MIP",
        "estado": ESTADOS_GUROBI.get(m.Status, str(m.Status)),
        "objetivo": m.ObjVal if hay_sol else None,
        "cota": None if relajado else m.ObjBound,
        "gap (%)": None if relajado or not hay_sol else 100 * m.MIPGap,
        "nodos": None if relajado else int(m.NodeCount),
        "tiempo (s)": m.Runtime,
    }
    sol = None
    if hay_sol and not relajado:
        sol = armar_solucion(inst, m.ObjVal, *(m.getAttr("X", v) for v in m._vars))
    return fila, sol


# Modelo en CPLEX --------------------------------------------

def construir_modelo_cplex(inst: Instancia, relajado: bool = False):
    """Devuelve el modelo y sus variables (X, Z, W, dW, rW), con las mismas claves que en Gurobi."""
    m = ModeloCplex(name=inst.nombre)
    clientes = set(inst.C)
    s, e = inst.salida, inst.regreso

    # Relajación continua: las variables binarias se crean continuas en [0, 1].
    def binarias(claves, nombre):
        if relajado:
            return m.continuous_var_dict(claves, lb=0, ub=1, name=nombre)
        return m.binary_var_dict(claves, name=nombre)

    idx_X = [(r, i, j, t) for r in inst.R for (i, j, t) in inst.A]
    X = binarias(idx_X, "X")
    Z = binarias([(r, i) for r in inst.R for i in inst.C], "Z")
    W = m.continuous_var_dict(inst.O, lb=inst.aW, ub=inst.bW, name="W")
    dW = m.continuous_var_dict(inst.R, lb=0, ub=inst.bW, name="dW")
    rW = m.continuous_var_dict([(r, t) for r in inst.R for t in inst.D], lb=inst.aW, name="rW")

    # docplex no tiene el comodín "*" de tupledict.sum(): se arman las listas de vecinos.
    entrantes, salientes = defaultdict(list), defaultdict(list)
    for (i, j, t) in inst.A:
        entrantes[j, t].append(i)
        salientes[i, t].append(j)

    # (1)
    m.minimize(m.sum(inst.td[i, j] * X[r, i, j, t] for (r, i, j, t) in idx_X))

    M7, M9, M10 = big_m(inst)

    # (2)-(5)
    m.add_constraints((m.sum(Z[r, i] for r in inst.R) == 1, f"asignacion_{i}") for i in inst.C)
    m.add_constraints((m.sum(X[r, i, j, t] for i in entrantes[j, t]) == Z[r, j], f"entrada_{j}_{t}_{r}")
                      for (j, t) in inst.O for r in inst.R)
    m.add_constraints((m.sum(X[r, i, j, t] for j in salientes[i, t]) == Z[r, i], f"salida_{i}_{t}_{r}")
                      for (i, t) in inst.O for r in inst.R)
    m.add_constraints((m.sum(X[r, s, j, t] for j in salientes[s, t]) == 1, f"salida_deposito_{t}_{r}")
                      for t in inst.D for r in inst.R)

    # (7)
    m.add_constraints((W[i, t] + inst.st[i, t] + inst.tt[i, j] <= W[j, t] + M7[i, j, t] * (1 - X[r, i, j, t]),
                       f"tiempo_{i}_{j}_{t}_{r}")
                      for (i, j, t) in M7 if M7[i, j, t] > 0 for r in inst.R)

    # (8)
    m.add_constraints((W[i, t] >= inst.ventana[i][0], f"ventana_ini_{i}_{t}")
                      for (i, t) in inst.O if i in inst.ventana)
    m.add_constraints((W[i, t] <= inst.ventana[i][1], f"ventana_fin_{i}_{t}")
                      for (i, t) in inst.O if i in inst.ventana)

    # (9)-(10)
    m.add_constraints((dW[r] + inst.tt[s, j] <= W[j, t] + M9[j] * (1 - X[r, s, j, t]),
                       f"coord_salida_{j}_{t}_{r}")
                      for (i0, j, t) in inst.A if i0 == s and j in clientes and M9[j] > 0
                      for r in inst.R)
    m.add_constraints((W[i, t] + inst.st[i, t] + inst.tt[i, e] <= rW[r, t] + M10[i, t] * (1 - X[r, i, e, t]),
                       f"coord_regreso_{i}_{t}_{r}")
                      for (i, j0, t) in inst.A if j0 == e and i in clientes and M10[i, t] > 0
                      for r in inst.R)

    # (11)-(12)
    m.add_constraints((dW[r] >= inst.aW, f"hora_salida_{r}") for r in inst.R)
    m.add_constraints((rW[r, t] <= inst.bW, f"hora_regreso_{r}_{t}") for r in inst.R for t in inst.D)

    # (14)
    m.add_constraints((m.sum(inst.q[i, t] * Z[r, i] for i in inst.C if (i, t) in inst.q) <= inst.Q,
                       f"capacidad_{r}_{t}")
                      for r in inst.R for t in inst.D)

    return m, (X, Z, W, dW, rW)


# Códigos de estado de CPLEX (CPXMIP_* y CPX_STAT_*).
ESTADOS_CPLEX = {
    1: "óptimo", # LP óptimo
    101: "óptimo", # MIP óptimo
    102: "óptimo", # MIP óptimo dentro de la tolerancia de gap
    107: "límite de tiempo", # con solución entera
    108: "límite de tiempo", # sin solución entera
    11: "límite de tiempo", # LP
    103: "infactible",
    3: "infactible",
}


def resolver_cplex(inst: Instancia, relajado: bool, timelimit: float, threads: int, log: Path,
                   lp_file: Path = None):
    m, variables = construir_modelo_cplex(inst, relajado)
    if lp_file:
        m.export_as_lp(str(lp_file))
    m.parameters.timelimit = timelimit
    m.parameters.threads = threads
    with open(log, "w", encoding="utf-8") as f:
        sol_cplex = m.solve(log_output=f)  # log_output: CPLEX escribe su log en el archivo
    det = m.solve_details

    fila = {
        "solver": "CPLEX", "instancia": inst.nombre, "tipo": "LP" if relajado else "MIP",
        "estado": ESTADOS_CPLEX.get(det.status_code, det.status),
        "objetivo": sol_cplex.objective_value if sol_cplex else None,
        "cota": None if relajado else det.best_bound,
        "gap (%)": None if relajado or not sol_cplex else 100 * det.mip_relative_gap,
        "nodos": None if relajado else det.nb_nodes_processed,
        "tiempo (s)": det.time,
    }
    sol = None
    if sol_cplex and not relajado:
        valores = ({k: v.solution_value for k, v in d.items()} for d in variables)
        sol = armar_solucion(inst, sol_cplex.objective_value, *valores)
    m.end()
    return fila, sol


RESOLVER = {"gurobi": resolver_gurobi, "cplex": resolver_cplex}

# Solución y validación --------------------------------------------

def armar_solucion(inst: Instancia, objetivo, x, z, w, dw, rw) -> dict:
    """x, z, w, dw, rw: valor de cada variable, con las mismas claves en Gurobi y en CPLEX"""
    siguiente = {(r, i, t): j for (r, i, j, t), v in x.items() if v > 0.5}
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
        "objetivo": objetivo,
        "rutas": rutas,
        "asignacion": {i: r for (r, i), v in z.items() if v > 0.5},
        "llegada": w,
        "salida": dw,
        "regreso": rw,
    }


def validar_solucion(inst: Instancia, sol: dict, tol: float = 1e-6) -> list:
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


def graficar_rutas(inst: Instancia, sol: dict, titulo: str, archivo: Path) -> None:
    xy = lambda nodos: ([inst.coord[i][0] for i in nodos], [inst.coord[i][1] for i in nodos])
    fig, axs = plt.subplots(1, len(inst.D) + 1, figsize=(4.5 * (len(inst.D) + 1), 4.5),
                            sharex=True, sharey=True, layout="constrained")

    axs[0].scatter(*xy([i for i in inst.C if i in inst.ventana]), s=25, c="C3", label="Con ventana")
    axs[0].scatter(*xy([i for i in inst.C if i not in inst.ventana]), s=25,
                   facecolors="none", edgecolors="C3", label="Sin ventana")
    for i in inst.C:
        axs[0].annotate(i, inst.coord[i], fontsize=7, xytext=(3, 3), textcoords="offset points")
    axs[0].set_title("Instancia")

    for t, ax in zip(inst.D, axs[1:]):
        ax.scatter(*xy(inst.C), s=15, c="lightgray")
        for r in inst.R:
            camino = sol["rutas"][r, t]
            if len(camino) > 2:
                ax.plot(*xy(camino), "o-", ms=4, color=f"C{r}", label=f"Ruta {r}")
        ax.set_title(f"Día {t}")

    for ax in axs:
        ax.plot(*inst.coord[inst.salida], "ks", ms=9)
        ax.set_aspect("equal")
    leyenda = {l: h for ax in axs for h, l in zip(*ax.get_legend_handles_labels())}
    fig.legend(leyenda.values(), leyenda.keys(), loc="outside lower center", ncol=len(leyenda))
    fig.suptitle(titulo)
    fig.savefig(archivo, dpi=150)
    plt.close(fig)


# Comparación gurobi vs cplex --------------------------------------------

def formato(valor) -> str:
    if valor is None:
        return "-"
    if isinstance(valor, float):
        return f"{valor:.4f}"
    return str(valor)


def imprimir_tabla(filas: list) -> None:
    columnas = list(filas[0])
    anchos = [max(len(c), *(len(formato(f[c])) for f in filas)) for c in columnas]
    print("  ".join(c.ljust(a) for c, a in zip(columnas, anchos)))
    for f in filas:
        print("  ".join(formato(f[c]).ljust(a) for c, a in zip(columnas, anchos)))


def comparar(rutas: list, solvers: list, timelimit: float, threads: int, lp_file: bool) -> list:
    (CARPETA / "logs").mkdir(exist_ok=True)
    (CARPETA / "figuras").mkdir(exist_ok=True)
    filas = []
    for ruta in rutas:
        inst = cargar_instancia(ruta)
        print(resumen_instancia(inst))
        for solver in solvers:
            for relajado in (True, False):
                tipo = "LP" if relajado else "MIP"
                base = f"{inst.nombre}_{solver}_{tipo}"
                lp = CARPETA / "logs" / f"{base}.lp" if lp_file else None
                print(f"  {solver} {tipo}...", flush=True)
                fila, sol = RESOLVER[solver](inst, relajado, timelimit, threads,
                                             CARPETA / "logs" / f"{base}.log", lp)
                if sol is not None:
                    errores = validar_solucion(inst, sol)
                    fila["validación"] = "OK" if not errores else f"{len(errores)} errores"
                    for err in errores:
                        print("    -", err)
                    graficar_rutas(inst, sol, f"{inst.nombre} ({fila['solver']}): {fila['estado']}, "
                                   f"distancia {sol['objetivo']:.2f}", CARPETA / "figuras" / f"{base}.png")
                else:
                    fila["validación"] = "-"
                print(f"    {fila['estado']}, objetivo {formato(fila['objetivo'])}, "
                      f"{fila['tiempo (s)']:.2f} s", flush=True)
                filas.append(fila)
    return filas


def main():
    parser = argparse.ArgumentParser(description="ConVRP reducido: Gurobi vs. CPLEX")
    parser.add_argument("instancias", nargs="*",
                        default=[CARPETA / "instancias" / "inst_A.json", CARPETA / "instancias" / "inst_B.json"])
    parser.add_argument("--solver", choices=["gurobi", "cplex", "ambos"], default="ambos")
    parser.add_argument("--timelimit", type=float, default=TIMELIMIT)
    parser.add_argument("--threads", type=int, default=THREADS)
    parser.add_argument("--lp-file", action="store_true", help="escribe cada modelo en logs/*.lp")
    args = parser.parse_args()

    solvers = ["gurobi", "cplex"] if args.solver == "ambos" else [args.solver]
    filas = comparar(args.instancias, solvers, args.timelimit, args.threads, args.lp_file)

    print()
    imprimir_tabla(filas)
    with open(CARPETA / "resultados.csv", "w", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(f, fieldnames=list(filas[0]))
        escritor.writeheader()
        escritor.writerows(filas)

    # Mismo modelo: la LP debería coincidir entre solvers salvo diferencias numéricas
    if len(solvers) == 2:
        print()
        for tipo in ("LP", "MIP"):
            for nombre in dict.fromkeys(f["instancia"] for f in filas):
                g, c = (next(f for f in filas if f["solver"] == s and f["instancia"] == nombre
                             and f["tipo"] == tipo)["objetivo"] for s in ("Gurobi", "CPLEX"))
                if g is not None and c is not None:
                    print(f"{nombre} {tipo}: Gurobi {g:.6f}  CPLEX {c:.6f}  diferencia {abs(g - c):.2e}")


if __name__ == "__main__":
    main()
