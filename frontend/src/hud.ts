// path: frontend/src/hud.ts
//
// The HUD is DOM, not canvas. Text rendered by the browser is crisper at any DPI than
// anything drawn into a texture, it costs nothing to lay out, and it doesn't consume
// frame budget that belongs to the renderer.
//
// The only rule: nothing in here may read game state directly. The game pushes updates
// in; the HUD just renders what it's told.

import { TEAM_COLORS, TEAM_NAMES, type Phase, type ScoreRow, type Team, type WeaponId } from '@shared/protocol';

const KILLFEED_MAX = 5;
const KILLFEED_TTL = 6000;
const CHAT_MAX = 6;
const CHAT_TTL = 9000;

interface KillfeedEntry {
  el: HTMLElement;
  at: number;
}

export class HUD {
  private root: HTMLElement;
  private healthEl!: HTMLElement;
  private armorEl!: HTMLElement;
  private ammoEl!: HTMLElement;
  private reserveEl!: HTMLElement;
  private weaponEl!: HTMLElement;
  private timerEl!: HTMLElement;
  private scoreAEl!: HTMLElement;
  private scoreBEl!: HTMLElement;
  private phaseEl!: HTMLElement;
  private crosshair!: HTMLElement;
  private hitMarker!: HTMLElement;
  private damageFlash!: HTMLElement;
  private respawnEl!: HTMLElement;
  private killfeedEl!: HTMLElement;
  private chatLogEl!: HTMLElement;
  private chatInput!: HTMLInputElement;
  private scoreboardEl!: HTMLElement;
  private netgraphEl!: HTMLElement;
  private toastEl!: HTMLElement;
  private reloadEl!: HTMLElement;
  private scopeEl!: HTMLElement;
  private loadoutEl!: HTMLElement;

  private killfeed: KillfeedEntry[] = [];
  private chatLines: KillfeedEntry[] = [];
  private hitMarkerUntil = 0;
  private toastUntil = 0;
  private loadoutKey = '';

  onChatSubmit: ((msg: string) => void) | null = null;
  onChatOpenChange: ((open: boolean) => void) | null = null;

  constructor(container: HTMLElement, private localTeam: Team) {
    this.root = document.createElement('div');
    this.root.className = 'hud';
    container.appendChild(this.root);
    this.build();
  }

