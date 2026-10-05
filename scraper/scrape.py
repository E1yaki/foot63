#!/usr/bin/env python3
"""Récupère les plateaux du foot d'animation (foot63.fff.fr), garde l'historique en JSON
et prépare les fichiers lus par le site.

Commandes (à lancer depuis n'importe où) :
  python scraper/scrape.py add <url ou site_id> [...]   ajoute / actualise des plateaux précis
  python scraper/scrape.py sync [--all] [--discover]    actualise les plateaux connus (+ nouveaux avec --discover)
  python scraper/scrape.py discover [--fal-id 9638]     trouve les équipes, lit leurs calendriers, archive tous les plateaux
  python scraper/scrape.py logos                        télécharge les logos des clubs pour toutes les équipes archivées
  python scraper/scrape.py build                        régénère data/ (catalogue, catégories, alertes)
  python scraper/scrape.py glyphs [site_id ...]         apprend les chiffres des scores (une seule fois)
  python scraper/scrape.py recon [--fal-id 9638]        analyse le formulaire animation (résumé à me transmettre)
  python scraper/scrape.py recon-cup [--cp-no 459205]   pareil, pour le module Compétitions (ex. Festival U13)

Les scores sont affichés par le site sous forme d'images de chiffres : voir la classe Glyphs.
Aucune donnée nominative n'est collectée : uniquement clubs, équipes, horaires, lieux, scores.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

VERSION = "2026-09-25"  # à comparer avec le README si un comportement semble ne pas correspondre :
                        # si le log n'affiche pas cette date, le fichier déployé n'est pas le dernier.

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))


def _loop_diag() -> str:
    """Diagnostic : le processus a-t-il déjà une boucle asyncio EN COURS D'EXÉCUTION sur ce thread au
    démarrage ? (cause du bug « Sync API inside the asyncio loop » sur certains ordonnanceurs de tâches).
    Si oui, c'est normal et sans conséquence : tout le pilotage de Chromium passe par un thread séparé
    qui n'a jamais cette boucle (voir la classe Fetcher)."""
    try:
        import asyncio
        asyncio.get_running_loop()
        return "OUI (le thread dédié du Fetcher doit compenser)"
    except RuntimeError:
        return "non"


# ----------------------------------------------------------------------------- utilitaires
def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def slug(s: str) -> str:
    return norm(s).replace(" ", "-")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def site_id_of(x: str) -> str | None:
    x = x.strip()
    if x.startswith("http"):
        return (parse_qs(urlparse(x).query).get("site_id") or [None])[0]
    return x or None


def plateau_url(sid: str) -> str:
    return f"{CONFIG['base_url']}?site_id={sid}"


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = CONFIG.get("user_agent", "foot63-resultats/1.0")
    return s


# Identifie un plateau dans le calendrier (date + bloc de texte), quel que soit le club dont on lit le calendrier.
BLOCK_KEY_JS = """el => {
  let block = '', n = el;
  for (let i = 0; i < 6 && n; i++) { n = n.parentElement; if (n && n.innerText && n.innerText.length > 40) { block = n.innerText; break; } }
  let head = '', p = el;
  while (p && !head) {
    for (let s = p.previousElementSibling; s; s = s.previousElementSibling) { if (/^H[1-6]$/.test(s.tagName)) { head = s.innerText; break; } }
    p = p.parentElement;
  }
  return (head + ' | ' + block).replace(/\\s+/g, ' ').slice(0, 300);
}"""


def run_browser_worker() -> None:
    """Sous-processus dédié à Chromium (invoqué par Fetcher via `python scrape.py _pwworker`, jamais à la
    main). Un sous-processus est une garantie plus forte qu'un thread : il ne partage RIEN avec le
    processus appelant (pas de threading, pas d'état asyncio, pas de politique de boucle d'événements) —
    ce qui met à l'abri de l'erreur Playwright « Sync API inside the asyncio loop », quelle qu'en soit la
    cause exacte chez l'appelant (observée sur certains ordonnanceurs de tâches, cause non reproduite ici).

    Protocole : une ligne JSON en entrée, une ligne JSON en sortie, en boucle, jusqu'à la commande "close".
    """
    import base64

    from playwright.sync_api import sync_playwright

    def send(obj: dict) -> None:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    pw = sync_playwright().start()
    state: dict = {"browser": None, "ctx": None, "page": None, "warmed": False}

    def ctx():
        if state["ctx"] is None:
            headless = CONFIG.get("browser_headless", True)
            attempts = ([dict(headless=True, channel="chromium"),            # « nouveau headless » (Playwright récent)
                         dict(headless=False, args=["--headless=new"]),      # même chose pour les versions plus anciennes
                         dict(headless=True)] if headless else
                        [dict(headless=False, args=["--window-position=-2400,-2400"] if CONFIG.get("browser_offscreen", True) else [])])
            last: Exception | None = None
            for kw in attempts:
                try:
                    state["browser"] = pw.chromium.launch(**kw)
                    break
                except Exception as e:  # option inconnue de cette version de Playwright
                    last = e
            if state["browser"] is None:
                raise RuntimeError(f"Chromium ne démarre pas : {last} (playwright install chromium ?)")
            state["ctx"] = state["browser"].new_context(locale="fr-FR", viewport={"width": 1280, "height": 900})
            # On ne lit que du texte : pas d'images, de médias ni de polices (pages bien plus rapides).
            state["ctx"].route("**/*", lambda route: route.abort() if route.request.resource_type in ("image", "media", "font") else route.continue_())
        return state["ctx"]

    def page():
        if state["page"] is None or state["page"].is_closed():
            state["page"] = ctx().new_page()
        return state["page"]

    def warm_up() -> None:
        if state["warmed"]:
            return
        state["warmed"] = True
        try:
            page().goto(CONFIG["base_url"], wait_until="load", timeout=45000)
            page().wait_for_timeout(1500)
        except Exception:
            pass

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            cmd = req["cmd"]
            if cmd == "close":
                send({"ok": True})
                break

            elif cmd == "text":
                if req.get("warm_up") and not state["warmed"]:
                    warm_up()
                p = page()
                resp = p.goto(req["url"], wait_until="load", timeout=45000)
                if resp is not None and resp.status in (403, 429) and not state["warmed"]:
                    warm_up()
                    resp = p.goto(req["url"], wait_until="load", timeout=45000)
                status = resp.status if resp is not None else 200
                if status >= 400:
                    send({"ok": False, "error": f"HTTP {status} (essaie \"browser_headless\": false dans config.json)"})
                    continue
                if req.get("settle_ms"):
                    p.wait_for_timeout(req["settle_ms"])  # laisse le JavaScript de la page finir d'écrire le DOM
                send({"ok": True, "content": p.content()})

            elif cmd == "bytes":
                resp = ctx().request.get(req["url"], timeout=25000, headers={"Referer": CONFIG["base_url"]})
                if not resp.ok:
                    send({"ok": False, "error": f"HTTP {resp.status} pour {req['url']}"})
                    continue
                send({"ok": True, "b64": base64.b64encode(resp.body()).decode("ascii")})

            elif cmd == "click_probe":
                try:
                    p = page()
                    p.goto(req["url"], wait_until="load", timeout=45000)
                    loc = p.get_by_text(re.compile(r"\d+\s+équipe\(s\)")).first
                    with p.expect_navigation(timeout=15000):
                        loc.click()
                    send({"ok": True, "url": p.url})
                except Exception as e:
                    send({"ok": True, "url": f"(le clic n'a pas mené à une autre page : {e})"})

            elif cmd == "click_collect":
                p = page()
                p.goto(req["url"], wait_until="load", timeout=45000)
                pattern = re.compile(r"\d+\s+équipe\(s\)")
                seen = set(req.get("seen", []))
                found: list[str] = []
                for i in range(p.get_by_text(pattern).count()):
                    try:
                        if i:
                            p.goto(req["url"], wait_until="load", timeout=45000)
                        el = p.get_by_text(pattern).nth(i)
                        key = el.evaluate(BLOCK_KEY_JS)
                        if key in seen:
                            continue
                        with p.expect_navigation(timeout=15000):
                            el.click()
                        m = re.search(r"site_id=([0-9_]+)", p.url)
                        if m:
                            found.append(m.group(1))
                            seen.add(key)
                        time.sleep(req.get("delay", 1.0))
                    except Exception as e:
                        print(f"  ! clic sur le plateau {i + 1} : {e}", file=sys.stderr)
                send({"ok": True, "found": found, "seen": list(seen)})

            else:
                send({"ok": False, "error": f"commande inconnue : {cmd}"})
        except Exception as e:
            send({"ok": False, "error": f"{type(e).__name__}: {e}"})

    try:
        if state["browser"]:
            state["browser"].close()
        pw.stop()
    except Exception:
        pass


