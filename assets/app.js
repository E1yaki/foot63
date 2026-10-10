'use strict';
(() => {
  /* ============================================================
     Utilitaires
     ============================================================ */
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const norm = s => String(s).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  const slug = s => norm(s).replace(/ /g, '-');
  const icon = (id, cls = 'i') => `<svg class="${cls}" aria-hidden="true"><use href="#i-${id}"/></svg>`;
  const ord = n => (n === 1 ? 'er' : 'e');
  const signed = n => (n > 0 ? `+${n}` : String(n));

  const store = {
    get(k, d) { try { const v = localStorage.getItem('f63:' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem('f63:' + k, JSON.stringify(v)); } catch { /* stockage indisponible */ } }
  };

  const fmtDay = d => new Intl.DateTimeFormat('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' }).format(d);
  const fmtDayShort = d => new Intl.DateTimeFormat('fr-FR', { weekday: 'short', day: 'numeric', month: 'short' }).format(d).replace(/\./g, '');
  const fmtHour = d => `${d.getHours()}h${d.getMinutes() ? String(d.getMinutes()).padStart(2, '0') : ''}`;
  const fmtStamp = d => `${new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short' }).format(d).replace('.', '')}, ${fmtHour(d)}`;
  // Le module Compétitions (ex. Festival U13) ne publie pas toujours l'horaire à l'avance : on l'indique
  // plutôt que d'afficher un « 0h » qui laisserait croire à un vrai match de minuit.
  const hourLabel = p => (p.heure_connue === false ? 'heure à confirmer' : fmtHour(new Date(p.start)));
  function daysFrom(d) {
    const a = new Date(); a.setHours(0, 0, 0, 0);
    const b = new Date(d); b.setHours(0, 0, 0, 0);
    return Math.round((b - a) / 864e5);
  }
  function rel(d) {
    const n = daysFrom(d);
    if (n === 0) return 'aujourd’hui';
    if (n === 1) return 'demain';
    if (n === -1) return 'hier';
    return n > 1 ? `dans ${n} jours` : `il y a ${-n} jours`;
  }
  function ago(iso) {
    const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
    if (m < 2) return 'à l’instant';
    if (m < 60) return `il y a ${m} min`;
    if (m < 60 * 24) return `il y a ${Math.round(m / 60)} h`;
    return `il y a ${Math.round(m / 1440)} j`;
  }
  const weekKey = d => { const m = new Date(d); m.setHours(0, 0, 0, 0); m.setDate(m.getDate() - ((m.getDay() + 6) % 7)); return m.getTime(); };

  // « F.C. AUBIEROIS 1 » -> « F.C. Aubierois 1 »
  function pretty(n) {
    return String(n).toLowerCase()
      .replace(/(^|[\s\-'’])([a-zà-ÿ])/g, (m, a, b) => a + b.toUpperCase())
      .replace(/\b(f\.c\.|a\.s\.|u\.s\.|c\.s\.|c\.o\.|ent\.f\.c\.)/gi, s => s.toUpperCase());
  }
  const SKIP = new Set(['a', 's', 'fc', 'us', 'co', 'cs', 'es', 'esp', 'ent', 'sp', 'as', 'union', 'sportive', 'foot', 'de', 'du', 'des', 'la', 'le', 'les', 'st', 'saint', 'val', 'haute', 'et', 'f', 'c', 'u', 'o']);
  function initials(name) {
    const toks = norm(name).split(' ').filter(t => !/^\d+$/.test(t));
    const t = toks.find(x => !SKIP.has(x)) || toks[0] || '?';
    return t.slice(0, 2).toUpperCase();
  }

  const collator = new Intl.Collator('fr', { numeric: true, sensitivity: 'base' });

  /* ============================================================
     État
     ============================================================ */
  let D = null;
  const S = { favs: new Set(store.get('favs', [])), scope: store.get('scope', null), sheet: null };

  const groupOf = catId => (D.byCat.get(catId) && D.byCat.get(catId).group) || catId;
  const fkey = (catId, name) => `${groupOf(catId)}|${name}`;
  // Une même équipe peut apparaître dans plusieurs compétitions qui partagent le même « group » (ex. une
  // division du championnat ET le Festival U13) : en cliquant sur elle depuis une poule précise, on veut
  // systématiquement tomber sur CETTE apparition-là, jamais sur une autre choisie par défaut.
  function apIndexFor(key, cat, ph, po) {
    const apps = D.appear.get(key) || [];
    const idx = apps.findIndex(a => a.cat.id === cat.id && a.ph.n === ph.n && a.po.id === po.id);
    return idx >= 0 ? idx : 0;
  }
  const nameOfKey = k => k.slice(k.indexOf('|') + 1);
  const ruleFor = (catId, n) => (D.rules && D.rules[catId] && D.rules[catId][String(n)]) || null;

  async function fetchJSON(url) {
    const r = await fetch(url, { cache: 'no-cache' });
    if (!r.ok) throw new Error(url + ' : ' + r.status);
    return r.json();
  }

  async function loadData() {
    try {
      const cat = await fetchJSON('data/catalog.json');
      if (!cat.categories || !cat.categories.length) throw new Error('catalogue vide');
      const docs = await Promise.all(cat.categories.map(c => fetchJSON('data/' + c.file).then(d => ({ group: c.group, ...d })).catch(() => null)));
      const categories = docs.filter(Boolean);
      if (!categories.length) throw new Error('aucune catégorie lisible');
      let changes = [], rules = {};
      try { changes = await fetchJSON('data/changes.json'); } catch { /* facultatif */ }
      try { rules = await fetchJSON('data/rules.json'); } catch { /* facultatif */ }
      return { demo: false, season: cat.season, updated_at: cat.updated_at, checked_at: cat.checked_at, ntfy: cat.ntfy, categories, changes, rules };
    } catch (e) {
      return window.buildDemo();
    }
  }

  /* ============================================================
     Calculs : journées, classements, historique de position
     ============================================================ */
  const DEFAULT_PTS = { win: 3, draw: 1, loss: 0, forfeit: 0 };
  // Le barème (et un éventuel malus de forfait) peut différer d'une compétition à l'autre — ex. le
  // Festival U13 retire un point pour une défaite par forfait, contrairement aux autres catégories.
  const ptsFor = rule => ({ ...DEFAULT_PTS, ...((rule && rule.points) || (D.rules && D.rules.points)) });

  function standings(teams, matches, tb, upto, pts) {
    const T = new Map(teams.map(n => [n, { name: n, j: 0, g: 0, n: 0, p: 0, bp: 0, bc: 0, pts: 0, diff: 0, form: [] }]));
    matches.filter(m => m.played && m._j <= upto)
      .sort((a, b) => a._p.start.localeCompare(b._p.start))
      .forEach(m => {
        const H = T.get(m.home), A = T.get(m.away);
        if (!H || !A) return;
        [[H, m.hs, m.as], [A, m.as, m.hs]].forEach(([t, f, c]) => {
          t.j++; t.bp += f; t.bc += c;
          if (f > c) { t.g++; t.pts += pts.win; t.form.push('V'); }
          else if (f === c) { t.n++; t.pts += pts.draw; t.form.push('N'); }
          else { t.p++; t.pts += pts.loss; t.form.push('D'); }
        });
      });
    const arr = [...T.values()];
    arr.forEach(t => { t.diff = t.bp - t.bc; });
    arr.sort((a, b) => {
      // Une équipe qui n'a encore joué aucun match se classe toujours après celles qui ont déjà joué,
      // même si leurs points/différence de buts sont pour l'instant identiques (0 partout).
      if ((a.j === 0) !== (b.j === 0)) return a.j === 0 ? 1 : -1;
      for (const k of tb) if (b[k] !== a[k]) return b[k] - a[k];
      return a.name.localeCompare(b.name, 'fr');
    });
    arr.forEach((t, i) => {
      const prev = arr[i - 1];
      const sameGroup = prev && (prev.j === 0) === (t.j === 0);
      t.pos = prev && sameGroup && tb.every(k => prev[k] === t[k]) ? prev.pos : i + 1;
    });
    return arr;
  }

  function prepPoule(po, cat, rule) {
    const pl = (po.plateaux || []).slice().sort((a, b) => a.start.localeCompare(b.start));
    po.plateaux = pl;
    const explicit = pl.length > 0 && pl.every(p => Number.isInteger(p.journee));
    if (explicit) pl.forEach(p => { p._j = p.journee; });
    else {
      const wk = [...new Set(pl.map(p => weekKey(p.start)))].sort((a, b) => a - b);
      pl.forEach(p => { p._j = wk.indexOf(weekKey(p.start)) + 1; });
    }
    po.nJ = pl.reduce((mx, p) => Math.max(mx, p._j), 0);

    const names = new Set(po.teams || []);
    pl.forEach(p => {
      (p.equipes || []).forEach(n => names.add(n));
      (p.matchs || []).forEach(m => { names.add(m.home); names.add(m.away); });
    });
    po.teams = [...names];
    po.matches = pl.flatMap(p => (p.matchs || []).map(m => ({ ...m, _p: p, _j: p._j, played: Number.isInteger(m.hs) && Number.isInteger(m.as) })));

    po.played = {};
    po.anyPlayed = po.matches.some(m => m.played);  // tant qu'aucun score n'est publié, on ne colore rien
    po.matches.filter(m => m.played).forEach(m => {
      (po.played[m.home] = po.played[m.home] || new Set()).add(m.away);
      (po.played[m.away] = po.played[m.away] || new Set()).add(m.home);
    });

    const tb = (rule && rule.tiebreak) || (D.rules && D.rules.tiebreak) || ['pts', 'diff', 'bp'];
    po._pts = ptsFor(rule);
    po.standings = standings(po.teams, po.matches, tb, 999, po._pts);
    po.standings.forEach(t => { t.key = fkey(cat.id, t.name); });
    po.rankHist = {};
    [...new Set(po.matches.filter(m => m.played).map(m => m._j))].sort((a, b) => a - b).forEach(j => {
      standings(po.teams, po.matches, tb, j, po._pts).forEach(r => { (po.rankHist[r.name] = po.rankHist[r.name] || []).push(r.pos); });
    });
  }

  function prepare(raw) {
    D = raw;
    D.byCat = new Map(); D.appear = new Map(); D.logos = {};
    D.categories.forEach(c => { D.byCat.set(c.id, c); });
    D.categories.forEach(c => {
      Object.assign(D.logos, c.logos || {});
      c.phases.sort((a, b) => a.n - b.n);
      c.phases.forEach(ph => {
        ph.poules.sort((a, b) => collator.compare(a.id, b.id));
        ph.poules.forEach(po => {
          po._cat = c; po._ph = ph;
          prepPoule(po, c, ruleFor(c.id, ph.n));
          po.teams.forEach(t => {
            const k = fkey(c.id, t);
            if (!D.appear.has(k)) D.appear.set(k, []);
            D.appear.get(k).push({ cat: c, ph, po });
          });
        });
        prepPhase(c, ph);
      });
    });
    D.appear.forEach(list => list.sort((a, b) => b.ph.n - a.ph.n));
    const phSeen = new Map();
    D.categories.filter(c => c.kind !== 'cup').forEach(c => c.phases.forEach(p => { if (!phSeen.has(p.n)) phSeen.set(p.n, p.label); }));
    D.allPhases = [...phSeen.entries()].map(([n, label]) => ({ n, label })).sort((a, b) => a.n - b.n);
  }


  /* ------------------------------------------------------------
     Classement de la division (toutes poules) et montées / descentes.
     Règle du district : on compare d'abord les 1ers de chaque poule, puis les 2es, etc.
     (départage : points par match, différence de buts, buts marqués).
     Une place est « assurée » quand, dans le pire des cas pour l'équipe (elle perd tout)
     et le meilleur pour les autres, elle reste dans la zone. Le calcul est volontairement
     prudent : mieux vaut une barre claire de trop qu'une fausse certitude.
     ------------------------------------------------------------ */
  /* Deux façons de définir une « zone » (montée/descente/destination) dans data/rules.json :
     - portée « division » (par défaut) : rang toutes poules confondues d'une phase, comme pour les U13
       (les 1ers de chaque poule d'abord, puis les 2es...). Les seuils peuvent être des rangs fixes
       ({from,to} ou {last}), ou une fraction du total réellement connu ({from_pct,to_pct}, 0–100:
       utile pour « le meilleur quart », dont la taille dépend du nombre d'équipes qui se sera qualifié).
     - portée « poule » : rang À L'INTÉRIEUR de chaque poule, indépendamment des autres poules — le cas
       du brassage U10-U11 (les 1er/2e de CHAQUE poule montent, quelle que soit sa taille). {to:"end"}
       désigne la dernière place de cette poule précise (variable d'une poule à l'autre).
     Une même phase peut mélanger les deux : "groups" associe un sous-ensemble de poules (par le texte
     de leur libellé) à sa propre règle — par exemple les poules « Élite » (portée division, quarts) et
     les poules « Géographique » (portée poule, 3 premiers) d'une même phase U10-U11. */
  function compFor(rule, po) {
    if (rule && rule.groups) {
      const g = rule.groups.find(g => new RegExp(g.match, 'i').test(po.label || ''));
      return (g && g.composition) || {};
    }
    return (rule && rule.composition) || {};
  }
  function bucketsFor(rule, ph) {
    if (rule && rule.groups) return rule.groups.map(g => ({ ...g, poules: ph.poules.filter(po => new RegExp(g.match, 'i').test(po.label || '')) }));
    if (rule && rule.tiers && rule.tiers.length) return [{ scope: rule.scope || 'division', tiers: rule.tiers, composition: rule.composition, cross: rule.cross, poules: ph.poules }];
    return [];
  }
  const resolvePouleTiers = (raw, size) => raw.map(x => ({ kind: x.kind, label: x.label, from: x.from, to: x.to === 'end' ? size : x.to, openEnd: x.to === 'end' }));
  const resolveDivisionTiers = (raw, n) => raw.map(x => {
    if (x.last != null) return { kind: x.kind, label: x.label, from: n - x.last + 1, to: n };
    if (x.from_pct != null || x.to_pct != null) return { kind: x.kind, label: x.label, from: 1 + Math.floor((x.from_pct || 0) / 100 * n), to: Math.max(1, Math.ceil((x.to_pct == null ? 100 : x.to_pct) / 100 * n)) };
    return { kind: x.kind, label: x.label, from: x.from, to: x.to };
  });
  const sortRows = (rows, crit) => rows.sort((a, b) => {
    for (const k of crit) { if (k === 'pos') { if (a.pos !== b.pos) return a.pos - b.pos; } else if (b[k] !== a[k]) return b[k] - a[k]; }
    return a.name.localeCompare(b.name, 'fr');
  });

  function prepPhase(cat, ph) {
    const rule = ruleFor(cat.id, ph.n);
    const rows = [];
    ph.poules.forEach(po => {
      const comp = compFor(rule, po);
      const rounds = comp.rounds || 1;
      // Matchs restants : ce sont ceux que le calendrier liste et qui ne sont pas encore joués. Rien n'est
      // déduit de la taille de la poule : certaines équipes ne se rencontrent pas, d'autres se rencontrent
      // plusieurs fois (U10-U11). Le calendrier publié donne tous les matchs, à venir compris.
      po._size = po.teams.length;
      po._sizeLo = po.teams.length;
      po.standings.forEach(t => {
        t.poule = po;
        t.rem = po.matches.filter(m => !m.played && (m.home === t.name || m.away === t.name)).length;
        t.ppm = t.j ? t.pts / t.j : 0;
        t.tier = null; t.sure = false;
        rows.push(t);
      });
    });

    // Classement combiné de la phase (toutes poules), pour l'onglet Division — indépendant des zones.
    const flatComp = (rule && !rule.groups && rule.composition) || {};
    sortRows(rows, (rule && !rule.groups && rule.cross) || ['pos', 'ppm', 'diff', 'bp']);
    rows.forEach((t, i) => { t.rank = i + 1; });
    ph.overall = rows;
    ph.expected = flatComp.teams || rows.length;
    ph.missing = rule && !rule.groups ? Math.max(0, (flatComp.teams || 0) - rows.length) : 0;

    // Meilleure / pire place possible DANS SA PROPRE POULE : sert aux deux portées de zone, et au texte
    // d'explication (survol / fiche équipe), qui ne dépend jamais de la portée choisie.
    ph.poules.forEach(po => {
      const comp = compFor(rule, po);
      const absent = Math.max(0, po._size - po.teams.length);
      po.standings.forEach(t => {
        const minF = t.pts, maxF = t.pts + po._pts.win * t.rem;
        let possible = 0, sure = 0;
        po.standings.forEach(u => {
          if (u === t) return;
          if (u.pts + po._pts.win * u.rem >= minF) possible++;
          if (u.pts > maxF) sure++;
        });
        const worstPos = Math.min(1 + possible + absent, po._size || po.standings.length);
        const bestPos = 1 + sure;
        t.minF = minF; t.maxF = maxF; t.bestPos = bestPos; t.worstPos = worstPos;
      });
    });

    ph.tiers = [];
    bucketsFor(rule, ph).forEach(b => {
      if (!b.poules.length || !b.tiers || !b.tiers.length) return;
      if (b.scope === 'poule') {
        const seen = new Set();   // une seule entrée de légende par (kind, label), pas une par poule
        b.poules.forEach(po => {
          const tiers = resolvePouleTiers(b.tiers, po.standings.length);
          tiers.forEach(x => {
            const key = x.kind + '|' + x.label;
            if (!seen.has(key)) { seen.add(key); ph.tiers.push({ ...x, scope: 'poule' }); }
          });
          const finished = po.standings.every(t => t.rem === 0);
          po.standings.forEach(t => {
            const tier = tiers.find(x => t.pos >= x.from && t.pos <= x.to);
            if (!tier) return;
            t.tier = tier;
            t.sure = (t.j > 0 || finished) && (finished || (t.bestPos >= tier.from && t.worstPos <= tier.to));
          });
        });
      } else {
        const subRows = b.poules.flatMap(po => po.standings);
        sortRows(subRows, b.cross || ['pos', 'ppm', 'diff', 'bp']);
        subRows.forEach((t, i) => { t._brk = i + 1; });
        const n = (b.composition && b.composition.teams) || subRows.length;
        const tiers = resolveDivisionTiers(b.tiers, n);
        ph.tiers.push(...tiers.map(x => ({ ...x, scope: 'division' })));
        const maxQ = Math.max(0, ...b.poules.map(p => p._size));
        const bUnknown = Math.max(0, ((b.composition && b.composition.poules) || 0) - b.poules.length);
        const Chi = [0], Clo = [0];
        for (let q = 1; q <= maxQ; q++) {
          Chi[q] = Chi[q - 1] + b.poules.filter(p => p._size >= q).length + (q <= ((b.composition && b.composition.size) || 0) ? bUnknown : 0);
          Clo[q] = Clo[q - 1] + b.poules.filter(p => p._sizeLo >= q).length;
        }
        const finished = subRows.length >= n && subRows.every(t => t.rem === 0);
        subRows.forEach(t => {
          const worstRank = Chi[t.worstPos] ?? subRows.length;
          const bestRank = (Clo[Math.min(t.bestPos - 1, maxQ)] ?? 0) + 1;
          const tier = tiers.find(x => t._brk >= x.from && t._brk <= x.to);
          if (!tier) return;
          t.tier = tier;
          t.sure = (t.j > 0 || finished) && (finished || (bestRank >= tier.from && worstRank <= tier.to));
        });
      }
    });
  }

  function teamEvents(po, name) {
    return po.plateaux
      .filter(p => (p.equipes || []).includes(name) || (p.matchs || []).some(m => m.home === name || m.away === name))
      .map(p => {
        const ms = (p.matchs || []).filter(m => m.home === name || m.away === name).map(m => {
          const home = m.home === name;
          const f = home ? m.hs : m.as, c = home ? m.as : m.hs;
          const played = Number.isInteger(f) && Number.isInteger(c);
          return { opp: home ? m.away : m.home, f, c, played, res: played ? (f > c ? 'V' : f === c ? 'N' : 'D') : null };
        });
        const opps = ms.length ? ms.map(x => x.opp) : (p.equipes || []).filter(n => n !== name);
        return { p, ms, opps, when: new Date(p.start) };
      });
  }
  const isUpcoming = e => e.when.getTime() >= Date.now() - 3 * 3600e3;
  const mapsUrl = q => `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(q)}`;

  /* Une qualification assurée pour le tour/la phase suivante peut être affichée avant même que le site
     source ait publié la poule correspondante, à partir de la seule date connue (ex. le règlement du
     Festival U13 donne déjà la date du tour 2). Une équipe déjà éliminée ou dont la qualification n'est
     que provisoire n'a pas de « prochain » événement synthétique : on ne veut rien promettre à tort. */
  function nextTourStub(ap, name) {
    const rule = ruleFor(ap.cat.id, ap.ph.n);
    if (!rule || !rule.next_date) return null;
    const st = ap.po.standings.find(t => t.name === name);
    if (!st || !st.tier || st.tier.kind !== 'up' || !st.sure) return null;
    const when = new Date(rule.next_date + 'T00:00');
    return { p: { start: rule.next_date + 'T00:00', heure_connue: false, lieu: '' }, ms: [], opps: [],
      when, stub: true, label: rule.next_label || 'Tour suivant' };
  }
  function nextForTeam(ap, name) {
    const evs = familyEvents(fkey(ap.cat.id, name), isCupAp(ap)).filter(isUpcoming).sort((x, y) => x.when - y.when);
    return evs[0] || nextTourStub(ap, name) || null;
  }

  /* ============================================================
     Composants HTML
     ============================================================ */
  // les logos téléchargés par le scraper sont rangés dans data/logos/ (chemins relatifs à data/)
  const logoSrc = p => (/^(https?:|data:|\/|\.\/)/.test(p) ? p : 'data/' + p);
  function avatar(name, size = '') {
    const base = name.replace(/\s+\d+$/, '');
    let h = 0; for (const ch of base) h = (h * 31 + ch.charCodeAt(0)) % 360;
    const logo = D.logos && D.logos[name];
    return `<span class="av ${size}" style="--h:${h}" aria-hidden="true"><b>${esc(initials(name))}</b>${logo ? `<img src="${esc(logoSrc(logo))}" alt="" loading="lazy" decoding="async">` : ''}</span>`;
  }
  const starBtn = key => {
    const on = S.favs.has(key);
    return `<button class="star" type="button" data-fav="${esc(key)}" aria-pressed="${on}" aria-label="${on ? 'Ne plus suivre' : 'Suivre'} ${esc(pretty(nameOfKey(key)))}">${icon('star')}</button>`;
  };
  const dots = form => form.length ? `<span class="dots" role="img" aria-label="Derniers résultats : ${form.slice(-5).join(', ')}">${form.slice(-5).map(r => `<i class="dot ${r}"></i>`).join('')}</span>` : '';

  function zoneAt(rule, pos) {
    return rule && rule.zones ? rule.zones.find(z => pos >= z.from && pos <= z.to) : null;
  }

  function spark(ranks, n) {
    if (ranks.length < 2) return '';
    const w = 150, h = 40, pad = 5;
    const x = i => pad + i * (w - 2 * pad) / (ranks.length - 1);
    const y = r => (n <= 1 ? h / 2 : pad + (r - 1) * (h - 2 * pad) / (n - 1));
    const pts = ranks.map((r, i) => `${x(i).toFixed(1)},${y(r).toFixed(1)}`).join(' ');
    const last = ranks.length - 1;
    return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Évolution de la place : ${ranks.join(', ')}"><polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/><circle cx="${x(last).toFixed(1)}" cy="${y(ranks[last]).toFixed(1)}" r="4" fill="var(--accent)"/></svg>`;
  }

  /* ============================================================
     Navigation (barre de catégories / phases / poules)
     ============================================================ */
  function resolveScope(a = {}) {
    const wantPh = a.ph != null ? Number(a.ph) : (S.scope && S.scope.ph);
    let cat = D.byCat.get(a.cat) || D.byCat.get(S.scope && S.scope.cat) || D.categories[0];
    if (wantPh != null && !cat.phases.some(p => p.n === wantPh)) {
      // cette catégorie n'a pas (ou plus) cette phase : bascule vers la première qui l'a (de préférence
      // une catégorie « ligue » comme elle, jamais vers une compétition à part comme une coupe)
      cat = D.categories.find(c => c.kind !== 'cup' && c.phases.some(p => p.n === wantPh))
        || D.categories.find(c => c.phases.some(p => p.n === wantPh))
        || cat;
    }
    const sameCat = S.scope && S.scope.cat === cat.id;
    const hasData = p => p.poules.some(po => po.plateaux.length);
    const ph = cat.phases.find(p => p.n === wantPh)
      || (sameCat && cat.phases.find(p => p.n === S.scope.ph))
      || [...cat.phases].reverse().find(hasData) || cat.phases[0];
    const po = ph.poules.find(p => p.id === a.po)
      || (sameCat && S.scope.ph === ph.n && ph.poules.find(p => p.id === S.scope.po))
      || ph.poules.find(p => p.teams.some(t => S.favs.has(fkey(cat.id, t))))
      || ph.poules[0];
    return { cat, ph, po };
  }

  function scopeBar(view, sc, opts = {}) {
    // Deux familles bien séparées : le Championnat (Phase 1/2/3, avec des divisions qui changent d'une
    // phase à l'autre) et chaque compétition à part comme le Festival U13 (ses propres tours, pas de
    // division). Les mélanger casserait la hiérarchie dès la phase 2 du championnat.
    const isCup = sc.cat.kind === 'cup';
    const cupCats = D.categories.filter(c => c.kind === 'cup');
    const champDefault = () => {
      const remembered = S.scope && D.byCat.get(S.scope.cat);
      const cat = (remembered && remembered.kind !== 'cup') ? remembered : D.categories.find(c => c.kind !== 'cup');
      return resolveScope({ cat: cat && cat.id });
    };
    const families = [{ id: '__champ__', label: 'Championnat', get: champDefault }]
      .concat(cupCats.map(c => ({ id: c.id, label: c.label, get: () => resolveScope({ cat: c.id }) })));
    const compets = families.map(f => {
      const current = (isCup ? sc.cat.id : '__champ__') === f.id;
      const r = current ? sc : f.get();
      return `<a class="pill" href="#/${view}/${r.cat.id}/${r.ph.n}/${r.po.id}" ${current ? 'aria-current="true"' : ''}>${esc(f.label)}</a>`;
    }).join('');

    const phaseList = isCup ? sc.cat.phases : D.allPhases;
    const phases = phaseList.length > 1 ? phaseList.map(gp => {
      const r = resolveScope({ cat: sc.cat.id, ph: gp.n });
      return `<a class="pill sm" href="#/${view}/${r.cat.id}/${r.ph.n}/${r.po.id}" ${gp.n === sc.ph.n ? 'aria-current="true"' : ''}>${esc(gp.label)}</a>`;
    }).join('') : `<span class="pill sm" aria-current="true">${esc(sc.ph.label)}</span>`;

    // Division : seulement pour le championnat — le Festival n'en a pas besoin (le tour suffit).
    const catsInPhase = isCup ? [] : D.categories.filter(c => c.kind !== 'cup' && c.phases.some(p => p.n === sc.ph.n));
    const cats = catsInPhase.map(c => {
      const r = resolveScope({ cat: c.id, ph: sc.ph.n });
      return `<a class="pill sm" href="#/${view}/${c.id}/${r.ph.n}/${r.po.id}" ${c.id === sc.cat.id ? 'aria-current="true"' : ''}>${esc(c.label)}</a>`;
    }).join('');

    const poules = sc.ph.poules.map(po => {
      const fav = po.teams.some(t => S.favs.has(fkey(sc.cat.id, t)));
      return `<a class="chip" href="#/${view}/${sc.cat.id}/${sc.ph.n}/${po.id}" aria-label="${esc(po.label)}" ${po.id === sc.po.id ? 'aria-current="true"' : ''}>${esc(po.id)}${fav ? '<i class="has-fav" title="Une équipe suivie joue ici"></i>' : ''}</a>`;
    }).join('');
    const many = sc.ph.poules.length > 10;
    return `<div class="scope">
      ${families.length > 1 ? `<div class="scroller" aria-label="Compétition">${compets}</div>` : ''}
      <div class="scroller" aria-label="${isCup ? 'Tour' : 'Phase'}">${phases}</div>
      ${cats ? `<div class="scroller" aria-label="Catégorie">${cats}</div>` : ''}
      ${opts.noPoule ? '' : `<div class="scroller poules ${many ? 'wrap2' : ''}" aria-label="Poule">${poules}</div>`}
    </div>`;
  }

  const subtabs = (view, sc, extra = '') => `<nav class="subtabs" aria-label="Vue de la poule">
    <a href="#/classement/${sc.cat.id}/${sc.ph.n}/${sc.po.id}" ${view === 'classement' ? 'aria-current="true"' : ''}>Classement</a>
    ${sc.ph.poules.length > 1 ? `<a href="#/division/${sc.cat.id}/${sc.ph.n}/${sc.po.id}" ${view === 'division' ? 'aria-current="true"' : ''}>Division</a>` : ''}
    <a href="#/plateaux/${sc.cat.id}/${sc.ph.n}/${sc.po.id}${extra}" ${view === 'plateaux' ? 'aria-current="true"' : ''}>Plateaux</a>
  </nav>`;

  /* Statut d'une poule : une journée peut mettre plusieurs jours à être entièrement publiée,
     donc on affiche le pourcentage de scores connus plutôt qu'un simple « journée X sur N ». */
  function journeeStatus(po) {
    if (!po.nJ) return po.plateaux.length ? 'Calendrier en cours' : 'Calendrier à venir';
    const now = Date.now();
    const started = po.matches.filter(m => m._p && new Date(m._p.start).getTime() <= now);
    if (!started.length) return 'Calendrier à venir';
    const last = Math.max(...started.map(m => m._j));
    const due = started.filter(m => m._j === last);
    const done = due.filter(m => m.played).length;
    if (done === due.length) return `Après la journée ${last}${po.nJ > last ? ` sur ${po.nJ}` : ''}`;
    if (done === 0) return `Journée ${last} jouée, scores pas encore publiés`;
    return `Journée ${last} : ${Math.round(done / due.length * 100)} % des scores publiés (${done}/${due.length})`;
  }

  function pouleHead(sc) {
    return `<div class="poule-head"><h1>${esc(sc.cat.label)} · ${esc(sc.po.label)}</h1><p>${esc(sc.ph.label)} · ${esc(journeeStatus(sc.po))}</p></div>`;
  }

  /* ============================================================
     Vue : classement
     ============================================================ */
  const zoneStyle = t => (t.tier && t.poule.anyPlayed ? `style="--zone:var(--k-${t.tier.kind}${t.sure ? '' : '-l'})"` : '');
  const nth = n => `${n}${ord(n)}`;
  const games = n => `${n} match${n > 1 ? 's' : ''}`;
  /* Pourquoi une zone est « assurée » ou « provisoire » : affiché au survol et dans la fiche équipe. */
  function zoneText(t) {
    if (!t.tier) return '';
    const w = t.tier.kind === 'up' ? 'Montée' : t.tier.kind === 'down' ? 'Descente' : 'Destination';
    if (!t.sure) return `${w} provisoire : ${t.tier.label}. Il reste ${games(t.rem)} : en théorie, elle peut encore finir de ${nth(t.bestPos)} à ${nth(t.worstPos)} de sa poule (de ${t.pts} à ${t.maxF} pts).`;
    if (t.rem === 0) return `${w} assurée : ${t.tier.label}. Classement définitif.`;
    if (t.tier.kind === 'down') return `${w} assurée : ${t.tier.label}. Même en gagnant ses ${games(t.rem)} (${t.maxF} pts au maximum), elle ne peut pas finir mieux que ${nth(t.bestPos)} de sa poule.`;
    if (t.tier.kind === 'up') return `${w} assurée : ${t.tier.label}. Même en perdant ses ${games(t.rem)} (${t.pts} pts), elle finit au moins ${nth(t.worstPos)} de sa poule.`;
    return `${w} assurée : ${t.tier.label}. Quels que soient les résultats à venir, elle reste dans cette zone.`;
  }

  function rowHTML(t, division, minJ) {
    const po = t.poule, zt = zoneText(t);
    const showMJ = minJ != null && t.j === minJ;   // seulement les équipes les moins avancées
    const multi = po._ph.poules.length > 1;
    const sub = division ? `<span class="sub">Poule ${esc(po.id)} · ${t.pos}${ord(t.pos)}</span>` : '';
    const ai = apIndexFor(t.key, po._cat, po._ph, po);
    return `<tr class="${S.favs.has(t.key) ? 'is-fav' : ''}" data-team="${esc(t.name)}" data-cat="${esc(po._cat.id)}" data-ph="${po._ph.n}" data-poule="${esc(po.id)}"${zt ? ` title="${esc(zt)}"` : ''}>
      <td class="pos" ${zoneStyle(t)}>${division ? t.rank : t.pos}${zt ? `<span class="sr">${esc(zt)}</span>` : ''}</td>
      <td class="tm"><button class="team-btn" type="button" data-open="${esc(t.key)}" data-i="${ai}">${avatar(t.name)}<span class="tw"><span class="tn">${esc(pretty(t.name))}</span>${sub}</span></button></td>
      <td class="wide">${dots(t.form)}</td>
      <td class="wide">${t.j}</td>
      ${division ? `<td class="wide">${t.j ? t.ppm.toFixed(2).replace('.', ',') : '–'}</td>` : `<td class="wide">${t.g}</td><td class="wide">${t.n}</td><td class="wide">${t.p}</td>`}
      <td class="diff ${t.diff > 0 ? 'pos-d' : t.diff < 0 ? 'neg-d' : ''}">${signed(t.diff)}</td>
      <td class="pts"><b>${t.pts}</b>${showMJ ? `<span class="mj">${t.j} J</span>` : ''}</td>
      ${!division && multi ? `<td class="wide dv" title="Rang dans la division">${t.rank}</td>` : ''}
      <td class="fv">${starBtn(t.key)}</td>
    </tr>`;
  }

  function tableHTML(list, division) {
    const multi = list.length && list[0].poule._ph.poules.length > 1;
    const jVals = list.map(t => t.j);
    const minJ = jVals.length && Math.min(...jVals) !== Math.max(...jVals) ? Math.min(...jVals) : null;
    const head = division
      ? `<th>Rang</th><th class="tl">Équipe</th><th class="wide">Forme</th><th class="wide" title="Joués">J</th><th class="wide" title="Points par match">Moy.</th><th title="Différence de buts">Diff</th><th title="Points">Pts</th><th></th>`
      : `<th>#</th><th class="tl">Équipe</th><th class="wide">Forme</th><th class="wide" title="Joués">J</th><th class="wide" title="Gagnés">G</th><th class="wide" title="Nuls">N</th><th class="wide" title="Perdus">P</th><th title="Différence de buts">Diff</th><th title="Points">Pts</th>${multi ? '<th class="wide" title="Rang dans la division (toutes poules)">Div.</th>' : ''}<th></th>`;
    let prevPos = null;
    const body = list.map(t => {
      const grp = division && prevPos !== null && t.pos !== prevPos;
      prevPos = t.pos;
      return rowHTML(t, division, minJ).replace('<tr class="', `<tr class="${grp ? 'grp ' : ''}`);
    }).join('');
    return `<div class="card"><table class="tbl"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  function legendHTML(ph) {
    if (!ph.tiers.length) return '';
    const sw = k => `<i class="sw" style="--c:var(--k-${k}-l)"></i><i class="sw" style="--c:var(--k-${k})"></i>`;
    const range = x => x.openEnd ? `à partir du rang ${x.from}` : `rang${x.to > x.from ? 's' : ''} ${x.from}${x.to > x.from ? '–' + x.to : ''}`;
    return `<div class="legend">${ph.tiers.map(x => `<span>${sw(x.kind)}${esc(x.label)} <em>(${range(x)})</em></span>`).join('')}</div>
      <p class="note">Barre claire : place provisoire, d’après le classement actuel. Barre foncée : place assurée, quel que soit le résultat des matchs restants.</p>`;
  }

  function phaseNotes(sc) {
    const ph = sc.ph, rule = ruleFor(sc.cat.id, ph.n);
    const out = [];
    if (ph.missing) out.push(`Données incomplètes : ${ph.overall.length} équipes connues sur ${ph.expected}. Les places sont provisoires.`);
    if (ph.tiers.length && ph.poules.length > 1) out.push('Rang de la division : les premiers de chaque poule d’abord, puis les deuxièmes, etc., départagés à la moyenne de points par match, puis à la différence de buts, puis aux buts marqués.');
    if (rule && rule.note) out.push(rule.note);
    return out.map(t => `<p class="note">${esc(t)}</p>`).join('');
  }

  function viewClassement(a) {
    const sc = resolveScope(a);
    S.scope = { cat: sc.cat.id, ph: sc.ph.n, po: sc.po.id }; store.set('scope', S.scope);
    const empty = !sc.po.teams.length;
    return scopeBar('classement', sc) + pouleHead(sc) + subtabs('classement', sc) + (empty
      ? `<div class="empty"><h2>Poule vide</h2><p>Aucune équipe n’est encore enregistrée dans cette poule.</p></div>`
      : tableHTML(sc.po.standings, false) + legendHTML(sc.ph)
        + `<p class="note">Barème : ${sc.po._pts.win} points la victoire, ${sc.po._pts.draw} le nul, ${sc.po._pts.loss} la défaite${sc.po._pts.forfeit ? `, ${sc.po._pts.forfeit} en cas de forfait` : ''}. Égalité : différence de buts, puis buts marqués. Survole une équipe pour barrer celles qu’elle a déjà rencontrées.</p>`
        + phaseNotes(sc));
  }

  function viewDivision(a) {
    const sc = resolveScope(a);
    S.scope = { cat: sc.cat.id, ph: sc.ph.n, po: sc.po.id }; store.set('scope', S.scope);
    return scopeBar('division', sc, { noPoule: true })
      + `<div class="poule-head"><h1>${esc(sc.cat.label)} · toutes les poules</h1><p>${esc(sc.ph.label)} · ${sc.ph.overall.length} équipes</p></div>`
      + subtabs('division', sc)
      + tableHTML(sc.ph.overall, true) + legendHTML(sc.ph) + phaseNotes(sc);
  }

  /* ============================================================
     Vue : plateaux (feuille de plateau)
     ============================================================ */
  function plateauHTML(p, po) {
    const d = new Date(p.start);
    const cat = po._cat;
    const teams = (p.equipes && p.equipes.length) ? p.equipes : [...new Set((p.matchs || []).flatMap(m => [m.home, m.away]))];
    const hasM = (p.matchs || []).length > 0;
    const anyFav = teams.some(t => S.favs.has(fkey(cat.id, t)));
    const up = d.getTime() >= Date.now() - 3 * 3600e3;
    let body;
    if (hasM) {
      const cell = (r, c) => {
        if (r === c) return '<td class="x"></td>';
        const m = p.matchs.find(m => (m.home === r && m.away === c) || (m.home === c && m.away === r));
        if (!m) return '<td class="x"></td>';
        if (!(Number.isInteger(m.hs) && Number.isInteger(m.as))) return '<td class="tbd" title="À jouer">–</td>';
        const f = m.home === r ? m.hs : m.as, ag = m.home === r ? m.as : m.hs;
        return `<td class="sc ${f > ag ? 'w' : f < ag ? 'l' : 'd'}" title="${esc(pretty(r))} ${f}–${ag} ${esc(pretty(c))}">${f}–${ag}</td>`;
      };
      body = `<table class="xt"><colgroup><col class="c0">${teams.map(() => '<col>').join('')}</colgroup>
        <thead><tr><td></td>${teams.map(t => `<th scope="col" title="${esc(pretty(t))}">${avatar(t, 'sm')}</th>`).join('')}</tr></thead>
        <tbody>${teams.map(r => `<tr><th scope="row" class="rt"><button class="team-btn" type="button" data-open="${esc(fkey(cat.id, r))}" data-i="${apIndexFor(fkey(cat.id, r), cat, po._ph, po)}">${avatar(r, 'sm')}<span class="tn">${esc(pretty(r))}</span></button></th>${teams.map(c => cell(r, c)).join('')}</tr>`).join('')}</tbody></table>`;
    } else {
      body = `<div class="pl-teams">${teams.map(t => `<button class="tchip" type="button" data-open="${esc(fkey(cat.id, t))}" data-i="${apIndexFor(fkey(cat.id, t), cat, po._ph, po)}">${avatar(t, 'sm')}${esc(pretty(t))}</button>`).join('')}</div>`;
    }
    const where = p.lieu ? `<div class="pl-where">${icon('pin')}<span>${esc(pretty(p.lieu))}${p.organisateur ? ` · reçoit : ${esc(pretty(p.organisateur))}` : ''} · <a href="${mapsUrl(p.lieu)}" target="_blank" rel="noopener">Itinéraire</a></span></div>` : '';
    return `<article class="pl ${anyFav ? 'is-fav' : ''}">
      <div class="pl-head"><h3 class="pl-when">${esc(fmtDay(d))} · ${hourLabel(p)}</h3><span class="pl-rel">${rel(d)}</span></div>
      ${where}${body}</article>`;
  }

  function viewPlateaux(a) {
    const sc = resolveScope(a);
    S.scope = { cat: sc.cat.id, ph: sc.ph.n, po: sc.po.id }; store.set('scope', S.scope);
    const po = sc.po;
    const js = [...new Set(po.plateaux.map(p => p._j))].sort((x, y) => x - y);
    let j = Number(a.j);
    if (!js.includes(j)) {
      const next = js.find(x => po.plateaux.some(p => p._j === x && new Date(p.start).getTime() >= Date.now() - 3 * 3600e3));
      j = next || js[js.length - 1];
    }
    const base = `#/plateaux/${sc.cat.id}/${sc.ph.n}/${po.id}`;
    const idx = js.indexOf(j);
    const chips = js.map(x => {
      const first = po.plateaux.find(p => p._j === x);
      return `<a class="jchip" href="${base}/${x}" ${x === j ? 'aria-current="true"' : ''}><b>J${x}</b><span>${esc(fmtDayShort(new Date(first.start)))}</span></a>`;
    }).join('');
    const list = po.plateaux.filter(p => p._j === j)
      .sort((x, y) => {
        const fx = (x.equipes || []).some(t => S.favs.has(fkey(sc.cat.id, t))) ? 0 : 1;
        const fy = (y.equipes || []).some(t => S.favs.has(fkey(sc.cat.id, t))) ? 0 : 1;
        return fx - fy || x.start.localeCompare(y.start);
      });
    const nav = js.length ? `<div class="jnav">
      <a class="jbtn" href="${base}/${js[idx - 1] || j}" aria-label="Journée précédente" ${idx <= 0 ? 'aria-disabled="true"' : ''}>${icon('left')}</a>
      <div class="scroller" aria-label="Journées">${chips}</div>
      <a class="jbtn" href="${base}/${js[idx + 1] || j}" aria-label="Journée suivante" ${idx >= js.length - 1 ? 'aria-disabled="true"' : ''}>${icon('right')}</a></div>` : '';
    return scopeBar('plateaux', sc) + pouleHead(sc) + subtabs('plateaux', sc, j ? `/${j}` : '') + (js.length
      ? nav + `<div class="plist">${list.map(p => plateauHTML(p, po)).join('')}</div>`
      : `<div class="empty"><h2>Pas encore de plateau</h2><p>Le calendrier de cette poule n’a pas encore été récupéré.</p></div>`);
  }

  /* ============================================================
     Vue : mes équipes
     ============================================================ */
  function nextEventFor(key) {
    const ap = (D.appear.get(key) || [])[0];
    if (!ap) return null;
    const ev = nextForTeam(ap, nameOfKey(key));
    return ev ? { ...ev, ap, key } : null;
  }

  function viewEquipes() {
    const keys = [...S.favs].filter(k => D.appear.has(k));
    if (!keys.length) {
      return `<div class="empty"><h2>Suis ton équipe</h2>
        <p>Cherche un club et touche l’étoile : ses prochains plateaux, son classement et ses résultats apparaissent ici, et tu es prévenu quand un score tombe.</p>
        <button class="btn primary" type="button" data-action="search">${icon('search')}Chercher une équipe</button></div>`;
    }
    const nexts = keys.map(nextEventFor).filter(Boolean).sort((x, y) => x.when - y.when);
    let hero = '';
    if (nexts[0]) {
      const n = nexts[0];
      const name = nameOfKey(n.key);
      hero = `<section class="hero" aria-label="Prochain plateau">
        <div class="hero-when"><span class="hero-hour${n.p.heure_connue === false ? ' tbd' : ''}">${hourLabel(n.p)}</span><span class="hero-day">${esc(fmtDay(n.when))}</span></div>
        <p class="hero-rel">${rel(n.when)}</p>
        <p class="hero-team">${avatar(name)}${esc(pretty(name))}</p>
        <p class="hero-vs">${n.stub ? `${esc(n.label)} — poule à venir` : (n.opps.length ? 'Avec ' + n.opps.map(pretty).map(esc).join(' et ') : '')}</p>
        ${n.p.lieu ? `<p class="hero-where">${esc(pretty(n.p.lieu))}</p>` : ''}
        <div class="hero-actions">
          ${n.p.lieu ? `<a class="btn chalk" href="${mapsUrl(n.p.lieu)}" target="_blank" rel="noopener">${icon('pin')}Itinéraire</a>` : ''}
          ${n.stub ? '' : `<button class="btn ghost-chalk" type="button" data-action="ics" data-key="${esc(n.key)}">${icon('cal')}Ajouter à l’agenda</button>`}
        </div></section>`;
    }
    const cards = keys.map(k => {
      const ap = D.appear.get(k)[0];
      const name = nameOfKey(k);
      const st = ap.po.standings.find(t => t.name === name);
      const ev = teamEvents(ap.po, name);
      const nx = nextForTeam(ap, name);
      const last = ev.filter(e => !isUpcoming(e) && e.ms.some(m => m.played)).sort((x, y) => y.when - x.when)[0];
      const lastTxt = last ? last.ms.filter(m => m.played).map(m => `${m.res} ${m.f}–${m.c}`).join(' · ') : '';
      return `<article class="fav-card">
        <div class="fav-top"><button class="team-btn" type="button" data-open="${esc(k)}">${avatar(name)}<span class="tw"><span class="tn">${esc(pretty(name))}</span><span class="sub">${esc(ap.cat.label)} · ${esc(ap.ph.label)} · ${esc(ap.po.label)}</span></span></button>${starBtn(k)}</div>
        ${st && st.j ? `<div class="fav-stats"><span><b>${st.pos}${ord(st.pos)}</b> sur ${ap.po.standings.length}</span><span><b>${st.pts}</b> pts</span><span><b>${signed(st.diff)}</b> buts</span>${dots(st.form)}</div>` : ''}
        ${nx ? `<p class="fav-next"><span>Prochain plateau :</span> ${nx.stub ? `${esc(nx.label)} — à partir du ${esc(fmtDayShort(nx.when))}` : `${esc(fmtDayShort(nx.when))}, ${hourLabel(nx.p)}`}</p>` : ''}
        ${lastTxt ? `<p class="fav-next"><span>Dernier plateau :</span> ${esc(lastTxt)}</p>` : ''}
      </article>`;
    }).join('');
    return `${hero}<div class="fav-list">${cards}</div>`;
  }

  /* ============================================================
     Vue : alertes
     ============================================================ */
  const changeLabel = ch => (ch.match ? `${pretty(ch.match.home)} ${ch.match.hs}–${ch.match.as} ${pretty(ch.match.away)}` : (ch.text || ''));
  const relevantChanges = all => (D.changes || []).filter(ch => all || (ch.teams || []).some(n => S.favs.has(fkey(ch.cat, n))));
  const unseenCount = () => relevantChanges(false).filter(ch => ch.ts > store.get('seen', '')).length;
  function updateBadge() {
    const n = unseenCount(), b = $('#badge');
    b.hidden = n === 0; b.textContent = n > 9 ? '9+' : String(n);
  }

  function viewAlertes(a) {
    const all = a.filter === 'tout';
    const seen = store.get('seen', '');
    const list = relevantChanges(all).slice().sort((x, y) => y.ts.localeCompare(x.ts)).slice(0, 60);
    const perm = 'Notification' in window ? Notification.permission : 'indisponible';
    const notifPanel = perm === 'granted'
      ? `<p>Les notifications sont actives : tu es prévenu quand une équipe suivie change, tant que cette page est ouverte ou installée sur ton écran d’accueil.</p>`
      : perm === 'denied'
        ? `<p>Les notifications sont bloquées pour ce site. Autorise-les dans les réglages de ton navigateur, puis reviens ici.</p>`
        : perm === 'indisponible'
          ? `<p>Ce navigateur ne gère pas les notifications. L’historique ci-dessous reste à jour à chaque visite.</p>`
          : `<p>Sois prévenu dès qu’un score ou un horaire change pour une équipe que tu suis.</p><button class="btn primary" type="button" data-action="notif-on">${icon('bell')}Activer les notifications</button>`;
    const favKeys = [...S.favs].filter(k => D.appear.has(k));
    const ntfy = D.ntfy && favKeys.length ? `<section class="panel"><h2>Sur téléphone, page fermée</h2>
      <p>Installe l’application gratuite ntfy, puis abonne-toi au sujet de ton équipe : le message arrive même quand le site est fermé.</p>
      ${favKeys.map(k => { const t = `${D.ntfy.prefix}-${slug(nameOfKey(k))}`; return `<a class="topic" href="${esc(D.ntfy.server)}/${esc(t)}" target="_blank" rel="noopener"><b>${esc(pretty(nameOfKey(k)))}</b><br><small>${esc(D.ntfy.server.replace('https://', ''))}/${esc(t)}</small></a>`; }).join('')}
    </section>` : '';
    const feed = list.length ? `<ul class="feed">${list.map(ch => `<li class="${ch.ts > seen ? 'new' : ''}"><span class="pt"></span><div><p class="t">${esc(changeLabel(ch))}</p><p class="s">${esc(ago(ch.ts))}${ch.cat && D.byCat.get(ch.cat) ? ' · ' + esc(D.byCat.get(ch.cat).label) + (ch.poule ? ' · Poule ' + esc(ch.poule) : '') : ''}</p></div></li>`).join('')}</ul>`
      : `<p>${all ? 'Aucune mise à jour pour l’instant.' : (S.favs.size ? 'Rien de nouveau pour tes équipes.' : 'Suis une équipe pour voir ici ses mises à jour.')}</p>`;
    const demoBtn = D.demo ? `<button class="btn" type="button" data-action="simulate">Simuler une mise à jour</button>` : '';
    const view = `<div class="stack">
      <section class="panel"><h2>Notifications</h2>${notifPanel}${demoBtn}</section>
      ${ntfy}
      <section class="panel"><h2>Dernières mises à jour</h2>
        <nav class="subtabs" aria-label="Filtre"><a href="#/alertes" ${!all ? 'aria-current="true"' : ''}>Mes équipes</a><a href="#/alertes/tout" ${all ? 'aria-current="true"' : ''}>Toutes</a></nav>
        ${feed}</section></div>`;
    const newest = relevantChanges(false).reduce((m, c) => (c.ts > m ? c.ts : m), '');
    if (newest) store.set('seen', newest > seen ? newest : seen);
    setTimeout(updateBadge, 0);
    return view;
  }

  /* ============================================================
     Fiche équipe & recherche (fenêtres)
     ============================================================ */
  // Une ligne d'événement dans la fiche équipe : date, heure, adversaires, lieu, et désormais la compétition,
  // le tour/la phase et la poule (sinon on ne sait pas dans quelle poule on joue).
  function evItem(e, ap) {
    const where = ap ? `<p class="ev-poule">${esc(ap.cat.label)} · ${esc(ap.ph.label)} · ${esc(ap.po.label)}</p>` : '';
    if (e.stub) {
      return `<li class="ev"><div class="ev-date"><b>${esc(fmtDayShort(e.when))}</b></div><div><div class="ev-line"><span>${esc(e.label)}</span></div>${where}<p class="ev-where">Poule pas encore publiée</p></div></li>`;
    }
    const p = e.p;
    const lines = e.ms.length
      ? e.ms.map(m => `<div class="ev-line">${m.played ? `<span class="res ${m.res}">${m.res}</span><span class="res-sc">${m.f}–${m.c}</span>` : '<span class="res">·</span>'}<span>${esc(pretty(m.opp))}</span></div>`).join('')
      : `<div class="ev-line"><span>Avec ${e.opps.map(pretty).map(esc).join(' et ') || 'équipes à confirmer'}</span></div>`;
    return `<li class="ev"><div class="ev-date"><b>${esc(fmtDayShort(e.when))}</b><span>${hourLabel(p)}</span></div><div>${where}${lines}${p.lieu ? `<p class="ev-where">${esc(pretty(p.lieu))} · <a href="${mapsUrl(p.lieu)}" target="_blank" rel="noopener">Itinéraire</a></p>` : ''}</div></li>`;
  }

  const isCupAp = ap => ap.cat.kind === 'cup';
  // Tous les événements d'une équipe dans une famille de compétitions (championnat, ou festival), toutes poules
  // et tous tours confondus : chaque événement garde la poule dont il vient (ap), pour l'afficher.
  function familyEvents(key, cup) {
    const name = nameOfKey(key);
    return (D.appear.get(key) || []).filter(a => isCupAp(a) === cup)
      .flatMap(ap => teamEvents(ap.po, name).map(e => ({ ...e, ap })));
  }

  function sheetHTML(key, i) {
    const apps = D.appear.get(key) || [];
    const clicked = apps[i] || apps[0];
    if (!clicked) return `<div class="hint">Équipe introuvable.</div>`;
    const name = nameOfKey(key);
    const cup = isCupAp(clicked);
    const famApps = apps.filter(a => isCupAp(a) === cup);      // apps est trié : phase la plus récente d'abord
    const ap = famApps.includes(clicked) ? clicked : famApps[0];
    const st = ap.po.standings.find(t => t.name === name);
    const all = familyEvents(key, cup);
    const up = all.filter(isUpcoming).sort((x, y) => x.when - y.when);
    const past = all.filter(e => !isUpcoming(e)).sort((x, y) => y.when - x.when);
    const stub = up.length ? null : nextTourStub(ap, name);
    const upShown = stub ? [stub] : up;
    const tabs = [[false, 'Championnat'], [true, 'Festival U13']].filter(([c]) => apps.some(a => isCupAp(a) === c));
    const refs = tabs.length > 1 ? `<div class="sh-refs">${tabs.map(([c, label]) => `<button class="pill sm" type="button" data-open="${esc(key)}" data-i="${apps.findIndex(a => isCupAp(a) === c)}" ${c === cup ? 'aria-current="true"' : ''}>${label}</button>`).join('')}</div>` : '';
    const stats = st && st.j ? `<section class="sh-stats">
        <div class="big-rank">${st.pos}<small>${ord(st.pos)} sur ${ap.po.standings.length}</small></div>
        <div class="kv"><span><b>${st.pts}</b> pts</span><span><b>${st.j}</b> joués</span><span><b>${st.g}-${st.n}-${st.p}</b> V-N-D</span><span><b>${st.bp}–${st.bc}</b> buts</span></div>
        ${st.rem > 0 || st.tier ? `<p class="why">${st.rem > 0 ? `Il reste ${games(st.rem)} : de ${st.pts} à ${st.maxF} points possibles, place finale possible dans la poule : ${nth(st.bestPos)}${st.bestPos === st.worstPos ? '' : ' à ' + nth(st.worstPos)}.` : 'Poule terminée pour cette équipe.'}${st.tier ? ' ' + esc(zoneText(st)) : ''}</p>` : ''}
        <div class="spark">${(ap.po.rankHist[name] || []).length > 1 ? `<figure>${spark(ap.po.rankHist[name], ap.po.standings.length)}<figcaption>Place après chaque journée</figcaption></figure>` : ''}<figure>${dots(st.form)}<figcaption>Derniers matchs</figcaption></figure></div></section>`
      : `<section class="sh-stats"><div class="kv">Aucun match joué pour l’instant.</div></section>`;
    return `<header class="sh-head">${avatar(name, 'lg')}<div><h2>${esc(pretty(name))}</h2><p class="sub">${esc(ap.cat.label)} · ${esc(ap.ph.label)} · ${esc(ap.po.label)}</p></div>${starBtn(key)}<button class="star" type="button" data-action="close-sheet" aria-label="Fermer">${icon('close')}</button></header>
      ${refs}
      <div class="sh-body">${stats}
        ${upShown.length ? `<section><h3>À venir</h3><ul class="evs">${upShown.map(e => evItem(e, e.stub ? null : e.ap)).join('')}</ul></section>` : ''}
        ${past.length ? `<section><h3>Résultats</h3><ul class="evs">${past.map(e => evItem(e, e.ap)).join('')}</ul></section>` : ''}
        <div class="sh-foot">
          ${up.length ? `<button class="btn primary" type="button" data-action="ics" data-key="${esc(key)}" data-i="${apps.indexOf(ap)}">${icon('cal')}Ajouter les plateaux à l’agenda</button>` : ''}
          <a class="btn" href="#/classement/${ap.cat.id}/${ap.ph.n}/${ap.po.id}">Voir la poule</a>
        </div></div>`;
  }

  /* Fenêtres modales (fiche équipe, recherche) et bouton « précédent » : voir bind() pour l'écouteur
     popstate qui referme la fenêtre quand ce point d'arrêt est dépassé. */
  let modalNav = false;
  function openModal(dlg) {
    if (dlg.open) return;
    dlg.showModal();
    history.pushState({ modal: dlg.id }, '', location.href);
  }
  function requestClose(dlg) {
    if (!dlg.open) return;
    if (history.state && history.state.modal === dlg.id) history.back();   // le popstate ci-dessous ferme réellement la fenêtre
    else dlg.close();
  }

  function openSheet(key, i = 0) {
    S.sheet = { key, i };
    const dlg = $('#sheet');
    dlg.innerHTML = sheetHTML(key, i);
    openModal(dlg);
    dlg.scrollTop = 0;
  }

  function renderResults(v) {
    const ul = $('#results');
    const toks = norm(v).split(' ').filter(Boolean);
    if (!toks.length) {
      ul.innerHTML = `<li class="hint">Tape le nom d’un club ou d’une équipe, par exemple « Aubierois » ou « Riomois 2 ».</li>`;
      return;
    }
    const hits = [...D.appear.entries()].filter(([k]) => { const n = norm(nameOfKey(k)); return toks.every(t => n.includes(t)); })
      .sort((x, y) => nameOfKey(x[0]).localeCompare(nameOfKey(y[0]), 'fr')).slice(0, 40);
    ul.innerHTML = hits.length ? hits.map(([k, apps]) => {
      const ap = apps[0];
      return `<li class="result"><button class="team-btn" type="button" data-open="${esc(k)}">${avatar(nameOfKey(k))}<span class="tw"><span class="tn">${esc(pretty(nameOfKey(k)))}</span><span class="sub">${esc(ap.cat.label)} · ${esc(ap.ph.label)} · ${esc(ap.po.label)}</span></span></button>${starBtn(k)}</li>`;
    }).join('') : `<li class="hint">Aucune équipe ne correspond à « ${esc(v)} ».</li>`;
  }

  function openSearch() {
    const dlg = $('#search');
    dlg.innerHTML = `<div class="search-box"><input id="q" type="search" placeholder="Nom d’un club ou d’une équipe" autocomplete="off" enterkeyhint="search" aria-label="Rechercher une équipe"><button class="star" type="button" data-action="close-search" aria-label="Fermer la recherche">${icon('close')}</button></div><ul class="results" id="results"></ul>`;
    openModal(dlg);
    const q = $('#q');
    q.addEventListener('input', () => renderResults(q.value));
    renderResults('');
    q.focus();
  }

  /* ============================================================
     Actions : favoris, agenda, notifications
     ============================================================ */
  function toast(msg) {
    const t = $('#toast');
    t.textContent = msg; t.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { t.hidden = true; }, 2600);
  }

  function refreshAll() {
    const y = window.scrollY;
    render(true);
    window.scrollTo(0, y);
    if (S.sheet && $('#sheet').open) $('#sheet').innerHTML = sheetHTML(S.sheet.key, S.sheet.i);
    if ($('#search').open) renderResults($('#q').value);
    updateBadge();
  }

  function toggleFav(key) {
    const label = pretty(nameOfKey(key));
    if (S.favs.has(key)) { S.favs.delete(key); toast(`${label} : ne plus suivre`); }
    else { S.favs.add(key); toast(`Tu suis ${label}`); }
    store.set('favs', [...S.favs]);
    refreshAll();
  }

  const icsEsc = s => String(s).replace(/\\/g, '\\\\').replace(/\n/g, '\\n').replace(/,/g, '\\,').replace(/;/g, '\\;');
  function download(name, mime, text) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: mime }));
    a.download = name; document.body.append(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }
  const icsDate = d => `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}T${String(d.getHours()).padStart(2, '0')}${String(d.getMinutes()).padStart(2, '0')}00`;

  function exportIcs(key, i = 0) {
    const apps = D.appear.get(key) || [];
    const clicked = apps[i] || apps[0];
    if (!clicked) return;
    const name = nameOfKey(key);
    const evs = familyEvents(key, isCupAp(clicked)).filter(isUpcoming).sort((x, y) => x.when - y.when);
    if (!evs.length) { toast('Aucun plateau à venir'); return; }
    const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Foot animation 63//FR', 'CALSCALE:GREGORIAN', `X-WR-CALNAME:${icsEsc(pretty(name))}`];
    evs.forEach(e => {
      const ap = e.ap;
      const end = new Date(e.when.getTime() + 2 * 3600e3);
      lines.push('BEGIN:VEVENT', `UID:${slug(e.p.id || e.p.start)}-${slug(name)}@foot63`, `DTSTAMP:${icsDate(new Date())}`,
        `DTSTART:${icsDate(e.when)}`, `DTEND:${icsDate(end)}`,
        `SUMMARY:${icsEsc(`Plateau ${ap.cat.label} : ${pretty(name)}`)}`,
        `DESCRIPTION:${icsEsc(`${ap.cat.label} · ${ap.ph.label} · ${ap.po.label}\nAvec ${e.opps.map(pretty).join(' et ')}`)}`);
      if (e.p.lieu) lines.push(`LOCATION:${icsEsc(pretty(e.p.lieu))}`);
      lines.push('END:VEVENT');
    });
    lines.push('END:VCALENDAR');
    download(`${slug(name)}-plateaux.ics`, 'text/calendar', lines.join('\r\n'));
    toast('Calendrier téléchargé');
  }

  async function notify(title, body) {
    if (!('Notification' in window) || Notification.permission !== 'granted') return false;
    try {
      const reg = navigator.serviceWorker && await navigator.serviceWorker.getRegistration();
      if (reg) { await reg.showNotification(title, { body, icon: 'assets/icon.svg', tag: 'f63' }); return true; }
    } catch { /* on retombe sur l'API classique */ }
    try { new Notification(title, { body, icon: 'assets/icon.svg' }); return true; } catch { return false; }
  }

  const actions = {
    'close-sheet': () => requestClose($('#sheet')),
    'close-search': () => requestClose($('#search')),
    search: openSearch,
    ics: el => exportIcs(el.dataset.key, Number(el.dataset.i || 0)),
    'notif-on': async () => {
      if (!('Notification' in window)) return;
      const r = await Notification.requestPermission();
      if (r === 'granted') { toast('Notifications activées'); notify('Foot animation 63', 'Tu seras prévenu des changements de tes équipes.'); }
      render(true);
    },
    simulate: () => {
      const key = [...S.favs].find(k => D.appear.has(k)) || [...D.appear.keys()][0];
      const ap = D.appear.get(key)[0];
      const name = nameOfKey(key);
      const opp = ap.po.teams.find(t => t !== name);
      const ch = { id: 'demo-' + Date.now(), ts: new Date().toISOString(), type: 'score', cat: ap.cat.id, phase: ap.ph.n, poule: ap.po.id, teams: [name, opp], match: { home: name, away: opp, hs: 3, as: 2 } };
      D.changes = [ch, ...(D.changes || [])];
      notify('Score mis à jour', changeLabel(ch));
      toast('Mise à jour simulée');
      render(true); updateBadge();
    }
  };

  async function poll() {
    if (!D || D.demo) return;
    try {
      const changes = await fetchJSON('data/changes.json');
      const known = new Set((D.changes || []).map(c => c.id));
      const fresh = changes.filter(c => !known.has(c.id));
      if (!fresh.length) return;
      const raw = await loadData();
      if (raw.demo) return;
      prepare(raw);
      fresh.filter(c => (c.teams || []).some(n => S.favs.has(fkey(c.cat, n))))
        .forEach(c => notify('Foot animation 63', changeLabel(c)));
      refreshAll();
    } catch { /* réseau indisponible : on réessaiera */ }
  }

  /* ============================================================
     Routeur
     ============================================================ */
  function render(keep) {
    const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent);
    let view = parts[0] || (S.favs.size ? 'equipes' : 'classement');
    let html;
    try {
      if (view === 'equipes') html = viewEquipes();
      else if (view === 'division') html = viewDivision({ cat: parts[1], ph: parts[2], po: parts[3] });
      else if (view === 'plateaux') html = viewPlateaux({ cat: parts[1], ph: parts[2], po: parts[3], j: parts[4] });
      else if (view === 'alertes') html = viewAlertes({ filter: parts[1] });
      else { view = 'classement'; html = viewClassement({ cat: parts[1], ph: parts[2], po: parts[3] }); }
    } catch (err) {
      console.error(err);
      html = `<div class="empty"><h2>Un problème d’affichage</h2><p>Recharge la page. Si le souci persiste, les données du site source ont peut-être changé de forme.</p>
        <p class="note">Détail technique (utile si tu le signales) : <code>${esc(location.hash)}</code> — ${esc(err && err.message || String(err))}</p></div>`;
    }
    clearHover(true);
    $('#view').innerHTML = html;
    $$('.tab[data-tab]').forEach(a => { if (a.dataset.tab === (view === 'plateaux' || view === 'division' ? 'classement' : view)) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
    if (!keep) {
      window.scrollTo(0, 0);
      const cur = $('.jchip[aria-current="true"]'); if (cur) cur.scrollIntoView({ inline: 'center', block: 'nearest' });
      $$('.scope [aria-current="true"]').forEach(el => el.scrollIntoView({ inline: 'center', block: 'nearest' }));
    }
  }


  /* Survol (souris) ou tapotement (tactile) d'une équipe : les équipes déjà rencontrées sont
     barrées. Un tapotement « épingle » la sélection : il faut retapoter la même équipe pour
     l'enlever, plutôt que de dépendre du survol (qui n'existe pas au doigt). */
  let hoverRow = null, pinned = false;
  const clearHover = (force) => {
    if (pinned && !force) return;
    $$('tr.vs-me, tr.vs-played').forEach(r => r.classList.remove('vs-me', 'vs-played'));
    hoverRow = null; pinned = false;
  };
  function hoverOn(tr) {
    if (tr === hoverRow) return;
    clearHover(true);
    hoverRow = tr;
    const cat = D.byCat.get(tr.dataset.cat);
    const ph = cat && cat.phases.find(p => String(p.n) === tr.dataset.ph);
    const po = ph && ph.poules.find(p => p.id === tr.dataset.poule);
    const met = po && po.played[tr.dataset.team];
    tr.classList.add('vs-me');
    if (!met) return;
    $$('tr[data-team]', tr.closest('table')).forEach(r => {
      if (r !== tr && r.dataset.poule === tr.dataset.poule && met.has(r.dataset.team)) r.classList.add('vs-played');
    });
  }
  function togglePin(tr) {
    if (pinned && tr === hoverRow) { clearHover(true); return; }
    hoverOn(tr);
    pinned = true;
  }

  function bind() {
    document.addEventListener('click', e => {
      const fav = e.target.closest('[data-fav]');
      if (fav) { toggleFav(fav.dataset.fav); return; }
      const op = e.target.closest('[data-open]');
      if (op) { if ($('#search').open) $('#search').close(); openSheet(op.dataset.open, Number(op.dataset.i || 0)); return; }
      const act = e.target.closest('[data-action]');
      if (act && actions[act.dataset.action]) actions[act.dataset.action](act);
      const navLink = e.target.closest && e.target.closest('a[href^="#/"]');
      if (navLink) $$('dialog[open]').forEach(d => d.close());   // navigation directe : ferme les fenêtres même sans changement de hash
    });
    ['sheet', 'search'].forEach(id => {
      const dlg = $('#' + id);
      dlg.addEventListener('click', e => { if (e.target === dlg) requestClose(dlg); });
      dlg.addEventListener('close', () => { if (id === 'sheet') S.sheet = null; });
    });
    // Fenêtre modale + bouton « précédent » du téléphone : ouvrir pousse un point d'arrêt dans
    // l'historique, pour que le retour du navigateur ferme la fenêtre au lieu de changer de page.
    window.addEventListener('popstate', e => {
      ['sheet', 'search'].forEach(id => {
        const dlg = $('#' + id);
        if (dlg.open && !(e.state && e.state.modal === id)) { modalNav = true; dlg.close(); modalNav = false; }
      });
    });
    // Naviguer ailleurs (ex. « Voir la poule ») ferme aussi les fenêtres ouvertes, sans passer par l'historique.
    window.addEventListener('hashchange', () => { $$('dialog[open]').forEach(d => d.close()); });
    // un logo qui charge remplace les initiales ; un logo cassé disparaît (les initiales restent)
    document.addEventListener('load', e => { const im = e.target; if (im && im.tagName === 'IMG' && im.parentNode && im.parentNode.classList && im.parentNode.classList.contains('av')) im.parentNode.classList.add('logo'); }, true);
    document.addEventListener('error', e => { const im = e.target; if (im && im.tagName === 'IMG' && im.parentNode && im.parentNode.classList && im.parentNode.classList.contains('av')) im.remove(); }, true);
    const view = $('#view');
    view.addEventListener('mouseover', e => { if (pinned) return; const tr = e.target.closest && e.target.closest('tr[data-team]'); if (tr) hoverOn(tr); else clearHover(); });
    view.addEventListener('mouseleave', () => clearHover());
    view.addEventListener('focusin', e => { if (pinned) return; const tr = e.target.closest && e.target.closest('tr[data-team]'); if (tr) hoverOn(tr); });
    view.addEventListener('focusout', () => clearHover());
    view.addEventListener('click', e => {
      const pos = e.target.closest && e.target.closest('td.pos');
      const tr = pos && pos.closest('tr[data-team]');
      if (tr) togglePin(tr);
    });
    window.addEventListener('hashchange', () => { render(); updateBadge(); });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
  }

  /* Thème : suit le système par défaut ; le bouton fixe un choix explicite (mémorisé). */
  function applyTheme() {
    const t = store.get('theme', null);
    if (t) document.documentElement.setAttribute('data-theme', t);
    else document.documentElement.removeAttribute('data-theme');
  }
  function toggleTheme() {
    const sysDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    const current = store.get('theme', null) || (sysDark ? 'dark' : 'light');
    store.set('theme', current === 'dark' ? 'light' : 'dark');
    applyTheme();
  }

  async function init() {
    applyTheme();
    $('#theme-btn').addEventListener('click', toggleTheme);
    prepare(await loadData());
    if (D.demo && store.get('favs', null) === null && D.demoFavs) { D.demoFavs.forEach(k => S.favs.add(k)); store.set('favs', [...S.favs]); }
    if (!store.get('seen')) store.set('seen', new Date(Date.now() - (D.demo ? 90 : 0) * 60000).toISOString());
    $('#demo-banner').hidden = !D.demo;
    if (!D.demo && (D.checked_at || D.updated_at)) {
      const checkedStamp = D.checked_at && fmtStamp(new Date(D.checked_at));
      const updatedStamp = D.updated_at && fmtStamp(new Date(D.updated_at));
      const checked = checkedStamp ? `Vérifié le ${checkedStamp}` : '';
      $('#updated').textContent = checked || `Données du ${updatedStamp}`;
      if (checked && updatedStamp && updatedStamp !== checkedStamp) $('#updated').title = `${checked} (dernière mise à jour le ${updatedStamp})`;
    }
    bind();
    render();
    updateBadge();
    setInterval(poll, 120000);
    if ('serviceWorker' in navigator && /^https?:$/.test(location.protocol)) navigator.serviceWorker.register('sw.js').catch(() => { });
  }

  init();
})();
