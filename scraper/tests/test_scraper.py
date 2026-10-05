"""Tests sans réseau : python scraper/tests/test_scraper.py"""
import io, json, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import scrape  # noqa: E402

BASE = "https://foot63.fff.fr/wp-content/themes/fff/inc/frontOffice/img/scores/"
NAMES = {  # nom d'image -> chiffre, établi à la main sur la page d'exemple et recoupé avec son mini-classement
    "2465a5af5a6f1fecdf5e2970e167ec31e09ea584": "0", "6517c689e8baa703828244bea58f33b138fe0181": "5",
    "125c549753e3e33904bce90fd63102e52765ca54": "4", "adad42108371da8bb8b2b0abb82e6e7ac1ba9488": "0",
    "ac078ea9a19dc1a22f5f5a5e214f6b109a192572": "4", "9c52b11b15e02e42f607cbeca43ef7241797ff57": "2",
}
SID = "9638_9007_15130_39905_38098_3"
ASCSJ, BM, VEYRE = "A. S. CLERMONT SAINT JACQUES FOOT 2", "UNION SPORTIVE BASSIN MINIER 1", "C.O. VEYRE MONTON 1"


def glyphs(tmp, **override):
    g = scrape.Glyphs(Path(tmp) / "g.json"); g.names = {**NAMES, **override}; return g


def fixture():
    return (HERE / "fixture_plateau.html").read_text(encoding="utf-8")


def test_parse():
    with tempfile.TemporaryDirectory() as tmp:
        r = scrape.parse_plateau(fixture(), SID, glyphs(tmp))
    assert r["cat"] == "u13-d2" and r["phase"] == 1 and r["poule"] == "A", r
    assert r["start"] == "2026-09-19T14:00"
    assert r["lieu"] == "VEYRE MONTON - STADE MUNICIPAL 1" and r["organisateur"] == "C.O. VEYRE MONTON"
    assert r["equipes"] == [ASCSJ, BM, VEYRE]
    got = [(m["home"], m["away"], m["hs"], m["as"]) for m in r["matchs"]]
    assert got == [(BM, ASCSJ, 0, 5), (VEYRE, BM, 4, 0), (ASCSJ, VEYRE, 4, 2)], got   # 3 matchs, pas de « match » fantôme
    assert r["logos"][ASCSJ] == "https://cdn-transverse.azureedge.net/phlogos/BC525985.jpg", r["logos"]
    assert not r["score_illisible"] and not r["score_incoherent"]
    print("ok parse (page réelle d'exemple : équipes, lieu, 3 matchs, logos)")


def test_scores_checked_against_plateau_table():
    wrong = {"9c52b11b15e02e42f607cbeca43ef7241797ff57": "3"}   # un « 2 » lu comme « 3 »
    with tempfile.TemporaryDirectory() as tmp:
        r = scrape.parse_plateau(fixture(), SID, glyphs(tmp, **wrong))
    assert r["score_incoherent"] and all(m["hs"] is None and m["as"] is None for m in r["matchs"]), r["matchs"]
    print("ok contrôle : un chiffre mal lu est détecté grâce au classement du plateau, aucun faux score publié")


def test_unknown_glyph_is_not_a_zero():
    with tempfile.TemporaryDirectory() as tmp:
        g = glyphs(tmp); del g.names["9c52b11b15e02e42f607cbeca43ef7241797ff57"]
        r = scrape.parse_plateau(fixture(), SID, g)
    assert r["score_illisible"] and r["matchs"][2]["as"] is None and r["matchs"][2]["hs"] == 4
    print("ok chiffre inconnu -> score vide, jamais un faux zéro")


def test_upcoming_without_matches():
    html = fixture()
    html = html[:html.index("<h2>Matchs</h2>")] + "</div></main></body></html>"
    with tempfile.TemporaryDirectory() as tmp:
        r = scrape.parse_plateau(html, SID, glyphs(tmp))
    assert r["matchs"] == [] and len(r["equipes"]) == 3
    print("ok plateau à venir")


