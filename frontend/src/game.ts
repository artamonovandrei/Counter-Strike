// path: frontend/src/game.ts
//
// Orchestration: owns the renderer, the fixed-rate input loop, and the wiring between
// network events and everything that reacts to them.
//
// Two clocks run here. Input is sampled at exactly the server's tick rate via an
// accumulator, so a 144 Hz monitor doesn't flood the server and a 30 Hz one doesn't
// starve it. Rendering runs at whatever rate the browser offers.

import * as THREE from 'three';
import {
  F_DEAD,
  F_RELOADING,
  K_ADS,
  K_FIRE,
  TEAM_NAMES,
  type GameEvent,
  type InputCmd,
  type Snapshot,
  type Team,
  type Welcome,
  type WeaponConfig,
  type WeaponId,
} from '@shared/protocol';
import { AudioEngine, spatialise } from './audio';
import { Effects } from './effects';
import { HUD, type CrosshairStyle, type ReticleKind } from './hud';
import { InputManager } from './input';
import type { MenuSettings } from './menu';
import { NetClient, SnapshotBuffer } from './net';
import { Predictor } from './predict';
import { RemotePlayers } from './remote';
import {
  applyFog,
  buildLevel,
  buildLights,
  buildSky,
  CollisionWorld,
  configureRenderer,
} from './world';

const MAX_FRAME_DT = 0.1; // never simulate more than 100 ms of catch-up in one frame

export class Game {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera: THREE.PerspectiveCamera;

  private net = new NetClient();
  private snapshots = new SnapshotBuffer();
  private input: InputManager;
  private hud: HUD | null = null;
  private effects: Effects | null = null;
  private remotes: RemotePlayers | null = null;
  private predictor: Predictor | null = null;
  private world: CollisionWorld | null = null;
  private audio = new AudioEngine();

  private welcome: Welcome | null = null;
  private weapons = new Map<WeaponId, WeaponConfig>();
  private myId = 0;
  private myTeam: Team = 'A';

  private inputSeq = 0;
  private accumulator = 0;
  private tickDt = 1 / 60;
  private outgoing: InputCmd[] = [];

  private running = false;
  private paused = false;
  private lastFrame = 0;
  private fps = 60;
  private fpsAccum = 0;
  private fpsFrames = 0;

  private currentWeapon: WeaponId = 'rifle';
  private lastLocalShotAt = 0;
  private prevFireHeld = false;
  private prevYaw = 0;
  private prevPitch = 0;
  private wasDead = false;
  private lastPhase = '';
  private selfAmmo = 0;
  private selfReloading = false;
  private maxAnisotropy = 4;
  /** Locally predicted sight-raise, 0..1. Predicted so zoom is instant, not a round trip. */
  private adsProgress = 0;
  private adsHeld = false;
  private scopeVisible = false;

  onExit: (() => void) | null = null;
  onFatal: ((message: string) => void) | null = null;