class Fetcher:
    """Télécharge pages et images.

    Mode « auto » : `requests` d'abord ; si le site répond 401/403/429 (filtre anti-robots), on bascule pour
    toute la session sur un vrai navigateur (Playwright/Chromium), piloté dans un SOUS-PROCESSUS dédié qui
    garde un seul onglet ouvert et le réutilise (pas de fenêtres qui apparaissent et disparaissent). Modes
    forcés via config.json : "fetch_mode": "requests" | "browser" | "auto".

    Le sous-processus (voir `run_browser_worker`) est la protection contre l'erreur Playwright « Sync API
    inside the asyncio loop » : quelle qu'en soit la cause chez l'appelant (observée sur certains
    ordonnanceurs de tâches), un sous-processus tout neuf ne peut jamais l'hériter.
    """

    def __init__(self, mode: str = "auto"):
        self.mode = mode
        self.session = make_session()
        self._proc: subprocess.Popen | None = None

    def _worker(self) -> subprocess.Popen:
        if self._proc is None or self._proc.poll() is not None:
            self._proc = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "_pwworker"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr,
                text=True, encoding="utf-8", bufsize=1,
            )
        return self._proc

    def _rpc(self, **req) -> dict:
        p = self._worker()
        try:
            p.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            p.stdin.flush()
            line = p.stdout.readline()
        except (BrokenPipeError, OSError) as e:
            self._proc = None
            raise RuntimeError(f"le sous-processus Chromium a été interrompu : {e}") from e
        if not line:
            self._proc = None
            raise RuntimeError("le sous-processus Chromium s'est arrêté de façon inattendue (voir le log ci-dessus)")
        resp = json.loads(line)
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "erreur inconnue du sous-processus Chromium"))
        return resp

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.poll() is None:
                self._rpc(cmd="close")
        except Exception:
            pass
        finally:
            try:
                if self._proc and self._proc.poll() is None:
                    self._proc.terminate()
            except Exception:
                pass
            self._proc = None

    def _blocked(self, status: int) -> bool:
        if status in (401, 403, 429) and self.mode == "auto":
            print(f"  … accès refusé (HTTP {status}) : passage au navigateur Chromium", file=sys.stderr)
            self.mode = "browser"
            return True
        return False

    def force_browser(self) -> None:
        if self.mode != "requests":
            self.mode = "browser"

    def text(self, url: str, tries: int = 3, settle_ms: int = 0) -> str:
        last: Exception | None = None
        for n in range(tries):
            try:
                if self.mode != "browser":
                    r = self.session.get(url, timeout=25)
                    if not self._blocked(r.status_code):
                        r.raise_for_status()
                        r.encoding = "utf-8"
                        return r.text
                resp = self._rpc(cmd="text", url=url, warm_up=(n == 0 and CONFIG.get("browser_warm_up", True)), settle_ms=settle_ms)
                return resp["content"]
            except Exception as e:  # réseau, 5xx, navigateur…
                last = e
                time.sleep(1.5 * (n + 1))
        raise RuntimeError(f"échec de {url} : {last}")

    def click_probe(self, url: str) -> str:
        """Diagnostic : clique sur le premier plateau du calendrier et renvoie l'adresse où le site mène."""
        return self._rpc(cmd="click_probe", url=url)["url"]

    def click_collect(self, url: str, seen: set[str], delay: float = 1.0) -> list[str]:
        """Repli si les identifiants de plateau ne sont pas dans le HTML : on clique sur chaque plateau du
        calendrier, comme un visiteur, et on lit `site_id` dans l'adresse où le site nous mène.
        `seen` évite de recliquer sur un plateau déjà vu depuis le calendrier d'un autre club."""
        resp = self._rpc(cmd="click_collect", url=url, seen=list(seen), delay=delay)
        seen.clear()
        seen.update(resp["seen"])
        return resp["found"]

    def bytes(self, url: str) -> bytes:
        if self.mode != "browser":
            r = self.session.get(url, timeout=25, headers={"Referer": CONFIG["base_url"]})
            if not self._blocked(r.status_code):
                r.raise_for_status()
                return r.content
        import base64
        return base64.b64decode(self._rpc(cmd="bytes", url=url)["b64"])


# ----------------------------------------------------------------------------- chiffres des scores
W, H = 14, 20  # taille de l'empreinte d'un chiffre