def test_merge():
    with tempfile.TemporaryDirectory() as tmp:
        new = scrape.parse_plateau(fixture(), SID, glyphs(tmp))
    rec, ch = scrape.merge(None, new, "2026-09-12T15:00:00Z")
    assert [c["type"] for c in ch] == ["nouveau"]
    rec2, ch2 = scrape.merge(rec, json.loads(json.dumps(new)), "2026-09-12T16:00:00Z")
    assert ch2 == [] and len(rec2["history"]) == 1
    changed = json.loads(json.dumps(new)); changed["matchs"][0]["hs"] = 7
    rec3, ch3 = scrape.merge(rec2, changed, "2026-09-12T17:00:00Z")
    assert [c["type"] for c in ch3] == ["score"] and len(rec3["history"]) == 2
    hidden = json.loads(json.dumps(new)); hidden["matchs"][0].update(hs=None, **{"as": None})
    rec4, _ = scrape.merge(rec3, hidden, "2026-09-12T18:00:00Z")
    assert rec4["matchs"][0]["hs"] == 7, "un score connu ne doit jamais disparaître"
    moved = json.loads(json.dumps(new)); moved["start"] = "2026-09-19T15:30"
    _, ch5 = scrape.merge(rec4, moved, "2026-09-12T19:00:00Z")
    assert "report" in [c["type"] for c in ch5]
    print("ok fusion / historique / détection")


def test_glyph_fingerprint():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("skip empreintes (Pillow absent)"); return
    def render(d, size, dx=0, dy=0, dark=False):
        try:
            f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
        except OSError:
            f = ImageFont.load_default(size)
        im = Image.new("RGBA", (size + 10, size + 14), (0, 0, 0, 255) if dark else (255, 255, 255, 0))
        ImageDraw.Draw(im).text((4 + dx, 2 + dy), d, font=f, fill=(255, 255, 255, 255) if dark else (30, 30, 30, 255))
        b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()
    with tempfile.TemporaryDirectory() as tmp:
        g = scrape.Glyphs(Path(tmp) / "g.json")
        for d in "0123456789":
            g.fps[scrape.Glyphs.fingerprint(render(d, 30))] = d
        ok = 0
        for d in "0123456789":
            for size, dx, dy, dark in [(22, 0, 0, False), (40, 2, 1, False), (26, 1, 0, True)]:
                ok += g.classify(scrape.Glyphs.fingerprint(render(d, size, dx, dy, dark))) == d
    assert ok == 30, f"{ok}/30 chiffres reconnus"
    print("ok empreintes : 30/30 (autres tailles, décalage, fond sombre)")


