/* Données de démonstration.
   Utilisées quand data/catalog.json est introuvable (ouverture du fichier en local,
   ou site publié avant le premier passage du scraper).
   Les clubs sont réels, les écussons génériques, les scores et horaires générés au hasard, mais le calendrier
   respecte la vraie formule du district : poules de 9, 3 plateaux de 3 équipes par journée,
   4 journées, chaque équipe rencontre 8 adversaires différents (plan affine d'ordre 3). */
(function () {
  const CLUBS = [
    'A. S. CLERMONT SAINT JACQUES FOOT', 'AULNAT SP.', 'C.O. VEYRE MONTON', 'C.S. PONT DU CHATEAU',
    'CLERMONT FOOT 63', 'ECUREUILS FRANC ROSIER', 'ENT.F.C. ST AMANT ET TALLENDE', 'ESP. CEYRATOISE',
    'F. C. CLERMONT METROPOLE', 'F.C. AUBIEROIS', 'F.C. BLANZAT', 'F.C. CHAMALIERES', 'F.C. CHATEL GUYON',
    'F.C. LEZOUX', 'F.C. RIOMOIS', 'HAUTE COMBRAILLE FOOT', 'LEMPDES SP.', 'U.S. COURPIEROISE',
    'U.S. ISSOIRE A. DU MAS', 'U.S. LES MARTRES DE VEYRE', 'U.S. ST GERVAISIENNE', 'U.S. VIC LE COMTE',
    'UNION SPORTIVE BASSIN MINIER', 'VAL DE SIOULE FOOT'
  ];

  const CATS = [
    { id: 'u10-u11', label: 'U10-U11', group: 'u10-u11', fal_id: 9634, poules: ['A', 'B'], off: [-7, 0, 14, 21], done: 2 },
    { id: 'u13-brassage', label: 'U13 Brassage', group: 'u13', fal_id: 9639, poules: ['A', 'B', 'C'], off: [-7, 0, 14, 21], done: 2 },
    { id: 'u13-d2', label: 'U13 D2', group: 'u13', fal_id: 9638, poules: ['A', 'B', 'C'], off: [-14, -7, 0, 7], done: 3 },
    { id: 'u13-d1', label: 'U13 D1', group: 'u13', fal_id: 9637, poules: ['A', 'B'], off: [-21, -14, -7, 0], done: 4 }
  ];

  // Les 4 classes de parallèles du plan affine 3x3 : chaque paire d'équipes se retrouve exactement une fois.
  const CLASSES = [
    i => Math.floor(i / 3),
    i => i % 3,
    i => (Math.floor(i / 3) + (i % 3)) % 3,
    i => (Math.floor(i / 3) + 2 * (i % 3)) % 3
  ];

  // Écussons génériques pour la démo (les vrais logos sont téléchargés par le scraper)
  const SKIP = new Set(['a', 's', 'fc', 'us', 'co', 'cs', 'es', 'esp', 'ent', 'sp', 'as', 'union', 'sportive', 'foot', 'de', 'du', 'des', 'la', 'le', 'les', 'st', 'saint', 'val', 'haute', 'et', 'f', 'c', 'u', 'o']);
  function shield(name) {
    const base = name.replace(/\s+\d+$/, '');
    let h = 0; for (const ch of base) h = (h * 31 + ch.charCodeAt(0)) % 360;
    const toks = base.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').split(/[^a-z0-9]+/).filter(Boolean);
    const ini = (toks.find(t => !SKIP.has(t)) || toks[0] || '?').slice(0, 2).toUpperCase();
    const svg = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><path d='M8 6h48v28c0 12-10 20-24 25C18 54 8 46 8 34z' fill='hsl(${h} 55% 38%)'/><path d='M8 6h24v53C18 54 8 46 8 34z' fill='hsl(${(h + 40) % 360} 60% 52%)'/><path d='M8 6h48v28c0 12-10 20-24 25C18 54 8 46 8 34z' fill='none' stroke='white' stroke-width='3'/><text x='32' y='39' font-family='Arial,sans-serif' font-size='19' font-weight='700' fill='white' text-anchor='middle'>${ini}</text></svg>`;
    return 'data:image/svg+xml,' + encodeURIComponent(svg);
  }

  function rng(seed) {
    return function () {
      seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  const pad = n => String(n).padStart(2, '0');
  const iso = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;

  window.buildDemo = function () {
    const now = new Date();
    // samedi le plus récent (aujourd'hui inclus) : J2 est joué, J1 aussi, J3 et J4 sont à venir
    const sat = new Date(now); sat.setHours(14, 0, 0, 0);
    sat.setDate(now.getDate() - ((now.getDay() + 1) % 7));
    const at = days => { const d = new Date(sat); d.setDate(d.getDate() + days); return d; };
    const hours = [[14, 0], [14, 0], [14, 30]];

    // Une équipe n'appartient qu'à une seule division d'un même groupe d'âge : on tire les noms
    // dans un paquet mélangé par groupe (24 clubs x 3 numéros = 72 équipes max).
    const pools = {};
    const draw = group => {
      if (!pools[group]) {
        const Rg = rng(9000 + group.length * 31);
        const all = Array.from({ length: 72 }, (_, i) => `${CLUBS[i % 24]} ${1 + Math.floor(i / 24)}`);
        for (let i = all.length - 1; i > 0; i--) { const j = Math.floor(Rg() * (i + 1)); [all[i], all[j]] = [all[j], all[i]]; }
        pools[group] = all;
      }
      return pools[group].splice(0, 9);
    };

    const categories = CATS.map((c, ci) => {
      const poules = c.poules.map((pid, pi) => {
        const R = rng(1000 * (ci + 1) + 17 * (pi + 1));
        const teams = draw(c.group);
        const force = teams.map(() => 0.4 + R() * 1.6);
        const plateaux = [];
        c.off.map(at).forEach((date, j) => {
          for (let g = 0; g < 3; g++) {
            const idx = [...Array(9).keys()].filter(i => CLASSES[j](i) === g);
            const members = idx.map(i => teams[i]);
            const start = new Date(date); start.setHours(hours[g][0], hours[g][1]);
            const played = j < c.done;
            const pairs = [[0, 1], [2, 0], [1, 2]];
            const matchs = played ? pairs.map(([a, b]) => {
              const ga = Math.min(8, Math.round(R() * 3.2 * force[idx[a]] / 1.2));
              const gb = Math.min(8, Math.round(R() * 3.2 * force[idx[b]] / 1.2));
              return { home: members[a], away: members[b], hs: ga, as: gb };
            }) : [];
            const host = members[Math.floor(R() * 3)];
            plateaux.push({
              id: `demo_${c.fal_id}_${pid}_${j + 1}_${g + 1}`,
              journee: j + 1,
              start: iso(start),
              lieu: `STADE MUNICIPAL - ${host.replace(/ \d+$/, '')}`,
              organisateur: host.replace(/ \d+$/, ''),
              equipes: members,
              matchs
            });
          }
        });
        return { id: pid, label: `Poule ${pid}`, teams, plateaux };
      });
      return {
        id: c.id, label: c.label, group: c.group, fal_id: c.fal_id, season: '2026-2027',
        logos: Object.fromEntries(poules.flatMap(p => p.teams.map(t => [t, shield(t)]))),
        phases: [{ n: 1, label: 'Phase 1', poules }]
      };
    });

    // Festival U13 (module Compétitions) : structurellement différent (poules de 3, un seul tour pour
    // l'instant, scores numériques directs, horaire souvent pas encore publié) — construit à part plutôt
    // que via la mécanique de journées/plateaux des autres catégories.
    const festivalPoules = ['12', '13', '14', '15', '21', '22'].map((pid, i) => {
      const teams = Array.from({ length: 3 }, (_, k) => `${CLUBS[(i * 3 + k + 2) % 24]} ${1 + Math.floor((i * 3 + k) / 24)}`);
      const played = i === 0;  // une poule déjà jouée, pour montrer les couleurs qualifié/éliminé
      const start = new Date(sat); start.setDate(start.getDate() + (played ? -7 : 21));
      const scores = [[2, 0], [3, 1], [1, 1]];
      return {
        id: pid, label: `Poule ${pid}`, teams,
        plateaux: [{
          id: `festival_${pid}_1`, journee: 1, start: iso(start), lieu: `STADE MUNICIPAL - ${teams[0].replace(/ \d+$/, '')}`,
          organisateur: '', heure_connue: played, equipes: teams,
          matchs: [[0, 1], [0, 2], [1, 2]].map(([a, b], m) => ({ home: teams[a], away: teams[b], hs: played ? scores[m][0] : null, as: played ? scores[m][1] : null }))
        }]
      };
    });
    categories.push({
      id: 'u13-festival', label: 'Festival U13', group: 'u13', fal_id: null, kind: 'cup', season: '2026-2027',
      logos: Object.fromEntries(festivalPoules.flatMap(p => p.teams.map(t => [t, shield(t)]))),
      phases: [{ n: 1, label: '1ER TOUR', poules: festivalPoules }]
    });

    const stamp = m => new Date(now.getTime() - m * 60000).toISOString();
    const p = categories[2].phases[0].poules[0];
    const done = p.plateaux.find(x => x.matchs.length && x.journee === 2);
    const mk = (id, mins, m) => ({
      id, ts: stamp(mins), type: 'score', cat: 'u13-d2', phase: 1, poule: 'A', teams: done.equipes,
      match: { home: m.home, away: m.away, hs: m.hs, as: m.as }
    });
    const changes = done ? [
      mk('demo-3', 12, done.matchs[0]),
      mk('demo-2', 47, done.matchs[1]),
      { id: 'demo-1', ts: stamp(60 * 26), type: 'nouveau', cat: 'u13-d2', phase: 1, poule: 'A', teams: done.equipes,
        text: 'Les plateaux de la journée 3 sont publiés' }
    ] : [];

    return {
      demo: true,
      season: '2026-2027',
      updated_at: now.toISOString(),
      categories,
      changes,
      rules: {
        tiebreak: ['pts', 'diff', 'bp'],
        cross: ['pos', 'ppm', 'diff', 'bp'],
        'u13-d1': { '1': {
          composition: { poules: 2, size: 9, teams: 18 },
          tiers: [
            { kind: 'up', label: 'Monte en P2 Critérium interdistrict', from: 1, to: 3 },
            { kind: 'down', label: 'Descend en P2 D2', last: 4 }
          ],
          note: 'Critérium : les premiers des deux poules et le meilleur deuxième (points, puis goal-average général, puis meilleure attaque).'
        } },
        'u13-festival': { '1': {
          scope: 'poule', points: { win: 3, draw: 1, loss: 0, forfeit: -1 },
          tiers: [
            { kind: 'up', label: 'Qualifié pour le tour 2', from: 1, to: 2 },
            { kind: 'down', label: 'Éliminé', from: 3, to: 'end' }
          ],
          note: '1er tour : 42 poules de 3 ; les deux premiers de chaque poule sont qualifiés pour le tour 2.',
          next_date: '2026-10-17', next_label: 'Tour 2'
        } },
        'u13-d2': { '1': {
          composition: { poules: 3, size: 9, teams: 27 },
          tiers: [
            { kind: 'up', label: 'Monte en P2 D1', from: 1, to: 7 },
            { kind: 'down', label: 'Descend en P2 D3', last: 10 }
          ]
        } }
      },
      demoFavs: done ? ['u13|' + done.equipes[0]] : [],
      ntfy: { server: 'https://ntfy.sh', prefix: 'foot63-demo' }
    };
  };
})();