class Glyphs:
    """Les scores sont des images (une par chiffre) dont le nom est un hachage.

    Le nom de fichier ne dit rien du chiffre : sur la page d'exemple, deux « 0 » ont des noms différents.
    On reconnaît donc le DESSIN (empreinte réduite de l'image), comparé à des chiffres appris une fois
    avec `scrape.py glyphs`, à la main. `names` n'est qu'un cache de la session en cours.
    """

    def __init__(self, path: Path):
        self.path = path
        d = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.names: dict[str, object] = {}   # cache de session : nom d'image -> (chiffre, empreinte)
        self.fps: dict[str, str] = d.get("fingerprints", {})
        self.unknown: dict[str, str] = {}
        self.last_fp = ""                     # empreinte de la dernière image lue ("" si non téléchargée)

    def save(self) -> None:
        self.path.write_text(
            json.dumps({"fingerprints": self.fps}, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def fingerprint(data: bytes) -> str:
        from PIL import Image  # import tardif : Pillow n'est requis que pour lire des images

        im = Image.open(io.BytesIO(data)).convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        g = bg.convert("L")
        corner = g.getpixel((0, 0))  # le fond, quelle que soit la polarité du dessin
        ink = g.point(lambda v: 255 if abs(v - corner) > 60 else 0)
        box = ink.getbbox()
        if not box:
            return "0" * (W * H)
        small = ink.crop(box).resize((W, H), Image.LANCZOS)
        raw = small.tobytes()  # équivalent de getdata() sans le futur avertissement Pillow (mode L : 1 octet/pixel)
        return "".join("1" if v > 127 else "0" for v in raw)

    @staticmethod
    def art(fp: str) -> str:
        return "\n".join("".join("#" if c == "1" else "." for c in fp[r * W:(r + 1) * W]) for r in range(H))

    def classify(self, fp: str) -> str | None:
        """Chiffre le plus proche, à condition qu'il soit nettement plus proche que tous les autres."""
        best: dict[str, int] = {}
        for k, digit in self.fps.items():
            dist = sum(a != b for a, b in zip(k, fp))
            if digit not in best or dist < best[digit]:
                best[digit] = dist
        ranked = sorted((d, dg) for dg, d in best.items())
        if not ranked or ranked[0][0] > 0.25 * len(fp):
            return None
        if len(ranked) > 1 and ranked[1][0] - ranked[0][0] < 15:
            return None  # ambigu : mieux vaut un score « illisible » qu'un faux score
        return ranked[0][1]

    def read(self, fetcher: Fetcher | None, url: str) -> str | None:
        name = Path(urlparse(url).path).stem
        self.last_fp = ""
        if name in self.names:
            v = self.names[name]
            d, self.last_fp = v if isinstance(v, tuple) else (v, "")
            return d
        if fetcher is None:
            self.unknown[name] = url
            return None
        try:
            fp = self.fingerprint(fetcher.bytes(url))
        except Exception:  # image illisible, Pillow absent…
            self.unknown[name] = url
            return None
        self.last_fp = fp
        digit = self.classify(fp)
        if digit is None:
            self.unknown[name] = url
            return None
        self.names[name] = (digit, fp)
        return digit


# ----------------------------------------------------------------------------- analyse d'une page de plateau
MONTHS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
          "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12}
DATE_RE = re.compile(r"(\d{1,2})\s+([A-Za-zéèêûôîàç]+)\s+(\d{4})\s*[-–—]\s*(\d{1,2})\s*h\s*(\d{2})?", re.I)
STOP_RE = re.compile(r"^(compétitions?|competitions?|sélectionnez|selectionnez)\b", re.I)


class ParseError(Exception):
    pass


def tokenize(soup: BeautifulSoup) -> tuple[list[tuple[str, str]], list]:
    """Aplatit la page en une suite ordonnée : ('t', texte) ou ('i', url d'image).
    Renvoie aussi la liste parallèle des nœuds HTML d'origine."""
    toks: list[tuple[str, str]] = []
    nodes: list = []
    for el in soup.descendants:
        if isinstance(el, NavigableString):
            if el.parent and el.parent.name in ("script", "style", "noscript", "template"):
                continue
            if type(el).__name__ in ("Comment", "Doctype", "CData"):
                continue
            t = " ".join(str(el).split())
            if t:
                toks.append(("t", t))
                nodes.append(el)
        elif isinstance(el, Tag) and el.name == "img":
            toks.append(("i", el.get("src") or el.get("data-src") or ""))
            nodes.append(el)
    return toks, nodes


def logo_near(node, name: str) -> str | None:
    """Logo d'une équipe : la première image (hors chiffres de score) du plus petit conteneur
    qui ne contient que ce nom d'équipe, qu'elle soit placée avant ou après le nom."""
    el = node.parent
    for _ in range(4):
        if el is None or not isinstance(el, Tag):
            return None
        if " ".join(el.get_text(" ").split()) != name:
            if el is not node.parent:
                return None
        for img in el.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""
            if src and "/scores/" not in src and "here-blue" not in src:
                return src
        el = el.parent
    return None


def category_for(site_id: str, label: str) -> str | None:
    fal = site_id.split("_")[0]
    for c in CONFIG["categories"]:
        if str(c["fal_id"]) == fal:
            return c["id"]
    for c in CONFIG["categories"]:
        if norm(c["label"]) == norm(label):
            return c["id"]
    return None


def parse_mini(cells: list[str], teams: set[str]) -> dict[str, dict[str, int]]:
    """Mini-classement du plateau : « # | Équipe | Pts | J | G | N | P | D | BP | BC | Dif »."""
    try:
        n = next(i for i, c in enumerate(cells) if norm(c) == "dif") + 1
    except StopIteration:
        return {}
    head = [norm(c) for c in cells[:n]]
    if "equipe" not in head or "bp" not in head or "bc" not in head or "j" not in head:
        return {}
    out: dict[str, dict[str, int]] = {}
    body = cells[n:]
    for i in range(0, len(body) - n + 1, n):
        row = dict(zip(head, body[i:i + n]))
        team = next((c for c in body[i:i + n] if c in teams), None)
        try:
            if team:
                out[team] = {k: int(row[k]) for k in ("pts", "j", "bp", "bc")}
        except (KeyError, ValueError):
            continue
    return out


def scores_consistent(matchs: list[dict], mini: dict[str, dict[str, int]]) -> bool:
    """Buts pour / contre et nombre de matchs de chaque équipe, recalculés depuis les scores lus,
    doivent égaler le classement affiché sur la page du plateau (contrôle contre une erreur de lecture d'un chiffre)."""
    played = [m for m in matchs if m["hs"] is not None and m["as"] is not None]
    if not played:
        return True
    tot = {t: [0, 0, 0] for t in mini}
    for m in played:
        for team, gf, ga in ((m["home"], m["hs"], m["as"]), (m["away"], m["as"], m["hs"])):
            if team in tot:
                tot[team][0] += gf
                tot[team][1] += ga
                tot[team][2] += 1
    return all(tot[t] == [r["bp"], r["bc"], r["j"]] for t, r in mini.items())


def parse_plateau(html: str, site_id: str, glyphs: Glyphs, fetcher: Fetcher | None = None) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    toks, nodes = tokenize(soup)
    base = plateau_url(site_id)

    i0 = next((i for i, (k, v) in enumerate(toks) if k == "t" and DATE_RE.search(v)), None)
    if i0 is None:
        raise ParseError("date du plateau introuvable : page inattendue")
    toks, nodes = toks[i0:], nodes[i0:]
    end = next((i for i, (k, v) in enumerate(toks) if k == "t" and STOP_RE.match(v)), len(toks))
    toks, nodes = toks[:end], nodes[:end]

    def idx(label: str) -> int | None:
        return next((i for i, (k, v) in enumerate(toks) if k == "t" and norm(v) == norm(label)), None)

    i_org, i_info, i_eq, i_m = idx("Club organisateur"), idx("Informations générales"), idx("équipes"), idx("Matchs")
    i_cl = idx("Classement")  # mini-classement du plateau : sert à vérifier les scores lus
    if i_eq is None:
        raise ParseError("section « équipes » introuvable")
    stop_eq = i_m if i_m is not None else len(toks)

    m = DATE_RE.search(toks[0][1])
    month = MONTHS.get(norm(m.group(2)))
    if not month:
        raise ParseError(f"mois inconnu : {m.group(2)}")
    start = datetime(int(m.group(3)), month, int(m.group(1)), int(m.group(4)), int(m.group(5) or 0))

    first_meta = i_org if i_org is not None else (i_info if i_info is not None else i_eq)
    lieu = " ".join(v for k, v in toks[1:first_meta] if k == "t")
    organisateur = ""
    if i_org is not None:
        after = [v for k, v in toks[i_org + 1:(i_info if i_info is not None else i_eq)] if k == "t"]
        organisateur = after[0] if after else ""

    info: dict[str, str] = {}
    if i_info is not None:
        vals = [v for k, v in toks[i_info + 1:i_eq] if k == "t"]
        h = len(vals) // 2
        info = {norm(a): b for a, b in zip(vals[:h], vals[h:2 * h])}
    label, phase_label = (info.get("epreuve phase", "") + " / ").split("/")[:2]
    label, phase_label = label.strip(), phase_label.strip()
    pm = re.search(r"\d+", phase_label)
    poule_label = info.get("poule", "").strip()
    poule_id = "U" if not poule_label or "unique" in norm(poule_label) else poule_label.split()[-1].upper()

    equipes: list[str] = []
    logos: dict[str, str] = {}
    for n in range(i_eq + 1, stop_eq):
        k, v = toks[n]
        if k != "t":
            continue
        equipes.append(v)
        src = logo_near(nodes[n], v)
        if src:
            logos[v] = urljoin(base, src)

    matchs: list[dict] = []
    pending: list[dict] = []   # empreintes des chiffres de chaque match : permet de relire les scores hors ligne
    unknown = False
    if i_m is not None:
        names = set(equipes)
        home = None
        side, left, right, lfp, rfp = "L", [], [], [], []

        def number(digits: list[str | None]) -> int | None:
            if not digits or any(d is None for d in digits):
                return None
            return int("".join(digits))  # type: ignore[arg-type]

        stop_m = i_cl if (i_cl is not None and i_cl > i_m) else len(toks)
        for k, v in toks[i_m + 1:stop_m]:
            if k == "t" and v in names:
                if home is None:
                    home, side, left, right, lfp, rfp = v, "L", [], [], [], []
                else:
                    hs, as_ = number(left), number(right)
                    if (left and hs is None) or (right and as_ is None):
                        unknown = True
                    matchs.append({"home": home, "away": v, "hs": hs, "as": as_})
                    pending.append({"l": lfp, "r": rfp})
                    home = None
            elif home is not None and k == "t" and v in ("-", "–", "—", ":"):
                side = "R"
            elif home is not None and k == "t" and v.isdigit():
                (left if side == "L" else right).append(v)
                (lfp if side == "L" else rfp).append("=" + v)
            elif home is not None and k == "i" and "/scores/" in v:
                d = glyphs.read(fetcher, urljoin(base, v))
                (left if side == "L" else right).append(d)
                (lfp if side == "L" else rfp).append(glyphs.last_fp)

    incoherent = False
    mini = parse_mini([v for k, v in toks[i_cl + 1:] if k == "t"], set(equipes)) if (matchs and i_cl is not None) else {}
    if matchs and mini and not unknown:
        if not scores_consistent(matchs, mini):
            for m in matchs:  # mieux vaut pas de score qu'un faux score
                m["hs"] = m["as"] = None
            incoherent = True

    return {
        "id": site_id,
        "cat": category_for(site_id, label),
        "cat_label": label,
        "phase": int(pm.group()) if pm else 1,
        "phase_label": phase_label or "Phase 1",
        "poule": poule_id,
        "poule_label": poule_label or "Poule unique",
        "start": start.strftime("%Y-%m-%dT%H:%M"),
        "lieu": lieu,
        "organisateur": organisateur,
        "equipes": equipes,
        "logos": logos,
        "matchs": matchs,
        "score_illisible": unknown,
        "score_incoherent": incoherent,
        "glyph_fps": pending if (unknown or incoherent) else [],   # nettoyé dès que les scores sont lus et cohérents
        "mini": mini if (unknown or incoherent) else {},
    }


# ----------------------------------------------------------------------------- logos des clubs
LOGO_DIR = DATA / "logos"


def is_image(data: bytes) -> bool:
    return data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n" or data[:4] == b"GIF8" or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")


def store_logos(fetcher: Fetcher, logos: dict[str, str]) -> dict[str, str]:
    """Télécharge chaque logo une seule fois dans data/logos/ et renvoie {équipe: chemin relatif à data/}.
    Un logo qui ne se télécharge pas reste en URL distante (le site retombe sur les initiales s'il échoue)."""
    out: dict[str, str] = {}
    for team, url in logos.items():
        ext = Path(urlparse(url).path).suffix.lower()
        if url.startswith("data:"):  # image de remplissage d'un chargement différé : pas un logo
            continue
        if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
            out[team] = url
            continue
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(urlparse(url).path).name)
        dest = LOGO_DIR / name
        if not dest.exists():
            try:
                data = fetcher.bytes(url)
                if not is_image(data):
                    raise ValueError("la réponse n'est pas une image")
                LOGO_DIR.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
            except Exception as e:  # réseau, 404, page d'erreur…
                print(f"  ! logo {url} : {e}", file=sys.stderr)
                out[team] = url
                continue
        out[team] = f"logos/{name}"
    return out


# ----------------------------------------------------------------------------- relecture des scores hors ligne
def reresolve(rec: dict, glyphs: Glyphs) -> bool:
    """Relit les scores d'un plateau archivé à partir des empreintes conservées, sans aucune requête :
    utile après avoir appris de nouveaux chiffres. Renvoie True si le plateau a changé."""
    pend = rec.get("glyph_fps")
    if not pend or len(pend) != len(rec["matchs"]):
        return False

    def number(tokens: list[str]) -> int | None:
        digits = []
        for t in tokens:
            d = t[1:] if t.startswith("=") else (glyphs.classify(t) if t else None)
            if d is None:
                return None
            digits.append(d)
        return int("".join(digits)) if digits else None

    before = json.dumps([rec["matchs"], rec.get("score_illisible"), rec.get("score_incoherent")])
    matchs, unresolved = [dict(m) for m in rec["matchs"]], False
    for m, p in zip(matchs, pend):
        hs, as_ = number(p["l"]), number(p["r"])
        if (p["l"] and hs is None) or (p["r"] and as_ is None):
            unresolved = True
        if hs is not None:
            m["hs"] = hs
        if as_ is not None:
            m["as"] = as_
    incoherent = False
    if not unresolved and rec.get("mini") and not scores_consistent(matchs, rec["mini"]):
        for m in matchs:
            m["hs"] = m["as"] = None
        incoherent = True
    rec.update(matchs=matchs, score_illisible=unresolved, score_incoherent=incoherent)
    if not unresolved and not incoherent:
        rec["glyph_fps"], rec["mini"] = [], {}
    return json.dumps([rec["matchs"], rec["score_illisible"], rec["score_incoherent"]]) != before or not (rec["score_illisible"] or rec["score_incoherent"])


