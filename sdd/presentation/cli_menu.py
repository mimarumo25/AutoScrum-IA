#!/usr/bin/env python3
"""Interfaz interactiva (menu en la terminal) del comando `sdd`.

Extraida de cli.py para que ambos modulos queden por debajo del limite
de 500 lineas. Importa `cli` de forma diferida (dentro de las funciones)
porque cli.py la invoca solo desde main(): cuando se ejecuta, cli ya esta
cargado, asi que no hay ciclo de importacion.
"""
from sdd.presentation import cli


# --- Interfaz interactiva (menú en la terminal) ----------------------------

def _ns(**kw):
    return cli.argparse.Namespace(**kw)


def _clear():
    print("\033[2J\033[3J\033[H", end="") if cli.C._on else print("\n")


def _pause():
    try:
        input(cli.C.gray("\n  Enter para volver al menú… "))
    except (EOFError, KeyboardInterrupt):
        print()


def _dashboard():
    cfg = cli.config.load()
    prov = cfg.get("provider") or "anthropic"
    model = cfg.get("model") or cli.providers.default_model(prov) or "(default del proveedor)"
    keyname = cli.KEY_ENV.get(prov, "ANTHROPIC_API_KEY")
    haskey = bool(cfg["keys"].get(prov)) or bool(cli.os.environ.get(keyname))
    print("  " + cli.C.bold("SDD") + cli.C.gray("  ·  plano de control multi-agente (repo-as-state)"))
    print(cli.C.gray("  ────────────────────────────────────────────────────────────"))
    dot = cli.C.green("● listo") if haskey else cli.C.red("● falta API key")
    print(f"  proveedor {cli.C.cyan(prov)}   modelo {cli.C.cyan(model)}   {dot}")
    print(f"  salida    {cli.C.gray((cfg.get('output_base') or 'project') + '/<proyecto>/<tarea>')}")
    projs = cli.config.list_projects()
    if projs:
        print("\n  " + cli.C.bold("PROYECTOS"))
        for p in projs[:6]:
            tasks = cli.config.list_tasks(p)
            done = sum(1 for t in tasks if t.get("final") == "done")
            print(f"    {cli.C.cyan('{:<20}'.format(p))} {cli.C.gray('{} tarea(s), {} completada(s)'.format(len(tasks), done))}")
        if len(projs) > 6:
            print(cli.C.gray("    … y {} más".format(len(projs) - 6)))
    else:
        print("\n  " + cli.C.gray("(aún no hay proyectos — la opción 1 o 2 crea el primero)"))


_MENU = [
    ("1", "Correr simulación (demo · 0 tokens)"),
    ("2", "Correr proyecto real (agentes)"),
    ("3", "Ver proyectos y tareas"),
    ("4", "Configuración (proveedor · modelo · key · ruta · tema)"),
    ("5", "Doctor (verificar proveedor)"),
    ("6", "Panel web"),
    ("7", "Consola de comandos (avanzado)"),
    ("q", "Salir"),
]


def _menu_run():
    print("\n  " + cli.C.bold("NUEVA CORRIDA REAL"))
    try:
        proj = input("  proyecto: ").strip()
        if not proj:
            print(cli.C.gray("  cancelado")); return
        task = input("  tarea [tarea-1]: ").strip() or "tarea-1"
        intake = input("  archivo de idea [intake.yaml]: ").strip() or None
        autonomous = input("  modo autónomo [S/n]: ").strip().lower() not in {"n", "no"}
    except (EOFError, KeyboardInterrupt):
        print(); return
    cli.run(_ns(project=proj, workdir=None, intake=intake, task=task,
                autonomous=autonomous))