def test_fetcher_falls_back_to_browser_on_403():
    """Repli sur Chromium (sous-processus) après un 403 en requête simple ; test de bout en bout,
    Chromium réel compris."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    hits = {"n": 0}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/always403":
                self.send_response(403); self.end_headers(); return
            hits["n"] += 1
            if hits["n"] == 1:  # la toute première requête (simple, avant le repli) échoue
                self.send_response(403); self.end_headers(); return
            body = b"<html>rendu par le navigateur</html>"
            self.send_response(200); self.send_header("content-type", "text/html; charset=utf-8")
            self.end_headers(); self.wfile.write(body)
        def log_message(self, *a): pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"

    f = scrape.Fetcher("auto")
    try:
        assert "navigateur" in f.text(f"{base}/x") and f.mode == "browser"
        assert len(f.bytes(f"{base}/x")) > 0
    finally:
        f.close()
    strict = scrape.Fetcher("requests")
    try:
        strict.text(f"{base}/always403", tries=1)
        raise AssertionError("aurait dû échouer")
    except RuntimeError:
        pass
    srv.shutdown()
    print("ok repli navigateur sur 403 (mode auto), erreur nette en mode requests")


def test_discovery_helpers():
    html = ('<a href="?fal_id=9638&amp;type=fa&amp;clNo=10554&amp;clCod=525985">A</a>'
            '<li data-clNo="777" data-clCod="123456">B</li>'
            '<a href="x?fal_id=9638&type=fa&clCod=222222&clNo=99">C</a>')
    assert scrape.find_team_pairs(html) == [("10554", "525985"), ("777", "123456"), ("99", "222222")], scrape.find_team_pairs(html)
    cal = '<a href="football-animation-et-loisirs?site_id=9638_9007_15130_39905_38098_3">J1</a> <a href="?site_id=9638_9007_15130_39905_38099_3">J2</a> <a href="?site_id=9637_1_2_3_4_5">autre</a>'
    assert scrape.site_ids_in(cal, 9638) == ["9638_9007_15130_39905_38098_3", "9638_9007_15130_39905_38099_3"]
    assert scrape.option_candidates('<select><option value="0">x</option><option value="10554|525985">Team 2</option></select>') == [("10554", "525985", "Team 2")]
    assert "clNo" in scrape.calendar_url(9638, 1, 2) and scrape.calendar_url(9638, 1, 2).endswith("checkDate=false")
    print("ok découverte : paires d'équipes, site_id, liste déroulante")


def test_logos_local_copy():
    import tempfile
    class F:
        def bytes(self, url): return b"\x89PNG\r\n\x1a\nfake"
    with tempfile.TemporaryDirectory() as tmp:
        scrape.LOGO_DIR = Path(tmp) / "logos"
        out = scrape.store_logos(F(), {"A": "https://cdn.example/phlogos/BC525985.jpg", "B": "https://cdn.example/phlogos/BC525985.jpg", "C": "https://x/y.php"})
        assert out["A"] == out["B"] == "logos/BC525985.jpg" and out["C"] == "https://x/y.php"
        assert (Path(tmp) / "logos" / "BC525985.jpg").read_bytes().startswith(b"\x89PNG")
    print("ok logos : téléchargés une fois, partagés entre les équipes d'un même club")


def test_site_ids_wherever_they_are():
    html = ('<form method="get" action="football-animation-et-loisirs"><input type="hidden" name="site_id" value="9638_9007_15130_39905_38098_3"><button>3 équipe(s)</button></form>'
            '<div onclick="go(\'9638_9007_15130_39905_38099_3\')">x</div><a data-site="9638_9007_15130_39905_38100_3">y</a><p>9638_12_3 n\'est pas un plateau</p>')
    assert scrape.site_ids_in(html, 9638) == ["9638_9007_15130_39905_38098_3", "9638_9007_15130_39905_38099_3", "9638_9007_15130_39905_38100_3"]
    print("ok plateaux reconnus dans un champ caché, un onclick, un attribut data")


def test_clubs_and_club_logos():
    html = ('<select id="club"><option value="?fal_id=9638&amp;type=fa"></option>'
            '<option value="?fal_id=9638&amp;type=fa&amp;clNo=10554&amp;clCod=525985">A. S. CLERMONT SAINT JACQUES FOOT</option>'
            '<option value="?fal_id=9638&amp;type=fa&amp;clNo=15371&amp;clCod=533145">F.C. AUBIEROIS</option>'
            '<option value="?fal_id=9638&amp;type=fa&amp;clNo=17117&amp;clCod=535789">CLERMONT FOOT 63</option></select>')
    clubs = scrape.find_clubs(html)
    assert [(c["name"], c["clNo"], c["clCod"]) for c in clubs] == [("A. S. CLERMONT SAINT JACQUES FOOT", "10554", "525985"), ("F.C. AUBIEROIS", "15371", "533145"), ("CLERMONT FOOT 63", "17117", "535789")], clubs
    scrape._CLUBS = {scrape.norm(c["name"]): c["clCod"] for c in clubs}
    try:
        assert scrape.club_logo_url("F.C. AUBIEROIS 1").endswith("/BC533145.jpg")
        assert scrape.club_logo_url("A. S. CLERMONT SAINT JACQUES FOOT 2").endswith("/BC525985.jpg")
        assert scrape.club_logo_url("CLERMONT FOOT 63 3").endswith("/BC535789.jpg")
        assert scrape.club_logo_url("CLUB INCONNU 1") is None
        out = scrape.with_club_logos(["F.C. AUBIEROIS 1", "CLUB INCONNU 1"], {"CLUB INCONNU 1": "https://x/y.jpg"})
        assert out["F.C. AUBIEROIS 1"].endswith("BC533145.jpg") and out["CLUB INCONNU 1"] == "https://x/y.jpg"
    finally:
        scrape._CLUBS = None
    print("ok clubs : noms + codes lus dans la liste déroulante, logo de secours par code de club")


def test_logo_must_be_an_image():
    import tempfile
    class Html:
        def bytes(self, url): return b"<html>403 Forbidden</html>"
    with tempfile.TemporaryDirectory() as tmp:
        scrape.LOGO_DIR = Path(tmp) / "logos"
        out = scrape.store_logos(Html(), {"A": "https://cdn.example/phlogos/BC1.jpg", "B": "data:image/gif;base64,R0lGOD"})
        assert out == {"A": "https://cdn.example/phlogos/BC1.jpg"}, out   # repli sur l'URL distante, rien d'écrit
        assert not (Path(tmp) / "logos" / "BC1.jpg").exists()
    print("ok logos : une page d'erreur n'est jamais enregistrée comme logo, une image de chargement différé est ignorée")


def test_click_fallback_collects_site_ids():
    """Calendrier sans identifiant dans le HTML : on clique sur chaque plateau et on lit l'adresse
    d'arrivée. Test de bout en bout avec un vrai Chromium (sous-processus) contre un petit serveur local."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    PAGES = {
        "/cal1.html": """<html><body>
            <div><h2>Samedi 12 septembre</h2><p>Puy de Dôme - Stade Untel - 14h - Club A</p><a href="/detail.html?site_id=9638_1">3 équipe(s)</a></div>
            <div><h2>Samedi 19 septembre</h2><p>Puy de Dôme - Stade Untel - 14h - Club B</p><a href="/detail.html?site_id=9638_2">3 équipe(s)</a></div>
        </body></html>""",
        "/cal2.html": """<html><body>
            <div><h2>Samedi 12 septembre</h2><p>Puy de Dôme - Stade Untel - 14h - Club A</p><a href="/detail.html?site_id=9638_1">3 équipe(s)</a></div>
            <div><h2>Samedi 26 septembre</h2><p>Puy de Dôme - Stade Untel - 14h - Club C</p><a href="/detail.html?site_id=9638_3">3 équipe(s)</a></div>
        </body></html>""",
        "/detail.html": "<html><body>Plateau</body></html>",
    }

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = PAGES.get(self.path.split("?")[0], "").encode("utf-8")
            self.send_response(200 if body else 404)
            self.send_header("content-type", "text/html; charset=utf-8"); self.end_headers()
            self.wfile.write(body)
        def log_message(self, *a): pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"

    f = scrape.Fetcher("browser")
    try:
        seen: set[str] = set()
        ids1 = f.click_collect(f"{base}/cal1.html", seen, delay=0)
        assert set(ids1) == {"9638_1", "9638_2"}, ids1
        # même plateau du 12 septembre revu depuis un autre club (cal2) : pas recompté, seul le nouveau (26 sept) l'est
        ids2 = f.click_collect(f"{base}/cal2.html", seen, delay=0)
        assert ids2 == ["9638_3"], ids2
    finally:
        f.close()
        srv.shutdown()
    print("ok repli par clic (Chromium réel, sous-processus) : plateaux repérés sans identifiant dans le HTML, sans doublon d'un club à l'autre")