  private build(): void {
    this.root.innerHTML = `
      <div class="hud-top">
        <div class="score score-a"><span class="team-name"></span><b>0</b></div>
        <div class="timer-wrap"><div class="timer">0:00</div><div class="phase"></div></div>
        <div class="score score-b"><b>0</b><span class="team-name"></span></div>
      </div>

      <div class="crosshair"><i></i><i></i><i></i><i></i></div>
      <div class="hitmarker"><i></i><i></i><i></i><i></i></div>
      <div class="damage-flash"></div>
      <div class="scope">
        <div class="scope-lens"><i class="v"></i><i class="h"></i></div>
        <div class="scope-mil"></div>
      </div>

      <div class="killfeed"></div>

      <div class="hud-bottom">
        <div class="vitals">
          <div class="stat health"><label>HP</label><b>100</b></div>
          <div class="stat armor"><label>AR</label><b>0</b></div>
        </div>
        <div class="ammo-block">
          <div class="weapon-name"></div>
          <div class="ammo"><b>30</b><span>/90</span></div>
          <div class="reloading">RELOADING</div>
          <div class="loadout"></div>
        </div>
      </div>

      <div class="respawn"></div>
      <div class="toast"></div>

      <div class="chat-log"></div>
      <input class="chat-input" type="text" maxlength="120" placeholder="Say something…" />

      <div class="netgraph"></div>

      <div class="scoreboard"><div class="sb-inner"></div></div>
    `;

    const q = <T extends HTMLElement>(sel: string): T => this.root.querySelector(sel) as T;
    this.healthEl = q('.health b');
    this.armorEl = q('.armor b');
    this.ammoEl = q('.ammo b');
    this.reserveEl = q('.ammo span');
    this.weaponEl = q('.weapon-name');
    this.reloadEl = q('.reloading');
    this.timerEl = q('.timer');
    this.phaseEl = q('.phase');
    this.scoreAEl = q('.score-a b');
    this.scoreBEl = q('.score-b b');
    this.crosshair = q('.crosshair');
    this.hitMarker = q('.hitmarker');
    this.damageFlash = q('.damage-flash');
    this.respawnEl = q('.respawn');
    this.killfeedEl = q('.killfeed');
    this.chatLogEl = q('.chat-log');
    this.chatInput = q('.chat-input');
    this.scoreboardEl = q('.scoreboard');
    this.netgraphEl = q('.netgraph');
    this.toastEl = q('.toast');
    this.scopeEl = q('.scope');
    this.loadoutEl = q('.loadout');

    const nameA = q<HTMLElement>('.score-a .team-name');
    const nameB = q<HTMLElement>('.score-b .team-name');
    nameA.textContent = TEAM_NAMES.A;
    nameB.textContent = TEAM_NAMES.B;
    nameA.style.color = TEAM_COLORS.A;
    nameB.style.color = TEAM_COLORS.B;
    q<HTMLElement>('.score-a').classList.toggle('mine', this.localTeam === 'A');
    q<HTMLElement>('.score-b').classList.toggle('mine', this.localTeam === 'B');

    this.chatInput.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Enter') {
        const msg = this.chatInput.value.trim();
        this.chatInput.value = '';
        this.closeChat();
        if (msg) this.onChatSubmit?.(msg);
      } else if (e.key === 'Escape') {
        this.chatInput.value = '';
        this.closeChat();
      }
    });
  }

  // ── vitals ──────────────────────────────────────────────────────────────────

  setVitals(hp: number, armor: number): void {
    this.healthEl.textContent = String(hp);
    this.armorEl.textContent = String(armor);
    this.healthEl.parentElement!.classList.toggle('low', hp <= 30);
    this.armorEl.parentElement!.classList.toggle('empty', armor <= 0);
  }

  setAmmo(name: string, mag: number, reserve: number, melee: boolean): void {
    this.weaponEl.textContent = name;
    if (melee) {
      this.ammoEl.textContent = '—';
      this.reserveEl.textContent = '';
    } else {
      this.ammoEl.textContent = String(mag);
      this.reserveEl.textContent = `/${reserve}`;
      this.ammoEl.parentElement!.classList.toggle('low', mag <= 5);
    }
  }

  setReloading(active: boolean): void {
    this.reloadEl.classList.toggle('active', active);
  }

  /** Crosshair gap tracks the server's spread model, so it reads as real feedback. */
  setSpread(spreadPixels: number): void {
    this.crosshair.style.setProperty('--gap', `${Math.min(90, spreadPixels).toFixed(1)}px`);
  }

  setCrosshairVisible(visible: boolean): void {
    this.crosshair.style.opacity = visible ? '1' : '0';
  }

  /** Full scope overlay. Only the bolt gun uses this; everything else just zooms. */
  setScope(active: boolean): void {
    this.scopeEl.classList.toggle('active', active);
  }

  /**
   * The weapon strip: what you're carrying and how much is left in each.
   *
   * With four possible primaries, "press 1" stops being self-explanatory — the strip is
   * what tells a new player they even have a pistol.
   */
  setLoadout(
    slots: { id: WeaponId; ammo: number; reserve: number }[],
    current: WeaponId,
    configs: Map<WeaponId, { name: string; slot: number; melee: boolean }>,
  ): void {
    const key = slots.map((s) => `${s.id}:${s.ammo}/${s.reserve}`).join('|') + `#${current}`;
    if (key === this.loadoutKey) return; // rebuilding the DOM every snapshot is wasteful
    this.loadoutKey = key;

    const ordered = [...slots].sort(
      (a, b) => (configs.get(a.id)?.slot ?? 9) - (configs.get(b.id)?.slot ?? 9),
    );
    this.loadoutEl.innerHTML = ordered
      .map((s) => {
        const cfg = configs.get(s.id);
        const active = s.id === current ? ' active' : '';
        const ammo = cfg?.melee ? '' : `<i>${s.ammo}</i>`;
        return `<span class="lo${active}"><b>${cfg?.slot ?? '?'}</b>${escapeHtml(
          shortName(cfg?.name ?? s.id),
        )}${ammo}</span>`;
      })
      .join('');
  }

  // ── round state ─────────────────────────────────────────────────────────────

  setRound(phase: Phase, secondsLeft: number, scoreA: number, scoreB: number): void {
    const m = Math.floor(Math.max(0, secondsLeft) / 60);
    const s = Math.floor(Math.max(0, secondsLeft) % 60);
    this.timerEl.textContent = `${m}:${String(s).padStart(2, '0')}`;
    this.timerEl.classList.toggle('urgent', phase === 'live' && secondsLeft <= 30);
    this.phaseEl.textContent =
      phase === 'warmup' ? 'WARMUP' : phase === 'intermission' ? 'ROUND OVER' : '';
    this.scoreAEl.textContent = String(scoreA);
    this.scoreBEl.textContent = String(scoreB);
  }

  setRespawn(seconds: number): void {
    if (seconds > 0) {
      this.respawnEl.classList.add('active');
      this.respawnEl.innerHTML = `<div>You were eliminated</div><b>${seconds.toFixed(1)}</b><div>respawning…</div>`;
    } else {
      this.respawnEl.classList.remove('active');
      this.respawnEl.innerHTML = '';
    }
  }

  toast(message: string, ms = 3000): void {
    this.toastEl.textContent = message;
    this.toastEl.classList.add('active');
    this.toastUntil = performance.now() + ms;
  }

  // ── feedback ────────────────────────────────────────────────────────────────

  showHitMarker(headshot: boolean, kill: boolean): void {
    this.hitMarker.classList.remove('hs', 'kill');
    if (kill) this.hitMarker.classList.add('kill');
    else if (headshot) this.hitMarker.classList.add('hs');
    this.hitMarker.classList.add('active');
    this.hitMarkerUntil = performance.now() + (kill ? 260 : 140);
  }

  flashDamage(intensity: number): void {
    this.damageFlash.style.opacity = String(Math.min(0.75, intensity));
    this.damageFlash.classList.add('active');
  }

  addKill(killer: string, victim: string, weapon: WeaponId, headshot: boolean, team: Team): void {
    const el = document.createElement('div');
    el.className = 'kf-row';
    const icon = weapon === 'knife' ? '🗡' : weapon === 'pistol' ? '•' : '»';
    el.innerHTML = `
      <span class="kf-killer" style="color:${TEAM_COLORS[team]}">${escapeHtml(killer)}</span>
      <span class="kf-weapon">${icon}${headshot ? ' ⌾' : ''}</span>
      <span class="kf-victim">${escapeHtml(victim)}</span>`;
    this.killfeedEl.appendChild(el);
    this.killfeed.push({ el, at: performance.now() });
    while (this.killfeed.length > KILLFEED_MAX) {
      const old = this.killfeed.shift()!;
      old.el.remove();
    }
  }

  addChat(name: string, team: Team, msg: string): void {
    const el = document.createElement('div');
    el.className = 'chat-line';
    el.innerHTML = `<b style="color:${TEAM_COLORS[team]}">${escapeHtml(name)}</b>: ${escapeHtml(msg)}`;
    this.chatLogEl.appendChild(el);
    this.chatLines.push({ el, at: performance.now() });
    while (this.chatLines.length > CHAT_MAX) {
      const old = this.chatLines.shift()!;
      old.el.remove();
    }
  }

  addNotice(msg: string): void {
    const el = document.createElement('div');
    el.className = 'chat-line notice';
    el.textContent = msg;
    this.chatLogEl.appendChild(el);
    this.chatLines.push({ el, at: performance.now() });
    while (this.chatLines.length > CHAT_MAX) {
      const old = this.chatLines.shift()!;
      old.el.remove();
    }
  }

  // ── chat input ──────────────────────────────────────────────────────────────

  openChat(): void {
    this.chatInput.classList.add('active');
    this.chatInput.focus();
    this.onChatOpenChange?.(true);
  }

  closeChat(): void {
    this.chatInput.classList.remove('active');
    this.chatInput.blur();
    this.onChatOpenChange?.(false);
  }

  get chatOpen(): boolean {
    return this.chatInput.classList.contains('active');
  }

  // ── scoreboard ──────────────────────────────────────────────────────────────

  setScoreboard(rows: ScoreRow[]): void {
    const teams: Record<Team, ScoreRow[]> = { A: [], B: [] };
    for (const r of rows) teams[r.team]?.push(r);

    const table = (team: Team) => `
      <div class="sb-team">
        <div class="sb-team-head" style="color:${TEAM_COLORS[team]}">
          ${TEAM_NAMES[team]}
          <span>${teams[team].reduce((a, r) => a + r.kills, 0)}</span>
        </div>
        <div class="sb-row sb-head"><span>Player</span><i>K</i><i>D</i><i>Ping</i></div>
        ${teams[team]
          .map(
            (r) => `<div class="sb-row${r.bot ? ' bot' : ''}">
              <span>${escapeHtml(r.name)}${r.bot ? ' <em>BOT</em>' : ''}</span>
              <i>${r.kills}</i><i>${r.deaths}</i><i>${r.bot ? '—' : r.ping}</i>
            </div>`,
          )
          .join('')}
      </div>`;

    (this.scoreboardEl.querySelector('.sb-inner') as HTMLElement).innerHTML =
      table('A') + table('B');
  }

  setScoreboardVisible(visible: boolean): void {
    this.scoreboardEl.classList.toggle('active', visible);
  }

  // ── netgraph ────────────────────────────────────────────────────────────────

  setNetgraphVisible(visible: boolean): void {
    this.netgraphEl.classList.toggle('active', visible);
  }

  get netgraphVisible(): boolean {
    return this.netgraphEl.classList.contains('active');
  }

  setNetgraph(data: {
    fps: number;
    ping: number;
    snapshots: number;
    pending: number;
    correction: number;
    hardSnaps: number;
    entities: number;
  }): void {
    if (!this.netgraphVisible) return;
    this.netgraphEl.innerHTML = `
      <div>fps <b>${data.fps.toFixed(0)}</b></div>
      <div>ping <b>${data.ping.toFixed(0)} ms</b></div>
      <div>snaps <b>${data.snapshots}</b></div>
      <div>pending <b>${data.pending}</b></div>
      <div>correction <b>${(data.correction * 100).toFixed(1)} cm</b></div>
      <div>snaps-hard <b>${data.hardSnaps}</b></div>
      <div>entities <b>${data.entities}</b></div>`;
  }

  // ── per-frame ───────────────────────────────────────────────────────────────

  update(now: number): void {
    if (this.hitMarkerUntil && now > this.hitMarkerUntil) {
      this.hitMarker.classList.remove('active');
      this.hitMarkerUntil = 0;
    }
    if (this.toastUntil && now > this.toastUntil) {
      this.toastEl.classList.remove('active');
      this.toastUntil = 0;
    }
    if (this.damageFlash.classList.contains('active')) {
      const current = parseFloat(this.damageFlash.style.opacity || '0');
      const next = current - 0.035;
      if (next <= 0) {
        this.damageFlash.classList.remove('active');
        this.damageFlash.style.opacity = '0';
      } else {
        this.damageFlash.style.opacity = String(next);
      }
    }
    for (const entry of [...this.killfeed]) {
      if (now - entry.at > KILLFEED_TTL) {
        entry.el.remove();
        this.killfeed.splice(this.killfeed.indexOf(entry), 1);
      }
    }
    for (const entry of [...this.chatLines]) {
      if (now - entry.at > CHAT_TTL) {
        entry.el.classList.add('fade');
        if (now - entry.at > CHAT_TTL + 600) {
          entry.el.remove();
          this.chatLines.splice(this.chatLines.indexOf(entry), 1);
        }
      }
    }
  }

  destroy(): void {
    this.root.remove();
  }
}

/** "MR-9 Rifle" -> "MR-9": the strip has no room for marketing names. */
function shortName(full: string): string {
  return full.split(' ')[0];
}

function escapeHtml(s: string): string {
  return s.replace(
    /[&<>"']/g,
    (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] as string,
  );
}