def reresolve_all(glyphs: Glyphs) -> tuple[int, int]:
    """(plateaux désormais lus, plateaux encore incomplets) parmi ceux qui avaient des chiffres à lire."""
    done = left = 0
    for p in sorted(RAW.glob("*.json")) if RAW.exists() else []:
        rec = json.loads(p.read_text(encoding="utf-8"))
        if not rec.get("glyph_fps"):
            continue
        if reresolve(rec, glyphs):
            save_raw(rec)
        if rec["score_illisible"] or rec["score_incoherent"]:
            left += 1
        else:
            done += 1
    return done, left


# ----------------------------------------------------------------------------- historique et détection des changements
def load_raw(sid: str) -> dict | None:
    p = RAW / f"{sid}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save_raw(rec: dict) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / f"{rec['id']}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def merge(old: dict | None, new: dict, ts: str) -> tuple[dict, list[dict]]:
    """Fusionne une nouvelle lecture avec l'historique. On ne perd jamais un score déjà connu."""
    base = {"cat": new["cat"], "phase": new["phase"], "poule": new["poule"]}

    def change(kind: str, teams: list[str], **extra) -> dict:
        return {"type": kind, "teams": teams, "plateau": new["id"], **base, **extra}

    if old is None:
        rec = {**new, "first_seen": ts, "last_seen": ts, "last_change": ts,
               "history": [{"ts": ts, "start": new["start"], "lieu": new["lieu"], "matchs": new["matchs"]}]}
        return rec, [change("nouveau", new["equipes"], text=f"Nouveau plateau le {new['start'].replace('T', ' à ')}")]

    known = {(m["home"], m["away"]): m for m in old["matchs"]}
    matchs = []
    for m in new["matchs"]:
        o = known.get((m["home"], m["away"]))
        if o and m["hs"] is None and m["as"] is None and o["hs"] is not None:
            m = dict(o)  # la page ne l'affiche plus : on garde l'ancien score
        matchs.append(m)

    changes: list[dict] = []
    if old["start"] != new["start"] or old["lieu"] != new["lieu"]:
        changes.append(change("report", new["equipes"], text=f"Plateau modifié : {new['start'].replace('T', ' à ')}"))
    for m in matchs:
        o = known.get((m["home"], m["away"]))
        if m["hs"] is not None and (o is None or (o["hs"], o["as"]) != (m["hs"], m["as"])):
            changes.append(change("score", [m["home"], m["away"]], match=dict(m)))

    rec = {**old, **new, "matchs": matchs, "first_seen": old["first_seen"], "last_seen": ts,
           "history": old["history"]}
    rec["logos"] = {**old.get("logos", {}), **new.get("logos", {})}
    if changes:
        rec["last_change"] = ts
        rec["history"] = old["history"] + [{"ts": ts, "start": new["start"], "lieu": new["lieu"], "matchs": matchs}]
    else:
        rec["last_change"] = old["last_change"]
    return rec, changes


def paris_now() -> datetime:
    """Heure locale du district (les horaires des plateaux sont donnés à l'heure de Paris), sans fuseau."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Paris")).replace(tzinfo=None)
    except Exception:
        return datetime.now()


# ----------------------------------------------------------------------------- module Compétitions
# (Festival U13, coupes…) : une vraie API JSON, structurellement différente des pages plateau de
# football-animation-et-loisirs mais assez proche dans les grandes lignes (poules, journées, terrain
# souvent partagé par les matchs d'une même journée) pour être ramenée au même format « plateau ».
CUP_API_BASE = "https://api-dofa.fff.fr/api"
CUP_ID_RE = re.compile(r"^cup(\d+)_(\d+)_(\d+)_\d+$")


def cup_poule_key(sid: str) -> tuple[int, int, int] | None:
    m = CUP_ID_RE.match(sid)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def fetch_cup_json(session: requests.Session, url: str) -> dict:
    r = session.get(url, timeout=25, headers={"Accept": "application/ld+json"})
    r.raise_for_status()
    return r.json()


# Noms officiels des clubs : les mêmes que sur les pages animation (« A. S. CLERMONT SAINT JACQUES FOOT »),
# pour qu'une équipe porte EXACTEMENT le même nom dans toutes les catégories (recherche, favoris…).
# ATTENTION : la fiche club de l'API contient aussi des données personnelles (contacts des dirigeants) :
# on n'en lit que le nom, et rien d'autre n'est conservé.
CLUB_NAMES_FILE = HERE / "club_names.json"
_CLUB_NAMES: dict[int, str] | None = None
_CLUB_FAILED: set[int] = set()


def club_names() -> dict[int, str]:
    global _CLUB_NAMES
    if _CLUB_NAMES is None:
        names: dict[int, str] = {}
        tp = HERE / "teams.json"
        if tp.exists():  # noms déjà connus grâce à `discover`
            for lst in json.loads(tp.read_text(encoding="utf-8")).values():
                for t in lst:
                    if t.get("clNo") and t.get("name"):
                        names[int(t["clNo"])] = t["name"]
        if CLUB_NAMES_FILE.exists():
            names.update({int(k): v for k, v in json.loads(CLUB_NAMES_FILE.read_text(encoding="utf-8")).items()})
        _CLUB_NAMES = names
    return _CLUB_NAMES


def ensure_club_names(session: requests.Session, calendrier: dict) -> None:
    """Complète les noms officiels des clubs présents dans un calendrier (un appel par club inconnu)."""
    names = club_names()
    added = False
    for m in calendrier.get("hydra:member", []):
        for side in ("home", "away"):
            no = ((m.get(side) or {}).get("club") or {}).get("cl_no")
            if not no or no in names or no in _CLUB_FAILED:
                continue
            try:
                name = fetch_cup_json(session, f"{CUP_API_BASE}/clubs/{no}").get("name")
            except Exception as e:
                print(f"  ! nom du club {no} : {e}", file=sys.stderr)
                _CLUB_FAILED.add(no)
                continue
            if isinstance(name, str) and name.strip():
                names[no] = name.strip()
                added = True
            else:
                _CLUB_FAILED.add(no)
            time.sleep(CONFIG.get("cup_delay_seconds", 0.3))
    if added:
        CLUB_NAMES_FILE.write_text(json.dumps({str(k): v for k, v in sorted(names.items())}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def parse_cup_time(raw: str | None) -> str | None:
    """« 14:30 », « 14:30:00 », « 14h30 », « 14H », « 1430 » -> « 14:30 » ; tout le reste -> None (horaire inconnu)."""
    m = re.match(r"^\s*(\d{1,2})\s*(?:[:hH]\s*(\d{2})?)?\s*(?::\s*\d{2})?\s*$", raw or "") or re.match(r"^\s*(\d{2})(\d{2})\s*$", raw or "")
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    return f"{h:02d}:{mi:02d}" if h < 24 and mi < 60 else None


def parse_cup_match(m: dict, names: dict[int, str] | None = None) -> dict:
    names = names if names is not None else club_names()

    def team_name(side: str) -> str:
        t = m.get(side) or {}
        base = names.get(((t.get("club") or {}).get("cl_no"))) or t.get("short_name_federation") or t.get("short_name") or "?"
        code = t.get("code")  # numéro d'équipe du club : toujours affiché, comme sur les pages animation (« … 1 »)
        return f"{base} {code}" if code else base

    def logo(side: str) -> str | None:
        return ((m.get(side) or {}).get("club") or {}).get("logo") or None

    hs, as_ = m.get("home_score"), m.get("away_score")
    return {
        "home": team_name("home"), "away": team_name("away"),
        "hs": hs if isinstance(hs, int) else None, "as": as_ if isinstance(as_, int) else None,
        "home_logo": logo("home"), "away_logo": logo("away"),
    }


def cup_raw_records(cp_no: int, cat_id: str, phase_number: int, phase_label: str,
                     stage_number: int, poule_label: str, calendrier: dict,
                     names: dict[int, str] | None = None) -> list[dict]:
    """Le calendrier d'une poule (module Compétitions) -> nos plateaux habituels, un par journée (les
    matchs d'une même journée y partagent en général le même terrain, comme un plateau)."""
    by_j: dict[int, list[dict]] = {}
    for m in calendrier.get("hydra:member", []):
        j = (m.get("poule_journee") or {}).get("number") or 0
        by_j.setdefault(j, []).append(m)
    out = []
    for j, ms in sorted(by_j.items()):
        parsed = [parse_cup_match(m, names) for m in ms]
        equipes = sorted({p["home"] for p in parsed} | {p["away"] for p in parsed})
        logos = {}
        for p in parsed:
            if p["home_logo"]:
                logos[p["home"]] = p["home_logo"]
            if p["away_logo"]:
                logos[p["away"]] = p["away_logo"]
        terrain = next((m.get("terrain") for m in ms if m.get("terrain")), None) or {}
        lieu = ", ".join(x for x in [terrain.get("name"), terrain.get("address"), terrain.get("city")] if x)
        dates = sorted(d for d in ((m.get("date") or "")[:10] for m in ms) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d))
        heures = sorted(h for h in (parse_cup_time(m.get("time")) for m in ms) if h)
        date = dates[0] if dates else ""
        out.append({
            "id": f"cup{cp_no}_{phase_number}_{stage_number}_{j}",
            "cat": cat_id, "cat_label": None, "phase": phase_number, "phase_label": phase_label,
            "poule": str(stage_number), "poule_label": poule_label,
            "start": f"{date}T{heures[0] if heures else '00:00'}" if date else "1970-01-01T00:00",
            "heure_connue": bool(heures) and bool(date),
            "lieu": lieu, "organisateur": "",
            "equipes": equipes, "logos": logos,
            "matchs": [{"home": p["home"], "away": p["away"], "hs": p["hs"], "as": p["as"]} for p in parsed],
            "score_illisible": False, "score_incoherent": False, "glyph_fps": [], "mini": {},
        })
    return out