def _digit_png(d, size=26):
    import io
    from PIL import Image, ImageDraw, ImageFont
    try:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
    except OSError:
        f = ImageFont.load_default(size)
    im = Image.new("RGBA", (size + 8, size + 12), (255, 255, 255, 0))
    ImageDraw.Draw(im).text((4, 2), d, font=f, fill=(20, 20, 20, 255)); b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()


def test_offline_reread_after_learning_new_digits():
    """Chiffres appris APRÈS l'archivage : les scores se relisent hors ligne, sans nouvelle requête ;
    un chiffre mal étiqueté est détecté par le mini-classement, puis corrigé."""
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("skip relecture hors ligne (Pillow absent)"); return
    class Fake:  # sert les images de chiffres, comme le site
        mode = "requests"
        def bytes(self, url): return _digit_png(NAMES[Path(url).stem])
    with tempfile.TemporaryDirectory() as tmp:
        g = scrape.Glyphs(Path(tmp) / "g.json")
        for d in "045":                      # « 2 » pas encore appris
            g.fps[scrape.Glyphs.fingerprint(_digit_png(d))] = d
        rec = scrape.parse_plateau(fixture(), SID, g, Fake())
        assert rec["score_illisible"] and rec["glyph_fps"] and rec["mini"], "les empreintes doivent être conservées"
        assert rec["matchs"][2]["as"] is None and rec["matchs"][0]["as"] == 5      # ce qui est lisible reste lu
        # on apprend le « 2 » : relecture hors ligne
        g.fps[scrape.Glyphs.fingerprint(_digit_png("2"))] = "2"
        assert scrape.reresolve(rec, g)
        got = [(m["hs"], m["as"]) for m in rec["matchs"]]
        assert got == [(0, 5), (4, 0), (4, 2)] and not rec["score_illisible"] and rec["glyph_fps"] == [], got
        # variante : le « 2 » est étiqueté « 3 » par erreur -> incohérent, scores retirés, empreintes gardées
        rec2 = scrape.parse_plateau(fixture(), SID, g, Fake())
        assert not rec2["score_illisible"]
        g2 = scrape.Glyphs(Path(tmp) / "g2.json")
        for d in "045": g2.fps[scrape.Glyphs.fingerprint(_digit_png(d))] = d
        g2.fps[scrape.Glyphs.fingerprint(_digit_png("2"))] = "3"
        rec3 = scrape.parse_plateau(fixture(), SID, g2, Fake())
        assert rec3["score_incoherent"] and rec3["glyph_fps"] and all(m["hs"] is None for m in rec3["matchs"])
        fix = scrape.Glyphs.fingerprint(_digit_png("2")); g2.fps[fix] = "2"      # correction (ce que fait --review)
        assert scrape.reresolve(rec3, g2) and [(m["hs"], m["as"]) for m in rec3["matchs"]] == [(0, 5), (4, 0), (4, 2)]
    print("ok relecture hors ligne : chiffres appris après coup, mauvais étiquetage détecté puis corrigé, sans requête")