  constructor(
    canvas: HTMLCanvasElement,
    private overlay: HTMLElement,
    private settings: MenuSettings,
  ) {
    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      powerPreference: 'high-performance',
    });
    // Cap at 2x: on a 3x phone-class display the shading cost triples for a difference
    // nobody can see at arm's length.
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(window.innerWidth, window.innerHeight);
    configureRenderer(this.renderer, settings.quality, settings.brightness);
    this.maxAnisotropy = Math.min(8, this.renderer.capabilities.getMaxAnisotropy());

    this.camera = new THREE.PerspectiveCamera(
      settings.fov,
      window.innerWidth / window.innerHeight,
      0.05,
      500,
    );
    this.camera.rotation.order = 'YXZ';

    this.input = new InputManager(canvas);
    this.input.settings = { sensitivity: settings.sensitivity, invertY: settings.invertY };
    this.audio.setVolume(settings.volume);

    window.addEventListener('resize', this.onResize);
    document.addEventListener('visibilitychange', this.onVisibility);
  }

  // ── lifecycle ───────────────────────────────────────────────────────────────

  async connect(): Promise<void> {
    this.net.setHandlers({
      onWelcome: (w) => this.onWelcome(w),
      onSnapshot: (s) => this.onSnapshot(s),
      onEvent: (e) => this.onEvent(e),
      onDisconnect: (reason) => this.onDisconnect(reason),
      onError: (msg) => this.hud?.toast(msg, 4000),
    });

    const match = await this.net.findMatch(
      this.settings.name || 'Recruit',
      this.settings.team,
      this.settings.primary,
    );
    await this.net.joinGame(match.ticket!);
  }

  private onWelcome(w: Welcome): void {
    this.welcome = w;
    this.myId = w.playerId;
    this.myTeam = w.team;
    this.tickDt = 1 / w.config.tickHz;
    for (const weapon of w.weapons) this.weapons.set(weapon.id, weapon);

    // ── scene ────────────────────────────────────────────────────────────────
    this.scene.clear();
    this.scene.add(buildSky(w.map));
    this.scene.add(buildLevel(w.map, this.maxAnisotropy));
    this.scene.add(buildLights(w.map, this.settings.quality));
    applyFog(this.scene, w.map);

    this.world = new CollisionWorld(w.map);
    this.predictor = new Predictor(this.world, w.config);
    this.remotes = new RemotePlayers(this.scene, w.team);
    this.effects = new Effects(this.scene, this.camera);
    this.currentWeapon = w.primary;
    this.effects.setWeapon(w.primary, this.weapons.get(w.primary) ?? null);

    this.hud = new HUD(this.overlay, w.team);
    this.hud.applyCrosshairStyle(crosshairStyleFrom(this.settings));
    this.hud.onChatSubmit = (msg) => this.net.sendChat(msg);
    this.hud.onChatOpenChange = (open) => {
      this.input.textEntryActive = open;
      if (open) this.input.exitLock();
      else this.input.requestLock();
    };
    this.hud.addNotice(`Joined ${TEAM_NAMES[w.team]} on ${w.map.name}`);

    this.input.onToggleScoreboard = (v) => this.hud?.setScoreboardVisible(v);
    this.input.onChat = () => this.hud?.openChat();
    this.input.onDrop = () => this.net.dropWeapon();
    this.input.onNetGraph = () => this.hud?.setNetgraphVisible(!this.hud.netgraphVisible);
    this.input.onLockChange = (locked) => {
      if (!locked && !this.hud?.chatOpen) this.pause();
    };

    // Face the way the map's spawn point wants us to.
    this.input.yaw = 0;
    this.input.pitch = 0;

    this.audio.init();
    this.running = true;
    this.lastFrame = performance.now();
    requestAnimationFrame(this.frame);
    this.input.requestLock();
  }

  private onDisconnect(reason: string): void {
    if (!this.running) return;
    this.running = false;
    this.onFatal?.(
      reason === 'io server disconnect'
        ? 'Disconnected by the server.'
        : 'Connection lost. Check your network and try again.',
    );
  }

  pause(): void {
    if (!this.running || this.paused) return;
    this.paused = true;
    this.input.exitLock();
    this.onPauseRequested?.();
  }

  onPauseRequested: (() => void) | null = null;

  resume(): void {
    this.paused = false;
    this.input.requestLock();
  }

  applySettings(s: MenuSettings): void {
    this.settings = s;
    this.input.settings = { sensitivity: s.sensitivity, invertY: s.invertY };
    this.audio.setVolume(s.volume);
    // Applied live, so dragging the brightness slider or restyling the crosshair in the
    // pause menu shows the result on the frame behind it instead of after a rejoin.
    configureRenderer(this.renderer, s.quality, s.brightness);
    this.hud?.applyCrosshairStyle(crosshairStyleFrom(s));
    // Don't stomp the FOV mid-zoom; updateCamera will pick the new base up next frame.
    if (this.adsProgress === 0) {
      this.camera.fov = s.fov;
      this.camera.updateProjectionMatrix();
    }
  }

  destroy(): void {
    this.running = false;
    window.removeEventListener('resize', this.onResize);
    document.removeEventListener('visibilitychange', this.onVisibility);
    this.input.dispose();
    this.effects?.dispose();
    this.remotes?.dispose();
    this.hud?.destroy();
    this.net.disconnect();
    this.renderer.dispose();
  }

  private onResize = (): void => {
    this.camera.aspect = window.innerWidth / window.innerHeight;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(window.innerWidth, window.innerHeight);
  };

  private onVisibility = (): void => {
    // Rendering stops when the tab is hidden, but the socket stays open and snapshots
    // keep arriving — disconnecting a backgrounded player would be worse than a few
    // wasted packets.
    if (document.hidden) this.pause();
  };

  // ── network handlers ────────────────────────────────────────────────────────

  private onSnapshot(s: Snapshot): void {
    this.snapshots.push(s);
    const predictor = this.predictor;
    if (!predictor) return;

    const dead = (s.self.f & F_DEAD) !== 0;
    predictor.reconcile(s.self, s.ack, dead);

    if (s.self.w !== this.currentWeapon) {
      this.currentWeapon = s.self.w;
      this.effects?.setWeapon(s.self.w, this.weapons.get(s.self.w) ?? null);
      // A weapon swap drops the sights; don't leave the camera zoomed into a pistol.
      this.adsProgress = 0;
    }
    this.selfAmmo = s.self.am;
    this.selfReloading = (s.self.f & F_RELOADING) !== 0;

    const hud = this.hud;
    if (hud) {
      const def = this.weapons.get(s.self.w);
      hud.setVitals(s.self.hp, s.self.ar);
      hud.setAmmo(def?.name ?? s.self.w, s.self.am, s.self.rs, def?.melee ?? false);
      hud.setReloading((s.self.f & F_RELOADING) !== 0);
      hud.setRound(s.ph, s.pt, s.sc.A, s.sc.B);
      hud.setRespawn(dead ? s.self.rt : 0);
      hud.setLoadout(s.self.sl ?? [], s.self.w, this.weapons);
    }

    if (dead && !this.wasDead) this.audio.play('death', 0, 0.9);
    if (!dead && this.wasDead) this.audio.play('spawn', 0, 0.7);
    this.wasDead = dead;

    if (s.ph !== this.lastPhase) {
      this.lastPhase = s.ph;
      if (s.ph === 'live') this.hud?.toast('Round start', 1800);
    }
  }

  private onEvent(e: GameEvent): void {
    const hud = this.hud;
    const effects = this.effects;
    const px = this.predictor?.renderX() ?? 0;
    const pz = this.predictor?.renderZ() ?? 0;

    switch (e.e) {
      case 'shot': {
        // Our own shot was already drawn locally the instant we pulled the trigger;
        // drawing the server's echo too would double every muzzle flash.
        if (e.id === this.myId) return;
        const origin = new THREE.Vector3(e.o[0], e.o[1], e.o[2]);
        const dir = new THREE.Vector3(e.d[0], e.d[1], e.d[2]).normalize();
        // Push the flash forward out of the shooter's face, toward the muzzle.
        effects?.muzzleFlash(origin.clone().addScaledVector(dir, 0.5), 0.85);
        const { pan, gain } = spatialise(px, pz, this.input.yaw, e.o[0], e.o[2]);
        this.audio.play(soundFor(e.w), pan, gain);
        break;
      }
      case 'tracer': {
        // The server sends one of these per projectile, so a shotgun blast really does
        // draw nine diverging tracers rather than one fudged average.
        if (e.id === this.myId) return;
        const origin = new THREE.Vector3(e.o[0], e.o[1], e.o[2]);
        const end = new THREE.Vector3(e.p[0], e.p[1], e.p[2]);
        effects?.tracer(origin.clone().addScaledVector(end.clone().sub(origin).normalize(), 0.5), end);
        break;
      }
      case 'impact': {
        effects?.impact(
          new THREE.Vector3(e.p[0], e.p[1], e.p[2]),
          new THREE.Vector3(e.n[0], e.n[1], e.n[2]),
          e.m,
        );
        break;
      }
      case 'hit': {
        hud?.showHitMarker(e.hs, e.kill);
        this.audio.play(e.hs ? 'headshot' : 'hit', 0, 0.8);
        // Blood on the body you actually hit, so you can see where shots are landing.
        const victim = this.remotes?.positionOf(e.vid);
        if (victim) {
          effects?.bloodPuff(
            new THREE.Vector3(victim.x, victim.y + 1.1, victim.z),
            new THREE.Vector3(0, 1, 0),
          );
        }
        break;
      }
      case 'hurt': {
        hud?.flashDamage(0.2 + Math.min(0.5, e.amt / 100));
        this.audio.play('hurt', 0, 0.7);
        break;
      }
      case 'kick': {
        // Recoil moves the player's actual view — controlling a spray means pulling back
        // against this, exactly as the server's spread model assumes.
        this.input.applyKick(e.y, e.p);
        break;
      }
      case 'kill': {
        hud?.addKill(e.k, e.v, e.w, e.hs, e.team);
        if (e.vid === this.myId) hud?.toast(`Eliminated by ${e.k}`, 2200);
        break;
      }
      case 'spawn': {
        if (e.id !== this.myId) {
          const { pan, gain } = spatialise(px, pz, this.input.yaw, e.p[0], e.p[2], 40);
          this.audio.play('spawn', pan, gain * 0.4);
        }
        break;
      }
      case 'round': {
        if (e.ph === 'intermission' && e.winner) {
          const label =
            e.winner === 'draw'
              ? 'Round drawn'
              : e.winner === this.myTeam
                ? 'Your team wins the round'
                : `${TEAM_NAMES[e.winner as Team]} wins the round`;
          hud?.toast(label, 4000);
        }
        break;
      }
      case 'join': {
        this.remotes?.setNameFor(e.id, e.name, e.team);
        if (e.id !== this.myId && !e.bot) hud?.addNotice(`${e.name} joined`);
        break;
      }
      case 'leave': {
        this.remotes?.remove(e.id);
        break;
      }
      case 'chat': {
        hud?.addChat(e.name, e.team, e.msg);
        this.audio.play('ui', 0, 0.4);
        break;
      }
      case 'scoreboard': {
        hud?.setScoreboard(e.rows);
        for (const row of e.rows) this.remotes?.setNameFor(row.id, row.name, row.team);
        break;
      }
      case 'switch': {
        if (e.id === this.myId) this.audio.play('switch', 0, 0.5);
        break;
      }
      default:
        break;
    }
  }

  // ── the loop ────────────────────────────────────────────────────────────────

  private frame = (now: number): void => {
    if (!this.running) return;
    requestAnimationFrame(this.frame);

    let dt = (now - this.lastFrame) / 1000;
    this.lastFrame = now;
    if (dt > MAX_FRAME_DT) dt = MAX_FRAME_DT;
    if (dt <= 0) return;

    this.fpsAccum += dt;
    this.fpsFrames++;
    if (this.fpsAccum >= 0.5) {
      this.fps = this.fpsFrames / this.fpsAccum;
      this.fpsAccum = 0;
      this.fpsFrames = 0;
    }

    if (!this.paused) {
      this.input.applyMouse();
      this.effects?.addSway(this.input.yaw - this.prevYaw, this.input.pitch - this.prevPitch);
      this.prevYaw = this.input.yaw;
      this.prevPitch = this.input.pitch;

      this.sampleInputs(dt, now);
    }

    this.updateAds(dt);
    this.predictor?.update(dt);
    this.updateCamera();
    this.updateRemotes(dt);
    this.effects?.update(dt);
    this.effects?.updateViewModel(
      dt,
      {
        speed: this.predictor?.speed() ?? 0,
        grounded: this.predictor?.grounded ?? true,
        adsProgress: this.adsProgress,
        reloading: this.selfReloading,
        dead: this.wasDead,
      },
      this.welcome!.config,
    );
    this.updateHud(now);

    this.renderer.render(this.scene, this.camera);
  };

  /**
   * Raise and lower the sights locally.
   *
   * Predicted rather than driven by the snapshot for the same reason movement is: a
   * 100 ms delay between pressing the button and the zoom starting feels broken, even
   * though the server is the one that decides what the spread actually was.
   */
  private updateAds(dt: number): void {
    const def = this.weapons.get(this.currentWeapon);
    const canAds = !!def && def.adsFov > 0 && !this.wasDead && !this.selfReloading;
    const want = this.adsHeld && canAds;
    const time = Math.max(0.05, def?.adsTime ?? 0.2);
    const step = dt / time;
    this.adsProgress = want
      ? Math.min(1, this.adsProgress + step)
      : Math.max(0, this.adsProgress - step);

    // Scope overlay replaces the model at high zoom for genuinely scoped weapons only.
    const scoped = def?.scope === true && this.adsProgress > 0.82;
    if (scoped !== this.scopeVisible) {
      this.scopeVisible = scoped;
      this.hud?.setScope(scoped);
    }
  }

  /** Produce input commands at exactly the server tick rate. */
  private sampleInputs(dt: number, now: number): void {
    this.accumulator += dt;
    // Bound the catch-up: a long stall should not release a burst of commands the server
    // would only throw away.
    const maxSteps = 4;
    let steps = 0;
    this.outgoing.length = 0;

    while (this.accumulator >= this.tickDt && steps < maxSteps) {
      this.accumulator -= this.tickDt;
      steps++;

      const keys = this.input.keyMask();
      this.adsHeld = (keys & K_ADS) !== 0;
      const slot = this.input.consumeWeaponRequest(this.weapons.get(this.currentWeapon)?.slot ?? 1);

      this.inputSeq++;
      const cmd: InputCmd = {
        s: this.inputSeq,
        dt: this.tickDt * 1000,
        k: keys,
        y: this.input.yaw,
        p: this.input.pitch,
        w: slot,
      };

      this.predictor?.apply(cmd);
      this.outgoing.push(cmd);
      this.predictLocalFire(keys, now);
    }
    if (this.accumulator > this.tickDt * maxSteps) this.accumulator = 0;

    this.net.sendInputBatch(this.outgoing);
  }

  /**
   * Draw and sound our own gunshot immediately rather than waiting for the server echo.
   *
   * The server remains the authority on whether the shot happened and what it hit — this
   * only predicts the *presentation*. Worst case on a mispredict is one extra muzzle
   * flash, which nobody notices; the alternative is a full round-trip of delay between
   * clicking and hearing your own gun, which everybody notices.
   */
  private predictLocalFire(keys: number, now: number): void {
    const def = this.weapons.get(this.currentWeapon);
    const predictor = this.predictor;
    if (!def || !predictor || this.wasDead) {
      this.prevFireHeld = false;
      return;
    }

    const firing = (keys & K_FIRE) !== 0;
    if (!firing) {
      this.prevFireHeld = false;
      return;
    }
    const wasHeld = this.prevFireHeld;
    this.prevFireHeld = true;
    if (!def.auto && wasHeld) return;

    // Don't predict a shot the server is certain to refuse.
    if (this.selfReloading) return;
    if (!def.melee && this.selfAmmo <= 0) {
      if (!wasHeld) this.audio.play('empty', 0, 0.6);
      return;
    }

    const interval = 60000 / def.rpm;
    if (now - this.lastLocalShotAt < interval) return;
    this.lastLocalShotAt = now;

    const eye = new THREE.Vector3(
      predictor.renderX(),
      predictor.renderY() + this.welcome!.config.eyeHeight,
      predictor.renderZ(),
    );

    if (def.melee) {
      this.audio.play('knife', 0, 0.9);
      this.effects?.fired(0.3);
      return;
    }

    this.effects?.fired(recoilStrength(def.id));
    this.audio.play(soundFor(def.id), 0, 1);

    // Tracers start at the view model's muzzle so they line up with the flash instead of
    // sprouting from the player's forehead.
    const muzzle = this.effects?.viewMuzzleWorld() ?? eye;

    // Predict the same cone shape the server uses. It won't match shot for shot — the
    // server has its own RNG — but the *spread* looks right, which is what the player
    // actually reads, especially for a shotgun.
    const spreadDeg = this.currentSpreadDeg(def);
    const pellets = Math.max(1, def.pellets);
    for (let i = 0; i < pellets; i++) {
      const cone = i === 0 ? spreadDeg * 0.25 : spreadDeg;
      const dir = this.spreadDirection(cone);
      const hit = this.world?.raycast(eye, dir, def.range);
      const end = hit ? hit.point : eye.clone().addScaledVector(dir, def.range);
      this.effects?.tracer(muzzle, end);
      if (hit) this.effects?.impact(hit.point, hit.normal, hit.material);
    }
  }

  /** Direction from the current aim, perturbed inside a cone of `deg` degrees. */
  private spreadDirection(deg: number): THREE.Vector3 {
    let yaw = this.input.yaw;
    let pitch = this.input.pitch;
    if (deg > 0) {
      const r = Math.sqrt(Math.random()) * deg * (Math.PI / 180);
      const theta = Math.random() * Math.PI * 2;
      yaw += Math.cos(theta) * r;
      pitch += Math.sin(theta) * r;
    }
    return new THREE.Vector3(
      -Math.sin(yaw) * Math.cos(pitch),
      Math.sin(pitch),
      -Math.cos(yaw) * Math.cos(pitch),
    );
  }

  /** Mirror of Arsenal.current_spread_deg, minus the per-shot bloom the server tracks. */
  private currentSpreadDeg(def: WeaponConfig): number {
    if (def.melee) return 0;
    const cfg = this.welcome!.config;
    const speed = this.predictor?.speed() ?? 0;
    let spread = def.spreadBase;
    if (!this.predictor?.grounded) spread += def.spreadAir;
    else if (speed > 0.1) spread += def.spreadMove * Math.min(1, speed / cfg.sprintSpeed);
    if (this.adsProgress > 0) spread *= 1 + (def.adsSpreadMult - 1) * this.adsProgress;
    return spread;
  }

  private updateCamera(): void {
    const p = this.predictor;
    if (!p || !this.welcome) return;
    this.camera.position.set(
      p.renderX(),
      p.renderY() + this.welcome.config.eyeHeight,
      p.renderZ(),
    );
    this.camera.rotation.y = this.input.yaw;
    this.camera.rotation.x = this.input.pitch;

    // Zoom follows the sights. Interpolating the FOV rather than snapping is what makes
    // scoping read as raising a weapon instead of teleporting the camera.
    const def = this.weapons.get(this.currentWeapon);
    const targetFov =
      def && def.adsFov > 0
        ? THREE.MathUtils.lerp(this.settings.fov, def.adsFov, easeInOut(this.adsProgress))
        : this.settings.fov;
    if (Math.abs(this.camera.fov - targetFov) > 0.01) {
      this.camera.fov = targetFov;
      this.camera.updateProjectionMatrix();
    }

    // Mouse sensitivity scales with the zoom, or a scoped sniper is unusable.
    this.input.zoomFactor = targetFov / this.settings.fov;
  }

  private updateRemotes(dt: number): void {
    if (!this.remotes || !this.welcome) return;
    // Render remote entities in the past by the interpolation delay. The server's clock
    // is tracked via the newest snapshot's timestamp plus local elapsed time.
    const elapsed = performance.now() - this.net.lastServerTimeAt;
    const renderTime =
      this.net.lastServerTime + elapsed - this.welcome.config.interpDelayMs;
    const pair = this.snapshots.pair(renderTime);
    if (!pair) return;
    this.remotes.update(pair.from, pair.to, pair.alpha, dt);
  }

  private updateHud(now: number): void {
    const hud = this.hud;
    if (!hud) return;
    hud.update(now);

    const def = this.weapons.get(this.currentWeapon);
    if (def && this.predictor) {
      // The crosshair gap tracks the real spread model, so it is honest feedback rather
      // than decoration — including the fact that a hip-fired sniper is a shotgun.
      hud.setSpread(4 + this.currentSpreadDeg(def) * 6);
      // While sighted, the weapon's own optic takes over from the HUD crosshair. Showing
      // both at once is the classic way to make aiming look wrong.
      const sighted = this.adsProgress > 0.45;
      hud.setCrosshairVisible(!this.scopeVisible && !sighted);
      hud.setReticle(reticleFor(def), this.adsProgress);
    }

    const latest = this.snapshots.latest();
    hud.setNetgraph({
      fps: this.fps,
      ping: this.net.ping,
      snapshots: this.net.snapshotCount,
      pending: this.predictor?.pendingCount() ?? 0,
      correction: this.predictor?.lastCorrection ?? 0,
      hardSnaps: this.predictor?.hardSnaps ?? 0,
      entities: latest?.ents.length ?? 0,
    });
  }
}