def refresh_cup_poule(session: requests.Session, cat: dict, meta: dict, cp_no: int,
                       phase_number: int, stage_number: int, ts: str) -> list[dict]:
    ph = next((p for p in meta.get("phases", []) if p.get("number") == phase_number), None)
    gp = next((g for g in (ph or {}).get("groups", []) if g.get("stage_number") == stage_number), None)
    phase_label = (ph or {}).get("name") or f"Phase {phase_number}"
    poule_label = (gp or {}).get("name") or f"Poule {stage_number}"
    cal = fetch_cup_json(session, f"{CUP_API_BASE}/compets/{cp_no}/phases/{phase_number}/poules/{stage_number}/calendrier?page=1")
    ensure_club_names(session, cal)
    changes = []
    for rec in cup_raw_records(cp_no, cat["id"], phase_number, phase_label, stage_number, poule_label, cal):
        old = load_raw(rec["id"])
        merged, ch = merge(old, rec, ts)
        save_raw(merged)
        changes += ch
        j = rec["id"].rsplit("_", 1)[-1]
        print(f"  {'+' if ch else '='} {rec['id']}  {cat['label']} {poule_label} journée {j}  {rec['start']}")
    return changes


def sync_cup_category(session: requests.Session, cat: dict, ts: str) -> list[dict]:
    """Un passage complet sur une compétition du module Compétitions : liste ses poules puis relit le
    calendrier de chacune. Des appels JSON légers (pas de Chromium) : on les relit à chaque synchronisation
    plutôt que d'essayer de deviner lesquels ont changé."""
    cp_no = cat["cp_no"]
    meta = fetch_cup_json(session, f"{CUP_API_BASE}/compets/{cp_no}")
    poules = [(ph.get("number"), gp.get("stage_number")) for ph in meta.get("phases", []) for gp in ph.get("groups", [])]
    print(f"  {cat['label']} : {len(poules)} poule(s) sur {len(meta.get('phases', []))} phase(s)")
    changes = []
    for phase_number, stage_number in poules:
        try:
            changes += refresh_cup_poule(session, cat, meta, cp_no, phase_number, stage_number, ts)
        except Exception as e:
            print(f"  ! {cat['id']} phase {phase_number} poule {stage_number} : {e}", file=sys.stderr)
        time.sleep(CONFIG.get("cup_delay_seconds", 0.3))
    return changes


def refresh_reason(rec: dict | None, upcoming: bool = False, now: datetime | None = None) -> str | None:
    """Pourquoi relire ce plateau ? None = inutile. On ne relit ni les plateaux dont tous les scores sont connus,
    ni ceux qui ne sont pas encore joués (sauf `upcoming`, pour repérer un changement d'horaire)."""
    if rec is None:
        return "nouveau"
    now = now or paris_now()
    start = datetime.strptime(rec["start"], "%Y-%m-%dT%H:%M")
    if start > now:
        return "à venir (option --upcoming)" if upcoming else None
    if rec.get("glyph_fps"):
        return None   # chiffres à apprendre : relire la page ne changerait rien, c'est le travail de « glyphs »
    if rec["matchs"] and all(m["hs"] is not None and m["as"] is not None for m in rec["matchs"]):
        return None   # scores complets
    if (now - start).days > CONFIG.get("rescan_window_days", 14):
        return None   # plateau abandonné (annulé, forfait…) : on n'insiste plus, sauf avec --all
    return "joué, scores incomplets" if rec["matchs"] else "joué, pas encore de scores"


def should_refresh(rec: dict | None, upcoming: bool = False, now: datetime | None = None) -> bool:
    return refresh_reason(rec, upcoming, now) is not None


# ----------------------------------------------------------------------------- commandes
def read_seeds() -> list[str]:
    p = HERE / "seeds.txt"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        sid = site_id_of(line) if line else None
        if sid:
            out.append(sid)
    return out


def with_club_logos(equipes: list[str], logos: dict[str, str]) -> dict[str, str]:
    """Logos trouvés dans la page, complétés par le logo du club (même code que dans la liste des clubs)."""
    out = dict(logos)
    for team in equipes:
        if team not in out:
            url = club_logo_url(team)
            if url:
                out[team] = url
    return out


def refresh(ids: list[str], notify: bool, quiet: bool = False, include_cup: bool = False) -> list[dict]:
    glyphs = Glyphs(HERE / "glyphs.json")
    fetcher = Fetcher(CONFIG.get("fetch_mode", "auto"))
    first_run = not any(RAW.glob("*.json")) if RAW.exists() else True
    ts, all_changes = now_iso(), []
    if include_cup:
        for cat in CONFIG["categories"]:
            if cat.get("kind") != "cup":
                continue
            try:
                all_changes += sync_cup_category(fetcher.session, cat, ts)
            except Exception as e:
                print(f"  ! {cat['id']} : {e}", file=sys.stderr)
    for n, sid in enumerate(ids):
        if cup_poule_key(sid):
            continue  # module Compétitions : déjà traité ci-dessus, poule entière à la fois
        try:
            new = parse_plateau(fetcher.text(plateau_url(sid)), sid, glyphs, fetcher)
        except (ParseError, RuntimeError) as e:
            print(f"  ! {sid} : {e}", file=sys.stderr)
            continue
        new["logos"] = store_logos(fetcher, with_club_logos(new["equipes"], new["logos"]))
        if new["cat"] is None:
            print(f"  ! {sid} : catégorie inconnue ({new['cat_label']})", file=sys.stderr)
            continue
        rec, changes = merge(load_raw(sid), new, ts)
        save_raw(rec)
        all_changes += changes
        print(f"  {'+' if changes else '='} {sid}  {new['cat_label']} {new['poule_label']}  {new['start']}")
        if new["score_illisible"]:
            print("    chiffres non reconnus : lancer « scrape.py glyphs » (voir README)", file=sys.stderr)
        if new.get("score_incoherent"):
            print("    scores en désaccord avec le classement du plateau (chiffre mal lu ?) : non publiés ; relancer « scrape.py glyphs »", file=sys.stderr)
        time.sleep(CONFIG.get("delay_seconds", 0.7))
    fetcher.close()
    glyphs.save()
    if first_run or quiet or len(all_changes) > 30:  # import massif : on n'inonde pas les alertes
        if len(all_changes) > 30:
            print(f"  ({len(all_changes)} changements d'un coup : import massif, alertes ignorées)")
        return []
    record_changes(all_changes, ts)
    if notify:
        send_ntfy(all_changes)
    return all_changes