def test_cluster_groups_variants():
    a = "0" * 280; b = "0" * 270 + "1" * 10; c = "1" * 280
    groups = scrape.cluster_fps({a: 5, b: 3, c: 2})
    assert [n for _, n in groups] == [8, 2], groups
    print("ok regroupement des images inconnues par ressemblance")


def test_sync_only_rereads_what_is_needed():
    from datetime import datetime
    now = datetime(2026, 9, 20, 16, 0)
    def rec(start, scores, **kw):
        return {"start": start, "matchs": [{"home": "A", "away": "B", "hs": h, "as": a} for h, a in scores], **kw}
    R = lambda r, **k: scrape.refresh_reason(r, now=now, **k)
    assert R(None) == "nouveau"
    assert R(rec("2026-09-19T14:00", [(1, 0), (2, 2)])) is None                      # scores complets
    assert R(rec("2026-10-03T14:00", [])) is None                                    # pas encore joué
    assert R(rec("2026-10-03T14:00", []), upcoming=True) is not None                 # ... sauf --upcoming
    assert R(rec("2026-09-20T14:00", [(1, 0), (None, None)])) is not None            # joué, un match sans score
    assert R(rec("2026-09-20T14:00", [])) is not None                                # joué, rien encore publié
    assert R(rec("2026-09-20T14:00", [(None, None)], glyph_fps=[{"l": ["x"], "r": []}])) is None   # c'est à « glyphs » de jouer
    assert R(rec("2026-08-01T14:00", [])) is None                                    # abandonné depuis longtemps
    print("ok sync : ni scores complets, ni plateaux à venir, ni plateaux abandonnés")


def test_natural_poule_sort():
    ids = ["10", "2", "1", "B", "A", "11"]
    assert sorted(ids, key=scrape.natural_key) == ["1", "2", "10", "11", "A", "B"]
    print("ok tri naturel des poules (2 avant 10)")


def test_checked_at_advances_even_without_changes():
    import shutil, time as _t
    with tempfile.TemporaryDirectory() as tmp:
        scrape.DATA, scrape.RAW = Path(tmp) / "data", Path(tmp) / "data" / "raw"
        scrape.RAW.mkdir(parents=True)
        scrape.CONFIG["categories"] = [c for c in scrape.CONFIG["categories"] if c["id"] == "u13-d2"]
        with tempfile.TemporaryDirectory() as gtmp:
            g = glyphs(gtmp); new = scrape.parse_plateau(fixture(), SID, g)
            scrape.save_raw(scrape.merge(None, new, "2026-09-19T15:00:00Z")[0])
        scrape.cmd_build()
        c1 = json.loads((scrape.DATA / "catalog.json").read_text())
        _t.sleep(1.1)
        scrape.cmd_build()   # rien de nouveau : « updated_at » ne doit pas bouger, « checked_at » si
        c2 = json.loads((scrape.DATA / "catalog.json").read_text())
        assert c2["updated_at"] == c1["updated_at"] and c2["checked_at"] > c1["checked_at"], (c1, c2)
    print("ok checked_at avance à chaque vérification, updated_at seulement quand les données changent")