def _menu_projects():
    projs = cli.config.list_projects()
    if not projs:
        print(cli.C.gray("\n  no hay proyectos todavía.")); return
    print("\n  " + cli.C.bold("PROYECTOS"))
    for i, p in enumerate(projs, 1):
        print(f"    {cli.C.green(str(i))}. {p}")
    try:
        sel = input("\n  proyecto a abrir (Enter para volver): ").strip()
    except (EOFError, KeyboardInterrupt):
        print(); return
    if not (sel.isdigit() and 1 <= int(sel) <= len(projs)):
        return
    proj = projs[int(sel) - 1]
    tasks = cli.config.list_tasks(proj)
    if not tasks:
        print(cli.C.gray("  este proyecto no tiene tareas.")); return
    print("\n  " + cli.C.bold("TAREAS de " + proj))
    for i, t in enumerate(tasks, 1):
        st = t.get("final") or "?"
        col = {"done": cli.C.green, "escalated": cli.C.red}.get(st, cli.C.yellow)
        print(f"    {cli.C.green(str(i))}. {'{:<18}'.format(t['task'])} {col(st):<10} "
              f"{cli.C.gray('{}/{} nodos · {} llamadas'.format(t['done'], t['total'], t['calls']))}")
    try:
        sel = input("\n  tarea a ver en detalle (Enter para volver): ").strip()
    except (EOFError, KeyboardInterrupt):
        print(); return
    if sel.isdigit() and 1 <= int(sel) <= len(tasks):
        wd = cli.config.resolve_output(proj, tasks[int(sel) - 1]["task"])
        cli.show(_ns(workdir=str(wd)))


def _menu_config():
    import getpass
    cfg = cli.config.load()
    provs = list(cli.providers.MODEL_CHOICES.keys())
    print("\n  " + cli.C.bold("CONFIGURACIÓN") + cli.C.gray("   (Enter deja el valor actual)"))
    print(cli.C.gray("  proveedores: " + ", ".join(provs)))
    try:
        cur_prov = cfg.get("provider") or "anthropic"
        prov = input(f"  proveedor [{cur_prov}]: ").strip() or cur_prov
        if prov not in provs:
            print(cli.C.red("  proveedor desconocido — sin cambios")); return
        choices = cli.providers.MODEL_CHOICES.get(prov, [])
        if choices:
            print(cli.C.gray("  modelos: " + ", ".join(choices)))
        cur_model = cfg.get("model") or (choices[0] if choices else "")
        model = input(f"  modelo [{cur_model}]: ").strip() or cur_model
        cur_base = cfg.get("output_base") or "project"
        outbase = input(f"  ruta base [{cur_base}]: ").strip() or cur_base
        cur_theme = cfg.get("theme") or "auto"
        theme = input(f"  tema (auto/dark/light) [{cur_theme}]: ").strip() or cur_theme
        # La key la teclea el propio usuario, oculta; nunca se muestra ni se registra.
        key = getpass.getpass(f"  API key de {prov} (oculta · Enter para no cambiarla): ").strip()
    except (EOFError, KeyboardInterrupt):
        print(); return
    patch = {"provider": prov, "model": model, "output_base": outbase, "theme": theme}
    if key:
        patch["keys"] = {prov: key}
    cli.config.save(patch)
    print(cli.C.green("  ✓ configuración guardada en ") + cli.C.gray(str(cli.config.CONFIG_PATH)))


def _menu_action(ch):
    if ch == "1":
        cli.demo(_ns(workdir="../demo-repo")); _pause()
    elif ch == "2":
        _menu_run(); _pause()
    elif ch == "3":
        _menu_projects(); _pause()
    elif ch == "4":
        _menu_config(); _pause()
    elif ch == "5":
        cli.doctor(_ns()); _pause()
    elif ch == "6":
        print(cli.C.gray("  levantando el panel web… Ctrl+cli.C para volver al menú"))
        try:
            cli.serve(_ns(port=8770, no_open=False))
        except KeyboardInterrupt:
            print()
    elif ch == "7":
        cli.shell(cli.build_parser())
    else:
        print(cli.C.gray("  opción no reconocida")); _pause()


def home():
    while True:
        _clear()
        _dashboard()
        print("\n  " + cli.C.bold("MENÚ"))
        for k, label in _MENU:
            print(f"    {cli.C.green('[' + k + ']')} {label}")
        try:
            ch = input(cli.C.green("\n  > ")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(); break
        if ch in ("q", "quit", "exit", "salir", "0"):
            break
        _menu_action(ch)
    print(cli.C.gray("  hasta luego."))
    return 0