def record_changes(changes: list[dict], ts: str) -> None:
    if not changes:
        return
    p = DATA / "changes.json"
    old = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    fresh = [{"id": f"{ts}-{c['plateau']}-{i}", "ts": ts, **{k: v for k, v in c.items() if k != "plateau"}}
             for i, c in enumerate(changes)]
    DATA.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps((fresh + old)[:300], ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def pretty(name: str) -> str:
    return re.sub(r"(^|[\s\-'’])([a-zà-ÿ])", lambda m: m.group(1) + m.group(2).upper(), name.lower())


def send_ntfy(changes: list[dict]) -> None:
    cfg = CONFIG.get("ntfy")
    if not cfg or not changes:
        return
    for c in changes:
        if c.get("match"):
            m = c["match"]
            body = f"{pretty(m['home'])} {m['hs']}–{m['as']} {pretty(m['away'])}"
        else:
            body = c.get("text", "Mise à jour")
        for team in dict.fromkeys(c.get("teams", [])):
            topic = f"{cfg['prefix']}-{slug(team)}"
            try:
                requests.post(f"{cfg['server']}/{topic}", data=body.encode("utf-8"),
                              headers={"Title": "Foot animation 63", "Tags": "soccer"}, timeout=15)
            except requests.RequestException as e:
                print(f"  ! ntfy {topic} : {e}", file=sys.stderr)


def write_if_changed(path: Path, obj, ignore: tuple[str, ...] = ()) -> bool:
    text = json.dumps(obj, ensure_ascii=False, indent=1) + "\n"
    if path.exists():
        try:
            cur = json.loads(path.read_text(encoding="utf-8"))
            a = {k: v for k, v in cur.items() if k not in ignore} if isinstance(cur, dict) else cur
            b = {k: v for k, v in obj.items() if k not in ignore} if isinstance(obj, dict) else obj
            if a == b:
                return False
        except json.JSONDecodeError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def natural_key(s: str) -> list:
    """Tri « naturel » d'un identifiant de poule : 2 avant 10, A avant B, quel que soit le mélange."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def cmd_build(_args=None) -> None:
    reresolve_all(Glyphs(HERE / "glyphs.json"))   # prend en compte les chiffres appris depuis la dernière fois
    recs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(RAW.glob("*.json"))] if RAW.exists() else []
    by_cat = defaultdict(list)
    for r in recs:
        by_cat[r["cat"]].append(r)
    season = CONFIG["season"]
    state_path = DATA / ".state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    now = now_iso()
    catalog = {"season": season, "checked_at": now, "updated_at": state.get("updated_at", now), "ntfy": CONFIG.get("ntfy"), "categories": []}
    any_changed = False
    for c in CONFIG["categories"]:
        rs = by_cat.get(c["id"])
        if not rs:
            continue
        phases: dict[int, dict] = {}
        logos: dict[str, str] = {}
        for r in rs:
            logos.update(r.get("logos", {}))
            ph = phases.setdefault(r["phase"], {"n": r["phase"], "label": r["phase_label"], "poules": {}})
            po = ph["poules"].setdefault(r["poule"], {"id": r["poule"], "label": r["poule_label"], "teams": set(), "plateaux": []})
            po["teams"].update(r["equipes"])
            po["plateaux"].append({
                **{k: r[k] for k in ("id", "start", "lieu", "organisateur", "equipes", "matchs")},
                "heure_connue": r.get("heure_connue", True),
            })
        doc = {"id": c["id"], "label": c["label"], "group": c["group"], "fal_id": c.get("fal_id"), "kind": c.get("kind"), "season": season,
               "logos": dict(sorted(logos.items())), "phases": []}
        for n in sorted(phases):
            ph = phases[n]
            poules = []
            for pid in sorted(ph["poules"], key=natural_key):
                po = ph["poules"][pid]
                po["teams"] = sorted(po["teams"])
                po["plateaux"].sort(key=lambda p: p["start"])
                poules.append(po)
            doc["phases"].append({"n": n, "label": ph["label"], "poules": poules})
        file = f"{season}/{c['id']}.json"
        if write_if_changed(DATA / file, doc):
            any_changed = True
        catalog["categories"].append({"id": c["id"], "label": c["label"], "group": c["group"], "file": file})
    if not catalog["categories"]:
        print("Aucun plateau archivé : rien à publier (data/ non modifié)")
        return
    if any_changed or not state_path.exists():
        catalog["updated_at"] = now
    state_path.write_text(json.dumps({"updated_at": catalog["updated_at"]}, indent=1) + "\n", encoding="utf-8")
    # catalog.json est toujours réécrit (checked_at avance à chaque passage), même sans autre changement
    (DATA / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"data/ vérifié : {len(recs)} plateaux, {len(catalog['categories'])} catégories"
          + (" (mise à jour)" if any_changed else " (rien de nouveau)"))


# ----------------------------------------------------------------------------- découverte des plateaux
# Ce que l'on sait du site (relevé avec `recon`) : tout passe par de simples pages GET.
#   ?fal_id=9638&type=animation                            page de la catégorie (formulaire « choisir une équipe »)
#   ?fal_id=9638&type=fa&clNo=10554&clCod=525985           calendrier à venir d'une équipe
#   ...&checkDate=false                                    idem, journées passées comprises
#   ?site_id=9638_9007_15130_39905_38098_3                 détail d'un plateau
RECON = HERE / "recon"
PAIR_A = re.compile(r"clNo=(\d+)(?:&amp;|&|\\u0026)clCod=(\d+)")
PAIR_B = re.compile(r"clCod=(\d+)(?:&amp;|&|\\u0026)clNo=(\d+)")


def calendar_url(fal, cl_no, cl_cod) -> str:
    return f"{CONFIG['base_url']}?fal_id={fal}&type=fa&clNo={cl_no}&clCod={cl_cod}&checkDate=false"


def site_ids_in(html: str, fal) -> list[str]:
    """Identifiants de plateau (9638_9007_15130_39905_38098_3…) où qu'ils soient dans la page :
    lien `?site_id=`, champ de formulaire caché, attribut data-*, script."""
    return sorted(set(re.findall(rf"(?<![0-9_]){fal}(?:_[0-9]+){{4,}}(?![0-9_])", html)))


def find_team_pairs(html: str) -> list[tuple[str, str]]:
    """Couples (clNo, clCod) visibles dans la page : liens, attributs data-*."""
    pairs = set(PAIR_A.findall(html)) | {(b, a) for a, b in PAIR_B.findall(html)}
    for el in BeautifulSoup(html, "html.parser").find_all(True):
        attrs = {k.lower().replace("-", "").replace("_", ""): (" ".join(v) if isinstance(v, list) else str(v))
                 for k, v in el.attrs.items()}
        no = attrs.get("dataclno") or attrs.get("clno")
        cod = attrs.get("dataclcod") or attrs.get("clcod")
        if no and cod and no.isdigit() and cod.isdigit():
            pairs.add((no, cod))
    return sorted(pairs)


def find_clubs(html: str) -> list[dict]:
    """Liste déroulante des clubs : <option value="?fal_id=…&type=fa&clNo=…&clCod=…">NOM DU CLUB</option>."""
    out, seen = [], set()
    for opt in BeautifulSoup(html, "html.parser").find_all("option"):
        m = PAIR_A.search(opt.get("value") or "") or None
        if not m:
            continue
        key = (m.group(1), m.group(2))
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": " ".join(opt.get_text().split()), "clNo": m.group(1), "clCod": m.group(2)})
    return out


LOGO_URL = "https://cdn-transverse.azureedge.net/phlogos/BC{cod}.jpg"  # BC + code du club (clCod), vérifié sur 5 clubs
_CLUBS: dict[str, str] | None = None


def load_clubs() -> dict[str, str]:
    """{nom normalisé du club: clCod}, d'après scraper/teams.json (écrit par `discover`)."""
    global _CLUBS
    if _CLUBS is None:
        _CLUBS = {}
        p = HERE / "teams.json"
        if p.exists():
            for lst in json.loads(p.read_text(encoding="utf-8")).values():
                for t in lst:
                    if t.get("name"):
                        _CLUBS[norm(t["name"])] = str(t["clCod"])
    return _CLUBS


def club_logo_url(team: str) -> str | None:
    """« F.C. AUBIEROIS 1 » -> club « F.C. AUBIEROIS » -> logo BC533145. Le plus long nom de club qui commence l'équipe."""
    t = norm(team)
    best = None
    for name, cod in load_clubs().items():
        if (t == name or t.startswith(name + " ")) and (best is None or len(name) > len(best[0])):
            best = (name, cod)
    return LOGO_URL.format(cod=best[1]) if best else None


def option_candidates(html: str) -> list[tuple[str, str, str]]:
    """Liste déroulante dont chaque valeur contient deux nombres (ex. « 10554|525985 ») : candidats à vérifier."""
    out = []
    for opt in BeautifulSoup(html, "html.parser").find_all("option"):
        nums = re.findall(r"\d{3,}", opt.get("value") or "")
        if len(nums) == 2:
            out.append((nums[0], nums[1], " ".join(opt.get_text().split())))
    return out


def describe(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    lines = [f"taille de la page : {len(html)} octets ; occurrences de « clNo » : {html.count('clNo')} ; « site_id » : {html.count('site_id')}"]
    for sel in soup.find_all("select"):
        opts = [f"{o.get('value')!r} = {' '.join(o.get_text().split())[:40]}" for o in sel.find_all("option")]
        lines.append(f"select id={sel.get('id')!r} name={sel.get('name')!r} : {len(opts)} options")
        lines += [f"    {o}" for o in opts[:14]] + (["    …"] if len(opts) > 14 else [])
    for f in soup.find_all("form"):
        lines.append(f"form action={f.get('action')!r} method={f.get('method')!r} champs={[i.get('name') or i.get('id') for i in f.find_all(['input', 'select', 'button'])]}")
    return "\n".join(lines)


def scan_calendars(fetcher: Fetcher, teams: dict) -> set[str]:
    sids: set[str] = set()
    debug_done = False
    seen_blocks: set[str] = set()
    total = sum(len(v) for v in teams.values())
    n = 0
    for fal, lst in teams.items():
        for t in lst:
            n += 1
            try:
                html = fetcher.text(calendar_url(fal, t["clNo"], t["clCod"]))
            except RuntimeError as e:
                print(f"  ! calendrier {t.get('name', t['clNo'])} : {e}", file=sys.stderr)
                continue
            ids = site_ids_in(html, fal)
            if not ids and "quipe(s)" in html and fetcher.mode == "browser":
                if not debug_done:
                    print("  identifiants absents du HTML : on clique sur les plateaux (plus lent)")
                ids = fetcher.click_collect(calendar_url(fal, t["clNo"], t["clCod"]), seen_blocks, CONFIG.get("delay_seconds", 1.0))
            sids |= set(ids)
            if n % 10 == 0 or n == total:
                print(f"  calendriers lus : {n}/{total}, {len(sids)} plateau(x) trouvé(s)")
            if not ids and not debug_done and "quipe(s)" in html:  # une page avec des plateaux mais sans identifiant reconnu
                debug_done = True
                RECON.mkdir(exist_ok=True)
                (RECON / "calendrier.html").write_text(html, encoding="utf-8")
                k = html.index("quipe(s)")
                probe = fetcher.click_probe(calendar_url(fal, t["clNo"], t["clCod"])) if fetcher.mode == "browser" else "(non testé : mode requests)"
                (RECON / "calendar_debug.txt").write_text(
                    f"{calendar_url(fal, t['clNo'], t['clCod'])}\nclic sur le premier plateau -> {probe}\n\n" + html[max(0, k - 1500):k + 1500], encoding="utf-8")
                print("  ! calendrier avec des plateaux mais sans identifiant reconnu : voir scraper/recon/calendar_debug.txt", file=sys.stderr)
            time.sleep(CONFIG.get("delay_seconds", 0.7))
    return sids


def cmd_discover(args) -> None:
    """Trouve les équipes de chaque catégorie, lit leur calendrier complet et archive tous les plateaux."""
    RECON.mkdir(exist_ok=True)
    teams_path = HERE / "teams.json"
    teams: dict = json.loads(teams_path.read_text(encoding="utf-8")) if teams_path.exists() else {}
    global _CLUBS
    _CLUBS = None
    fetcher = Fetcher(CONFIG.get("fetch_mode", "auto"))
    fals = [int(args.fal_id)] if args.fal_id else [c["fal_id"] for c in CONFIG["categories"] if c.get("kind") != "cup"]
    report: list[str] = []
    for fal in fals:
        if str(fal) in teams and not args.refresh_teams and all("name" in t for t in teams[str(fal)]):
            continue
        boot = CONFIG.get("bootstrap_club")
        urls = [f"{CONFIG['base_url']}?fal_id={fal}&type=animation"]
        if boot:  # page calendrier d'un club connu : la liste des clubs y est dans le HTML du serveur
            urls.append(calendar_url(fal, boot["clNo"], boot["clCod"]))
        html, pairs = "", []
        for url in urls:
            html = fetcher.text(url, settle_ms=2500)
            pairs = find_team_pairs(html)
            if not pairs and fetcher.mode != "browser" and not option_candidates(html):
                fetcher.force_browser()  # le formulaire est peut-être écrit par JavaScript
                html = fetcher.text(url, settle_ms=2500)
                pairs = find_team_pairs(html)
            if pairs or option_candidates(html):
                break
        (RECON / f"fal_{fal}.html").write_text(html, encoding="utf-8")
        found = find_clubs(html) or [{"clNo": a, "clCod": b} for a, b in pairs]
        if not found:  # valeurs de liste déroulante « nombre|nombre » : l'ordre se déduit du club connu
            order = None
            if boot:
                for a, b, _label in option_candidates(html):
                    if (a, b) == (str(boot["clNo"]), str(boot["clCod"])):
                        order = 0
                    elif (b, a) == (str(boot["clNo"]), str(boot["clCod"])):
                        order = 1
            for a, b, label in option_candidates(html):
                for no, cod in ([(a, b), (b, a)] if order is None else [(a, b) if order == 0 else (b, a)]):
                    got = site_ids_in(fetcher.text(calendar_url(fal, no, cod)), fal)
                    if got:
                        order = 0 if (no, cod) == (a, b) else 1
                        found.append({"clNo": no, "clCod": cod, "label": label})
                        break
                    time.sleep(CONFIG.get("delay_seconds", 0.7))
        report.append(f"--- fal_id {fal} : {len(found)} équipe(s) trouvée(s) ---\n{describe(html)}")
        if found:
            teams[str(fal)] = found
        print(f"fal_id {fal} : {len(found)} équipe(s)")
    teams_path.write_text(json.dumps(teams, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if report:
        (RECON / "discover.txt").write_text("\n\n".join(report), encoding="utf-8")
    if not teams:
        fetcher.close()
        print("\nListe des équipes introuvable dans la page. Envoie-moi scraper/recon/discover.txt (et fal_*.html si possible).")
        return
    sids = scan_calendars(fetcher, teams)
    fetcher.close()
    print(f"{len(sids)} plateau(x) trouvé(s) dans les calendriers")
    refresh(sorted(sids | set(read_seeds())), notify=False, quiet=True, include_cup=True)
    cmd_build()


def cmd_logos(_args=None) -> None:
    """Complète les logos de toutes les équipes déjà archivées (logo du club) et régénère data/."""
    fetcher = Fetcher(CONFIG.get("fetch_mode", "auto"))
    n = 0
    for p in sorted(RAW.glob("*.json")) if RAW.exists() else []:
        rec = json.loads(p.read_text(encoding="utf-8"))
        if cup_poule_key(rec["id"]):
            continue  # module Compétitions : logos déjà fournis directement par l'API
        before = dict(rec.get("logos", {}))
        rec["logos"] = store_logos(fetcher, with_club_logos(rec["equipes"], before))
        if rec["logos"] != before:
            save_raw(rec)
            n += 1
    fetcher.close()
    print(f"logos complétés sur {n} plateau(x)")
    cmd_build()


def cmd_add(args) -> None:
    ids = [s for s in (site_id_of(u) for u in args.urls) if s]
    refresh(ids, notify=False)
    cmd_build()


def cmd_sync(args) -> None:
    """Mise à jour courante : ne relit que le nécessaire (nouveaux plateaux, plateaux joués dont les scores
    ne sont pas complets). `--dry-run` liste sans rien télécharger."""
    found: set[str] = set()
    teams_path = HERE / "teams.json"
    if getattr(args, "discover", False) and teams_path.exists() and not args.dry_run:
        f = Fetcher(CONFIG.get("fetch_mode", "auto"))
        found = scan_calendars(f, json.loads(teams_path.read_text(encoding="utf-8")))
        f.close()
        print(f"{len(found)} plateau(x) vu(s) dans les calendriers d'équipes")
    ids = sorted(found | set(read_seeds()) | ({p.stem for p in RAW.glob("*.json")} if RAW.exists() else set()))
    ids = [sid for sid in ids if not cup_poule_key(sid)]  # module Compétitions : compté et relu à part, ci-dessous
    cup_cats = [c for c in CONFIG["categories"] if c.get("kind") == "cup"]
    now = paris_now()
    todo, skipped = [], {"scores complets": 0, "à venir": 0, "autre": 0}
    for sid in ids:
        rec = load_raw(sid)
        why = "tout relire (--all)" if args.all else refresh_reason(rec, args.upcoming, now)
        if why:
            todo.append((sid, why, rec))
        elif rec and datetime.strptime(rec["start"], "%Y-%m-%dT%H:%M") > now:
            skipped["à venir"] += 1
        elif rec and rec["matchs"] and all(m["hs"] is not None and m["as"] is not None for m in rec["matchs"]):
            skipped["scores complets"] += 1
        else:
            skipped["autre"] += 1
    print(f"{len(ids)} plateau(x) connus : {len(todo)} à relire ; ignorés : {skipped['scores complets']} avec scores complets, "
          f"{skipped['à venir']} pas encore joués" + (f", {skipped['autre']} en attente de « glyphs » ou abandonnés" if skipped["autre"] else ""))
    if cup_cats:
        print(f"+ {len(cup_cats)} compétition(s) du module « Compétitions » (ex. Festival U13), toujours revérifiée(s) en entier (léger, pas de Chromium)"
              + (" [--dry-run : non comptées ci-dessus]" if args.dry_run else ""))
    if args.dry_run:
        for sid, why, rec in todo:
            print(f"  - {sid}  {rec['cat_label'] + ' ' + rec['poule_label'] + ' ' + rec['start'] if rec else '(inconnu)'}  [{why}]")
        return
    refresh([sid for sid, _, _ in todo], notify=os.environ.get("NTFY_ENABLED") == "1", include_cup=True)
    cmd_build()


def cluster_fps(counts: dict[str, int], radius: int = 34) -> list[tuple[str, int]]:
    """Regroupe les empreintes quasi identiques (même chiffre, rendu légèrement différent).
    Renvoie [(empreinte représentative, nombre d'images du groupe)], le plus fréquent d'abord."""
    reps: list[list] = []
    for fp, n in sorted(counts.items(), key=lambda x: -x[1]):
        for r in reps:
            if sum(a != b for a, b in zip(r[0], fp)) <= radius:
                r[1] += n
                break
        else:
            reps.append([fp, n])
    return sorted(((r[0], r[1]) for r in reps), key=lambda x: -x[1])


def cmd_glyphs(args) -> None:
    """Apprentissage des chiffres, en une seule passe :
      1. relit une fois les plateaux archivés dont un score était illisible (pour retenir leurs images) ;
      2. regroupe toutes les images inconnues par ressemblance et te demande le chiffre de chaque groupe,
         le plus fréquent d'abord ;
      3. relit TOUS les plateaux concernés hors ligne (aucune requête) et les recoupe avec le mini-classement
         de leur page. Pas besoin de « sync --all »."""
    ids = [site_id_of(i) for i in args.site_ids] or read_seeds()
    need = [sid for sid in ids if load_raw(sid) is None]
    for p in sorted(RAW.glob("*.json")) if RAW.exists() else []:
        rec = json.loads(p.read_text(encoding="utf-8"))
        if (rec.get("score_illisible") or rec.get("score_incoherent")) and not rec.get("glyph_fps"):
            need.append(rec["id"])
    if need:
        print(f"{len(need)} plateau(x) à relire une fois pour retenir leurs chiffres…")
        refresh(sorted(set(need)), notify=False, quiet=True)

    glyphs = Glyphs(HERE / "glyphs.json")
    counts: dict[str, int] = {}
    for p in sorted(RAW.glob("*.json")) if RAW.exists() else []:
        rec = json.loads(p.read_text(encoding="utf-8"))
        for pend in rec.get("glyph_fps", []):
            for tok in pend["l"] + pend["r"]:
                if tok and not tok.startswith("=") and (glyphs.classify(tok) is None or (args.review and rec.get("score_incoherent"))):
                    counts[tok] = counts.get(tok, 0) + 1
    skipped: set[str] = set()
    asked = 0
    while True:
        groups = cluster_fps({f: c for f, c in counts.items() if f not in skipped and (args.review or glyphs.classify(f) is None)})
        if not groups:
            break
        if asked == 0:
            print(f"{sum(n for _, n in groups)} image(s) de chiffres inconnues, en {len(groups)} groupe(s) de ressemblance.")
            print("Tape le chiffre affiché, Entrée pour passer ce groupe, « q » pour finir.")
        fp, n = groups[0]
        print(f"\n[{len(groups)} groupe(s) restant(s)] — vu {n} fois")
        print(Glyphs.art(fp))
        now = glyphs.classify(fp)
        d = input("Quel chiffre ?" + (f" (actuellement lu : {now}, Entrée pour garder) " if now is not None else " ")).strip().lower()
        asked += 1
        if d == "q":
            break
        skipped.add(fp)
        if d.isdigit() and len(d) == 1:
            for k in [k for k, v in glyphs.fps.items() if v != d and sum(a != b for a, b in zip(k, fp)) <= 34]:
                del glyphs.fps[k]   # une ancienne étiquette contradictoire pour ce même dessin
            glyphs.fps[fp] = d
    glyphs.save()

    done, left = reresolve_all(glyphs)
    cmd_build()
    print(f"\nRelecture hors ligne : {done} plateau(x) lus et cohérents, {left} encore incomplet(s).")
    for sid in ids:  # plateaux de référence : à comparer avec la page du site
        rec = load_raw(sid)
        if rec:
            scores = ", ".join(f"{m['hs']}–{m['as']}" for m in rec["matchs"]) or "aucun match"
            etat = "INCOHÉRENT avec le classement du plateau" if rec["score_incoherent"] else ("chiffre non reconnu" if rec["score_illisible"] else "ok")
            print(f"  {sid} : {scores}  [{etat}]")
    if left:
        print("Il reste des plateaux incomplets : relance « glyphs » pour les groupes ignorés, ou « glyphs --review » si des plateaux sont INCOHÉRENTS (un chiffre a sans doute été mal étiqueté : il te redemande les chiffres concernés).")


DESCRIBE_JS = """() => {
  const short = v => String(v == null ? '' : v).slice(0, 60);
  const field = e => ({
    tag: e.tagName.toLowerCase(), id: e.id, name: e.name || '', type: e.type || '', value: short(e.value), checked: !!e.checked,
    options: e.tagName === 'SELECT' ? [...e.options].slice(0, 6).map(o => o.value + ' = ' + short(o.text.trim())).concat(e.options.length > 6 ? ['… ' + e.options.length + ' options'] : []) : undefined
  });
  const forms = [...document.forms].map(f => ({ id: f.id, cls: short(f.className), action: f.action, method: f.method, fields: [...f.elements].map(field) }));
  const loose = [...document.querySelectorAll('select, input')].filter(e => !e.form).map(field);
  const links = [...document.querySelectorAll('a[href*="site_id="]')].map(a => a.href);
  const attrs = [...document.querySelectorAll('[data-site_id],[data-site-id],[data-siteid]')].slice(0, 8).map(e => e.outerHTML.slice(0, 240));
  return { url: location.href, forms, loose, nLinksSiteId: links.length, linksSiteId: links.slice(0, 12), dataAttrs: attrs };
}"""


def _recon_session(url: str, instructions: list[str], out_name: str) -> None:
    """Ouvre une page dans Chromium ; tu fais les manipulations à la main ; on écrit
    scraper/recon/<out_name>.txt (requêtes réseau + structure de la page) à me transmettre."""
    from playwright.sync_api import sync_playwright  # pip install playwright && playwright install chromium

    out = HERE / "recon"
    out.mkdir(exist_ok=True)
    calls: list[dict] = []

    def on_response(resp) -> None:
        req = resp.request
        host = urlparse(resp.url).netloc
        if not host.endswith("fff.fr") or req.resource_type not in ("xhr", "fetch", "document"):
            return
        ctype = resp.headers.get("content-type", "")
        try:
            body = resp.text()
        except Exception:
            body = "(corps illisible)"
        full = "json" in ctype or "ld+json" in ctype  # les réponses de l'API sont gardées en entier ; le HTML est tronqué
        calls.append({"method": req.method, "url": resp.url, "type": req.resource_type, "status": resp.status,
                      "post_data": (req.post_data or "")[:600], "content_type": ctype,
                      "body_start" if not full else "body": body if full else body[:500]})

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_context(locale="fr-FR").new_page()
        page.on("response", on_response)
        page.goto(url)
        print()
        for i, line in enumerate(instructions, 1):
            print(f"{i}. {line}")
        input("Ensuite, reviens ici et appuie sur Entrée… ")
        info = page.evaluate(DESCRIBE_JS)
        (out / f"{out_name}.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(out / f"{out_name}.png"), full_page=True)
        browser.close()
    lines = ["=== REQUÊTES RÉSEAU (fff.fr) ===", json.dumps(calls, ensure_ascii=False, indent=1),
             "", "=== STRUCTURE DE LA PAGE ===", json.dumps(info, ensure_ascii=False, indent=1)]
    (out / f"{out_name}.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nÉcrit : {out / (out_name + '.txt')} (à copier dans la conversation), {out_name}.html et {out_name}.png dans le même dossier.")


def cmd_recon(args) -> None:
    url = f"{CONFIG['base_url']}?fal_id={args.fal_id}&type=animation"
    _recon_session(url, [
        "Choisis une équipe dans le formulaire et attends l'affichage du calendrier à venir.",
        "Décoche « uniquement les dates à venir ».",
        "Clique sur une journée passée (le détail s'affiche).",
    ], "summary")


def cmd_recon_cup(args) -> None:
    url = f"{CONFIG.get('competitions_url', 'https://foot63.fff.fr/competitions')}?id={args.cp_no}"
    _recon_session(url, [
        "Dans l'onglet « COUPE », choisis FESTIVAL U13 dans le menu, puis clique « Valider ».",
        "Choisis le 1er tour (ou un autre) et une poule qui a déjà des résultats, valide de nouveau.",
        "Clique sur l'onglet « Résultats », puis sur l'onglet « Classement » (attends l'affichage à chaque fois).",
        "Clique sur un match qui a un score pour ouvrir son détail (lieu, terrain) ; attends l'affichage.",
    ], "recon_cup")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "_pwworker":
        run_browser_worker()  # sous-processus interne (voir Fetcher) : jamais à lancer à la main
        return
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("urls", nargs="+"); a.set_defaults(fn=cmd_add)
    s = sub.add_parser("sync"); s.add_argument("--all", action="store_true", help="relit absolument tout"); s.add_argument("--upcoming", action="store_true", help="relit aussi les plateaux pas encore joués (changement d'horaire)"); s.add_argument("--dry-run", action="store_true", help="liste ce qui serait relu, sans rien télécharger"); s.add_argument("--discover", action="store_true", help="relit aussi les calendriers d'équipes (nouveaux plateaux)"); s.set_defaults(fn=cmd_sync)
    sub.add_parser("logos").set_defaults(fn=cmd_logos)
    d = sub.add_parser("discover"); d.add_argument("--fal-id"); d.add_argument("--refresh-teams", action="store_true"); d.set_defaults(fn=cmd_discover)
    sub.add_parser("build").set_defaults(fn=cmd_build)
    g = sub.add_parser("glyphs"); g.add_argument("site_ids", nargs="*"); g.add_argument("--review", action="store_true", help="redemande les chiffres des plateaux incohérents (correction d'une erreur d'étiquetage)"); g.set_defaults(fn=cmd_glyphs)
    r = sub.add_parser("recon"); r.add_argument("--fal-id", default="9638"); r.set_defaults(fn=cmd_recon)
    rc = sub.add_parser("recon-cup"); rc.add_argument("--cp-no", default="459205"); rc.set_defaults(fn=cmd_recon_cup)
    args = ap.parse_args()
    if args.cmd in ("sync", "add", "discover", "glyphs"):
        print(f"[scrape.py {VERSION}] boucle asyncio déjà active au démarrage : {_loop_diag()} "
              f"(sans conséquence désormais : Chromium tourne dans un sous-processus séparé)", file=sys.stderr)
    args.fn(args)


if __name__ == "__main__":
    main()