def test_fetcher_works_inside_a_running_asyncio_loop():
    """Reproduit le bug observé sur le NAS de l'utilisateur (tâche planifiée) : le processus a déjà
    une boucle asyncio EN COURS D'EXÉCUTION sur son thread principal quand le scraper tourne, ce que
    l'API synchrone de Playwright refuse normalement (« Sync API inside the asyncio loop »). Le
    Fetcher doit fonctionner quand même, car tout son pilotage de Chromium passe par un thread dédié."""
    import asyncio
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("content-type", "text/html"); self.end_headers()
            self.wfile.write(b"<html><body>ok</body></html>")
        def log_message(self, *a): pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/"

    async def run():
        f = scrape.Fetcher("browser")
        try:
            html = f.text(url)
        finally:
            f.close()
        return html

    try:
        html = asyncio.run(run())
    except Exception as e:
        if "asyncio loop" in str(e):
            raise AssertionError("le bug de la boucle asyncio n'est pas corrigé") from e
        raise
    finally:
        srv.shutdown()
    assert "ok" in html
    print("ok Fetcher (mode browser) fonctionne même avec une boucle asyncio déjà active sur le thread appelant")


def test_cup_parses_real_fixture():
    """Festival U13 (module Compétitions) : fixture capturée sur le vrai site (aucun score, la
    compétition n'a pas commencé) — vérifie le format, avant que le premier tour ne soit joué."""
    compet = json.loads((HERE / "fixtures" / "cup_compet_459205.json").read_text(encoding="utf-8"))
    cal = json.loads((HERE / "fixtures" / "cup_calendrier_459205_p1_g14.json").read_text(encoding="utf-8"))
    assert len(compet["phases"]) == 1 and compet["phases"][0]["number"] == 1
    assert len(compet["phases"][0]["groups"]) == 42, "42 poules attendues au 1er tour"
    gp = next(g for g in compet["phases"][0]["groups"] if g["stage_number"] == 14)
    assert gp["name"] == "POULE 14"

    # noms officiels tels que la fiche club de l'API les donne (capture réelle) ; Beaumont (3369) : inconnu ici
    names = {9287: "A.S. ROYAT A.C.", 10554: "A. S. CLERMONT SAINT JACQUES FOOT"}
    recs = scrape.cup_raw_records(459205, "u13-festival", 1, "1ER TOUR", 14, gp["name"], cal, names)
    assert len(recs) == 1, "les 3 matchs sont tous à la même journée"
    rec = recs[0]
    assert rec["id"] == "cup459205_1_14_1"
    # même nom que sur les pages animation, numéro d'équipe toujours présent (y compris « 1 »)
    assert rec["equipes"] == sorted(["A.S. ROYAT A.C. 2", "A. S. CLERMONT SAINT JACQUES FOOT 2", "BEAUMONT US 1"]), rec["equipes"]
    assert len(rec["matchs"]) == 3
    assert all(m["hs"] is None and m["as"] is None for m in rec["matchs"]), "rien de joué : compétition pas commencée"
    assert rec["lieu"].startswith("STADE DE L' ESPÉRANCE")
    assert rec["start"] == "2026-09-26T00:00"  # date connue, horaire pas encore publié (time vide)
    assert rec["heure_connue"] is False, "l'horaire n'est pas publié : ne doit pas être affiché comme un vrai 0h"
    assert rec["logos"]["A.S. ROYAT A.C. 2"].endswith("BC524117.jpg")
    assert not rec["score_illisible"] and not rec["score_incoherent"], "pas de reconnaissance de chiffres à faire ici : scores déjà numériques"
    print("ok Festival U13 : fixture réelle (42 poules, poule 14 : 3 équipes, aucun score, lieu et logos lus)")


def test_cup_scores_and_multi_day_journee():
    """Une fois des scores publiés, et une journée à deux dates (terrains différents) : vérifié sur des
    données synthétiques, dans le même format que la fixture réelle."""
    cal = {"hydra:member": [
        {"poule_journee": {"number": 2}, "date": "2026-10-03T00:00:00+00:00", "time": "14:30:00",
         "terrain": {"name": "STADE A", "address": "1 rue A", "city": "VILLE A"},
         "home": {"short_name_federation": "CLUB A", "club": {"logo": "https://x/BC1.jpg"}},
         "away": {"short_name_federation": "CLUB B", "club": {"logo": "https://x/BC2.jpg"}},
         "home_score": 3, "away_score": 1},
        {"poule_journee": {"number": 2}, "date": "2026-10-03T00:00:00+00:00", "time": "",
         "terrain": {"name": "STADE A", "address": "1 rue A", "city": "VILLE A"},
         "home": {"short_name_federation": "CLUB C", "club": {}},
         "away": {"short_name_federation": "CLUB A", "club": {}},
         "home_score": None, "away_score": None},
    ]}
    recs = scrape.cup_raw_records(1, "cat", 1, "1ER TOUR", 5, "POULE 5", cal)
    assert len(recs) == 1 and recs[0]["id"] == "cup1_1_5_2"
    assert recs[0]["start"] == "2026-10-03T14:30", "heure publiée pour le 1er match du jour : utilisée"
    got = {(m["home"], m["away"]): (m["hs"], m["as"]) for m in recs[0]["matchs"]}
    assert got[("CLUB A", "CLUB B")] == (3, 1) and got[("CLUB C", "CLUB A")] == (None, None)
    print("ok Festival U13 : scores numériques directs (pas d'OCR), horaire du 1er match du jour retenu")


