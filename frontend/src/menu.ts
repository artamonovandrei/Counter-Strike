// path: frontend/src/menu.ts
//
// Main menu, settings and the pause overlay.
//
// Settings persist in localStorage. Nothing gameplay-affecting lives here — sensitivity
// and volume are client concerns, and the server neither knows nor cares about them.

import {
  PRIMARY_WEAPONS,
  TEAM_COLORS,
  TEAM_NAMES,
  WEAPON_BLURBS,
  type PrimaryWeaponId,
  type Team,
} from '@shared/protocol';

export interface MenuSettings {
  name: string;
  team: Team | null;
  primary: PrimaryWeaponId;
  sensitivity: number;
  volume: number;
  invertY: boolean;
  fov: number;
  quality: 'low' | 'high';
  /** Tone-mapping exposure. Monitors vary enormously; one baked-in value fits nobody. */
  brightness: number;
  crosshairShape: 'cross' | 'tshape' | 'dot' | 'circle';
  crosshairColor: string;
  crosshairThickness: number;
  crosshairLength: number;
  crosshairDot: boolean;
  crosshairOutline: boolean;
}

// Bumped from v1 because the shape changed; a stale v1 blob would leave `primary`
// undefined and the loadout picker blank.
const STORAGE_KEY = 'webstrike.settings.v4';

const DEFAULTS: MenuSettings = {
  name: '',
  team: null,
  primary: 'rifle',
  sensitivity: 1.0,
  volume: 0.6,
  invertY: false,
  fov: 90,
  quality: 'high',
  brightness: 1.25,
  crosshairShape: 'cross',
  // Cyan-green reads clearly against concrete, brick, sky and blood alike, which is why
  // most shooters converge on roughly this hue.
  crosshairColor: '#4dffb8',
  crosshairThickness: 2,
  crosshairLength: 7,
  crosshairDot: false,
  crosshairOutline: true,
};

const CROSSHAIR_COLORS = ['#4dffb8', '#ffffff', '#ffd93d', '#ff4d6d', '#4ea8ff', '#b45cff'];

const WEAPON_LABELS: Record<PrimaryWeaponId, string> = {
  rifle: 'Rifle',
  smg: 'SMG',
  sniper: 'Sniper',
  shotgun: 'Shotgun',
};

export function loadSettings(): MenuSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULTS };
    return { ...DEFAULTS, ...(JSON.parse(raw) as Partial<MenuSettings>) };
  } catch {
    // Corrupt or unavailable storage (private mode, disabled cookies) must not block play.
    return { ...DEFAULTS };
  }
}

export function saveSettings(s: MenuSettings): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch {
    /* not important enough to interrupt the player */
  }
}

export class Menu {
  private root: HTMLElement;
  settings: MenuSettings;

  onPlay: ((settings: MenuSettings) => void) | null = null;
  onResume: (() => void) | null = null;
  onQuit: (() => void) | null = null;
  onSettingsChange: ((settings: MenuSettings) => void) | null = null;

  constructor(container: HTMLElement) {
    this.settings = loadSettings();
    this.root = document.createElement('div');
    this.root.className = 'menu active';
    container.appendChild(this.root);
    this.renderMain();
  }

  // ── screens ─────────────────────────────────────────────────────────────────