/** Which synthesised report a weapon uses. */
function soundFor(w: WeaponId): 'rifle' | 'smg' | 'sniper' | 'shotgun' | 'pistol' | 'knife' {
  switch (w) {
    case 'smg':
      return 'smg';
    case 'sniper':
      return 'sniper';
    case 'shotgun':
      return 'shotgun';
    case 'pistol':
      return 'pistol';
    case 'knife':
      return 'knife';
    default:
      return 'rifle';
  }
}

/** How hard the view model kicks, relative to the rifle. */
function recoilStrength(w: WeaponId): number {
  switch (w) {
    case 'sniper':
      return 2.6;
    case 'shotgun':
      return 2.2;
    case 'pistol':
      return 1.1;
    case 'smg':
      return 0.7;
    default:
      return 1;
  }
}

function easeInOut(t: number): number {
  return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
}

/** The optic each weapon actually carries, matching its model. */
function reticleFor(def: WeaponConfig): ReticleKind {
  if (def.scope) return 'none'; // the full scope overlay handles this one
  switch (def.id) {
    case 'rifle':
    case 'smg':
      return 'dot';
    case 'shotgun':
      return 'bead';
    case 'pistol':
      return 'irons';
    default:
      return 'none';
  }
}

function crosshairStyleFrom(s: MenuSettings): CrosshairStyle {
  return {
    shape: s.crosshairShape,
    color: s.crosshairColor,
    thickness: s.crosshairThickness,
    length: s.crosshairLength,
    dot: s.crosshairDot,
    outline: s.crosshairOutline,
  };
}