def test_cup_id_routing_and_dedup():
    assert scrape.cup_poule_key("cup459205_1_14_2") == (459205, 1, 14)
    assert scrape.cup_poule_key("9638_9007_15130_39905_38098_3") is None
    print("ok identifiants du module Compétitions reconnus et distingués des plateaux animation")


def test_cup_time_parsing_and_bad_time_never_breaks_start():
    p = scrape.parse_cup_time
    assert [p(x) for x in ["14:30", "14:30:00", "14h30", "14H", "1430", " 9:05 "]] == ["14:30", "14:30", "14:30", "14:00", "14:30", "09:05"]
    assert all(p(x) is None for x in ["", None, "abc", "25:00", "14h99"])
    cal = {"hydra:member": [
        {"poule_journee": {"number": 1}, "date": "2026-09-26T00:00:00+00:00", "time": "à définir",
         "home": {"short_name": "A", "code": 1, "club": {"cl_no": 1}}, "away": {"short_name": "B", "code": 1, "club": {"cl_no": 2}}}]}
    rec = scrape.cup_raw_records(1, "c", 1, "T1", 3, "P3", cal, {})[0]
    assert rec["start"] == "2026-09-26T00:00" and rec["heure_connue"] is False, rec["start"]   # jamais une date invalide
    print("ok horaires du module Compétitions : formats variés reconnus, horaire illisible = inconnu (jamais de date cassée)")


def test_cup_names_identical_across_categories_and_teams_of_same_club_distinct():
    names = {10554: "A. S. CLERMONT SAINT JACQUES FOOT"}
    def team(code): return {"short_name": "CLT ST JACQUES", "code": code, "club": {"cl_no": 10554}}
    other = {"short_name": "X", "code": 1, "club": {"cl_no": 9}}
    cal = {"hydra:member": [
        {"poule_journee": {"number": 1}, "date": "2026-09-26T00:00:00+00:00", "home": team(1), "away": other},
        {"poule_journee": {"number": 1}, "date": "2026-09-26T00:00:00+00:00", "home": team(2), "away": other}]}
    rec = scrape.cup_raw_records(1, "c", 1, "T1", 3, "P3", cal, names)[0]
    assert "A. S. CLERMONT SAINT JACQUES FOOT 1" in rec["equipes"] and "A. S. CLERMONT SAINT JACQUES FOOT 2" in rec["equipes"], rec["equipes"]
    print("ok deux équipes d'un même club : deux noms distincts, identiques à ceux des pages animation")


if __name__ == "__main__":
    for t in [test_parse, test_scores_checked_against_plateau_table, test_unknown_glyph_is_not_a_zero, test_upcoming_without_matches, test_merge, test_glyph_fingerprint, test_fetcher_falls_back_to_browser_on_403, test_discovery_helpers, test_site_ids_wherever_they_are, test_clubs_and_club_logos, test_logos_local_copy, test_logo_must_be_an_image, test_click_fallback_collects_site_ids, test_offline_reread_after_learning_new_digits, test_cluster_groups_variants, test_sync_only_rereads_what_is_needed, test_natural_poule_sort, test_checked_at_advances_even_without_changes, test_fetcher_works_inside_a_running_asyncio_loop, test_cup_parses_real_fixture, test_cup_scores_and_multi_day_journee, test_cup_id_routing_and_dedup, test_cup_time_parsing_and_bad_time_never_breaks_start, test_cup_names_identical_across_categories_and_teams_of_same_club_distinct]:
        t()
    print("TOUS LES TESTS PASSENT")