  renderMain(status = ''): void {
    this.root.classList.add('active');
    this.root.innerHTML = `
      <div class="menu-panel">
        <h1>WEB<span>STRIKE</span></h1>
        <p class="tagline">Team Deathmatch · server-authoritative · bots included</p>

        <label class="field">
          <span>Callsign</span>
          <input id="m-name" type="text" maxlength="16" placeholder="Recruit"
                 value="${escapeAttr(this.settings.name)}" />
        </label>

        <div class="field">
          <span>Team</span>
          <div class="team-picker">
            <button data-team="auto" class="team-btn">Auto</button>
            <button data-team="A" class="team-btn" style="--c:${TEAM_COLORS.A}">${TEAM_NAMES.A}</button>
            <button data-team="B" class="team-btn" style="--c:${TEAM_COLORS.B}">${TEAM_NAMES.B}</button>
          </div>
        </div>

        <div class="field">
          <span>Primary weapon</span>
          <div class="weapon-picker">
            ${PRIMARY_WEAPONS.map(
              (w) => `<button data-weapon="${w}" class="weapon-btn">
                 <b>${WEAPON_LABELS[w]}</b>
               </button>`,
            ).join('')}
          </div>
          <div class="weapon-blurb"></div>
        </div>

        ${this.settingsMarkup()}

        <button id="m-play" class="primary">Deploy</button>
        <div class="status">${escapeHtml(status)}</div>

        <details class="controls">
          <summary>Controls</summary>
          <div class="control-grid">
            <b>W A S D</b><span>Move</span>
            <b>Space</b><span>Jump</span>
            <b>Shift</b><span>Sprint</span>
            <b>Mouse</b><span>Look</span>
            <b>Left click</b><span>Fire</span>
            <b>R</b><span>Reload</span>
            <b>Right click</b><span>Aim down sights</span>
            <b>1 / 2 / 3</b><span>Primary / Pistol / Knife</span>
            <b>Wheel</b><span>Cycle weapon</span>
            <b>G</b><span>Drop weapon</span>
            <b>Tab</b><span>Scoreboard</span>
            <b>Y</b><span>Chat</span>
            <b>F3</b><span>Net graph</span>
            <b>Esc</b><span>Menu</span>
          </div>
        </details>
      </div>`;

    this.wireSettings();
    this.updateTeamButtons();
    this.updateWeaponButtons();

    this.root.querySelectorAll<HTMLElement>('.weapon-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        this.settings.primary = btn.dataset.weapon as PrimaryWeaponId;
        this.updateWeaponButtons();
        this.persist();
      });
    });

    this.root.querySelectorAll<HTMLElement>('.team-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const t = btn.dataset.team;
        this.settings.team = t === 'auto' ? null : (t as Team);
        this.updateTeamButtons();
        this.persist();
      });
    });

    const nameInput = this.root.querySelector<HTMLInputElement>('#m-name')!;
    const play = this.root.querySelector<HTMLButtonElement>('#m-play')!;
    const start = () => {
      this.settings.name = nameInput.value.trim();
      this.persist();
      play.disabled = true;
      play.textContent = 'Connecting…';
      this.onPlay?.(this.settings);
    };
    play.addEventListener('click', start);
    nameInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') start();
    });
    nameInput.focus();
  }

  renderPause(): void {
    this.root.classList.add('active');
    this.root.innerHTML = `
      <div class="menu-panel">
        <h2>Paused</h2>
        ${this.settingsMarkup()}
        <button id="m-resume" class="primary">Resume</button>
        <button id="m-quit" class="ghost">Leave match</button>
      </div>`;
    this.wireSettings();
    this.root.querySelector('#m-resume')!.addEventListener('click', () => this.onResume?.());
    this.root.querySelector('#m-quit')!.addEventListener('click', () => this.onQuit?.());
  }

  showError(message: string): void {
    this.renderMain(message);
  }

  hide(): void {
    this.root.classList.remove('active');
  }

  get visible(): boolean {
    return this.root.classList.contains('active');
  }

  // ── settings widget (shared by both screens) ────────────────────────────────

  private settingsMarkup(): string {
    const s = this.settings;
    return `
      <div class="settings">
        <label class="slider">
          <span>Sensitivity <b id="v-sens">${s.sensitivity.toFixed(2)}</b></span>
          <input id="m-sens" type="range" min="0.1" max="4" step="0.05" value="${s.sensitivity}" />
        </label>
        <label class="slider">
          <span>Volume <b id="v-vol">${Math.round(s.volume * 100)}%</b></span>
          <input id="m-vol" type="range" min="0" max="1" step="0.05" value="${s.volume}" />
        </label>
        <label class="slider">
          <span>Field of view <b id="v-fov">${s.fov}</b></span>
          <input id="m-fov" type="range" min="70" max="110" step="1" value="${s.fov}" />
        </label>
        <label class="slider">
          <span>Brightness <b id="v-bright">${s.brightness.toFixed(2)}</b></span>
          <input id="m-bright" type="range" min="0.7" max="2.2" step="0.05" value="${s.brightness}" />
        </label>
        <label class="check">
          <input id="m-invert" type="checkbox" ${s.invertY ? 'checked' : ''} />
          <span>Invert vertical look</span>
        </label>
        <label class="check">
          <input id="m-quality" type="checkbox" ${s.quality === 'high' ? 'checked' : ''} />
          <span>High quality shadows</span>
        </label>
      </div>

      <details class="crosshair-settings">
        <summary>Crosshair</summary>
        <div class="xh-preview"><span class="xh-sample"></span></div>
        <div class="xh-row">
          ${(
            [
              ['cross', 'Cross'],
              ['tshape', 'T'],
              ['dot', 'Dot'],
              ['circle', 'Circle'],
            ] as const
          )
            .map(
              ([k, label]) =>
                `<button class="xh-shape" data-shape="${k}">${label}</button>`,
            )
            .join('')}
        </div>
        <div class="xh-row colors">
          ${CROSSHAIR_COLORS.map(
            (c) => `<button class="xh-color" data-color="${c}" style="--c:${c}"></button>`,
          ).join('')}
        </div>
        <label class="slider">
          <span>Thickness <b id="v-xht">${s.crosshairThickness}</b></span>
          <input id="m-xht" type="range" min="1" max="6" step="1" value="${s.crosshairThickness}" />
        </label>
        <label class="slider">
          <span>Length <b id="v-xhl">${s.crosshairLength}</b></span>
          <input id="m-xhl" type="range" min="2" max="20" step="1" value="${s.crosshairLength}" />
        </label>
        <label class="check">
          <input id="m-xhdot" type="checkbox" ${s.crosshairDot ? 'checked' : ''} />
          <span>Centre dot</span>
        </label>
        <label class="check">
          <input id="m-xhout" type="checkbox" ${s.crosshairOutline ? 'checked' : ''} />
          <span>Outline</span>
        </label>
      </details>`;
  }

  private wireSettings(): void {
    const bind = (id: string, readout: string, apply: (v: number) => void, fmt: (v: number) => string) => {
      const el = this.root.querySelector<HTMLInputElement>(id);
      const out = this.root.querySelector<HTMLElement>(readout);
      if (!el || !out) return;
      el.addEventListener('input', () => {
        const v = parseFloat(el.value);
        apply(v);
        out.textContent = fmt(v);
        this.persist();
      });
    };

    bind('#m-sens', '#v-sens', (v) => (this.settings.sensitivity = v), (v) => v.toFixed(2));
    bind('#m-vol', '#v-vol', (v) => (this.settings.volume = v), (v) => `${Math.round(v * 100)}%`);
    bind('#m-fov', '#v-fov', (v) => (this.settings.fov = v), (v) => String(v));
    bind(
      '#m-bright',
      '#v-bright',
      (v) => (this.settings.brightness = v),
      (v) => v.toFixed(2),
    );

    const invert = this.root.querySelector<HTMLInputElement>('#m-invert');
    invert?.addEventListener('change', () => {
      this.settings.invertY = invert.checked;
      this.persist();
    });

    const quality = this.root.querySelector<HTMLInputElement>('#m-quality');
    quality?.addEventListener('change', () => {
      this.settings.quality = quality.checked ? 'high' : 'low';
      this.persist();
    });

    // ── crosshair ────────────────────────────────────────────────────────────
    bind('#m-xht', '#v-xht', (v) => (this.settings.crosshairThickness = v), (v) => String(v));
    bind('#m-xhl', '#v-xhl', (v) => (this.settings.crosshairLength = v), (v) => String(v));

    this.root.querySelectorAll<HTMLElement>('.xh-shape').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        this.settings.crosshairShape = btn.dataset.shape as MenuSettings['crosshairShape'];
        this.updateCrosshairUi();
        this.persist();
      });
    });
    this.root.querySelectorAll<HTMLElement>('.xh-color').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        this.settings.crosshairColor = btn.dataset.color!;
        this.updateCrosshairUi();
        this.persist();
      });
    });

    const xhDot = this.root.querySelector<HTMLInputElement>('#m-xhdot');
    xhDot?.addEventListener('change', () => {
      this.settings.crosshairDot = xhDot.checked;
      this.updateCrosshairUi();
      this.persist();
    });
    const xhOut = this.root.querySelector<HTMLInputElement>('#m-xhout');
    xhOut?.addEventListener('change', () => {
      this.settings.crosshairOutline = xhOut.checked;
      this.updateCrosshairUi();
      this.persist();
    });

    this.updateCrosshairUi();
  }

  /** Keep the little preview swatch in step with the settings. */
  private updateCrosshairUi(): void {
    const s = this.settings;
    this.root.querySelectorAll<HTMLElement>('.xh-shape').forEach((b) => {
      b.classList.toggle('selected', b.dataset.shape === s.crosshairShape);
    });
    this.root.querySelectorAll<HTMLElement>('.xh-color').forEach((b) => {
      b.classList.toggle('selected', b.dataset.color === s.crosshairColor);
    });
    const sample = this.root.querySelector<HTMLElement>('.xh-sample');
    if (sample) {
      sample.style.setProperty('--c', s.crosshairColor);
      sample.style.setProperty('--t', `${s.crosshairThickness}px`);
      sample.style.setProperty('--l', `${s.crosshairLength}px`);
      sample.dataset.shape = s.crosshairShape;
      sample.classList.toggle('has-dot', s.crosshairDot);
    }
  }

  private updateWeaponButtons(): void {
    this.root.querySelectorAll<HTMLElement>('.weapon-btn').forEach((btn) => {
      btn.classList.toggle('selected', btn.dataset.weapon === this.settings.primary);
    });
    const blurb = this.root.querySelector<HTMLElement>('.weapon-blurb');
    if (blurb) blurb.textContent = WEAPON_BLURBS[this.settings.primary] ?? '';
  }

  private updateTeamButtons(): void {
    this.root.querySelectorAll<HTMLElement>('.team-btn').forEach((btn) => {
      const t = btn.dataset.team === 'auto' ? null : (btn.dataset.team as Team);
      btn.classList.toggle('selected', t === this.settings.team);
    });
  }

  private persist(): void {
    saveSettings(this.settings);
    this.onSettingsChange?.(this.settings);
  }
}

function escapeHtml(s: string): string {
  return s.replace(
    /[&<>"']/g,
    (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] as string,
  );
}

function escapeAttr(s: string): string {
  return escapeHtml(s);
}
