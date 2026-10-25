"""Serveur local du lecteur vocal.

Sert toujours le dossier qui contient ce script — jamais le dossier courant —
et n'écoute que sur cet appareil (127.0.0.1). On peut donc le lancer d'où l'on
veut sans risquer d'exposer son dossier personnel (.ssh, .git-credentials…) :

    python ~/Ai-pythoune/lecteur_vocal/serve.py --open
"""
from __future__ import annotations

import argparse
import functools
import shutil
import subprocess
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def check_root(root: Path = ROOT) -> None:
    if not (root / "index.html").is_file():
        sys.exit(f"Erreur : {root / 'index.html'} est introuvable. "
                 "Mettez le dépôt à jour (git pull) puis relancez.")


def serve(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = False) -> None:
    check_root()
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(ROOT))
    server = ThreadingHTTPServer((host, port), handler)
    shown_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    url = f"http://{shown_host}:{port}/"
    print("Lecteur vocal")
    print(f"  Dossier servi : {ROOT}")
    print(f"  Ouvrez : {url}")
    if host not in ("127.0.0.1", "localhost"):
        print("  Attention : le lecteur est accessible depuis le réseau local.")
    print("  Ctrl+C pour arrêter.")
    if open_browser:
        opener = shutil.which("termux-open-url")
        if opener:
            subprocess.Popen([opener, url])
        else:
            import webbrowser
            webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt du lecteur vocal.")
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Serveur local du lecteur vocal.")
    parser.add_argument("--host", default="127.0.0.1",
                        help="adresse d'écoute (défaut : 127.0.0.1, cet appareil uniquement)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--open", action="store_true", help="ouvrir le navigateur au démarrage")
    args = parser.parse_args()
    serve(args.host, args.port, args.open)
